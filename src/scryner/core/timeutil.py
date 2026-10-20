"""Time helpers: wall clock in float seconds, bucket alignment."""

from __future__ import annotations

import time


def now() -> float:
    return time.time()


def now_ms() -> int:
    return int(time.time() * 1000)


def bucket(ts: float, size_sec: int) -> int:
    """Start of the bucket (unix seconds) that contains ``ts``."""
    return int(ts // size_sec) * size_sec


def parse_ts(value: float | int | str | None, default: float | None = None) -> float:
    """Normalise an exchange timestamp (seconds, milliseconds, microseconds, nanoseconds)."""
    if value is None:
        return default if default is not None else time.time()
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default if default is not None else time.time()
    if v > 1e17:  # nanoseconds
        return v / 1e9
    if v > 1e14:  # microseconds
        return v / 1e6
    if v > 1e11:  # milliseconds
        return v / 1e3
    return v
