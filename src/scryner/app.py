"""Process composition: ``all`` (single process), ``worker``, ``gateway``, ``feeds``."""

from __future__ import annotations

import asyncio

from scryner.core.bus import MemoryBus, RedisBus, create_bus
from scryner.core.config import ScrynerConfig
from scryner.core.logging import get_logger
from scryner.storage import Storage, create_storage
from scryner.worker import SourceWorker

log = get_logger("app")


async def _open_storage(cfg: ScrynerConfig) -> Storage:
    st = create_storage(cfg.storage)
    await st.open()
    return st


async def run_worker(cfg: ScrynerConfig, source: str, stop: asyncio.Event) -> None:
    if source not in cfg.exchanges:
        raise SystemExit(f"unknown source {source}; known: {', '.join(cfg.exchanges)}")
    bus = create_bus(cfg.bus.backend, cfg.bus.redis_url)
    await bus.start()
    storage = await _open_storage(cfg)
    worker = SourceWorker(cfg, cfg.exchanges[source], bus, storage)
    task = asyncio.create_task(worker.run())
    await stop.wait()
    worker.stop()
    await asyncio.wait_for(task, timeout=30)
    await storage.close()
    await bus.stop()


async def run_gateway(cfg: ScrynerConfig, stop: asyncio.Event) -> None:
    from scryner.api.server import Gateway

    bus = create_bus(cfg.bus.backend, cfg.bus.redis_url)
    await bus.start()
    storage = await _open_storage(cfg)
    gw = Gateway(cfg, bus, storage, workers={})
    await gw.serve(stop)
    await storage.close()
    await bus.stop()


async def run_feeds(cfg: ScrynerConfig, stop: asyncio.Event) -> None:
    from scryner.feeds.runner import FeedRunner

    bus = create_bus(cfg.bus.backend, cfg.bus.redis_url)
    await bus.start()
    storage = await _open_storage(cfg)
    runner = FeedRunner(cfg, bus, storage)
    task = asyncio.create_task(runner.run())
    await stop.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await storage.close()
    await bus.stop()


async def run_all(cfg: ScrynerConfig, stop: asyncio.Event, subset: list[str] | None = None) -> None:
    """Everything in one process over the in-memory bus (dev / small deployments).

    WARNING: All sources share CPU/memory in this mode. A misbehaving collector
    can impact all others. For production with many sources, use worker mode:
    docker-compose with one service per source + redis bus for isolation.
    """
    from scryner.api.server import Gateway
    from scryner.feeds.runner import FeedRunner

    bus: MemoryBus | RedisBus = create_bus(cfg.bus.backend, cfg.bus.redis_url)
    await bus.start()
    storage = await _open_storage(cfg)
    workers: dict[str, SourceWorker] = {}
    tasks: list[asyncio.Task[None]] = []
    for sid, ex in cfg.enabled_sources().items():
        if subset and sid not in subset:
            continue
        w = SourceWorker(cfg, ex, bus, storage)
        workers[sid] = w
        tasks.append(asyncio.create_task(w.run(), name=f"worker:{sid}"))
    log.info("workers_started", count=len(workers))
    feeds = FeedRunner(cfg, bus, storage)
    tasks.append(asyncio.create_task(feeds.run(), name="feeds"))
    gw = Gateway(cfg, bus, storage, workers=workers)
    await gw.serve(stop)
    for w in workers.values():
        w.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await storage.close()
    await bus.stop()
