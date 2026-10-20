"""Bybit v5 public WebSocket (linear perpetuals and spot).

Topics: ``publicTrade.<sym>``, ``orderbook.50.<sym>``, ``tickers.<sym>`` (open interest,
turnover24h, price24hPcnt, markPrice). Instruments: ``/v5/market/instruments-info``.
"""

from __future__ import annotations

from typing import Any

import orjson

from scryner.core.models import Bar, Instrument
from scryner.core.timeutil import now, parse_ts

from . import register
from .base import Collector, WsClient, chunks, f, levels


class _Bybit(Collector):
    category = "linear"

    async def fetch_instruments(self) -> list[Instrument]:
        out: list[Instrument] = []
        cursor = ""
        while True:
            params: dict[str, Any] = {"category": self.category, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            d = await self.rest.get("/v5/market/instruments-info", params=params)
            res = d.get("result", {})
            for s in res.get("list", []):
                if s.get("status") != "Trading":
                    continue
                if self.category == "linear" and s.get("contractType") not in ("LinearPerpetual",):
                    continue
                pf, lf = s.get("priceFilter", {}), s.get("lotSizeFilter", {})
                out.append(self.make_instrument(
                    native_symbol=s["symbol"], pair=s["symbol"], base=s["baseCoin"], quote=s["quoteCoin"],
                    tick_size=f(pf.get("tickSize")), step_size=f(lf.get("qtyStep") or lf.get("basePrecision")),
                    listed_at=parse_ts(s.get("launchTime")) if s.get("launchTime") else None,
                ))
            cursor = res.get("nextPageCursor") or ""
            if not cursor:
                break
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        for i, batch in enumerate(chunks(instruments, self.cfg.streams_per_connection)):
            topics = [f"publicTrade.{x.native_symbol}" for x in batch] + [f"tickers.{x.native_symbol}" for x in batch]
            if self.cfg.depth_enabled:
                topics += [f"orderbook.50.{x.native_symbol}" for x in batch]

            async def on_open(ws: WsClient, topics: list[str] = topics) -> None:
                for part in chunks(topics, 10):
                    await ws.send_json({"op": "subscribe", "args": list(part)})

            self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=None,
                                 app_ping=lambda: orjson.dumps({"op": "ping"}).decode(), app_ping_interval=20))

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict) or "topic" not in msg:
            return
        topic: str = msg["topic"]
        data = msg.get("data")
        if topic.startswith("publicTrade."):
            for t in data or []:
                inst = self.by_native.get(t.get("s", ""))
                if inst is None:
                    continue
                self.emit_trade(inst, parse_ts(t.get("T")), f(t.get("p")), f(t.get("v")), "buy" if t.get("S") == "Buy" else "sell", str(t.get("i", "")))
        elif topic.startswith("orderbook."):
            sym = topic.split(".")[-1]
            inst = self.by_native.get(sym)
            if inst is None or not isinstance(data, dict):
                return
            self.emit_book(inst, parse_ts(msg.get("ts")), levels(data.get("b", [])), levels(data.get("a", [])),
                           msg.get("type") == "snapshot", seq=int(data.get("u", 0)))
        elif topic.startswith("tickers."):
            sym = topic.split(".")[-1]
            inst = self.by_native.get(sym)
            if inst is None or not isinstance(data, dict):
                return
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
            if "openInterest" in data and self.category == "linear":
                oi = f(data["openInterest"])
                oi_value = f(data.get("openInterestValue")) if data.get("openInterestValue") else None
                self.emit_oi(inst, ts, oi, oi_value)

    async def poll_tickers(self, instruments: list[Instrument]) -> None:
        # REST fallback keeps 24h stats fresh even when a ticker delta never carried them.
        d = await self.rest.get("/v5/market/tickers", params={"category": self.category})
        ts = now()
        for t in d.get("result", {}).get("list", []):
            inst = self.by_native.get(t.get("symbol", ""))
            if inst is None:
                continue
            self.emit_ticker(inst, ts, volume_24h=f(t.get("turnover24h")), price_change_24h_pct=f(t.get("price24hPcnt")) * 100.0,
                             last_price=f(t.get("lastPrice")), fair_price=f(t.get("markPrice")) if t.get("markPrice") else None)
            if self.category == "linear" and t.get("openInterest"):
                self.emit_oi(inst, ts, f(t["openInterest"]), f(t.get("openInterestValue")) if t.get("openInterestValue") else None)

    async def fetch_klines_1m(self, inst: Instrument, limit: int) -> list[Bar]:
        d = await self.rest.get("/v5/market/kline", params={"category": self.category, "symbol": inst.native_symbol, "interval": "1", "limit": min(limit, 1000)}, retries=1)
        out: list[Bar] = []
        now_min = int(now() // 60) * 60
        for r in reversed(d.get("result", {}).get("list", [])):  # newest first in the response
            ts = int(int(r[0]) // 1000)
            if ts >= now_min:
                continue
            total = f(r[6])
            out.append(Bar(ts=ts, o=f(r[1]), h=f(r[2]), l=f(r[3]), c=f(r[4]), total_quote=total, had_trade=total > 0))
        return out

@register("bybit_futures")
class BybitFutures(_Bybit):
    category = "linear"


@register("bybit_spot")
class BybitSpot(_Bybit):
    category = "spot"
