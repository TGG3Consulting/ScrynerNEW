"""Runs the non-exchange feeds: Telegram bot, Telegram channels, TWAP indexer, stock quotes."""

from __future__ import annotations

import asyncio
from typing import Any

from scryner.api.auth import AuthService
from scryner.core.bus import MemoryBus, RedisBus
from scryner.core.config import ScrynerConfig
from scryner.core.logging import get_logger
from scryner.detectors.twap import TwapIndexer
from scryner.storage import Storage

from .stocks import StockQuotes
from .telegram_bot import TelegramBot
from .telegram_channels import TelegramChannels


class FeedRunner:
    def __init__(self, cfg: ScrynerConfig, bus: MemoryBus | RedisBus, storage: Storage) -> None:
        self.cfg = cfg
        self.bus = bus
        self.storage = storage
        self.log = get_logger("feeds")
        self.twap = TwapIndexer(cfg.twap, self._twap_patch)

    async def _twap_patch(self, seq: int, updates: dict[str, Any], deletes: list[str]) -> None:
        await self.bus.publish("twap", {"type": "patch", "seq": seq, "updates": updates, "delete": deletes})

    async def _twap_snapshots(self) -> None:
        sub = self.bus.subscribe("cmd.twap")
        try:
            async for _t, payload in sub:
                if payload.get("type") == "snapshot_request":
                    await self.bus.publish("twap", {"type": "snapshot", "seq": self.twap.seq, "data": self.twap.snapshot()})
        finally:
            sub.close()

    async def _twap_periodic_snapshot(self) -> None:
        # a full snapshot every minute keeps late-joining gateways consistent
        while True:
            await asyncio.sleep(60)
            await self.bus.publish("twap", {"type": "snapshot", "seq": self.twap.seq, "data": self.twap.snapshot()})

    async def run(self) -> None:
        auth = AuthService(self.storage, self.cfg.api, trial_days=self.cfg.api.trial_days, admin_telegram_ids=tuple(self.cfg.api.admin_telegram_ids))
        await auth.init()
        tasks = [
            asyncio.create_task(self._guard("twap", self.twap.run())),
            asyncio.create_task(self._twap_snapshots()),
            asyncio.create_task(self._twap_periodic_snapshot()),
            asyncio.create_task(self._guard("telegram_bot", TelegramBot(self.cfg.telegram, auth).run())),
            asyncio.create_task(self._guard("telegram_channels", TelegramChannels(self.cfg.telegram, self.bus).run())),
            asyncio.create_task(self._guard("stocks", StockQuotes(self.cfg.stocks, self.bus).run())),
        ]
        await asyncio.gather(*tasks)

    async def _guard(self, name: str, coro: Any) -> None:
        delay = 5.0
        while True:
            try:
                await coro
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log.warning("feed_crashed", feed=name, error=str(exc)[:200], restart_in=delay)
                await asyncio.sleep(delay)
                delay = min(120.0, delay * 2)
                # re-create the coroutine for the next attempt
                coro = self._recreate(name)

    def _recreate(self, name: str) -> Any:
        if name == "twap":
            self.twap = TwapIndexer(self.cfg.twap, self._twap_patch)
            return self.twap.run()
        if name == "telegram_channels":
            return TelegramChannels(self.cfg.telegram, self.bus).run()
        if name == "stocks":
            return StockQuotes(self.cfg.stocks, self.bus).run()
        if name == "telegram_bot":
            auth = AuthService(self.storage, self.cfg.api, trial_days=self.cfg.api.trial_days, admin_telegram_ids=tuple(self.cfg.api.admin_telegram_ids))
            return TelegramBot(self.cfg.telegram, auth).run()
        raise ValueError(name)
