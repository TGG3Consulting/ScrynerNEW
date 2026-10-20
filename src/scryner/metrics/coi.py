"""Combined open interest (COI) across exchanges per base asset.

For every ``coi_base`` (``crypto::BTC``) we keep a time series of the summed OI value in
quote currency across all instruments that reported OI, and derive deltas over the windows
``1m, 5m, 15m, 1h, 24h`` plus the number of contributing sources per window.

Output format mirrors the original ``coi`` map:
``[[d_1m, d_5m, d_15m, d_1h, d_24h], [n_1m, n_5m, n_15m, n_1h, n_24h], n_total, ts]``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

WINDOWS = (60, 300, 900, 3600, 86_400)


@dataclass(slots=True)
class _Source:
    oi_value: float
    ts: float
    history: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=90_000))


class CoiAggregator:
    def __init__(self, sample_sec: float = 1.0) -> None:
        self.sample_sec = sample_sec
        self._sources: dict[str, dict[str, _Source]] = {}  # coi_base -> key -> source

    def update(self, coi_base: str, key: str, oi_value: float | None, ts: float, inverse: bool = False) -> None:
        if oi_value is None or oi_value <= 0:
            return
        srcs = self._sources.setdefault(coi_base, {})
        s = srcs.get(key)
        if s is None:
            s = _Source(oi_value=oi_value, ts=ts)
            srcs[key] = s
        s.oi_value, s.ts = oi_value, ts
        if not s.history or ts - s.history[-1][0] >= self.sample_sec:
            s.history.append((ts, oi_value))

    @staticmethod
    def _value_at(h: deque[tuple[float, float]], ts: float) -> float | None:
        """Last sample at or before ``ts`` (linear scan from the right; windows are short)."""
        if not h or h[0][0] > ts:
            return None
        for t, v in reversed(h):
            if t <= ts:
                return v
        return None

    def snapshot(self, now: float, min_age_sec: float = 0.0) -> dict[str, list[object]]:
        out: dict[str, list[object]] = {}
        for base, srcs in self._sources.items():
            deltas: list[float] = []
            counts: list[int] = []
            for w in WINDOWS:
                d = 0.0
                n = 0
                for s in srcs.values():
                    if now - s.ts > 3600:
                        continue
                    past = self._value_at(s.history, now - w)
                    if past is None:
                        continue
                    d += s.oi_value - past
                    n += 1
                deltas.append(d)
                counts.append(n)
            total = sum(1 for s in srcs.values() if now - s.ts <= 3600)
            if total:
                out[base] = [deltas, counts, total, now]
        return out
