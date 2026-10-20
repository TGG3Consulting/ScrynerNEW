"""Event bus.

Two real implementations behind one interface:

* :class:`MemoryBus` — in-process fan-out via ``asyncio.Queue`` (single-process deployments,
  tests, dev).
* :class:`RedisBus` — Redis Streams + Pub/Sub for multi-process deployments (one worker per
  exchange, one API gateway).

Messages are ``dict`` payloads serialised with orjson.  Topics are plain strings such as
``metrics.binance_futures`` or ``events.tick``.
"""

from __future__ import annotations

import asyncio
import fnmatch
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

import orjson

Handler = Callable[[str, dict[str, Any]], Awaitable[None]]


class Bus(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, topic: str, payload: dict[str, Any]) -> None: ...
    def subscribe(self, pattern: str, maxsize: int = 10_000) -> Subscription: ...


class Subscription:
    """An async iterator over ``(topic, payload)`` for a topic pattern (fnmatch style)."""

    def __init__(self, bus: MemoryBus | RedisBus, pattern: str, maxsize: int) -> None:
        self._bus = bus
        self.pattern = pattern
        self.queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def matches(self, topic: str) -> bool:
        return fnmatch.fnmatchcase(topic, self.pattern)

    def push(self, topic: str, payload: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait((topic, payload))
        except asyncio.QueueFull:
            # Drop the oldest message: real-time consumers prefer fresh data to backlog.
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.dropped += 1
            self.queue.put_nowait((topic, payload))

    def __aiter__(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        return self

    async def __anext__(self) -> tuple[str, dict[str, Any]]:
        return await self.queue.get()

    def close(self) -> None:
        self._bus._remove(self)


class MemoryBus:
    def __init__(self) -> None:
        self._subs: list[Subscription] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self._subs.clear()

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        for sub in self._subs:
            if sub.matches(topic):
                sub.push(topic, payload)

    def subscribe(self, pattern: str, maxsize: int = 10_000) -> Subscription:
        sub = Subscription(self, pattern, maxsize)
        self._subs.append(sub)
        return sub

    def _remove(self, sub: Subscription) -> None:
        if sub in self._subs:
            self._subs.remove(sub)


class RedisBus:
    """Redis Pub/Sub bus. Pattern subscriptions use Redis ``PSUBSCRIBE`` with glob patterns."""

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._url = url
        self._redis = aioredis.from_url(url, decode_responses=False)
        self._pubsub = self._redis.pubsub()
        self._subs: list[Subscription] = []
        self._task: asyncio.Task[None] | None = None
        self._patterns: set[str] = set()

    async def start(self) -> None:
        await self._redis.ping()
        self._task = asyncio.create_task(self._reader(), name="redis-bus-reader")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._pubsub.aclose()
        await self._redis.aclose()

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        await self._redis.publish(topic, orjson.dumps(payload))

    def subscribe(self, pattern: str, maxsize: int = 10_000) -> Subscription:
        sub = Subscription(self, pattern, maxsize)
        self._subs.append(sub)
        if pattern not in self._patterns:
            self._patterns.add(pattern)
            asyncio.get_running_loop().create_task(self._pubsub.psubscribe(pattern))
        return sub

    def _remove(self, sub: Subscription) -> None:
        if sub in self._subs:
            self._subs.remove(sub)

    async def _reader(self) -> None:
        while True:
            msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is None:
                await asyncio.sleep(0)
                continue
            if msg.get("type") not in ("message", "pmessage"):
                continue
            channel = msg["channel"]
            topic = channel.decode() if isinstance(channel, bytes) else str(channel)
            payload = orjson.loads(msg["data"])
            for sub in self._subs:
                if sub.matches(topic):
                    sub.push(topic, payload)

    @property
    def redis(self) -> Any:
        return self._redis


def create_bus(backend: str, redis_url: str) -> MemoryBus | RedisBus:
    if backend == "redis":
        return RedisBus(redis_url)
    if backend == "memory":
        return MemoryBus()
    raise ValueError(f"unknown bus backend: {backend}")
