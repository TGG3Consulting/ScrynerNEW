"""Gateway state: merged view of all sources.

The gateway keeps the latest record of every instrument (from ``metrics.<source>`` patches),
the catalogs, ring buffers of recent events (ticks, densities, bots, news…), the TWAP map and
COI aggregation. Channels (``/ws/all/v1`` etc.) read from here. In single-process mode the
workers are also reachable directly for bar history and partial bars.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from scryner.core.bus import MemoryBus, RedisBus
from scryner.core.config import ScrynerConfig
from scryner.core.logging import get_logger
from scryner.metrics.coi import CoiAggregator
from scryner.worker import SourceWorker

Listener = Callable[[str, dict[str, Any]], None]

ROUTE_SCOPES: dict[str, dict[str, Any]] = {
    "all_futures": {"market_scope": "futures", "exclude_stocks": True},
    "all_spots": {"market_scope": "spot", "exclude_stocks": True},
    "all_stocks": {"market_scope": "all", "stocks_only": True},
    "all_in_one": {"market_scope": "all", "exclude_stocks": False},
    "all_ticks": {"market_scope": "all", "exclude_stocks": False},
}


class GatewayState:
    def __init__(self, cfg: ScrynerConfig, bus: MemoryBus | RedisBus, workers: dict[str, SourceWorker]) -> None:
        self.cfg = cfg
        self.bus = bus
        self.workers = workers
        self.log = get_logger("gateway-state")
        self.records: dict[str, dict[str, Any]] = {}
        self.record_ts: dict[str, float] = {}
        self.catalogs: dict[str, dict[str, Any]] = {}          # source -> {key: catalog record}
        self.catalog_versions: dict[str, str] = {}
        self.seq = 0
        self.ticks: deque[dict[str, Any]] = deque(maxlen=20_000)
        self.densities: deque[dict[str, Any]] = deque(maxlen=cfg.density.history_size * 4)
        self.density_by_id: dict[str, dict[str, Any]] = {}
        self.bots: deque[dict[str, Any]] = deque(maxlen=cfg.bots.history_size * 4)
        self.news: dict[str, deque[dict[str, Any]]] = {k: deque(maxlen=cfg.telegram.history_size * 2) for k in ("news", "onchain", "listings")}
        self.twap: dict[str, dict[str, Any]] = {}
        self.twap_seq = 0
        self.stock_quotes: dict[str, dict[str, Any]] = {}
        self.coi = CoiAggregator()
        self.coi_snapshot: dict[str, list[object]] = {}
        self._listeners: dict[str, list[Listener]] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self.started_at = time.time()

    # ------------------------------------------------------------------ pub/sub for channels
    def listen(self, kind: str, fn: Listener) -> Callable[[], None]:
        self._listeners.setdefault(kind, []).append(fn)

        def off() -> None:
            lst = self._listeners.get(kind, [])
            if fn in lst:
                lst.remove(fn)

        return off

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        for fn in list(self._listeners.get(kind, [])):
            try:
                fn(kind, payload)
            except Exception as exc:
                self.log.warning("listener_error", kind=kind, error=str(exc)[:200])

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._consume("metrics.*", self._on_metrics)),
            asyncio.create_task(self._consume("catalog.*", self._on_catalog)),
            asyncio.create_task(self._consume("tick.*", self._on_tick)),
            asyncio.create_task(self._consume("density.*", self._on_density)),
            asyncio.create_task(self._consume("bots.*", self._on_bot)),
            asyncio.create_task(self._consume("chart.*", lambda t, p: self._emit("chart", p))),
            asyncio.create_task(self._consume("bar.*", lambda t, p: self._emit("bar", p))),
            asyncio.create_task(self._consume("feed.*", self._on_feed)),
            asyncio.create_task(self._consume("twap", self._on_twap)),
            asyncio.create_task(self._consume("stocks.quotes", self._on_stock_quote)),
            asyncio.create_task(self._coi_loop()),
        ]
        # ask workers for a full snapshot (redis mode); in memory mode the records flow in via patches too
        for sid in self.cfg.enabled_sources():
            await self.bus.publish(f"cmd.{sid}", {"type": "snapshot_request"})

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _consume(self, pattern: str, handler: Callable[[str, dict[str, Any]], Any]) -> None:
        sub = self.bus.subscribe(pattern, maxsize=50_000)
        try:
            async for topic, payload in sub:
                try:
                    res = handler(topic, payload)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as exc:
                    self.log.warning("consume_error", topic=topic, error=str(exc)[:200])
        finally:
            sub.close()

    # ------------------------------------------------------------------ handlers
    def _on_metrics(self, topic: str, payload: dict[str, Any]) -> None:
        now = time.time()
        if payload.get("type") == "snapshot":
            for key, rec in payload.get("records", {}).items():
                self.records[key] = rec
                self.record_ts[key] = now
            self.seq += 1
            self._emit("metrics", {"type": "snapshot", "keys": list(payload.get("records", {}))})
            return
        updates = payload.get("updates", {})
        changed: dict[str, dict[str, Any]] = {}
        for key, d in updates.items():
            rec = self.records.get(key)
            if rec is None:
                rec = {}
                self.records[key] = rec
            rec.update(d)
            self.record_ts[key] = now
            changed[key] = d
            if "coi_oi" in d or "last_price" in d:
                self._coi_update(key, rec, now)
        self.seq += 1
        if changed:
            self._emit("metrics", {"type": "patch", "updates": changed})

    def _coi_update(self, key: str, rec: dict[str, Any], now: float) -> None:
        cat = self.catalog_record(key)
        if cat is None or rec.get("market_type") != "futures":
            return
        oi = rec.get("coi_oi")
        price = rec.get("last_price")
        if oi is None or not price:
            return
        value = oi * price if not rec.get("coi_inverse") else oi  # inverse contracts are already quoted in USD
        self.coi.update(cat.get("coi_base", ""), key, value, rec.get("coi_oi_ts") or now)

    async def _coi_loop(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            self.coi_snapshot = self.coi.snapshot(time.time())

    def _on_catalog(self, topic: str, payload: dict[str, Any]) -> None:
        sid = topic.split(".", 1)[1]
        self.catalogs[sid] = payload.get("catalog", {})
        self.catalog_versions[sid] = payload.get("version", "")
        self._emit("catalog", {"source": sid})

    def _on_tick(self, topic: str, payload: dict[str, Any]) -> None:
        self.ticks.append(payload)
        self._emit("tick", payload)

    def _on_density(self, topic: str, payload: dict[str, Any]) -> None:
        ev = payload.get("event")
        if ev == "density.message":
            self.densities.append(payload)
            self.density_by_id[payload["id"]] = payload
            self._emit("density", payload)
        elif ev == "density.impact":
            rec = self.density_by_id.get(payload["id"])
            if rec is not None:
                rec.setdefault("impact", {})[str(payload["window_sec"])] = {"label": payload["label"], "move_pct": payload["move_pct"]}
                self._emit("density_impact", payload)
        elif ev == "density.removed":
            rec = self.density_by_id.get(payload["id"])
            if rec is not None:
                rec["removed_at"] = payload["removed_at"]
                self._emit("density_removed", payload)
        while len(self.density_by_id) > self.densities.maxlen * 2:  # type: ignore[operator]
            oldest = next(iter(self.density_by_id))
            self.density_by_id.pop(oldest, None)

    def _on_bot(self, topic: str, payload: dict[str, Any]) -> None:
        self.bots.append(payload)
        self._emit("bots", payload)

    def _on_feed(self, topic: str, payload: dict[str, Any]) -> None:
        kind = topic.split(".", 1)[1]
        q = self.news.get(kind)
        if q is not None:
            q.append(payload)
        self._emit(f"feed.{kind}", payload)

    def _on_twap(self, topic: str, payload: dict[str, Any]) -> None:
        if payload.get("type") == "snapshot":
            self.twap = dict(payload.get("data", {}))
        else:
            for k, v in payload.get("updates", {}).items():
                self.twap[k] = v
            for k in payload.get("delete", []):
                self.twap.pop(k, None)
        self.twap_seq += 1
        self._emit("twap", payload)

    def _on_stock_quote(self, topic: str, payload: dict[str, Any]) -> None:
        for listing, q in payload.get("quotes", {}).items():
            self.stock_quotes[listing] = q
        self._emit("stock_quote", payload)

    # ------------------------------------------------------------------ queries
    def catalog_record(self, key: str) -> dict[str, Any] | None:
        for cat in self.catalogs.values():
            rec = cat.get(key)
            if rec is not None:
                return rec
        return None

    def all_catalog(self, sources: set[str] | None = None) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for sid, cat in self.catalogs.items():
            if sources is None or sid in sources:
                out.update(cat)
        return out

    def select_keys(self, *, market_scope: str = "all", exclude_stocks: bool = False, stocks_only: bool = False,
                    source_futures: set[str] | None = None, source_spot: set[str] | None = None, route: str | None = None) -> list[str]:
        if route and route in ROUTE_SCOPES and market_scope == "all":
            scope = ROUTE_SCOPES[route]
            market_scope = scope.get("market_scope", market_scope)
            exclude_stocks = exclude_stocks or bool(scope.get("exclude_stocks"))
            stocks_only = stocks_only or bool(scope.get("stocks_only"))
        elif route and route not in ROUTE_SCOPES and "_" in route:
            # per-source route such as binance_futures
            ex, _, mt = route.rpartition("_")
            if mt == "futures":
                source_futures = {ex}
                market_scope = "futures"
            elif mt in ("spot", "alpha"):
                source_spot = {ex}
                market_scope = "spot"
        out = []
        for key, rec in self.records.items():
            mt = rec.get("market_type")
            if market_scope == "futures" and mt != "futures":
                continue
            if market_scope == "spot" and mt != "spot":
                continue
            if exclude_stocks and rec.get("stock_scope"):
                continue
            if stocks_only and not rec.get("stock_scope"):
                continue
            ex = rec.get("exchange", "")
            if mt == "futures" and source_futures is not None and ex not in source_futures:
                continue
            if mt == "spot" and source_spot is not None and ex not in source_spot and ex.replace("_alpha", "") not in source_spot:
                continue
            out.append(key)
        return out

    def worker_for(self, key: str) -> SourceWorker | None:
        rec = self.records.get(key) or {}
        sid = rec.get("source_route_id")
        if sid is None:
            cat = self.catalog_record(key)
            sid = cat.get("source_route_id") if cat else None
        return self.workers.get(sid) if sid else None
