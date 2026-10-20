"""Persistent storage: closed 1m bars, events, users. SQLite (dev) or TimescaleDB (prod)."""

from __future__ import annotations

from scryner.core.config import StorageConfig

from .base import Storage


def create_storage(cfg: StorageConfig) -> Storage:
    if cfg.backend == "sqlite":
        from .sqlite import SqliteStorage

        return SqliteStorage(cfg)
    if cfg.backend == "timescale":
        from .timescale import TimescaleStorage

        return TimescaleStorage(cfg)
    raise ValueError(f"unknown storage backend: {cfg.backend}")


__all__ = ["Storage", "create_storage"]
