"""Binance USDⓈ-M futures, spot and Binance Alpha collectors.

Futures: ``wss://fstream.binance.com/stream`` combined streams ``<sym>@aggTrade``,
``<sym>@depth@100ms`` (diff depth, snapshot via REST ``/fapi/v1/depth``), ``!ticker@arr``
(24h stats for all symbols), ``!markPrice@arr@1s``; OI via REST ``/fapi/v1/openInterest``.
Spot: ``wss://stream.binance.com:9443/stream`` with the same stream names and
``/api/v3/depth``. Alpha: public REST ``bapi/defi`` endpoints polled for agg trades.
"""

from __future__ import annotations

import asyncio
from typing import Any

from scryner.core.models import Instrument
from scryner.core.timeutil import now, parse_ts

from . import register
from .base import Collector, WsClient, chunks, f, levels

STOCK_SUFFIX_HINTS = ("STOCK",)


class _BinanceLike(Collector):
    """Shared implementation for Binance-compatible APIs (Binance, Aster)."""

    futures_rest_prefix = "/fapi/v1"
    spot_rest_prefix = "/api/v3"
    exchange_name = "binance"

    def __init__(self, cfg: Any, sink: Any) -> None:
        super().__init__(cfg, sink)
        self._depth_pending: dict[str, list[dict[str, Any]]] = {}
        self._depth_last_u: dict[str, int] = {}

    @property
    def is_futures(self) -> bool:
        return self.cfg.market_type == "futures"

    @property
    def rest_prefix(self) -> str:
        return self.futures_rest_prefix if self.is_futures else self.spot_rest_prefix

    async def fetch_instruments(self) -> list[Instrument]:
        data = await self.rest.get(f"{self.rest_prefix}/exchangeInfo")
        out: list[Instrument] = []
        for s in data.get("symbols", []):
            status = s.get("status") or s.get("contractStatus")
            if status not in ("TRADING",):
                continue
            if self.is_futures and s.get("contractType") not in (None, "PERPETUAL"):
                continue
            if not self.is_futures and not s.get("isSpotTradingAllowed", True):
                continue
            quote = s["quoteAsset"]
            base = s["baseAsset"]
            tick = step = 0.0
            for flt in s.get("filters", []):
                if flt.get("filterType") == "PRICE_FILTER":
                    tick = f(flt.get("tickSize"))
                elif flt.get("filterType") == "LOT_SIZE":
                    step = f(flt.get("stepSize"))
            underlying = s.get("underlyingType", "")
            tags = s.get("permissionSets") or []
            is_stock = underlying == "STOCK" or any("STOCK" in str(t).upper() for t in tags) or base.endswith(STOCK_SUFFIX_HINTS)
            out.append(self.make_instrument(
                native_symbol=s["symbol"], pair=s["symbol"], base=base, quote=quote, tick_size=tick, step_size=step,
                asset_class="stock" if is_stock else "crypto", display_symbol=base if is_stock else None,
                listed_at=parse_ts(s.get("onboardDate")) if s.get("onboardDate") else None,
            ))
        return out

    # ------------------------------------------------------------------ streams
    async def open_streams(self, instruments: list[Instrument]) -> None:
        base_ws = self.cfg.ws_url.rstrip("/")
        per_conn = self.cfg.streams_per_connection
        # trades + depth per instrument, in batches
        for i, batch in enumerate(chunks(instruments, per_conn)):
            streams = [f"{inst.native_symbol.lower()}@aggTrade" for inst in batch]
            if self.cfg.depth_enabled:
                streams += [f"{inst.native_symbol.lower()}@depth@100ms" for inst in batch]
            url = f"{base_ws}/stream?streams={'/'.join(streams)}"
            self.add_ws(WsClient(url, self._on_stream, name=f"streams-{i}", ping_interval=None, idle_timeout=120))
        # one connection for market-wide 24h tickers (+ mark price on futures)
        wide = ["!ticker@arr"] + (["!markPrice@arr@1s"] if self.is_futures else [])
        self.add_ws(WsClient(f"{base_ws}/stream?streams={'/'.join(wide)}", self._on_stream, name="tickers", ping_interval=None, idle_timeout=120))
        if self.cfg.depth_enabled:
            for inst in instruments:
                self._depth_pending[inst.native_symbol] = []
            asyncio.get_running_loop().create_task(self._load_depth_snapshots(instruments))

    async def _load_depth_snapshots(self, instruments: list[Instrument]) -> None:
        await asyncio.sleep(2.0)  # let the diff stream buffer first
        limit = min(1000, max(5, self.cfg.depth_levels))
        for inst in instruments:
            try:
                snap = await self.rest.get(f"{self.rest_prefix}/depth", params={"symbol": inst.native_symbol, "limit": limit})
            except Exception as exc:
                self.log.debug("depth_snapshot_error", symbol=inst.native_symbol, error=str(exc)[:100])
                continue
            last_u = int(snap.get("lastUpdateId", 0))
            self.emit_book(inst, now(), levels(snap.get("bids", [])), levels(snap.get("asks", [])), True, seq=last_u)
            self._depth_last_u[inst.native_symbol] = last_u
            pending = self._depth_pending.pop(inst.native_symbol, [])
            for d in pending:
                self._apply_depth(inst, d)

    def _apply_depth(self, inst: Instrument, d: dict[str, Any]) -> None:
        sym = inst.native_symbol
        last_u = self._depth_last_u.get(sym)
        if last_u is None:
            return
        U, u = int(d.get("U", 0)), int(d.get("u", 0))
        if u <= last_u:
            return
        pu = int(d.get("pu", -1))
        if self.is_futures and pu != -1 and pu != last_u and U > last_u + 1:
            # gap: resync
            self._depth_last_u.pop(sym, None)
            self._depth_pending[sym] = []
            asyncio.get_running_loop().create_task(self._load_depth_snapshots([inst]))
            return
        self._depth_last_u[sym] = u
        self.emit_book(inst, parse_ts(d.get("E")), levels(d.get("b", [])), levels(d.get("a", [])), False, seq=u, prev_seq=U)

    def _on_stream(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        data = msg.get("data", msg)
        stream = msg.get("stream", "")
        if isinstance(data, list):  # !ticker@arr / !markPrice@arr
            for item in data:
                self._on_wide(item)
            return
        et = data.get("e")
        if et == "aggTrade":
            inst = self.by_native.get(data.get("s", ""))
            if inst is None:
                return
            price, qty = f(data.get("p")), f(data.get("q"))
            side = "sell" if data.get("m") else "buy"  # m = buyer is maker → taker sold
            self.emit_trade(inst, parse_ts(data.get("T")), price, qty, side, str(data.get("a", "")))
        elif et == "depthUpdate":
            inst = self.by_native.get(data.get("s", ""))
            if inst is None:
                return
            if inst.native_symbol in self._depth_pending:
                self._depth_pending[inst.native_symbol].append(data)
                if len(self._depth_pending[inst.native_symbol]) > 2000:
                    del self._depth_pending[inst.native_symbol][:1000]
            else:
                self._apply_depth(inst, data)
        elif et in ("24hrTicker", "markPriceUpdate"):
            self._on_wide(data)
        elif stream.endswith("@aggTrade") and "p" in data:
            pass

    def _on_wide(self, item: dict[str, Any]) -> None:
        inst = self.by_native.get(item.get("s", ""))
        if inst is None:
            return
        et = item.get("e")
        if et == "24hrTicker":
            self.emit_ticker(inst, parse_ts(item.get("E")), volume_24h=f(item.get("q")), price_change_24h_pct=f(item.get("P")),
                             last_price=f(item.get("c")), trades_24h=int(item.get("n", 0) or 0), high_24h=f(item.get("h")), low_24h=f(item.get("l")))
        elif et == "markPriceUpdate":
            self.emit_ticker(inst, parse_ts(item.get("E")), fair_price=f(item.get("p")))

    # ------------------------------------------------------------------ OI polling
    async def poll_open_interest(self, instruments: list[Instrument]) -> None:
        if not self.is_futures:
            return
        sem = asyncio.Semaphore(8)

        async def one(inst: Instrument) -> None:
            async with sem:
                try:
                    d = await self.rest.get(f"{self.futures_rest_prefix}/openInterest", params={"symbol": inst.native_symbol}, retries=1)
                except Exception as exc:
                    self.log.debug("oi_error", symbol=inst.native_symbol, error=str(exc)[:100])
                    return
                oi = f(d.get("openInterest"))
                self.emit_oi(inst, parse_ts(d.get("time")), oi)

        await asyncio.gather(*(one(i) for i in instruments))


@register("binance_futures")
class BinanceFutures(_BinanceLike):
    pass


@register("binance_spot")
class BinanceSpot(_BinanceLike):
    pass


@register("binance_alpha")
class BinanceAlpha(Collector):
    """Binance Alpha (on-chain tokens traded through Binance Wallet).

    Public endpoints (no auth):
    * token list: ``/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list``
    * agg trades: ``/bapi/defi/v1/public/alpha-trade/agg-trades?symbol=<alphaId>USDT&limit=…``
    * 24h ticker: ``/bapi/defi/v1/public/alpha-trade/ticker/24hr?symbol=…``
    Alpha has no public WebSocket trade feed, so trades are polled per symbol every
    ``extra.trade_poll_sec`` (default 2 s) with incremental ``fromId``.
    """

    rest_rate_per_sec = 20.0

    def __init__(self, cfg: Any, sink: Any) -> None:
        super().__init__(cfg, sink)
        self._last_trade_id: dict[str, int] = {}

    async def fetch_instruments(self) -> list[Instrument]:
        data = await self.rest.get("/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list")
        out: list[Instrument] = []
        for t in data.get("data", []) or []:
            if not t.get("listingCex", True) and not t.get("onlineTge", False):
                pass
            alpha_id = t.get("alphaId") or ""
            symbol = t.get("symbol") or ""
            if not alpha_id or not symbol or t.get("offline"):
                continue
            native = f"{alpha_id}USDT"
            pair = f"{symbol}/USDT"
            out.append(self.make_instrument(
                native_symbol=native, pair=pair, base=symbol, quote="USDT", tick_size=0.0, step_size=0.0,
                display_pair=pair, display_name=t.get("name"), chain_name=t.get("chainName"),
                contract_address=t.get("contractAddress"), base_asset_id=alpha_id,
                listed_at=parse_ts(t.get("listingTime")) if t.get("listingTime") else None,
            ))
        return out

    async def open_streams(self, instruments: list[Instrument]) -> None:
        self._tasks.append(asyncio.create_task(self._trade_poll_loop(), name="alpha-trades"))

    async def _trade_poll_loop(self) -> None:
        interval = float(self.cfg.extra.get("trade_poll_sec", 2.0))
        sem = asyncio.Semaphore(int(self.cfg.extra.get("trade_poll_concurrency", 16)))

        async def one(inst: Instrument) -> None:
            async with sem:
                params: dict[str, Any] = {"symbol": inst.native_symbol, "limit": 500}
                last = self._last_trade_id.get(inst.native_symbol)
                if last is not None:
                    params["fromId"] = last + 1
                try:
                    d = await self.rest.get("/bapi/defi/v1/public/alpha-trade/agg-trades", params=params, retries=0)
                except Exception:
                    return
                rows = d.get("data") or []
                for r in rows:
                    tid = int(r.get("a", 0))
                    if last is not None and tid <= last:
                        continue
                    self.emit_trade(inst, parse_ts(r.get("T")), f(r.get("p")), f(r.get("q")), "sell" if r.get("m") else "buy", str(tid))
                    self._last_trade_id[inst.native_symbol] = tid
                if last is None and rows:
                    self._last_trade_id[inst.native_symbol] = int(rows[-1].get("a", 0))

        while not self._stop.is_set():
            started = now()
            await asyncio.gather(*(one(i) for i in list(self.instruments.values())))
            await asyncio.sleep(max(0.2, interval - (now() - started)))

    async def poll_tickers(self, instruments: list[Instrument]) -> None:
        sem = asyncio.Semaphore(8)

        async def one(inst: Instrument) -> None:
            async with sem:
                try:
                    d = await self.rest.get("/bapi/defi/v1/public/alpha-trade/ticker/24hr", params={"symbol": inst.native_symbol}, retries=0)
                except Exception:
                    return
                t = d.get("data") or {}
                self.emit_ticker(inst, now(), volume_24h=f(t.get("quoteVolume")), price_change_24h_pct=f(t.get("priceChangePercent")),
                                 last_price=f(t.get("lastPrice")), trades_24h=int(t.get("count", 0) or 0))

        await asyncio.gather(*(one(i) for i in instruments))
