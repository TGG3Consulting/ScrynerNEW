"""TimescaleDB / PostgreSQL storage (asyncpg). ``bars_1m`` becomes a hypertable when the
timescaledb extension is available; plain PostgreSQL works too."""

from __future__ import annotations

from typing import Any

import asyncpg
import orjson

from scryner.core.config import StorageConfig
from scryner.core.models import Bar

from .base import BAR_COLUMNS, SCHEMA_PG, Storage, bar_row, row_bar


def _pg_params(sql: str) -> str:
    out = []
    n = 0
    for ch in sql:
        if ch == "?":
            n += 1
            out.append(f"${n}")
        else:
            out.append(ch)
    return "".join(out)


class TimescaleStorage(Storage):
    def __init__(self, cfg: StorageConfig) -> None:
        super().__init__(cfg)
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("storage is not open")
        return self._pool

    async def open(self) -> None:
        self._pool = await asyncpg.create_pool(self.cfg.timescale_dsn, min_size=1, max_size=8)
        async with self.pool.acquire() as con:
            await con.execute(SCHEMA_PG)
            try:
                await con.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
                await con.execute("SELECT create_hypertable('bars_1m', 'ts', chunk_time_interval => 86400, if_not_exists => TRUE)")
            except asyncpg.PostgresError:
                pass  # plain PostgreSQL without timescaledb

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def write_bars_1m(self, rows: list[tuple[str, Bar]]) -> None:
        if not rows:
            return
        cols = ",".join(BAR_COLUMNS)
        marks = ",".join(f"${i + 1}" for i in range(len(BAR_COLUMNS)))
        updates = ",".join(f"{c}=EXCLUDED.{c}" for c in BAR_COLUMNS[2:])
        async with self.pool.acquire() as con:
            await con.executemany(f"INSERT INTO bars_1m ({cols}) VALUES ({marks}) ON CONFLICT (key, ts) DO UPDATE SET {updates}",
                                  [bar_row(k, b) for k, b in rows])

    async def read_bars_1m(self, key: str, since_ts: int, limit: int) -> list[Bar]:
        cols = ",".join(BAR_COLUMNS)
        async with self.pool.acquire() as con:
            rows = await con.fetch(f"SELECT {cols} FROM (SELECT {cols} FROM bars_1m WHERE key=$1 AND ts>=$2 ORDER BY ts DESC LIMIT $3) t ORDER BY ts ASC",
                                   key, since_ts, limit)
        return [row_bar(tuple(r)) for r in rows]

    async def write_event(self, event_id: str, ts: float, type_: str, key: str, payload: dict[str, Any]) -> None:
        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO events (id, ts, type, key, payload) VALUES ($1,$2,$3,$4,$5::jsonb) ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload",
                              event_id, ts, type_, key, orjson.dumps(payload).decode())

    async def read_events(self, type_: str, since_ts: float, limit: int) -> list[dict[str, Any]]:
        async with self.pool.acquire() as con:
            rows = await con.fetch("SELECT payload FROM (SELECT ts, payload FROM events WHERE type=$1 AND ts>=$2 ORDER BY ts DESC LIMIT $3) t ORDER BY ts ASC",
                                   type_, since_ts, limit)
        return [orjson.loads(r[0]) if isinstance(r[0], str | bytes) else dict(r[0]) for r in rows]

    async def kv_get(self, k: str) -> str | None:
        async with self.pool.acquire() as con:
            r = await con.fetchrow("SELECT v FROM kv WHERE k=$1", k)
        return r[0] if r else None

    async def kv_set(self, k: str, v: str) -> None:
        import time

        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO kv (k, v, updated_at) VALUES ($1,$2,$3) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v, updated_at=EXCLUDED.updated_at",
                              k, v, time.time())

    async def prune(self, now: float) -> None:
        r = self.cfg.retention
        async with self.pool.acquire() as con:
            await con.execute("DELETE FROM bars_1m WHERE ts < $1", int(now - r.bars_1m_hours * 3600))
            await con.execute("DELETE FROM events WHERE ts < $1", now - r.events_days * 86400)

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        async with self.pool.acquire() as con:
            await con.execute(_pg_params(sql), *params)

    async def fetch(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        async with self.pool.acquire() as con:
            rows = await con.fetch(_pg_params(sql), *params)
        return [tuple(r) for r in rows]
