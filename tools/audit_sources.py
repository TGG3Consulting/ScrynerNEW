"""Audit every configured source: run each collector briefly and print a green/red
matrix of which data arrives (instruments, trades, order book, OI, ticker).

    python tools/audit_sources.py [seconds] [max_instruments] [source,source,...]

Examples:
    python tools/audit_sources.py                     # all enabled sources, 15s each
    python tools/audit_sources.py 20                  # 20s each
    python tools/audit_sources.py 15 10 binance_futures,okx_futures

Spot markets have no open interest, so OI shows "—" (not a failure) for them.
Run this on the machine that actually hosts the screener: geo-blocked exchanges
behave differently per network, so the matrix reflects *your* connectivity.
"""
from __future__ import annotations

import asyncio
import collections
import sys

from scryner.collectors import create_collector
from scryner.core.config import load_config
from scryner.core.logging import setup_logging
from scryner.core.models import BookUpdate, Instrument, OIUpdate, TickerUpdate, Trade


class Sink:
    def __init__(self) -> None:
        self.c: collections.Counter[str] = collections.Counter()
        self.instruments = 0

    def on_instruments(self, src: str, items: list[Instrument]) -> None:
        self.instruments = len(items)

    def on_trade(self, t: Trade) -> None:
        self.c["trade"] += 1

    def on_book(self, b: BookUpdate) -> None:
        self.c["book"] += 1

    def on_oi(self, u: OIUpdate) -> None:
        self.c["oi"] += 1

    def on_ticker(self, u: TickerUpdate) -> None:
        self.c["ticker"] += 1


async def run_one(name: str, ex, secs: float, limit: int) -> dict:
    ex.extra["max_instruments"] = limit
    sink = Sink()
    col = create_collector(ex, sink)
    task = asyncio.create_task(col.run())
    await asyncio.sleep(secs)
    ws_ok = any(c.connected for c in col._ws_clients) or bool(getattr(col, "_poll_tasks", None))
    col.stop()
    try:
        await asyncio.wait_for(task, timeout=10)
    except (TimeoutError, Exception):  # noqa: BLE001
        pass
    return {
        "name": name, "market": ex.market_type, "instruments": sink.instruments,
        "trade": sink.c["trade"], "book": sink.c["book"], "oi": sink.c["oi"], "ticker": sink.c["ticker"],
        "ws": ws_ok,
    }


def cell(n: int) -> str:
    return f"\033[32m✓{n:>6}\033[0m" if n > 0 else "\033[31m  ✗  \033[0m"


async def main() -> None:
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    only = sys.argv[3].split(",") if len(sys.argv) > 3 else []
    setup_logging("WARNING", json_output=False)
    cfg = load_config()
    sources = cfg.enabled_sources()
    if only:
        sources = {k: v for k, v in sources.items() if k in only}

    print(f"Auditing {len(sources)} sources × {secs:.0f}s (limit {limit} instruments)…\n")
    results = await asyncio.gather(*(run_one(n, ex, secs, limit) for n, ex in sources.items()))

    print(f"{'SOURCE':<22}{'INSTR':>7}  {'TRADE':>7} {'BOOK':>7} {'OI':>7} {'TICKER':>7}  WS")
    print("-" * 74)
    bad = []
    for r in sorted(results, key=lambda x: x["name"]):
        oi_cell = cell(r["oi"]) if r["market"] != "spot" else "   —   "
        print(f"{r['name']:<22}{r['instruments']:>7}  {cell(r['trade'])} {cell(r['book'])} {oi_cell} {cell(r['ticker'])}  {'✓' if r['ws'] else '✗'}")
        # data flowing implies instruments loaded; judge by the live params only
        need_oi = r["market"] != "spot"
        if not (r["trade"] and r["book"] and r["ticker"] and (r["oi"] or not need_oi)):
            bad.append(r["name"])
    print("-" * 74)
    if bad:
        print(f"\n\033[31mПроблемные источники ({len(bad)}):\033[0m {', '.join(bad)}")
        print("Если они зелёные у тебя в браузере на бирже — это сеть/гео контейнера; иначе пришли вывод.")
    else:
        print("\n\033[32mВсе источники отдают все нужные параметры ✓\033[0m")


if __name__ == "__main__":
    asyncio.run(main())
