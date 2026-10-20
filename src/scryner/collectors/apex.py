"""ApeX Omni perpetuals (``wss://quote.omni.apex.exchange/realtime_public?v=2``).

Topics: ``recentlyTrade.H.<SYM>``, ``orderBook200.H.<SYM>``, ``instrumentInfo.H.<SYM>``
(openInterest, turnover24h, price24hPcnt, markPrice). Symbols: ``/api/v3/symbols``
(``contractConfig.perpetualContract``).
"""

from __future__ import annotations

from typing import Any

import orjson

from scryner.core.models import Instrument
from scryner.core.timeutil import parse_ts

from . import register
from .base import Collector, WsClient, chunks, f, levels


@register("apex_futures")
class ApexFutures(Collector):
    async def fetch_instruments(self) -> list[Instrument]:
        d = await self.rest.get("/api/v3/symbols")
        data = d.get("data", {})
        contracts = (data.get("contractConfig") or {}).get("perpetualContract", [])
        out: list[Instrument] = []
        for s in contracts:
            if not s.get("enableTrade", True) or s.get("enableDisplay") is False:
                continue
            sym = s.get("crossSymbolName") or s.get("symbol")  # BTCUSDT
            base = s.get("underlyingCurrencyId", "")
            quote = s.get("settleCurrencyId", "USDT")
            out.append(self.make_instrument(
                native_symbol=sym, pair=sym, base=base, quote=quote, tick_size=f(s.get("tickSize")), step_size=f(s.get("stepSize")),
                display_name=s.get("symbolDisplayName"),
            ))
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        for i, batch in enumerate(chunks(instruments, self.cfg.streams_per_connection)):
            syms = [x.native_symbol for x in batch]

            async def on_open(ws: WsClient, syms: list[str] = syms) -> None:
                args = [f"recentlyTrade.H.{s}" for s in syms] + [f"instrumentInfo.H.{s}" for s in syms]
                if self.cfg.depth_enabled:
                    args += [f"orderBook200.H.{s}" for s in syms]
                for part in chunks(args, 20):
                    await ws.send_json({"op": "subscribe", "args": list(part)})

            self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=None,
                                 app_ping=lambda: orjson.dumps({"op": "ping", "args": [str(int(parse_ts(None) * 1000))]}).decode(), app_ping_interval=20))

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict) or "topic" not in msg:
            return
        topic: str = msg["topic"]
        parts = topic.split(".")
        if len(parts) < 3:
            return
        inst = self.by_native.get(parts[2])
        if inst is None:
            return
        data = msg.get("data")
        if parts[0] == "recentlyTrade":
            for t in data or []:
                self.emit_trade(inst, parse_ts(t.get("T")), f(t.get("p")), f(t.get("v")), "buy" if t.get("S") == "Buy" else "sell", str(t.get("i", "")))
        elif parts[0].startswith("orderBook"):
            if isinstance(data, dict):
                self.emit_book(inst, parse_ts(msg.get("ts")), levels(data.get("b", [])), levels(data.get("a", [])), msg.get("type") == "snapshot", seq=int(data.get("u", 0)))
        elif parts[0] == "instrumentInfo":
            if isinstance(data, dict):
                ts = parse_ts(msg.get("ts"))
                fields: dict[str, Any] = {}
                if "turnover24h" in data:
                    fields["volume_24h"] = f(data["turnover24h"])
                if "price24hPcnt" in data:
                    fields["price_change_24h_pct"] = f(data["price24hPcnt"]) * 100.0
                if "lastPrice" in data:
                    fields["last_price"] = f(data["lastPrice"])
                if "markPrice" in data:
                    fields["fair_price"] = f(data["markPrice"])
                if fields:
                    self.emit_ticker(inst, ts, **fields)
                if "openInterest" in data:
                    self.emit_oi(inst, ts, f(data["openInterest"]), None)
