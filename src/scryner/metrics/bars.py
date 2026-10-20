"""Compact ring buffers of closed bars backed by numpy.

Column layout (float64):
 0 ts | 1 o | 2 h | 3 l | 4 c | 5 buy_quote | 6 sell_quote | 7 total_quote | 8 trades_count
 9 buy_trades | 10 sell_trades | 11 oi_open | 12 oi_high | 13 oi_low | 14 oi_close | 15 oi_value
 16 had_trade | 17 max_trade_quote
Missing OI is stored as NaN.
"""

from __future__ import annotations

import math

import numpy as np

from scryner.core.models import Bar

COLS = 18
(TS, O, H, L, C, BUY, SELL, TOTAL, TRADES, BUY_TRADES, SELL_TRADES,
 OI_O, OI_H, OI_L, OI_C, OI_VALUE, HAD_TRADE, MAX_TRADE) = range(COLS)


def _nan(v: float | None) -> float:
    return math.nan if v is None else float(v)


class RingBars:
    """Fixed-capacity ring of closed bars with gap filling.

    Bars are appended in strictly increasing ``ts`` order aligned to ``size_sec``. Gaps are
    filled with flat bars (price = previous close, zero volume) so that index arithmetic
    maps directly to time, which makes window queries O(1) slices.
    """

    __slots__ = ("_buf", "_head", "_n", "capacity", "last_close", "last_ts", "size_sec")

    def __init__(self, size_sec: int, capacity: int) -> None:
        self.size_sec = size_sec
        self.capacity = capacity
        self._buf = np.full((capacity, COLS), np.nan, dtype=np.float64)
        self._n = 0
        self._head = 0  # index where the next bar is written
        self.last_ts: int | None = None
        self.last_close: float = math.nan

    def __len__(self) -> int:
        return self._n

    def _write(self, row: np.ndarray) -> None:
        self._buf[self._head] = row
        self._head = (self._head + 1) % self.capacity
        if self._n < self.capacity:
            self._n += 1

    def append(self, bar: Bar) -> None:
        ts = int(bar.ts)
        if self.last_ts is not None:
            if ts <= self.last_ts:
                return
            gap_bars = (ts - self.last_ts) // self.size_sec - 1
            if gap_bars > 0:
                fill = min(gap_bars, self.capacity)
                row = np.full(COLS, np.nan)
                row[[O, H, L, C]] = self.last_close
                row[[BUY, SELL, TOTAL, TRADES, BUY_TRADES, SELL_TRADES, HAD_TRADE, MAX_TRADE]] = 0.0
                last_oi = self._buf[(self._head - 1) % self.capacity, OI_C] if self._n else math.nan
                row[[OI_O, OI_H, OI_L, OI_C]] = last_oi
                start = self.last_ts + self.size_sec
                for i in range(fill):
                    row[TS] = start + (gap_bars - fill + i) * self.size_sec
                    self._write(row.copy())
        row = np.array([
            ts, bar.o, bar.h, bar.l, bar.c, bar.buy_quote, bar.sell_quote, bar.total_quote,
            bar.trades_count, bar.buy_trades, bar.sell_trades,
            _nan(bar.oi_open), _nan(bar.oi_high), _nan(bar.oi_low), _nan(bar.oi_close), _nan(bar.oi_value),
            1.0 if bar.had_trade else 0.0, bar.max_trade_quote,
        ], dtype=np.float64)
        self._write(row)
        self.last_ts = ts
        self.last_close = bar.c

    def view(self) -> np.ndarray:
        """Chronological view of stored bars (copy-free when the ring has not wrapped)."""
        if self._n < self.capacity:
            return self._buf[: self._n]
        return np.concatenate((self._buf[self._head :], self._buf[: self._head]))

    def last(self, count: int) -> np.ndarray:
        if count <= 0 or self._n == 0:
            return self._buf[:0]
        count = min(count, self._n)
        start = (self._head - count) % self.capacity
        if start + count <= self.capacity:
            return self._buf[start : start + count]
        return np.concatenate((self._buf[start:], self._buf[: (start + count) % self.capacity]))

    def since(self, ts: float) -> np.ndarray:
        """Bars with ``bar.ts >= ts``."""
        if self.last_ts is None or ts > self.last_ts:
            return self._buf[:0]
        count = int((self.last_ts - ts) // self.size_sec) + 1
        return self.last(count)

    def records(self, count: int, as_1s: bool = False) -> list[dict[str, float | int | None]]:
        out: list[dict[str, float | int | None]] = []
        for r in self.last(count):
            out.append(row_to_dict(r, as_1s))
        return out


def _opt(v: float) -> float | None:
    return None if math.isnan(v) else float(v)


def row_to_dict(r: np.ndarray, as_1s: bool = False) -> dict[str, float | int | None]:
    if as_1s:
        return {
            "ts": int(r[TS]), "o": float(r[O]), "h": float(r[H]), "l": float(r[L]), "c": float(r[C]),
            "total_quote": round(float(r[TOTAL]), 6), "buy_quote": round(float(r[BUY]), 6),
            "sell_quote": round(float(r[SELL]), 6), "trades_count": int(r[TRADES]),
            "oi_close": _opt(r[OI_C]), "oi_value": _opt(r[OI_VALUE]), "had_trade": int(r[HAD_TRADE]),
        }
    return {
        "ts": int(r[TS]), "o": float(r[O]), "h": float(r[H]), "l": float(r[L]), "c": float(r[C]),
        "buy_quote": round(float(r[BUY]), 6), "sell_quote": round(float(r[SELL]), 6),
        "total_quote": round(float(r[TOTAL]), 6), "trades_count": int(r[TRADES]),
        "oi_close": _opt(r[OI_C]), "oi_open": _opt(r[OI_O]), "oi_high": _opt(r[OI_H]), "oi_low": _opt(r[OI_L]),
    }


def aggregate(rows: np.ndarray, group: int) -> np.ndarray:
    """Aggregate consecutive ``group`` rows into one bar each (e.g. 1m → 5m).

    Only complete groups counted from the end are returned, aligned to the natural bucket
    boundary of ``group * size_sec`` (callers pass rows that start on a boundary).
    """
    n = (len(rows) // group) * group
    if n == 0:
        return rows[:0]
    r = rows[len(rows) - n :].reshape(-1, group, COLS)
    out = np.empty((r.shape[0], COLS))
    out[:, TS] = r[:, 0, TS]
    out[:, O] = r[:, 0, O]
    out[:, H] = np.nanmax(r[:, :, H], axis=1)
    out[:, L] = np.nanmin(r[:, :, L], axis=1)
    out[:, C] = r[:, -1, C]
    for col in (BUY, SELL, TOTAL, TRADES, BUY_TRADES, SELL_TRADES):
        out[:, col] = np.nansum(r[:, :, col], axis=1)
    out[:, OI_O] = r[:, 0, OI_O]
    with np.errstate(all="ignore"):
        out[:, OI_H] = np.nanmax(r[:, :, OI_H], axis=1)
        out[:, OI_L] = np.nanmin(r[:, :, OI_L], axis=1)
    out[:, OI_C] = r[:, -1, OI_C]
    out[:, OI_VALUE] = r[:, -1, OI_VALUE]
    out[:, HAD_TRADE] = np.max(r[:, :, HAD_TRADE], axis=1)
    out[:, MAX_TRADE] = np.max(r[:, :, MAX_TRADE], axis=1)
    return out


def true_range(rows: np.ndarray) -> np.ndarray:
    """ATR-style true range per bar: max(h-l, |h-prev_c|, |l-prev_c|)."""
    if len(rows) == 0:
        return rows[:0, 0]
    prev_c = np.concatenate(([rows[0, O]], rows[:-1, C]))
    hl = rows[:, H] - rows[:, L]
    hc = np.abs(rows[:, H] - prev_c)
    lc = np.abs(rows[:, L] - prev_c)
    return np.maximum.reduce([hl, hc, lc])
