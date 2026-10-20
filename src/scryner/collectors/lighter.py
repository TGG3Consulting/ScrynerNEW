"""Lighter (zkLighter) perpetuals (``wss://mainnet.zklighter.elliot.ai/stream``).

Channels: ``trade/<market_id>``, ``order_book/<market_id>``, ``market_stats/<market_id>``
(open_interest, daily_quote_token_volume, daily_price_change, mark_price). Markets:
``/api/v1/orderBooks`` (symbol, market_id, price/size decimals).
"""

from __future__ import annotations

from typing import Any

import orjson

from scryner.core.models import Bar, Instrument
from scryner.core.timeutil import now, parse_ts

from . import register
from .base import Collector, WsClient, chunks, f


@register("lighter_futures")
class LighterFutures(Collector):
    def __init__(self, cfg: Any, sink: Any) -> None:
        super().__init__(cfg, sink)
        self._by_market_id: dict[str, Instrument] = {}

    async def fetch_instruments(self) -> list[Instrument]:
        d = await self.rest.get("/api/v1/orderBooks")
        out: list[Instrument] = []
        self._by_market_id.clear()
        for s in d.get("order_books", []):
            if s.get("status") not in (None, "active"):
                continue
            sym = s["symbol"]
            inst = self.make_instrument(
                native_symbol=str(s["market_id"]), pair=sym, base=sym, quote="USDC",
                tick_size=10 ** (-int(s.get("supported_price_decimals", 0))), step_size=10 ** (-int(s.get("supported_size_decimals", 0))),
                base_asset_id=str(s["market_id"]),
            )
            out.append(inst)
            self._by_market_id[str(s["market_id"])] = inst
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        for i, batch in enumerate(chunks(instruments, self.cfg.streams_per_connection)):
            ids = [x.native_symbol for x in batch]

            async def on_open(ws: WsClient, ids: list[str] = ids) -> None:
                for mid in ids:
                    await ws.send_json({"type": "subscribe", "channel": f"trade/{mid}"})
                    await ws.send_json({"type": "subscribe", "channel": f"market_stats/{mid}"})
                    if self.cfg.depth_enabled:
                        await ws.send_json({"type": "subscribe", "channel": f"order_book/{mid}"})

            self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=None,
                                 app_ping=lambda: orjson.dumps({"type": "ping"}).decode(), app_ping_interval=20))

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        t = msg.get("type", "")
        ch: str = msg.get("channel", "")
        mid = ch.split(":")[-1] if ":" in ch else ch.split("/")[-1]
        inst = self._by_market_id.get(str(mid))
        if inst is None:
            return
        if t.endswith("trade"):
            for tr in msg.get("trades", []):
                side = "buy" if not tr.get("is_maker_ask", True) else "sell"
                # is_maker_ask=true → taker bought from an ask → buy
                side = "buy" if tr.get("is_maker_ask") else "sell"
                self.emit_trade(inst, parse_ts(tr.get("timestamp")), f(tr.get("price")), f(tr.get("size")), side, str(tr.get("trade_id", "")))
        elif t.endswith("order_book"):
            ob = msg.get("order_book", {})
            bids = [(f(x["price"]), f(x["size"])) for x in ob.get("bids", [])]
            asks = [(f(x["price"]), f(x["size"])) for x in ob.get("asks", [])]
            self.emit_book(inst, parse_ts(msg.get("timestamp")), bids, asks, t == "subscribed/order_book", seq=int(ob.get("offset", 0)))
        elif t.endswith("market_stats"):
            st = msg.get("market_stats", {})
            ts = parse_ts(None)
            mark = f(st.get("mark_price"))
            self.emit_ticker(inst, ts, volume_24h=f(st.get("daily_quote_token_volume")), price_change_24h_pct=f(st.get("daily_price_change")),
                             last_price=f(st.get("last_trade_price")) or None, fair_price=mark or None,
                             high_24h=f(st.get("daily_price_high")), low_24h=f(st.get("daily_price_low")))
            if st.get("open_interest") is not None:
                oi = f(st.get("open_interest"))
                self.emit_oi(inst, ts, oi, oi * mark if mark else None)

    async def fetch_klines_1m(self, inst: Instrument, limit: int) -> list[Bar]:
        end = int(now())
        d = await self.rest.get("/api/v1/candlesticks", params={"market_id": inst.native_symbol, "resolution": "1m", "start_timestamp": end - limit * 60, "end_timestamp": end, "count_back": min(limit, 1000)}, retries=1)
        out: list[Bar] = []
        now_min = int(now() // 60) * 60
        for r in d.get("candlesticks", []):
            ts = int(int(r["timestamp"]) // 1000) if int(r["timestamp"]) > 1e11 else int(r["timestamp"])
            if ts >= now_min:
                continue
            total = f(r.get("quote_token_volume"))
            out.append(Bar(ts=ts, o=f(r["open"]), h=f(r["high"]), l=f(r["low"]), c=f(r["close"]), total_quote=total, trades_count=int(r.get("trades") or 0), had_trade=total > 0))
        return out
