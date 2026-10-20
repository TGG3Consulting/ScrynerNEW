"""Gate.io v4 WebSocket (USDT perpetual futures and spot).

Futures (``wss://fx-ws.gateio.ws/v4/ws/usdt``): ``futures.trades``, ``futures.order_book_update``
(+ REST ``/futures/usdt/order_book`` snapshot), ``futures.tickers`` (volume_24h_quote,
total_size = OI in contracts, mark_price). Sizes are in contracts × ``quanto_multiplier``.
Spot (``wss://api.gateio.ws/ws/v4/``): ``spot.trades``, ``spot.order_book_update``, ``spot.tickers``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from scryner.core.models import Bar, Instrument
from scryner.core.timeutil import now, parse_ts

from . import register
from .base import Collector, WsClient, chunks, f, levels


class _Gate(Collector):
    futures = True

    @property
    def prefix(self) -> str:
        return "futures" if self.futures else "spot"

    async def fetch_instruments(self) -> list[Instrument]:
        out: list[Instrument] = []
        if self.futures:
            d = await self.rest.get("/api/v4/futures/usdt/contracts")
            for s in d:
                if s.get("in_delisting") or s.get("status", "trading") != "trading":
                    continue
                name = s["name"]  # BTC_USDT
                base, _, quote = name.partition("_")
                out.append(self.make_instrument(
                    native_symbol=name, pair=name, base=base, quote=quote, tick_size=f(s.get("order_price_round")),
                    step_size=f(s.get("quanto_multiplier"), 1.0), multiplier=f(s.get("quanto_multiplier"), 1.0),
                    listed_at=parse_ts(s.get("create_time")) if s.get("create_time") else None,
                ))
        else:
            d = await self.rest.get("/api/v4/spot/currency_pairs")
            for s in d:
                if s.get("trade_status") != "tradable":
                    continue
                out.append(self.make_instrument(
                    native_symbol=s["id"], pair=s["id"], base=s["base"], quote=s["quote"],
                    tick_size=10 ** (-int(s.get("precision", 0))), step_size=10 ** (-int(s.get("amount_precision", 0))),
                ))
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        p = self.prefix
        for i, batch in enumerate(chunks(instruments, self.cfg.streams_per_connection)):
            names = [x.native_symbol for x in batch]

            async def on_open(ws: WsClient, names: list[str] = names) -> None:
                t = int(time.time())
                await ws.send_json({"time": t, "channel": f"{p}.trades", "event": "subscribe", "payload": names})
                await ws.send_json({"time": t, "channel": f"{p}.tickers", "event": "subscribe", "payload": names})
                if self.cfg.depth_enabled:
                    for n in names:
                        await ws.send_json({"time": t, "channel": f"{p}.order_book_update", "event": "subscribe", "payload": [n, "100ms"]})

            self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=None,
                                 app_ping=lambda p=p: f'{{"time":{int(time.time())},"channel":"{p}.ping"}}', app_ping_interval=20))
        if self.cfg.depth_enabled:
            asyncio.get_running_loop().create_task(self._load_books(instruments))

    async def _load_books(self, instruments: list[Instrument]) -> None:
        await asyncio.sleep(2)
        for inst in instruments:
            try:
                if self.futures:
                    d = await self.rest.get("/api/v4/futures/usdt/order_book", params={"contract": inst.native_symbol, "limit": 50, "with_id": "true"})
                    bids = [(f(x["p"]), f(x["s"])) for x in d.get("bids", [])]
                    asks = [(f(x["p"]), f(x["s"])) for x in d.get("asks", [])]
                else:
                    d = await self.rest.get("/api/v4/spot/order_book", params={"currency_pair": inst.native_symbol, "limit": 50, "with_id": "true"})
                    bids, asks = levels(d.get("bids", [])), levels(d.get("asks", []))
                self.emit_book(inst, now(), bids, asks, True, seq=int(d.get("id", 0)))
            except Exception as exc:
                self.log.debug("book_snapshot_error", symbol=inst.native_symbol, error=str(exc)[:100])

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict) or msg.get("event") != "update":
            return
        ch = msg.get("channel", "")
        res = msg.get("result")
        if ch.endswith(".trades"):
            rows = res if isinstance(res, list) else [res]
            for t in rows:
                sym = t.get("contract") or t.get("currency_pair", "")
                inst = self.by_native.get(sym)
                if inst is None:
                    continue
                if self.futures:
                    size = f(t.get("size"))
                    side = "buy" if size > 0 else "sell"
                    self.emit_trade(inst, parse_ts(t.get("create_time_ms") or t.get("create_time")), f(t.get("price")), abs(size), side, str(t.get("id", "")))
                else:
                    self.emit_trade(inst, parse_ts(t.get("create_time_ms") or t.get("create_time")), f(t.get("price")), f(t.get("amount")), "buy" if t.get("side") == "buy" else "sell", str(t.get("id", "")))
        elif ch.endswith(".order_book_update"):
            sym = res.get("s", "")
            inst = self.by_native.get(sym)
            if inst is None:
                return
            if self.futures:
                bids = [(f(x["p"]), f(x["s"])) for x in res.get("b", [])]
                asks = [(f(x["p"]), f(x["s"])) for x in res.get("a", [])]
            else:
                bids, asks = levels(res.get("b", [])), levels(res.get("a", []))
            self.emit_book(inst, parse_ts(res.get("t")), bids, asks, False, seq=int(res.get("u", 0)), prev_seq=int(res.get("U", 0)))
        elif ch.endswith(".tickers"):
            rows = res if isinstance(res, list) else [res]
            for t in rows:
                sym = t.get("contract") or t.get("currency_pair", "")
                inst = self.by_native.get(sym)
                if inst is None:
                    continue
                ts = parse_ts(msg.get("time_ms") or msg.get("time"))
                if self.futures:
                    self.emit_ticker(inst, ts, volume_24h=f(t.get("volume_24h_quote")), price_change_24h_pct=f(t.get("change_percentage")),
                                     last_price=f(t.get("last")), fair_price=f(t.get("mark_price")) if t.get("mark_price") else None,
                                     high_24h=f(t.get("high_24h")), low_24h=f(t.get("low_24h")))
                    if t.get("total_size") is not None:
                        self.emit_oi(inst, ts, f(t.get("total_size")), None)
                else:
                    self.emit_ticker(inst, ts, volume_24h=f(t.get("quote_volume")), price_change_24h_pct=f(t.get("change_percentage")),
                                     last_price=f(t.get("last")), high_24h=f(t.get("high_24h")), low_24h=f(t.get("low_24h")))

    async def fetch_klines_1m(self, inst: Instrument, limit: int) -> list[Bar]:
        out: list[Bar] = []
        now_min = int(now() // 60) * 60
        if self.futures:
            rows = await self.rest.get("/api/v4/futures/usdt/candlesticks", params={"contract": inst.native_symbol, "interval": "1m", "limit": min(limit, 2000)}, retries=1)
            for r in rows:
                ts = int(r["t"])
                if ts >= now_min:
                    continue
                total = f(r.get("sum"))
                out.append(Bar(ts=ts, o=f(r["o"]), h=f(r["h"]), l=f(r["l"]), c=f(r["c"]), total_quote=total, had_trade=total > 0))
        else:
            rows = await self.rest.get("/api/v4/spot/candlesticks", params={"currency_pair": inst.native_symbol, "interval": "1m", "limit": min(limit, 1000)}, retries=1)
            for r in rows:  # [t, quote_vol, c, h, l, o, base_vol, closed]
                ts = int(r[0])
                if ts >= now_min or str(r[7]).lower() != "true":
                    continue
                total = f(r[1])
                out.append(Bar(ts=ts, o=f(r[5]), h=f(r[3]), l=f(r[4]), c=f(r[2]), total_quote=total, had_trade=total > 0))
        return out

@register("gateio_futures")
class GateFutures(_Gate):
    futures = True


@register("gateio_spot")
class GateSpot(_Gate):
    futures = False
