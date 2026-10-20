"""Run one collector against the live exchange for N seconds and report what arrived.

    python tools/smoke_collector.py okx_futures 20 [max_instruments]
"""
from __future__ import annotations

import asyncio
import collections
import sys

from scryner.collectors import create_collector
from scryner.core.config import load_config
from scryner.core.logging import setup_logging
from scryner.core.models import BookUpdate, Instrument, OIUpdate, TickerUpdate, Trade


class CountingSink:
    def __init__(self) -> None:
        self.c: collections.Counter[str] = collections.Counter()
        self.samples: dict[str, object] = {}
        self.instruments: list[Instrument] = []

    def on_instruments(self, src: str, items: list[Instrument]) -> None:
        self.instruments = items
        self.samples["instrument"] = items[0] if items else None

    def on_trade(self, t: Trade) -> None:
        self.c["trade"] += 1
        self.samples["trade"] = t

    def on_book(self, b: BookUpdate) -> None:
        self.c["book"] += 1
        self.samples["book"] = (b.key, b.ts, b.is_snapshot, len(b.bids), len(b.asks), b.bids[:2], b.asks[:2])

    def on_oi(self, u: OIUpdate) -> None:
        self.c["oi"] += 1
        self.samples["oi"] = u

    def on_ticker(self, u: TickerUpdate) -> None:
        self.c["ticker"] += 1
        self.samples["ticker"] = u


async def main() -> None:
    source, secs = sys.argv[1], float(sys.argv[2])
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    include = sys.argv[4].split(",") if len(sys.argv) > 4 else []
    setup_logging("INFO", json_output=False)
    cfg = load_config()
    ex = cfg.exchanges[source]
    ex.extra["max_instruments"] = limit
    if include:
        ex.extra["include_symbols"] = include
    sink = CountingSink()
    col = create_collector(ex, sink)
    task = asyncio.create_task(col.run())
    await asyncio.sleep(secs)
    print(f"\n=== {source}: instruments={len(sink.instruments)} counts={dict(sink.c)} ws={[(c.name, c.connected, c.messages, c.reconnects) for c in col._ws_clients][:4]}")
    for k, v in sink.samples.items():
        print(f"  {k}: {str(v)[:300]}")
    col.stop()
    try:
        await asyncio.wait_for(task, timeout=15)
    except TimeoutError:
        print("  (shutdown timed out)")


if __name__ == "__main__":
    asyncio.run(main())
