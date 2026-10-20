"""Bitget v2 public WebSocket (USDT-FUTURES and SPOT).

Channels: ``trade``, ``books`` (full depth, snapshot+update), ``ticker`` (openInterest,
quoteVolume, change24h, markPrice). Instruments: ``/api/v2/mix/market/contracts`` and
``/api/v2/spot/public/symbols``.
"""

from __future__ import annotations

from typing import Any

from scryner.core.models import Bar, Instrument
from scryner.core.timeutil import now, parse_ts

from . import register
from .base import Collector, WsClient, chunks, f, levels


class _Bitget(Collector):
    inst_type = "USDT-FUTURES"

    async def fetch_instruments(self) -> list[Instrument]:
        out: list[Instrument] = []
        if self.inst_type != "SPOT":
            for product in self.cfg.extra.get("product_types", ["USDT-FUTURES", "USDC-FUTURES"]):
                d = await self.rest.get("/api/v2/mix/market/contracts", params={"productType": product})
                for s in d.get("data", []):
                    if s.get("symbolStatus") != "normal":
                        continue
                    tick = 10 ** (-int(s.get("pricePlace", 0))) * f(s.get("priceEndStep"), 1.0)
                    out.append(self.make_instrument(
                        native_symbol=s["symbol"], pair=s["symbol"], base=s["baseCoin"], quote=s["quoteCoin"], tick_size=tick,
                        step_size=f(s.get("minTradeNum")), multiplier=f(s.get("sizeMultiplier"), 1.0),
                        listed_at=parse_ts(s.get("launchTime")) if s.get("launchTime") else None,
                    ))
                    self._product_of[s["symbol"]] = product
        else:
            d = await self.rest.get("/api/v2/spot/public/symbols")
            for s in d.get("data", []):
                if s.get("status") != "online":
                    continue
                out.append(self.make_instrument(
                    native_symbol=s["symbol"], pair=s["symbol"], base=s["baseCoin"], quote=s["quoteCoin"],
                    tick_size=10 ** (-int(s.get("pricePrecision", 0))), step_size=10 ** (-int(s.get("quantityPrecision", 0))),
                ))
        return out

    def __init__(self, cfg: Any, sink: Any) -> None:
        super().__init__(cfg, sink)
        self._product_of: dict[str, str] = {}

    def _inst_type_for(self, inst: Instrument) -> str:
        return "SPOT" if self.inst_type == "SPOT" else self._product_of.get(inst.native_symbol, "USDT-FUTURES")

    async def open_streams(self, instruments: list[Instrument]) -> None:
        for i, batch in enumerate(chunks(instruments, self.cfg.streams_per_connection)):
            args = []
            for x in batch:
                it = self._inst_type_for(x)
                args.append({"instType": it, "channel": "trade", "instId": x.native_symbol})
                args.append({"instType": it, "channel": "ticker", "instId": x.native_symbol})
                if self.cfg.depth_enabled:
                    args.append({"instType": it, "channel": "books", "instId": x.native_symbol})

            async def on_open(ws: WsClient, args: list[dict[str, str]] = args) -> None:
                for part in chunks(args, 50):
                    await ws.send_json({"op": "subscribe", "args": list(part)})

            self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=None,
                                 app_ping=lambda: "ping", app_ping_interval=25, decode=self._decode))

    @staticmethod
    def _decode(raw: str | bytes) -> Any:
        if raw in ("pong", b"pong"):
            return None
        return WsClient.default_decode(raw)

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict) or "arg" not in msg or "data" not in msg:
            return
        arg = msg["arg"]
        inst = self.by_native.get(arg.get("instId", ""))
        if inst is None:
            return
        ch = arg.get("channel")
        data = msg["data"]
        if ch == "trade":
            for t in data:
                self.emit_trade(inst, parse_ts(t.get("ts")), f(t.get("price")), f(t.get("size")), "buy" if t.get("side") == "buy" else "sell", str(t.get("tradeId", "")))
        elif ch == "books":
            for b in data:
                self.emit_book(inst, parse_ts(b.get("ts")), levels(b.get("bids", [])), levels(b.get("asks", [])), msg.get("action") == "snapshot", seq=int(b.get("seq", 0)))
        elif ch == "ticker":
            for t in data:
                ts = parse_ts(t.get("ts"))
                self.emit_ticker(inst, ts, volume_24h=f(t.get("quoteVolume")), price_change_24h_pct=f(t.get("change24h")) * 100.0,
                                 last_price=f(t.get("lastPr")), fair_price=f(t.get("markPrice")) if t.get("markPrice") else None,
                                 high_24h=f(t.get("high24h")), low_24h=f(t.get("low24h")))
                oi_raw = t.get("holdingAmount", t.get("openInterest"))  # bitget v2 futures field is holdingAmount (base coin)
                if oi_raw is not None and self.inst_type != "SPOT":
                    oi_base = f(oi_raw)
                    mark = f(t.get("markPrice")) or f(t.get("lastPr"))
                    self.emit_oi(inst, ts, oi_base, oi_base * mark if mark else None)

    async def fetch_klines_1m(self, inst: Instrument, limit: int) -> list[Bar]:
        if self.inst_type == "SPOT":
            d = await self.rest.get("/api/v2/spot/market/candles", params={"symbol": inst.native_symbol, "granularity": "1min", "limit": min(limit, 1000)}, retries=1)
        else:
            d = await self.rest.get("/api/v2/mix/market/candles", params={"symbol": inst.native_symbol, "productType": self._inst_type_for(inst), "granularity": "1m", "limit": min(limit, 1000)}, retries=1)
        out: list[Bar] = []
        now_min = int(now() // 60) * 60
        for r in d.get("data", []):
            ts = int(int(r[0]) // 1000)
            if ts >= now_min:
                continue
            total = f(r[6]) if len(r) > 6 else f(r[5]) * f(r[4])
            out.append(Bar(ts=ts, o=f(r[1]), h=f(r[2]), l=f(r[3]), c=f(r[4]), total_quote=total, had_trade=total > 0))
        return out

@register("bitget_futures")
class BitgetFutures(_Bitget):
    inst_type = "USDT-FUTURES"


@register("bitget_spot")
class BitgetSpot(_Bitget):
    inst_type = "SPOT"
