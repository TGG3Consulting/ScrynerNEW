"""Entry point.

    python -m scryner all                 # every enabled source + gateway in one process (memory bus)
                                          # WARNING: All sources share CPU/memory. For production use worker mode.
    python -m scryner worker <source>     # one source worker (redis bus) - production mode with isolation
    python -m scryner gateway             # API gateway (redis bus)
    python -m scryner feeds               # Telegram channels / TWAP / stock reference feeds
"""

from __future__ import annotations

import argparse
import asyncio
import signal

from scryner.app import run_all, run_feeds, run_gateway, run_worker
from scryner.core.config import load_config
from scryner.core.logging import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="scryner")
    parser.add_argument("mode", choices=["all", "worker", "gateway", "feeds"])
    parser.add_argument("source", nargs="?", help="source_route_id for worker mode")
    parser.add_argument("--config", help="path to YAML config")
    parser.add_argument("--sources", help="comma-separated subset of sources for `all`")
    args = parser.parse_args()
    cfg = load_config(args.config)
    setup_logging(cfg.app.log_level, cfg.app.log_json)

    async def runner() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass
        if args.mode == "all":
            subset = args.sources.split(",") if args.sources else None
            await run_all(cfg, stop, subset)
        elif args.mode == "worker":
            if not args.source:
                parser.error("worker mode requires a source")
            await run_worker(cfg, args.source, stop)
        elif args.mode == "gateway":
            await run_gateway(cfg, stop)
        else:
            await run_feeds(cfg, stop)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
