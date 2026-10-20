"""Hyperliquid perpetuals and spot (``wss://api.hyperliquid.xyz/ws``).

Subscriptions: ``{"type":"trades","coin":...}``, ``{"type":"l2Book","coin":...}``,
``{"type":"activeAssetCtx","coin":...}`` (openInterest, markPx, dayNtlVlm, prevDayPx).
Metadata via ``POST /info`` ``{"type":"metaAndAssetCtxs"}`` / ``{"type":"spotMetaAndAssetCtxs"}``.
Spot coins are addressed as ``@<index>`` with a human name from ``tokens``.
"""

from __future__ import annotations

from typing import Any

import orjson

from scryner.core.models import Bar, Instrument
from scryner.core.timeutil import now, parse_ts

from . import register
from .base import Collector, WsClient, chunks, f


class _Hyperliquid(Collector):
    spot = False

    async def fetch_instruments(self) -> list[Instrument]:
        out: list[Instrument] = []
        if not self.spot:
            meta, ctxs = await self.rest.post("/info", json={"type": "metaAndAssetCtxs"})
            for a, _ctx in zip(meta.get("universe", []), ctxs, strict=False):
                if a.get("isDelisted"):
                    continue
                name = a["name"]
                sz_dec = int(a.get("szDecimals", 0))
                out.append(self.make_instrument(
                    native_symbol=name, pair=f"{name}/USDC", base=name, quote="USDC", tick_size=0.0, step_size=10 ** (-sz_dec),
                    display_pair=f"{name}/USDC",
                ))
        else:
            meta, ctxs = await self.rest.post("/info", json={"type": "spotMetaAndAssetCtxs"})
            tokens = {t["index"]: t for t in meta.get("tokens", [])}
            for pair, _ctx in zip(meta.get("universe", []), ctxs, strict=False):
                name = pair["name"]  # "@107" or "PURR/USDC"
                base_tok = tokens.get(pair["tokens"][0], {})
                quote_tok = tokens.get(pair["tokens"][1], {})
                base = base_tok.get("name", name)
                quote = quote_tok.get("name", "USDC")
                display = f"{base}/{quote}"
                out.append(self.make_instrument(
                    native_symbol=name, pair=name if name.startswith("@") else display, base=base, quote=quote, tick_size=0.0,
                    step_size=10 ** (-int(base_tok.get("szDecimals", 0))), display_pair=display, display_symbol=base,
                    base_asset_id=str(pair["tokens"][0]), contract_address=base_tok.get("tokenId"),
                ))
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        for i, batch in enumerate(chunks(instruments, self.cfg.streams_per_connection)):
            coins = [x.native_symbol for x in batch]

            async def on_open(ws: WsClient, coins: list[str] = coins) -> None:
                for c in coins:
                    await ws.send_json({"method": "subscribe", "subscription": {"type": "trades", "coin": c}})
                    await ws.send_json({"method": "subscribe", "subscription": {"type": "activeAssetCtx", "coin": c}})
                    if self.cfg.depth_enabled:
                        await ws.send_json({"method": "subscribe", "subscription": {"type": "l2Book", "coin": c}})

            self.add_ws(WsClient(self.cfg.ws_url, self._on_message, name=f"ws-{i}", on_open=on_open, ping_interval=None,
                                 app_ping=lambda: orjson.dumps({"method": "ping"}).decode(), app_ping_interval=30))

    def _on_message(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        ch = msg.get("channel")
        data = msg.get("data")
        if ch == "trades":
            for t in data or []:
                inst = self.by_native.get(t.get("coin", ""))
                if inst is None:
                    continue
                self.emit_trade(inst, parse_ts(t.get("time")), f(t.get("px")), f(t.get("sz")), "buy" if t.get("side") == "B" else "sell", str(t.get("tid", "")))
        elif ch == "l2Book" and isinstance(data, dict):
            inst = self.by_native.get(data.get("coin", ""))
            if inst is None:
                return
            lv = data.get("levels", [[], []])
            bids = [(f(x["px"]), f(x["sz"])) for x in lv[0]]
            asks = [(f(x["px"]), f(x["sz"])) for x in lv[1]]
            self.emit_book(inst, parse_ts(data.get("time")), bids, asks, True)
        elif ch == "activeAssetCtx" and isinstance(data, dict):
            inst = self.by_native.get(data.get("coin", ""))
            if inst is None:
                return
            ctx = data.get("ctx", {})
            mark = f(ctx.get("markPx"))
            prev = f(ctx.get("prevDayPx"))
            ts = now()
            self.emit_ticker(inst, ts, volume_24h=f(ctx.get("dayNtlVlm")), fair_price=mark, last_price=mark or None,
                             price_change_24h_pct=((mark - prev) / prev * 100.0) if prev and mark else None)
            if not self.spot and ctx.get("openInterest") is not None:
                oi = f(ctx.get("openInterest"))
                self.emit_oi(inst, ts, oi, oi * mark if mark else None)

    async def fetch_klines_1m(self, inst: Instrument, limit: int) -> list[Bar]:
        end_ms = int(now() * 1000)
        rows = await self.rest.post("/info", json={"type": "candleSnapshot", "req": {"coin": inst.native_symbol, "interval": "1m", "startTime": end_ms - limit * 60_000, "endTime": end_ms}}, retries=1)
        out: list[Bar] = []
        now_min = int(now() // 60) * 60
        for r in rows or []:
            ts = int(int(r["t"]) // 1000)
            if ts >= now_min:
                continue
            total = f(r.get("v")) * f(r.get("c"))
            out.append(Bar(ts=ts, o=f(r["o"]), h=f(r["h"]), l=f(r["l"]), c=f(r["c"]), total_quote=total, trades_count=int(r.get("n") or 0), had_trade=total > 0))
        return out

@register("hyperliquid_futures")
class HyperliquidPerp(_Hyperliquid):
    spot = False


@register("hyperliquid_spot")
class HyperliquidSpot(_Hyperliquid):
    spot = True
