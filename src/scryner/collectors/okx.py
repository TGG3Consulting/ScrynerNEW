"""OKX v5 public WebSocket (USDT/USDC perpetual swaps and spot).

Channels: ``trades``, ``books`` (400 levels, snapshot + updates), ``tickers`` (volCcy24h),
``open-interest`` (swap), ``mark-price``. Instruments: ``/api/v5/public/instruments``.
Contract value: swaps are quoted in contracts, ``ctVal`` units of base per contract.
"""

from __future__ import annotations

from typing import Any

from scryner.core.models import Instrument
from scryner.core.timeutil import parse_ts

from . import register
from .base import Collector, WsClient, chunks, f, levels


class _Okx(Collector):
    inst_type = "SWAP"

    async def fetch_instruments(self) -> list[Instrument]:
        d = await self.rest.get("/api/v5/public/instruments", params={"instType": self.inst_type})
        out: list[Instrument] = []
        for s in d.get("data", []):
            if s.get("state") != "live":
                continue
            if self.inst_type == "SWAP":
                if s.get("ctType") != "linear":
                    continue
                base, quote = s.get("ctValCcy", ""), s.get("settleCcy", "")
                mult = f(s.get("ctVal"), 1.0)
                pair = s["instId"]
            else:
                base, quote = s.get("baseCcy", ""), s.get("quoteCcy", "")
                mult = 1.0
                pair = s["instId"]
            out.append(self.make_instrument(
                native_symbol=s["instId"], pair=pair, base=base, quote=quote, tick_size=f(s.get("tickSz")),
                step_size=f(s.get("lotSz")), multiplier=mult, listed_at=parse_ts(s.get("listTime")) if s.get("listTime") else None,
            ))
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        for i, batch in enumerate(chunks(instruments, self.cfg.streams_per_connection)):
            args = [{"channel": "trades", "instId": x.native_symbol} for x in batch]
            args += [{"channel": "tickers", "instId": x.native_symbol} for x in batch]
            if self.inst_type == "SWAP":
                args += [{"channel": "open-interest", "instId": x.native_symbol} for x in batch]
                args += [{"channel": "mark-price", "instId": x.native_symbol} for x in batch]
            if self.cfg.depth_enabled:
                args += [{"channel": "books", "instId": x.native_symbol} for x in batch]

            async def on_open(ws: WsClient, args: list[dict[str, str]] = args) -> None:
                for part in chunks(args, 100):
                    await ws.send_json({"op": "subscribe", "args": list(part)})

            self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=None,
                                 app_ping=lambda: "ping", app_ping_interval=25, decode=self._decode))

    @staticmethod
    def _decode(raw: str | bytes) -> Any:
        if raw == "pong" or raw == b"pong":
            return None
        return WsClient.default_decode(raw)

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict) or "arg" not in msg or "data" not in msg:
            return
        ch = msg["arg"].get("channel")
        inst = self.by_native.get(msg["arg"].get("instId", ""))
        if inst is None:
            return
        data = msg["data"]
        if ch == "trades":
            for t in data:
                self.emit_trade(inst, parse_ts(t.get("ts")), f(t.get("px")), f(t.get("sz")), "buy" if t.get("side") == "buy" else "sell", str(t.get("tradeId", "")))
        elif ch == "books":
            for b in data:
                self.emit_book(inst, parse_ts(b.get("ts")), levels(b.get("bids", [])), levels(b.get("asks", [])),
                               msg.get("action") == "snapshot", seq=int(b.get("seqId", 0)), prev_seq=int(b.get("prevSeqId", 0)))
        elif ch == "tickers":
            for t in data:
                last = f(t.get("last"))
                open24 = f(t.get("open24h"))
                chg = (last - open24) / open24 * 100.0 if open24 else None
                self.emit_ticker(inst, parse_ts(t.get("ts")), volume_24h=self._quote_vol(t, inst), price_change_24h_pct=chg, last_price=last, high_24h=f(t.get("high24h")), low_24h=f(t.get("low24h")))
        elif ch == "open-interest":
            for t in data:
                self.emit_oi(inst, parse_ts(t.get("ts")), f(t.get("oi")), None)
        elif ch == "mark-price":
            for t in data:
                self.emit_ticker(inst, parse_ts(t.get("ts")), fair_price=f(t.get("markPx")))

    def _quote_vol(self, t: dict[str, Any], inst: Instrument) -> float:
        # SWAP: volCcy24h is in base currency units; SPOT: volCcy24h is quote.
        if self.inst_type == "SWAP":
            return f(t.get("volCcy24h")) * f(t.get("last"))
        return f(t.get("volCcy24h"))


@register("okx_futures")
class OkxFutures(_Okx):
    inst_type = "SWAP"


@register("okx_spot")
class OkxSpot(_Okx):
    inst_type = "SPOT"
