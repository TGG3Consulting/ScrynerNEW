"""HTX (Huobi) USDT-margined linear swaps.

WS ``wss://api.hbdm.com/linear-swap-ws`` (gzip frames): ``market.<contract>.trade.detail``,
``market.<contract>.depth.step0``, ``market.<contract>.detail`` (24h stats). Server pings
``{"ping": ts}`` must be answered with ``{"pong": ts}``. Contracts:
``/linear-swap-api/v1/swap_contract_info`` (contract_size); OI: ``/linear-swap-api/v1/swap_open_interest``.
Trade ``amount`` is in contracts; ``trade_turnover`` is quote value.
"""

from __future__ import annotations

from typing import Any

from scryner.core.models import Instrument
from scryner.core.timeutil import now, parse_ts

from . import register
from .base import Collector, WsClient, chunks, f, levels


@register("htx_futures")
class HtxFutures(Collector):
    async def fetch_instruments(self) -> list[Instrument]:
        d = await self.rest.get("/linear-swap-api/v1/swap_contract_info", params={"business_type": "swap"})
        out: list[Instrument] = []
        for s in d.get("data", []):
            if s.get("contract_status") != 1:
                continue
            code = s["contract_code"]  # BTC-USDT
            base, _, quote = code.partition("-")
            out.append(self.make_instrument(
                native_symbol=code, pair=code, base=base, quote=quote, tick_size=f(s.get("price_tick")),
                step_size=f(s.get("contract_size"), 1.0), multiplier=f(s.get("contract_size"), 1.0),
                listed_at=parse_ts(s.get("create_date")) if s.get("create_date") else None,
            ))
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        for i, batch in enumerate(chunks(instruments, self.cfg.streams_per_connection)):
            codes = [x.native_symbol for x in batch]

            async def on_open(ws: WsClient, codes: list[str] = codes) -> None:
                for c in codes:
                    await ws.send_json({"sub": f"market.{c}.trade.detail", "id": f"t{c}"})
                    await ws.send_json({"sub": f"market.{c}.detail", "id": f"k{c}"})
                    if self.cfg.depth_enabled:
                        await ws.send_json({"sub": f"market.{c}.depth.step6", "id": f"d{c}"})

            client = WsClient(self.cfg.ws_url, self._make_handler(), name=f"ws-{i}", on_open=on_open, ping_interval=None, idle_timeout=60)
            self.add_ws(client)

    def _make_handler(self) -> Any:
        def handler(msg: Any) -> Any:
            return self._on_message(msg)

        return handler

    async def _on_message_async(self, ws: WsClient, msg: dict[str, Any]) -> None:
        await ws.send_json({"pong": msg["ping"]})

    def _on_message(self, msg: Any) -> Any:
        if not isinstance(msg, dict):
            return None
        if "ping" in msg:
            # answer the server ping on whichever client received it
            for c in self._ws_clients:
                if c.connected:
                    try:
                        return c.send_json({"pong": msg["ping"]})
                    except Exception:
                        continue
            return None
        ch: str = msg.get("ch", "")
        if not ch.startswith("market."):
            return None
        parts = ch.split(".")
        inst = self.by_native.get(parts[1])
        if inst is None:
            return None
        tick = msg.get("tick") or {}
        kind = ".".join(parts[2:])
        if kind == "trade.detail":
            for t in tick.get("data", []):
                self.emit_trade(inst, parse_ts(t.get("ts")), f(t.get("price")), f(t.get("amount")), "buy" if t.get("direction") == "buy" else "sell",
                                str(t.get("id", "")), quote=f(t.get("trade_turnover")) or None)
        elif kind.startswith("depth"):
            self.emit_book(inst, parse_ts(tick.get("ts") or msg.get("ts")), levels(tick.get("bids", [])), levels(tick.get("asks", [])), True, seq=int(tick.get("version", 0)))
        elif kind == "detail":
            open_ = f(tick.get("open"))
            close = f(tick.get("close"))
            self.emit_ticker(inst, parse_ts(msg.get("ts")), volume_24h=f(tick.get("trade_turnover")), last_price=close,
                             price_change_24h_pct=((close - open_) / open_ * 100.0) if open_ else None,
                             trades_24h=int(tick.get("count") or 0) or None, high_24h=f(tick.get("high")), low_24h=f(tick.get("low")))
        return None

    async def poll_open_interest(self, instruments: list[Instrument]) -> None:
        d = await self.rest.get("/linear-swap-api/v1/swap_open_interest", params={"business_type": "swap"})
        ts = now()
        for s in d.get("data", []):
            inst = self.by_native.get(s.get("contract_code", ""))
            if inst is None:
                continue
            self.emit_oi(inst, ts, f(s.get("volume")), f(s.get("value")) or None)
