"""SQLite storage (aiosqlite). Suitable for a single process; WAL mode, batched writes."""

from __future__ import annotations

import os
from typing import Any

import aiosqlite
import orjson

from scryner.core.config import StorageConfig
from scryner.core.models import Bar

from .base import BAR_COLUMNS, SCHEMA_SQLITE, Storage, bar_row, row_bar


class SqliteStorage(Storage):
    def __init__(self, cfg: StorageConfig) -> None:
        super().__init__(cfg)
        self._db: aiosqlite.Connection | None = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("storage is not open")
        return self._db

    async def open(self) -> None:
        d = os.path.dirname(self.cfg.sqlite_path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._db = await aiosqlite.connect(self.cfg.sqlite_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(SCHEMA_SQLITE)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def write_bars_1m(self, rows: list[tuple[str, Bar]]) -> None:
        if not rows:
            return
        cols = ",".join(BAR_COLUMNS)
        marks = ",".join("?" for _ in BAR_COLUMNS)
        await self.db.executemany(f"INSERT OR REPLACE INTO bars_1m ({cols}) VALUES ({marks})", [bar_row(k, b) for k, b in rows])
        await self.db.commit()

    async def read_bars_1m(self, key: str, since_ts: int, limit: int) -> list[Bar]:
        cols = ",".join(BAR_COLUMNS)
        cur = await self.db.execute(
            f"SELECT {cols} FROM (SELECT {cols} FROM bars_1m WHERE key=? AND ts>=? ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC",
            (key, since_ts, limit),
        )
        rows = await cur.fetchall()
        return [row_bar(tuple(r)) for r in rows]

    async def write_event(self, event_id: str, ts: float, type_: str, key: str, payload: dict[str, Any]) -> None:
        await self.db.execute("INSERT OR REPLACE INTO events (id, ts, type, key, payload) VALUES (?,?,?,?,?)",
                              (event_id, ts, type_, key, orjson.dumps(payload).decode()))
        await self.db.commit()

    async def read_events(self, type_: str, since_ts: float, limit: int) -> list[dict[str, Any]]:
        cur = await self.db.execute("SELECT payload FROM (SELECT ts, payload FROM events WHERE type=? AND ts>=? ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC",
                                    (type_, since_ts, limit))
        return [orjson.loads(r[0]) for r in await cur.fetchall()]

    async def kv_get(self, k: str) -> str | None:
        cur = await self.db.execute("SELECT v FROM kv WHERE k=?", (k,))
        r = await cur.fetchone()
        return r[0] if r else None

    async def kv_set(self, k: str, v: str) -> None:
        import time

        await self.db.execute("INSERT OR REPLACE INTO kv (k, v, updated_at) VALUES (?,?,?)", (k, v, time.time()))
        await self.db.commit()

    async def prune(self, now: float) -> None:
        r = self.cfg.retention
        await self.db.execute("DELETE FROM bars_1m WHERE ts < ?", (int(now - r.bars_1m_hours * 3600),))
        await self.db.execute("DELETE FROM events WHERE ts < ?", (now - r.events_days * 86400,))
        await self.db.commit()

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        await self.db.execute(sql, params)
        await self.db.commit()

    async def fetch(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        cur = await self.db.execute(sql, params)
        return [tuple(r) for r in await cur.fetchall()]
