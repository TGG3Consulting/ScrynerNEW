"""Kraken Futures (``wss://futures.kraken.com/ws/v1``) and Kraken Spot (``wss://ws.kraken.com/v2``).

Futures feeds: ``trade`` (price, qty, side), ``book`` (snapshot + deltas), ``ticker``
(openInterest, volumeQuote, markPrice, change). Instruments: ``/derivatives/api/v3/instruments``
(perpetuals ``PF_*`` / ``PI_*``). Spot v2 channels: ``trade``, ``book``, ``ticker``.
"""

from __future__ import annotations

from typing import Any

from scryner.core.models import Instrument
from scryner.core.timeutil import parse_ts

from . import register
from .base import Collector, WsClient, chunks, f


@register("kraken_futures")
class KrakenFutures(Collector):
    async def fetch_instruments(self) -> list[Instrument]:
        d = await self.rest.get("/derivatives/api/v3/instruments")
        out: list[Instrument] = []
        for s in d.get("instruments", []):
            if not s.get("tradeable") or s.get("type") not in ("flexible_futures", "futures_inverse"):
                continue
            sym = s["symbol"].upper()  # PF_XBTUSD
            if not sym.startswith(("PF_", "PI_")):
                continue
            pair = s.get("pair", "")  # XBT:USD
            base, _, quote = pair.partition(":")
            inverse = sym.startswith("PI_")
            out.append(self.make_instrument(
                native_symbol=sym, pair=sym, base=base or sym[3:-3], quote=quote or "USD", tick_size=f(s.get("tickSize")),
                step_size=f(s.get("contractValueTradePrecision"), 0.0), coi_inverse=inverse,
                listed_at=parse_ts(s.get("openingDate")) if s.get("openingDate") else None,
            ))
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        for i, batch in enumerate(chunks(instruments, self.cfg.streams_per_connection)):
            ids = [x.native_symbol for x in batch]

            async def on_open(ws: WsClient, ids: list[str] = ids) -> None:
                await ws.send_json({"event": "subscribe", "feed": "trade", "product_ids": ids})
                await ws.send_json({"event": "subscribe", "feed": "ticker", "product_ids": ids})
                if self.cfg.depth_enabled:
                    await ws.send_json({"event": "subscribe", "feed": "book", "product_ids": ids})

            self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=30))

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict) or "feed" not in msg:
            return
        feed = msg["feed"]
        inst = self.by_native.get(str(msg.get("product_id", "")).upper())
        if inst is None:
            return
        if feed == "trade":
            self.emit_trade(inst, parse_ts(msg.get("time")), f(msg.get("price")), f(msg.get("qty")), "buy" if msg.get("side") == "buy" else "sell", str(msg.get("uid", "")))
        elif feed == "trade_snapshot":
            return
        elif feed == "book_snapshot":
            bids = [(f(x["price"]), f(x["qty"])) for x in msg.get("bids", [])]
            asks = [(f(x["price"]), f(x["qty"])) for x in msg.get("asks", [])]
            self.emit_book(inst, parse_ts(msg.get("timestamp")), bids, asks, True, seq=int(msg.get("seq", 0)))
        elif feed == "book":
            lvl = (f(msg.get("price")), f(msg.get("qty")))
            if msg.get("side") == "buy":
                self.emit_book(inst, parse_ts(msg.get("timestamp")), [lvl], [], False, seq=int(msg.get("seq", 0)))
            else:
                self.emit_book(inst, parse_ts(msg.get("timestamp")), [], [lvl], False, seq=int(msg.get("seq", 0)))
        elif feed == "ticker":
            ts = parse_ts(msg.get("time"))
            self.emit_ticker(inst, ts, volume_24h=f(msg.get("volumeQuote")), price_change_24h_pct=f(msg.get("change")),
                             last_price=f(msg.get("last")), fair_price=f(msg.get("markPrice")) if msg.get("markPrice") else None)
            if msg.get("openInterest") is not None:
                self.emit_oi(inst, ts, f(msg.get("openInterest")), None)


@register("kraken_spot")
class KrakenSpot(Collector):
    async def fetch_instruments(self) -> list[Instrument]:
        d = await self.rest.get("/0/public/AssetPairs")
        out: list[Instrument] = []
        for _k, s in d.get("result", {}).items():
            if s.get("status") != "online":
                continue
            ws = s.get("wsname")  # "XBT/USD"
            if not ws:
                continue
            base, _, quote = ws.partition("/")
            out.append(self.make_instrument(
                native_symbol=ws, pair=ws, base=base, quote=quote, tick_size=10 ** (-int(s.get("pair_decimals", 8))),
                step_size=10 ** (-int(s.get("lot_decimals", 8))),
            ))
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        for i, batch in enumerate(chunks(instruments, self.cfg.streams_per_connection)):
            syms = [x.native_symbol for x in batch]

            async def on_open(ws: WsClient, syms: list[str] = syms) -> None:
                await ws.send_json({"method": "subscribe", "params": {"channel": "trade", "symbol": syms, "snapshot": False}})
                await ws.send_json({"method": "subscribe", "params": {"channel": "ticker", "symbol": syms}})
                if self.cfg.depth_enabled:
                    await ws.send_json({"method": "subscribe", "params": {"channel": "book", "symbol": syms, "depth": 25}})

            self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=30))

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict) or "channel" not in msg or "data" not in msg:
            return
        ch = msg["channel"]
        for row in msg["data"]:
            inst = self.by_native.get(row.get("symbol", ""))
            if inst is None:
                continue
            if ch == "trade":
                self.emit_trade(inst, parse_ts(row.get("timestamp")), f(row.get("price")), f(row.get("qty")), "buy" if row.get("side") == "buy" else "sell", str(row.get("trade_id", "")))
            elif ch == "book":
                bids = [(f(x["price"]), f(x["qty"])) for x in row.get("bids", [])]
                asks = [(f(x["price"]), f(x["qty"])) for x in row.get("asks", [])]
                self.emit_book(inst, parse_ts(row.get("timestamp")), bids, asks, msg.get("type") == "snapshot", seq=int(row.get("checksum", 0)))
            elif ch == "ticker":
                self.emit_ticker(inst, parse_ts(None), volume_24h=f(row.get("volume")) * f(row.get("vwap")), price_change_24h_pct=f(row.get("change_pct")),
                                 last_price=f(row.get("last")), high_24h=f(row.get("high")), low_24h=f(row.get("low")))
