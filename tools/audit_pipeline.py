"""End-to-end pipeline audit: run every source worker + the gateway on one bus and report
what actually reaches the gateway per source (TOP records, densities, bots, ticks).

    python tools/audit_pipeline.py [seconds] [max_instruments]

Unlike audit_sources.py (which tests collectors in isolation), this exercises the full
server chain: collector -> metrics/detectors -> bus -> GatewayState. Run it on the machine
that hosts the screener to see, per exchange, what the browser would actually receive.
A source with 0 TOP records did not connect (REST failed / geo-blocked on this network).
"""
from __future__ import annotations

import asyncio
import collections
import sys

from scryner.api.state import GatewayState
from scryner.core.bus import MemoryBus
from scryner.core.config import load_config
from scryner.core.logging import setup_logging
from scryner.worker import SourceWorker


async def main() -> None:
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 25
    setup_logging("WARNING", json_output=False)
    cfg = load_config()
    bus = MemoryBus()
    workers: dict[str, SourceWorker] = {}
    for sid, ex in cfg.enabled_sources().items():
        ex.extra["max_instruments"] = limit
        workers[sid] = SourceWorker(cfg, ex, bus, None)
    state = GatewayState(cfg, bus, workers)
    dens: collections.Counter[str] = collections.Counter()
    bots: collections.Counter[str] = collections.Counter()
    ticks: collections.Counter[str] = collections.Counter()
    state.listen("density", lambda _k, p: dens.update([p.get("source_route_id", "?")]))
    state.listen("bots", lambda _k, p: bots.update([p.get("source_route_id", "?")]))
    state.listen("tick", lambda _k, p: ticks.update([p.get("source_route_id", "?")]))

    print(f"Running full pipeline for {len(workers)} sources × {secs:.0f}s (limit {limit})…\n")
    await state.start()
    tasks = [asyncio.create_task(w.run()) for w in workers.values()]
    import time

    t0 = time.time()
    while time.time() - t0 < secs:
        await asyncio.sleep(max(10.0, secs / 5))
        print(f"  [{int(time.time() - t0):>3}s] records={len(state.records)} densities={sum(dens.values())} bots={sum(bots.values())} ticks={sum(ticks.values())}")

    recs: collections.Counter[str] = collections.Counter(r.get("source_route_id", "?") for r in state.records.values())
    for w in workers.values():
        w.stop()
    await asyncio.sleep(0.5)

    print(f"\n{'SOURCE':<22}{'TOP':>6}{'density':>9}{'bots':>7}{'ticks':>8}")
    print("-" * 52)
    dead = []
    for sid in sorted(cfg.enabled_sources()):
        r = recs.get(sid, 0)
        g = "\033[31m" if r == 0 else "\033[32m"
        print(f"{g}{sid:<22}\033[0m{r:>6}{dens.get(sid, 0):>9}{bots.get(sid, 0):>7}{ticks.get(sid, 0):>8}")
        if r == 0:
            dead.append(sid)
    print("-" * 52)
    if dead:
        print(f"\n\033[31mНе подключились ({len(dead)}):\033[0m {', '.join(dead)}")
        print("TOP=0 означает, что не загрузился каталог (REST не прошёл / гео-блок на этой сети).")
    else:
        print("\n\033[32mВсе источники дошли до gateway ✓\033[0m")


if __name__ == "__main__":
    asyncio.run(main())
