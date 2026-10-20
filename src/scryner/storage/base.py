"""Storage interface and the SQL schema shared by SQLite and TimescaleDB."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from scryner.core.config import StorageConfig
from scryner.core.models import Bar

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS bars_1m (
    key TEXT NOT NULL, ts INTEGER NOT NULL,
    o REAL, h REAL, l REAL, c REAL, buy_quote REAL, sell_quote REAL, total_quote REAL,
    trades_count INTEGER, buy_trades INTEGER, sell_trades INTEGER,
    oi_open REAL, oi_high REAL, oi_low REAL, oi_close REAL, oi_value REAL, had_trade INTEGER, max_trade_quote REAL,
    PRIMARY KEY (key, ts)
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, ts REAL NOT NULL, type TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events (type, ts);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL, updated_at REAL NOT NULL);
"""

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS bars_1m (
    key TEXT NOT NULL, ts BIGINT NOT NULL,
    o DOUBLE PRECISION, h DOUBLE PRECISION, l DOUBLE PRECISION, c DOUBLE PRECISION,
    buy_quote DOUBLE PRECISION, sell_quote DOUBLE PRECISION, total_quote DOUBLE PRECISION,
    trades_count INTEGER, buy_trades INTEGER, sell_trades INTEGER,
    oi_open DOUBLE PRECISION, oi_high DOUBLE PRECISION, oi_low DOUBLE PRECISION, oi_close DOUBLE PRECISION,
    oi_value DOUBLE PRECISION, had_trade SMALLINT, max_trade_quote DOUBLE PRECISION,
    PRIMARY KEY (key, ts)
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, ts DOUBLE PRECISION NOT NULL, type TEXT NOT NULL, key TEXT NOT NULL, payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events (type, ts);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL, updated_at DOUBLE PRECISION NOT NULL);
"""

BAR_COLUMNS = (
    "key", "ts", "o", "h", "l", "c", "buy_quote", "sell_quote", "total_quote", "trades_count", "buy_trades",
    "sell_trades", "oi_open", "oi_high", "oi_low", "oi_close", "oi_value", "had_trade", "max_trade_quote",
)


def bar_row(key: str, b: Bar) -> tuple[Any, ...]:
    return (
        key, int(b.ts), b.o, b.h, b.l, b.c, b.buy_quote, b.sell_quote, b.total_quote, b.trades_count, b.buy_trades,
        b.sell_trades, b.oi_open, b.oi_high, b.oi_low, b.oi_close, b.oi_value, int(b.had_trade), b.max_trade_quote,
    )


def row_bar(r: tuple[Any, ...]) -> Bar:
    return Bar(
        ts=int(r[1]), o=r[2], h=r[3], l=r[4], c=r[5], buy_quote=r[6] or 0.0, sell_quote=r[7] or 0.0, total_quote=r[8] or 0.0,
        trades_count=int(r[9] or 0), buy_trades=int(r[10] or 0), sell_trades=int(r[11] or 0), oi_open=r[12], oi_high=r[13],
        oi_low=r[14], oi_close=r[15], oi_value=r[16], had_trade=bool(r[17]), max_trade_quote=r[18] or 0.0,
    )


class Storage(ABC):
    def __init__(self, cfg: StorageConfig) -> None:
        self.cfg = cfg

    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def write_bars_1m(self, rows: list[tuple[str, Bar]]) -> None: ...

    @abstractmethod
    async def read_bars_1m(self, key: str, since_ts: int, limit: int) -> list[Bar]: ...

    @abstractmethod
    async def write_event(self, event_id: str, ts: float, type_: str, key: str, payload: dict[str, Any]) -> None: ...

    @abstractmethod
    async def read_events(self, type_: str, since_ts: float, limit: int) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def kv_get(self, k: str) -> str | None: ...

    @abstractmethod
    async def kv_set(self, k: str, v: str) -> None: ...

    @abstractmethod
    async def prune(self, now: float) -> None: ...

    @abstractmethod
    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None: ...

    @abstractmethod
    async def fetch(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]: ...
