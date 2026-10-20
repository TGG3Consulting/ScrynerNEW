"""KuCoin spot and futures.

The WS endpoint and token come from ``POST /api/v1/bullet-public``. Spot topics:
``/market/match:<syms>`` (trades), ``/spotMarket/level2Depth50:<syms>``, ``/market/ticker:all``.
Futures topics: ``/contractMarket/execution:<sym>``, ``/contractMarket/level2Depth50:<sym>``,
``/contract/instrument:<sym>`` (mark price). Futures OI + 24h stats via REST
``/api/v1/contracts/active`` (openInterest, turnoverOf24h, priceChgPct, multiplier).
"""

from __future__ import annotations

from typing import Any

import orjson

from scryner.core.models import Bar, Instrument
from scryner.core.timeutil import now, parse_ts

from . import register
from .base import Collector, WsClient, chunks, f, levels


class _KuCoin(Collector):
    futures = False

    async def _bullet(self) -> tuple[str, str, float]:
        d = await self.rest.post("/api/v1/bullet-public")
        data = d["data"]
        srv = data["instanceServers"][0]
        return srv["endpoint"], data["token"], float(srv.get("pingInterval", 18000)) / 1000.0

    async def fetch_instruments(self) -> list[Instrument]:
        out: list[Instrument] = []
        if self.futures:
            d = await self.rest.get("/api/v1/contracts/active")
            for s in d.get("data", []):
                if s.get("status") != "Open" or s.get("type") != "FFWCSX":  # perpetual
                    continue
                out.append(self.make_instrument(
                    native_symbol=s["symbol"], pair=s["symbol"], base=s["baseCurrency"], quote=s["quoteCurrency"],
                    tick_size=f(s.get("tickSize")), step_size=f(s.get("multiplier"), 1.0), multiplier=f(s.get("multiplier"), 1.0),
                    listed_at=parse_ts(s.get("firstOpenDate")) if s.get("firstOpenDate") else None,
                ))
        else:
            d = await self.rest.get("/api/v2/symbols")
            for s in d.get("data", []):
                if not s.get("enableTrading"):
                    continue
                out.append(self.make_instrument(
                    native_symbol=s["symbol"], pair=s["symbol"], base=s["baseCurrency"], quote=s["quoteCurrency"],
                    tick_size=f(s.get("priceIncrement")), step_size=f(s.get("baseIncrement")),
                ))
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        endpoint, token, ping = await self._bullet()
        url = f"{endpoint}?token={token}&connectId=scryner"
        per = min(self.cfg.streams_per_connection, 100)
        for i, batch in enumerate(chunks(instruments, per)):
            syms = [x.native_symbol for x in batch]

            async def on_open(ws: WsClient, syms: list[str] = syms, first: bool = i == 0) -> None:
                if self.futures:
                    for s in syms:
                        await ws.send_json({"id": f"t-{s}", "type": "subscribe", "topic": f"/contractMarket/execution:{s}", "response": False})
                        await ws.send_json({"id": f"i-{s}", "type": "subscribe", "topic": f"/contract/instrument:{s}", "response": False})
                        if self.cfg.depth_enabled:
                            await ws.send_json({"id": f"d-{s}", "type": "subscribe", "topic": f"/contractMarket/level2Depth50:{s}", "response": False})
                else:
                    joined = ",".join(syms)
                    await ws.send_json({"id": "t", "type": "subscribe", "topic": f"/market/match:{joined}", "response": False})
                    if self.cfg.depth_enabled:
                        await ws.send_json({"id": "d", "type": "subscribe", "topic": f"/spotMarket/level2Depth50:{joined}", "response": False})
                    if first:
                        await ws.send_json({"id": "k", "type": "subscribe", "topic": "/market/ticker:all", "response": False})

            self.add_ws(WsClient(url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=None,
                                 app_ping=lambda: orjson.dumps({"id": "ping", "type": "ping"}).decode(), app_ping_interval=ping * 0.8))

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict) or msg.get("type") != "message":
            return
        topic: str = msg.get("topic", "")
        data = msg.get("data") or {}
        if topic.startswith("/market/match") or topic.startswith("/contractMarket/execution"):
            inst = self.by_native.get(data.get("symbol", ""))
            if inst is None:
                return
            qty = f(data.get("size"))
            self.emit_trade(inst, parse_ts(data.get("ts") or data.get("time")), f(data.get("price")), qty, "buy" if data.get("side") == "buy" else "sell", str(data.get("tradeId", "")))
        elif "level2Depth50" in topic:
            sym = topic.split(":")[-1] if self.futures else msg.get("subject", "") or topic.split(":")[-1]
            if not self.futures:
                sym = topic.split(":")[-1] if "," not in topic else (msg.get("subject") or "")
            inst = self.by_native.get(sym)
            if inst is None:
                return
            self.emit_book(inst, parse_ts(data.get("timestamp") or data.get("ts")), levels(data.get("bids", [])), levels(data.get("asks", [])), True)
        elif topic.startswith("/market/ticker:all"):
            inst = self.by_native.get(msg.get("subject", ""))
            if inst is None:
                return
            self.emit_ticker(inst, parse_ts(data.get("time")), last_price=f(data.get("price")))
        elif topic.startswith("/contract/instrument"):
            inst = self.by_native.get(topic.split(":")[-1])
            if inst is None:
                return
            if msg.get("subject") == "mark.index.price":
                self.emit_ticker(inst, parse_ts(data.get("timestamp")), fair_price=f(data.get("markPrice")))

    async def poll_tickers(self, instruments: list[Instrument]) -> None:
        ts = now()
        if self.futures:
            d = await self.rest.get("/api/v1/contracts/active")
            for s in d.get("data", []):
                inst = self.by_native.get(s.get("symbol", ""))
                if inst is None:
                    continue
                self.emit_ticker(inst, ts, volume_24h=f(s.get("turnoverOf24h")), price_change_24h_pct=f(s.get("priceChgPct")) * 100.0,
                                 last_price=f(s.get("lastTradePrice")), fair_price=f(s.get("markPrice")) if s.get("markPrice") else None,
                                 high_24h=f(s.get("highPrice")), low_24h=f(s.get("lowPrice")))
                if s.get("openInterest") is not None:
                    self.emit_oi(inst, ts, f(s.get("openInterest")), None)
        else:
            d = await self.rest.get("/api/v1/market/allTickers")
            for t in d.get("data", {}).get("ticker", []):
                inst = self.by_native.get(t.get("symbol", ""))
                if inst is None:
                    continue
                self.emit_ticker(inst, ts, volume_24h=f(t.get("volValue")), price_change_24h_pct=f(t.get("changeRate")) * 100.0,
                                 last_price=f(t.get("last")), high_24h=f(t.get("high")), low_24h=f(t.get("low")))

    async def poll_open_interest(self, instruments: list[Instrument]) -> None:
        # OI arrives together with the ticker poll (same endpoint); nothing extra to do.
        return None

    async def fetch_klines_1m(self, inst: Instrument, limit: int) -> list[Bar]:
        out: list[Bar] = []
        now_min = int(now() // 60) * 60
        if self.futures:
            end_ms = int(now() * 1000)
            d = await self.rest.get("/api/v1/kline/query", params={"symbol": inst.native_symbol, "granularity": 1, "from": end_ms - limit * 60_000, "to": end_ms}, retries=1)
            for r in d.get("data", []):  # [time, o, h, l, c, volume(contracts)]
                ts = int(int(r[0]) // 1000)
                if ts >= now_min:
                    continue
                total = f(r[5]) * inst.contract_multiplier * f(r[4])
                out.append(Bar(ts=ts, o=f(r[1]), h=f(r[2]), l=f(r[3]), c=f(r[4]), total_quote=total, had_trade=total > 0))
        else:
            end = int(now())
            d = await self.rest.get("/api/v1/market/candles", params={"symbol": inst.native_symbol, "type": "1min", "startAt": end - limit * 60, "endAt": end}, retries=1)
            for r in reversed(d.get("data", [])):  # [time, o, c, h, l, volume, turnover]
                ts = int(r[0])
                if ts >= now_min:
                    continue
                total = f(r[6])
                out.append(Bar(ts=ts, o=f(r[1]), h=f(r[3]), l=f(r[4]), c=f(r[2]), total_quote=total, had_trade=total > 0))
        return out

@register("kucoin_spot")
class KuCoinSpot(_KuCoin):
    futures = False


@register("kucoin_futures")
class KuCoinFutures(_KuCoin):
    futures = True
