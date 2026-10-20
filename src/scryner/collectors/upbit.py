"""Upbit spot (``wss://api.upbit.com/websocket/v1``).

Subscription: ``[{"ticket":"…"},{"type":"trade","codes":[...]},{"type":"orderbook","codes":[...]},
{"type":"ticker","codes":[...]}]``. Markets: ``/v1/market/all``. Prices in KRW/USDT/BTC.
"""

from __future__ import annotations

import uuid
from typing import Any

from scryner.core.models import Instrument
from scryner.core.timeutil import parse_ts

from . import register
from .base import Collector, WsClient, chunks, f


@register("upbit_spot")
class UpbitSpot(Collector):
    async def fetch_instruments(self) -> list[Instrument]:
        d = await self.rest.get("/v1/market/all", params={"is_details": "true"})
        out: list[Instrument] = []
        for s in d:
            code = s["market"]  # KRW-BTC
            quote, _, base = code.partition("-")
            if s.get("market_event", {}).get("warning") is None and s.get("market_warning") == "CAUTION":
                pass
            out.append(self.make_instrument(native_symbol=code, pair=code, base=base, quote=quote, tick_size=0.0, step_size=0.0,
                                            display_name=s.get("english_name")))
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        for i, batch in enumerate(chunks(instruments, self.cfg.streams_per_connection)):
            codes = [x.native_symbol for x in batch]

            async def on_open(ws: WsClient, codes: list[str] = codes) -> None:
                payload: list[dict[str, Any]] = [{"ticket": str(uuid.uuid4())}, {"type": "trade", "codes": codes}, {"type": "ticker", "codes": codes}]
                if self.cfg.depth_enabled:
                    payload.append({"type": "orderbook", "codes": codes})
                payload.append({"format": "DEFAULT"})
                await ws.send_json(payload)

            self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=None,
                                 app_ping=lambda: "PING", app_ping_interval=60))

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        inst = self.by_native.get(msg.get("code", ""))
        if inst is None:
            return
        t = msg.get("type")
        if t == "trade":
            self.emit_trade(inst, parse_ts(msg.get("trade_timestamp")), f(msg.get("trade_price")), f(msg.get("trade_volume")),
                            "buy" if msg.get("ask_bid") == "BID" else "sell", str(msg.get("sequential_id", "")))
        elif t == "orderbook":
            units = msg.get("orderbook_units", [])
            bids = [(f(u.get("bid_price")), f(u.get("bid_size"))) for u in units]
            asks = [(f(u.get("ask_price")), f(u.get("ask_size"))) for u in units]
            self.emit_book(inst, parse_ts(msg.get("timestamp")), bids, asks, True)
        elif t == "ticker":
            self.emit_ticker(inst, parse_ts(msg.get("timestamp")), volume_24h=f(msg.get("acc_trade_price_24h")),
                             price_change_24h_pct=f(msg.get("signed_change_rate")) * 100.0, last_price=f(msg.get("trade_price")),
                             high_24h=f(msg.get("high_price")), low_24h=f(msg.get("low_price")))
