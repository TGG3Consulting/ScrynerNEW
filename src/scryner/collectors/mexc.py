"""MEXC collectors.

Futures (``wss://contract.mexc.com/edge``): ``sub.deal``, ``sub.depth``, ``sub.ticker``
(holdVol = OI in contracts, amount24 = quote volume, riseFallRate). Contracts via
``/api/v1/contract/detail`` (contractSize).

Spot (``wss://wbs-api.mexc.com/ws``): protobuf push (``spot@public.aggre.deals.v3.api.pb@100ms@<SYM>``,
``spot@public.aggre.depth.v3.api.pb@100ms@<SYM>``, ``spot@public.miniTickers.v3.api.pb@UTC+0``).
A minimal protobuf wire decoder is implemented here (no generated classes needed); field
numbers follow MEXC's published ``PushDataV3ApiWrapper`` schema.
"""

from __future__ import annotations

import asyncio
from typing import Any

import orjson

from scryner.core.models import Instrument
from scryner.core.timeutil import now, parse_ts

from . import register
from .base import Collector, WsClient, chunks, f, levels

# --------------------------------------------------------------------------- protobuf wire


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def pb_fields(buf: bytes) -> list[tuple[int, int, bytes | int]]:
    """Decode a protobuf message into ``(field_number, wire_type, value)`` triples."""
    out: list[tuple[int, int, bytes | int]] = []
    pos = 0
    n = len(buf)
    while pos < n:
        tag, pos = _read_varint(buf, pos)
        fno, wt = tag >> 3, tag & 7
        if wt == 0:
            v, pos = _read_varint(buf, pos)
            out.append((fno, wt, v))
        elif wt == 1:
            out.append((fno, wt, buf[pos : pos + 8]))
            pos += 8
        elif wt == 2:
            ln, pos = _read_varint(buf, pos)
            out.append((fno, wt, buf[pos : pos + ln]))
            pos += ln
        elif wt == 5:
            out.append((fno, wt, buf[pos : pos + 4]))
            pos += 4
        else:
            raise ValueError(f"unsupported wire type {wt}")
    return out


def pb_dict(buf: bytes) -> dict[int, list[bytes | int]]:
    d: dict[int, list[bytes | int]] = {}
    for fno, _wt, v in pb_fields(buf):
        d.setdefault(fno, []).append(v)
    return d


def _s(v: bytes | int | None) -> str:
    return v.decode() if isinstance(v, bytes) else ("" if v is None else str(v))


# PushDataV3ApiWrapper: 1 channel, 3 symbol, 6 sendTime, 301 publicDeals, 302 publicIncreaseDepths,
# 313 publicAggreDeals, 314 publicAggreDepths, 303 publicLimitDepths, 309 publicMiniTickers ... (per MEXC proto)
WRAP_CHANNEL, WRAP_SYMBOL, WRAP_SENDTIME = 1, 3, 6
WRAP_AGGRE_DEALS, WRAP_AGGRE_DEPTHS, WRAP_MINI_TICKERS, WRAP_LIMIT_DEPTHS = 313, 314, 309, 303


class _MexcSpotProto:
    @staticmethod
    def decode(raw: str | bytes) -> Any:
        if isinstance(raw, str):
            try:
                return orjson.loads(raw)
            except orjson.JSONDecodeError:
                return None
        top = pb_dict(raw)
        channel = _s(top.get(WRAP_CHANNEL, [b""])[0])
        symbol = _s(top.get(WRAP_SYMBOL, [b""])[0])
        send_time = top.get(WRAP_SENDTIME, [0])[0]
        msg: dict[str, Any] = {"channel": channel, "symbol": symbol, "ts": parse_ts(send_time)}
        if WRAP_AGGRE_DEALS in top:
            # PublicAggreDealsV3Api: 1 deals(repeated: 1 price, 2 quantity, 3 tradeType(1 buy,2 sell), 4 time), 2 eventType
            deals = []
            for item in pb_dict(top[WRAP_AGGRE_DEALS][0]).get(1, []):  # type: ignore[arg-type]
                d = pb_dict(item)  # type: ignore[arg-type]
                deals.append({"p": _s(d.get(1, [b"0"])[0]), "q": _s(d.get(2, [b"0"])[0]), "side": int(d.get(3, [1])[0]), "t": d.get(4, [0])[0]})
            msg["deals"] = deals
        if WRAP_AGGRE_DEPTHS in top:
            # PublicAggreDepthsV3Api: 1 asks(repeated: 1 price 2 quantity), 2 bids, 3 eventType, 4 fromVersion, 5 toVersion
            d = pb_dict(top[WRAP_AGGRE_DEPTHS][0])  # type: ignore[arg-type]
            msg["asks"] = [(_s(pb_dict(x).get(1, [b"0"])[0]), _s(pb_dict(x).get(2, [b"0"])[0])) for x in d.get(1, [])]  # type: ignore[arg-type]
            msg["bids"] = [(_s(pb_dict(x).get(1, [b"0"])[0]), _s(pb_dict(x).get(2, [b"0"])[0])) for x in d.get(2, [])]  # type: ignore[arg-type]
            msg["to_version"] = _s(d.get(5, [b"0"])[0])
        if WRAP_MINI_TICKERS in top:
            # PublicMiniTickersV3Api: 1 items(repeated MiniTicker: 1 symbol, 2 price, 3 rate, 4 zonedRate, 5 high, 6 low, 7 volume, 8 quantity, 9 lastCloseRate ...)
            items = []
            for x in pb_dict(top[WRAP_MINI_TICKERS][0]).get(1, []):  # type: ignore[arg-type]
                d = pb_dict(x)  # type: ignore[arg-type]
                items.append({"symbol": _s(d.get(1, [b""])[0]), "price": _s(d.get(2, [b"0"])[0]), "rate": _s(d.get(3, [b"0"])[0]),
                              "high": _s(d.get(5, [b"0"])[0]), "low": _s(d.get(6, [b"0"])[0]), "volume": _s(d.get(7, [b"0"])[0])})
            msg["tickers"] = items
        return msg


@register("mexc_spot")
class MexcSpot(Collector):
    async def fetch_instruments(self) -> list[Instrument]:
        d = await self.rest.get("/api/v3/exchangeInfo")
        out: list[Instrument] = []
        for s in d.get("symbols", []):
            if s.get("status") not in ("1", "ENABLED") or not s.get("isSpotTradingAllowed", True):
                continue
            out.append(self.make_instrument(
                native_symbol=s["symbol"], pair=s["symbol"], base=s["baseAsset"], quote=s["quoteAsset"],
                tick_size=10 ** (-int(s.get("quotePrecision", 8))), step_size=10 ** (-int(s.get("baseAssetPrecision", 8))),
            ))
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        per = min(self.cfg.streams_per_connection, 30)  # MEXC: max 30 subscriptions per connection
        for i, batch in enumerate(chunks(instruments, per)):
            params = [f"spot@public.aggre.deals.v3.api.pb@100ms@{x.native_symbol}" for x in batch]
            if self.cfg.depth_enabled:
                params += [f"spot@public.aggre.depth.v3.api.pb@100ms@{x.native_symbol}" for x in batch]

            async def on_open(ws: WsClient, params: list[str] = params) -> None:
                await ws.send_json({"method": "SUBSCRIPTION", "params": params})

            self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=None,
                                 app_ping=lambda: orjson.dumps({"method": "PING"}).decode(), app_ping_interval=20, decode=_MexcSpotProto.decode))
        self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name="tickers", ping_interval=None,
                             on_open=lambda ws: ws.send_json({"method": "SUBSCRIPTION", "params": ["spot@public.miniTickers.v3.api.pb@UTC+0"]}),
                             app_ping=lambda: orjson.dumps({"method": "PING"}).decode(), app_ping_interval=20, decode=_MexcSpotProto.decode))

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict) or "channel" not in msg:
            return
        if "deals" in msg:
            inst = self.by_native.get(msg["symbol"])
            if inst is None:
                return
            for d in msg["deals"]:
                self.emit_trade(inst, parse_ts(d["t"]), f(d["p"]), f(d["q"]), "buy" if d["side"] == 1 else "sell")
        elif "bids" in msg or "asks" in msg:
            inst = self.by_native.get(msg["symbol"])
            if inst is None:
                return
            self.emit_book(inst, msg["ts"], levels(msg.get("bids", [])), levels(msg.get("asks", [])), False, seq=int(f(msg.get("to_version"))))
        elif "tickers" in msg:
            for t in msg["tickers"]:
                inst = self.by_native.get(t["symbol"])
                if inst is None:
                    continue
                self.emit_ticker(inst, msg["ts"], volume_24h=f(t["volume"]), price_change_24h_pct=f(t["rate"]) * 100.0, last_price=f(t["price"]),
                                 high_24h=f(t["high"]), low_24h=f(t["low"]))

    async def poll_tickers(self, instruments: list[Instrument]) -> None:
        d = await self.rest.get("/api/v3/ticker/24hr")
        ts = now()
        for t in d:
            inst = self.by_native.get(t.get("symbol", ""))
            if inst is None:
                continue
            self.emit_ticker(inst, ts, volume_24h=f(t.get("quoteVolume")), price_change_24h_pct=f(t.get("priceChangePercent")) * 100.0,
                             last_price=f(t.get("lastPrice")), trades_24h=int(t.get("count") or 0) or None)


@register("mexc_futures")
class MexcFutures(Collector):
    async def fetch_instruments(self) -> list[Instrument]:
        d = await self.rest.get("/api/v1/contract/detail")
        out: list[Instrument] = []
        for s in d.get("data", []):
            if s.get("state") != 0 or s.get("apiAllowed") is False:
                continue
            out.append(self.make_instrument(
                native_symbol=s["symbol"], pair=s["symbol"], base=s["baseCoin"], quote=s["quoteCoin"],
                tick_size=f(s.get("priceUnit")), step_size=f(s.get("contractSize"), 1.0), multiplier=f(s.get("contractSize"), 1.0),
                display_name=s.get("displayNameEn"), listed_at=parse_ts(s.get("createTime")) if s.get("createTime") else None,
            ))
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        for i, batch in enumerate(chunks(instruments, self.cfg.streams_per_connection)):
            syms = [x.native_symbol for x in batch]

            async def on_open(ws: WsClient, syms: list[str] = syms) -> None:
                for s in syms:
                    await ws.send_json({"method": "sub.deal", "param": {"symbol": s}})
                    await ws.send_json({"method": "sub.ticker", "param": {"symbol": s}})
                    if self.cfg.depth_enabled:
                        await ws.send_json({"method": "sub.depth", "param": {"symbol": s}})
                    await asyncio.sleep(0.02)

            self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=None,
                                 app_ping=lambda: orjson.dumps({"method": "ping"}).decode(), app_ping_interval=15))

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        ch = msg.get("channel", "")
        inst = self.by_native.get(msg.get("symbol", ""))
        if inst is None:
            return
        data = msg.get("data")
        if ch == "push.deal":
            rows = data if isinstance(data, list) else [data]
            for t in rows:
                # T: 1 buy, 2 sell; v = contracts
                self.emit_trade(inst, parse_ts(t.get("t")), f(t.get("p")), f(t.get("v")), "buy" if int(t.get("T", 1)) == 1 else "sell")
        elif ch == "push.depth":
            bids = [(f(x[0]), f(x[1])) for x in data.get("bids", [])]
            asks = [(f(x[0]), f(x[1])) for x in data.get("asks", [])]
            self.emit_book(inst, parse_ts(msg.get("ts")), bids, asks, False, seq=int(data.get("version", 0)))
        elif ch == "push.ticker":
            ts = parse_ts(data.get("timestamp") or msg.get("ts"))
            self.emit_ticker(inst, ts, volume_24h=f(data.get("amount24")), price_change_24h_pct=f(data.get("riseFallRate")) * 100.0,
                             last_price=f(data.get("lastPrice")), fair_price=f(data.get("fairPrice")) if data.get("fairPrice") else None,
                             high_24h=f(data.get("high24Price")), low_24h=f(data.get("lower24Price")))
            if data.get("holdVol") is not None:
                self.emit_oi(inst, ts, f(data.get("holdVol")), None)
