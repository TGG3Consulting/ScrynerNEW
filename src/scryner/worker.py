"""Source worker: one exchange market → bars, metrics, detectors, bus.

A worker owns one :class:`Collector`, one :class:`MetricsEngine`, the cluster store and the
event detectors for its source, and publishes to the bus:

* ``metrics.<source>`` — ``{"type":"snapshot"|"patch","seq":n,"records":{key: fields}}``
  every ``publish_sec`` (only changed fields in patches, ``delete`` for removed keys)
* ``tick.<source>`` — one message per tick event
* ``density.<source>``, ``bots.<source>`` — detector events
* ``bar.<source>`` — closed 1m bars; ``chart.<source>`` — closed+partial bars for subscribed keys
* ``catalog.<source>`` — catalog snapshot on change

and listens on ``cmd.<source>`` for ``{"type":"chart_subscribe","keys":[...]}``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from scryner.collectors import create_collector
from scryner.core.bus import MemoryBus, RedisBus
from scryner.core.catalog import Catalog, catalog_record
from scryner.core.config import ExchangeConfig, ScrynerConfig
from scryner.core.logging import get_logger
from scryner.core.models import Bar, BookUpdate, Instrument, OIUpdate, TickerUpdate, TickEvent, Trade
from scryner.detectors.bots import BotDetector
from scryner.detectors.density import DensityDetector
from scryner.metrics.bars import row_to_dict
from scryner.metrics.clusters import ClusterStore
from scryner.metrics.engine import MetricsEngine
from scryner.metrics.records import build_record, diff_record
from scryner.storage import Storage


def tick_event_dict(ev: TickEvent, inst: Instrument) -> dict[str, Any]:
    d: dict[str, Any] = {
        "exchange": inst.exchange, "pair": inst.pair, "buy": round(ev.buy, 4), "sell": round(ev.sell, 4), "timestamp": ev.ts,
        "volatility_oc": ev.price_move_pct, "volatility_hl": ev.price_range_pct, "market_type": inst.market_type,
        "price_ts": ev.price_ts, "price": ev.price, "price_open": ev.price_open, "price_high": ev.price_high,
        "price_low": ev.price_low, "price_move_pct": ev.price_move_pct, "price_range_pct": ev.price_range_pct,
        "window_sec": ev.window_sec, "tick_threshold": ev.tick_threshold, "tick_threshold_base": ev.tick_threshold_base,
        "tick_threshold_multiplier": ev.tick_threshold_multiplier, "volume_24h": ev.volume_24h,
        "display_pair": inst.display_pair, "volume_currency": inst.volume_currency, "key": inst.key,
        "source_route_id": inst.source_route_id,
    }
    if inst.stock_scope:
        d["stock_scope"] = True
        d["stock_symbol"] = inst.stock_symbol
    if inst.display_symbol:
        d["display_symbol"] = inst.display_symbol
    if inst.display_name:
        d["display_name"] = inst.display_name
    return d


class SourceWorker:
    def __init__(self, cfg: ScrynerConfig, source: ExchangeConfig, bus: MemoryBus | RedisBus, storage: Storage | None) -> None:
        self.cfg = cfg
        self.source = source
        self.bus = bus
        self.storage = storage
        self.sid = source.source_route_id
        self.log = get_logger("worker", source=self.sid)
        self.catalog = Catalog()
        ret = cfg.storage.retention
        self.engine = MetricsEngine(
            cfg.metrics, cfg.ticks, bars_1s_capacity=ret.bars_1s_memory_minutes * 60,
            bars_1m_capacity=max(1500, min(ret.bars_1m_hours * 60, 60 * 48)), on_tick=self._on_tick, on_bar_close=self._on_bar_close,
        )
        self.clusters = ClusterStore(keep_columns=ret.clusters_days * 24 if ret.clusters_days <= 2 else 48)
        self.density = DensityDetector(cfg.density, source.exchange, self._on_density)
        self.bots = BotDetector(cfg.bots, self._on_bot)
        self.collector = create_collector(source, self)
        self.records: dict[str, dict[str, Any]] = {}
        self.seq = 0
        self._pending_bars: list[tuple[str, Bar]] = []
        self._chart_keys: set[str] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bar_q: asyncio.Queue[tuple[str, int, Bar]] = asyncio.Queue(maxsize=100_000)

    # ------------------------------------------------------------------ CollectorSink
    def on_instruments(self, source_route_id: str, instruments: list[Instrument]) -> None:
        present = set()
        for inst in instruments:
            present.add(inst.key)
            self.catalog.upsert(inst)
            self.engine.add_instrument(inst)
            self.clusters.set_increment(inst.key, inst.tick_size)
        for key in self.catalog.deactivate_missing(source_route_id, present):
            self.engine.remove_instrument(key)
            self.records.pop(key, None)
        self._schedule(self._publish_catalog())

    def on_trade(self, t: Trade) -> None:
        self.engine.on_trade(t)
        self.clusters.on_trade(t)
        self.bots.on_trade(t)
        self.density.on_trade(t)

    def on_book(self, b: BookUpdate) -> None:
        self.density.on_book(b)

    def on_oi(self, u: OIUpdate) -> None:
        self.engine.on_oi(u)

    def on_ticker(self, u: TickerUpdate) -> None:
        self.engine.on_ticker(u)

    # ------------------------------------------------------------------ engine callbacks
    def _on_tick(self, ev: TickEvent) -> None:
        inst = self.catalog.get(ev.key)
        if inst is None:
            return
        self._schedule(self.bus.publish(f"tick.{self.sid}", tick_event_dict(ev, inst)))

    def _on_bar_close(self, key: str, size: int, bar: Bar) -> None:
        if size == 60:
            self._pending_bars.append((key, bar))
            if key in self._chart_keys:
                self._schedule(self.bus.publish(f"bar.{self.sid}", {"key": key, "timeframe": "1m", "bar": bar.to_1m_dict()}))

    def _on_density(self, payload: dict[str, Any]) -> None:
        inst = self.catalog.get(payload["key"])
        if inst is None:
            return
        payload.update({
            "exchange": inst.exchange, "exchange_label": inst.source_title, "source_route_id": inst.source_route_id,
            "market_type": inst.market_type, "pair": inst.pair, "display_pair": inst.display_pair,
            "display_symbol": inst.display_symbol or inst.base_asset, "quote_currency": inst.quote_currency,
            "base": inst.base_asset, "quote": inst.quote_currency, "meta_symbol": inst.pair, "native_symbol": inst.native_symbol,
        })
        self._schedule(self.bus.publish(f"density.{self.sid}", payload))

    def _on_bot(self, payload: dict[str, Any]) -> None:
        inst = self.catalog.get(payload["key"])
        if inst is None:
            return
        payload.update({"exchange": inst.exchange, "source_title": inst.source_title, "pair": inst.pair, "display_pair": inst.display_pair,
                        "market_type": inst.market_type, "source_route_id": inst.source_route_id})
        self._schedule(self.bus.publish(f"bots.{self.sid}", payload))

    def _schedule(self, coro: Any) -> None:
        loop = self._loop or asyncio.get_event_loop()
        loop.create_task(coro)

    # ------------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._tasks = [
            asyncio.create_task(self.collector.run(), name=f"{self.sid}:collector"),
            asyncio.create_task(self._clock_loop(), name=f"{self.sid}:clock"),
            asyncio.create_task(self._publish_loop(), name=f"{self.sid}:publish"),
            asyncio.create_task(self._persist_loop(), name=f"{self.sid}:persist"),
            asyncio.create_task(self._chart_loop(), name=f"{self.sid}:chart"),
            asyncio.create_task(self._cmd_loop(), name=f"{self.sid}:cmd"),
            asyncio.create_task(self._seed_history(), name=f"{self.sid}:seed"),
        ]
        try:
            await self._stop.wait()
        finally:
            for t in self._tasks:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            await self.collector.shutdown()

    def stop(self) -> None:
        self._stop.set()

    async def _seed_history(self) -> None:
        """Seed 1m rings: from storage first, then backfill the gap from exchange klines."""
        await asyncio.sleep(5)  # wait for the catalog
        since = int(time.time()) - self.cfg.storage.retention.bars_1m_hours * 3600
        n_db = 0
        n_ex = 0
        want = int(self.source.extra.get("backfill_minutes", self.cfg.metrics.backfill_minutes))
        sem = asyncio.Semaphore(int(self.source.extra.get("backfill_concurrency", 4)))

        async def one(key: str) -> None:
            nonlocal n_db, n_ex
            bars: list[Bar] = []
            if self.storage is not None:
                try:
                    bars = await self.storage.read_bars_1m(key, since, 60 * 48)
                except Exception as exc:
                    self.log.warning("seed_db_error", key=key, error=str(exc)[:100])
            n_db += len(bars)
            last_ts = bars[-1].ts if bars else 0
            missing = int((time.time() - last_ts) // 60) if last_ts else want
            inst = self.catalog.get(key)
            if want > 0 and missing > 5 and inst is not None:
                async with sem:
                    try:
                        fresh = await self.collector.fetch_klines_1m(inst, min(want, missing))
                    except Exception as exc:
                        self.log.debug("seed_kline_error", key=key, error=str(exc)[:100])
                        fresh = []
                fresh = [b for b in fresh if b.ts > last_ts]
                bars.extend(fresh)
                n_ex += len(fresh)
                if fresh:
                    self._pending_bars.extend((key, b) for b in fresh)
            if bars:
                self.engine.seed_bars_1m(key, bars)

        await asyncio.gather(*(one(k) for k in self.catalog.keys(self.sid)))
        self.log.info("seeded_bars", from_db=n_db, from_exchange=n_ex)

    async def _clock_loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            self.engine.tick_clock(now)
            self.density.on_clock(now)
            self.bots.on_clock(now)
            await asyncio.sleep(max(0.05, 1.0 - (time.time() % 1.0)))

    async def _publish_loop(self) -> None:
        interval = self.cfg.metrics.publish_sec
        recompute = self.cfg.metrics.recompute_sec
        last_pub = 0.0
        while not self._stop.is_set():
            started = time.time()
            updates: dict[str, dict[str, Any]] = {}
            for key in self.catalog.keys(self.sid):
                st = self.engine.get(key)
                inst = self.catalog.get(key)
                if st is None or inst is None:
                    continue
                m = self.engine.compute(key, started)
                if m is None:
                    continue
                rec = build_record(inst, m, self.engine, self.source.port)
                d = diff_record(self.records.get(key), rec)
                if d:
                    updates[key] = d
                    self.records[key] = rec
                if time.time() - started > recompute * 0.8:
                    await asyncio.sleep(0)
            now = time.time()
            if updates and now - last_pub >= interval:
                self.seq += 1
                await self.bus.publish(f"metrics.{self.sid}", {"type": "patch", "seq": self.seq, "updates": updates, "ts": now})
                last_pub = now
            elif updates:
                # accumulate until the publish interval elapses
                pass
            await asyncio.sleep(max(0.1, recompute - (time.time() - started)))

    async def _persist_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(5)
            if self.storage is None or not self._pending_bars:
                continue
            batch, self._pending_bars = self._pending_bars, []
            try:
                await self.storage.write_bars_1m(batch)
            except Exception as exc:
                self.log.warning("persist_error", error=str(exc)[:200], rows=len(batch))

    async def _chart_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(0.5)
            if not self._chart_keys:
                continue
            msgs = []
            for key in list(self._chart_keys):
                st = self.engine.get(key)
                if st is None:
                    continue
                closed = st.ring_1m.last(1)
                msgs.append({
                    "key": key, "timeframe": "1m",
                    "closed": row_to_dict(closed[0]) if len(closed) else None,
                    "partial": st.cur_1m.to_1m_dict() if st.cur_1m is not None else None,
                    "source_route_id": self.sid,
                })
            if msgs:
                await self.bus.publish(f"chart.{self.sid}", {"charts": msgs})

    async def _cmd_loop(self) -> None:
        sub = self.bus.subscribe(f"cmd.{self.sid}")
        try:
            async for _topic, payload in sub:
                if payload.get("type") == "chart_subscribe":
                    self._chart_keys = {k for k in payload.get("keys", []) if k in self.catalog}
                elif payload.get("type") == "tick_multiplier":
                    self.engine.set_tick_multiplier(payload["key"], float(payload.get("multiplier", 1.0)))
                elif payload.get("type") == "snapshot_request":
                    await self.bus.publish(f"metrics.{self.sid}", {"type": "snapshot", "seq": self.seq, "records": self.records, "ts": time.time()})
        finally:
            sub.close()

    async def _publish_catalog(self) -> None:
        await self.bus.publish(f"catalog.{self.sid}", {"version": self.catalog.version, "catalog": {k: catalog_record(v) for k, v in ((k, self.catalog.get(k)) for k in self.catalog.keys(self.sid)) if v}})

    # ------------------------------------------------------------------ direct access (memory mode)
    def history_1m(self, key: str, limit: int) -> list[dict[str, Any]]:
        st = self.engine.get(key)
        return st.ring_1m.records(limit) if st else []

    def history_1s(self, key: str, limit: int) -> list[dict[str, Any]]:
        st = self.engine.get(key)
        return st.ring_1s.records(limit, as_1s=True) if st else []

    def partial(self, key: str, timeframe: str) -> dict[str, Any] | None:
        st = self.engine.get(key)
        if st is None:
            return None
        if timeframe == "1s":
            return st.cur_1s.to_1s_dict() if st.cur_1s else None
        return st.cur_1m.to_1m_dict() if st.cur_1m else None
