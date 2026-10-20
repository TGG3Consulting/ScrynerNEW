"""Coinbase Exchange market data feed (``wss://ws-feed.exchange.coinbase.com``).

Channels: ``matches`` (trades), ``level2_batch`` (snapshot + l2update), ``ticker``
(volume_24h in base, price). Products: ``/products``. 24h quote stats via ``/products/<id>/stats``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from scryner.core.models import Instrument
from scryner.core.timeutil import now, parse_ts

from . import register
from .base import Collector, WsClient, chunks, f, levels


@register("coinbase_spot")
class CoinbaseSpot(Collector):
    async def fetch_instruments(self) -> list[Instrument]:
        d = await self.rest.get("/products")
        out: list[Instrument] = []
        for s in d:
            if s.get("status") != "online" or s.get("trading_disabled"):
                continue
            out.append(self.make_instrument(
                native_symbol=s["id"], pair=s["id"], base=s["base_currency"], quote=s["quote_currency"],
                tick_size=f(s.get("quote_increment")), step_size=f(s.get("base_increment")),
            ))
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        for i, batch in enumerate(chunks(instruments, self.cfg.streams_per_connection)):
            ids = [x.native_symbol for x in batch]

            async def on_open(ws: WsClient, ids: list[str] = ids) -> None:
                channels: list[Any] = ["matches", "ticker"]
                if self.cfg.depth_enabled:
                    channels.append("level2_batch")
                await ws.send_json({"type": "subscribe", "product_ids": ids, "channels": channels})

            self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=20))

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        t = msg.get("type")
        inst = self.by_native.get(msg.get("product_id", ""))
        if inst is None:
            return
        if t in ("match", "last_match"):
            # side = maker side; taker is the opposite
            side = "sell" if msg.get("side") == "buy" else "buy"
            self.emit_trade(inst, parse_ts(msg.get("time")), f(msg.get("price")), f(msg.get("size")), side, str(msg.get("trade_id", "")))
        elif t == "snapshot":
            self.emit_book(inst, now(), levels(msg.get("bids", [])), levels(msg.get("asks", [])), True)
        elif t == "l2update":
            bids = [(f(p), f(s)) for side, p, s in msg.get("changes", []) if side == "buy"]
            asks = [(f(p), f(s)) for side, p, s in msg.get("changes", []) if side == "sell"]
            self.emit_book(inst, parse_ts(msg.get("time")), bids, asks, False)
        elif t == "ticker":
            price = f(msg.get("price"))
            open24 = f(msg.get("open_24h"))
            self.emit_ticker(inst, parse_ts(msg.get("time")), last_price=price, volume_24h=f(msg.get("volume_24h")) * price,
                             price_change_24h_pct=((price - open24) / open24 * 100.0) if open24 else None,
                             high_24h=f(msg.get("high_24h")), low_24h=f(msg.get("low_24h")))

    async def poll_tickers(self, instruments: list[Instrument]) -> None:
        sem = asyncio.Semaphore(5)

        async def one(inst: Instrument) -> None:
            async with sem:
                try:
                    d = await self.rest.get(f"/products/{inst.native_symbol}/stats", retries=0)
                except Exception:
                    return
                last, open_ = f(d.get("last")), f(d.get("open"))
                self.emit_ticker(inst, now(), volume_24h=f(d.get("volume")) * last, last_price=last,
                                 price_change_24h_pct=((last - open_) / open_ * 100.0) if open_ else None,
                                 high_24h=f(d.get("high")), low_24h=f(d.get("low")))

        await asyncio.gather(*(one(i) for i in instruments))
