"""Algorithmic-trading pattern detector (bots): P1 strict timer, C1 micro-pressure, S1 spreader.

Input: the trade stream of one source. Per instrument we keep the recent trades (last
``c1_window_sec``). Patterns are evaluated on the clock (once per second) and on arrival:

* **P1 · Strict timer** — ≥ ``min_series`` consecutive same-side trades whose quote sizes are
  within ``p1_size_tolerance`` of their median and whose inter-arrival intervals are within
  ``p1_interval_tolerance`` of their median (e.g. "$199 every ~5.0 s").
* **C1 · Micro-pressure** — ≥ ``c1_min_trades`` trades in ``c1_window_sec`` with one side
  holding ≥ ``c1_min_side_share`` of the volume and a measurable price drift in that
  direction (``pressure_pct``).
* **S1 · Spreader** — ≥ ``s1_min_pairs`` alternating buy/sell pairs of near-equal size
  within short intervals (market making / wash patterns).

Rating R0…R5 grows with series length and regularity. Each (key, pattern, side) has a
cooldown equal to the window so the same series is not reported twice.
"""

from __future__ import annotations

import itertools
import statistics
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from scryner.core.config import BotConfig
from scryner.core.models import Trade
from scryner.core.numfmt import short_usd

Sink = Callable[[dict[str, Any]], None]


class BotDetector:
    def __init__(self, cfg: BotConfig, sink: Sink) -> None:
        self.cfg = cfg
        self.sink = sink
        self.trades: dict[str, deque[Trade]] = {}
        self.last_eval: dict[str, float] = {}
        self.cooldown: dict[tuple[str, str, str], float] = {}

    def on_trade(self, t: Trade) -> None:
        if t.quote < self.cfg.min_tick_volume_usd:
            return
        q = self.trades.get(t.key)
        if q is None:
            q = deque(maxlen=2000)
            self.trades[t.key] = q
        q.append(t)

    def on_clock(self, now: float) -> None:
        horizon = now - self.cfg.c1_window_sec
        for key, q in self.trades.items():
            while q and q[0].ts < horizon:
                q.popleft()
            if len(q) < self.cfg.min_series:
                continue
            if now - self.last_eval.get(key, 0.0) < 1.0:
                continue
            self.last_eval[key] = now
            self._p1(key, q, now)
            self._c1(key, q, now)
            self._s1(key, q, now)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _rating(n: int, regularity: float) -> int:
        # regularity: 0 (noisy) … 1 (perfect); n: series length
        score = min(1.0, (n - 4) / 12) * 0.6 + regularity * 0.4
        return max(0, min(5, round(score * 5)))

    def _emit(self, key: str, pattern: str, side: str, trades: list[Trade], rating: int, avg_interval: float, now: float,
              pressure_pct: float | None = None, side_share: float | None = None) -> None:
        ck = (key, pattern, side)
        if now - self.cooldown.get(ck, 0.0) < self.cfg.c1_window_sec:
            return
        self.cooldown[ck] = now
        first_price = trades[0].price
        rows = []
        for t in trades[-10:]:
            move = (t.price - first_price) / first_price * 100.0 if first_price else 0.0
            rows.append({"ts": t.ts, "quote": round(t.quote, 2), "price": t.price, "move_pct": round(move, 4), "side": t.side})
        self.sink({
            "event": "bot.message", "id": f"{int(now)}-{uuid.uuid4().hex[:8]}", "key": key, "captured_at": now, "pattern": pattern,
            "side": side, "rating": rating, "avg_interval_sec": round(avg_interval, 2), "trades": rows,
            "pressure_pct": None if pressure_pct is None else round(pressure_pct, 2),
            "pressure_window_sec": int(self.cfg.c1_window_sec) if pressure_pct is not None else None,
            "side_volume_share": None if side_share is None else round(side_share, 3),
        })

    # ------------------------------------------------------------------ patterns
    def _p1(self, key: str, q: deque[Trade], now: float) -> None:
        for side in ("buy", "sell"):
            same = [t for t in q if t.side == side]
            if len(same) < self.cfg.min_series:
                continue
            # take the longest tail with regular size and interval
            tail = same[-30:]
            best: list[Trade] = []
            for start in range(len(tail) - self.cfg.min_series + 1):
                seg = tail[start:]
                sizes = [t.quote for t in seg]
                med = statistics.median(sizes)
                if med <= 0 or any(abs(s - med) / med > self.cfg.p1_size_tolerance for s in sizes):
                    continue
                gaps = [b.ts - a.ts for a, b in itertools.pairwise(seg)]
                gmed = statistics.median(gaps)
                if gmed <= 0 or gmed > self.cfg.max_gap_sec or any(abs(g - gmed) / gmed > self.cfg.p1_interval_tolerance for g in gaps):
                    continue
                best = seg
                break
            if len(best) >= self.cfg.min_series and now - best[-1].ts <= self.cfg.max_gap_sec * 2:
                gaps = [b.ts - a.ts for a, b in itertools.pairwise(best)]
                gmed = statistics.median(gaps)
                reg = 1.0 - min(1.0, statistics.pstdev(gaps) / gmed) if gmed else 0.0
                self._emit(key, "P1", side, best, self._rating(len(best), reg), gmed, now)

    def _c1(self, key: str, q: deque[Trade], now: float) -> None:
        trades = list(q)
        if len(trades) < self.cfg.c1_min_trades:
            return
        buy = sum(t.quote for t in trades if t.side == "buy")
        sell = sum(t.quote for t in trades if t.side == "sell")
        total = buy + sell
        if total <= 0:
            return
        share = max(buy, sell) / total
        if share < self.cfg.c1_min_side_share:
            return
        side = "buy" if buy >= sell else "sell"
        same = [t for t in trades if t.side == side]
        if len(same) < self.cfg.c1_min_trades:
            return
        first, last = same[0].price, same[-1].price
        pressure = (last - first) / first * 100.0 if first else 0.0
        if abs(pressure) < self.cfg.min_tick_move_pct:
            return
        if (side == "buy" and pressure <= 0) or (side == "sell" and pressure >= 0):
            return
        gaps = [b.ts - a.ts for a, b in itertools.pairwise(same)]
        gmed = statistics.median(gaps) if gaps else 0.0
        reg = 1.0 - min(1.0, statistics.pstdev(gaps) / gmed) if gmed else 0.0
        self._emit(key, "C1", side, same, self._rating(len(same), reg), gmed, now, pressure_pct=pressure, side_share=share)

    def _s1(self, key: str, q: deque[Trade], now: float) -> None:
        trades = list(q)[-40:]
        pairs = 0
        used: list[Trade] = []
        i = 0
        while i < len(trades) - 1:
            a, b = trades[i], trades[i + 1]
            if a.side != b.side and a.quote > 0 and abs(a.quote - b.quote) / a.quote <= self.cfg.s1_size_tolerance and b.ts - a.ts <= self.cfg.max_gap_sec:
                pairs += 1
                used.extend([a, b])
                i += 2
            else:
                i += 1
        if pairs >= self.cfg.s1_min_pairs and used and now - used[-1].ts <= self.cfg.max_gap_sec * 2:
            gaps = [b.ts - a.ts for a, b in itertools.pairwise(used)]
            gmed = statistics.median(gaps) if gaps else 0.0
            reg = 1.0 - min(1.0, statistics.pstdev(gaps) / gmed) if gmed else 0.0
            self._emit(key, "S1", "buy", used, self._rating(len(used), reg), gmed, now)


def bot_text(p: dict[str, Any]) -> str:
    """Render the card text in the reference layout."""
    side_emoji = "🟩" if p["side"] == "buy" else "🟥"
    arrow = ""
    if p["pattern"] == "C1":
        arrow = "▲" if p["side"] == "buy" else "▼"
    header = f"{p['pattern']} {side_emoji}{arrow} [{str(p.get('source_title', '')).upper()}]"
    lines = [header, "", f"{p.get('display_pair', '')} R{p['rating']}", "", f"🟨 ~{p['avg_interval_sec']:.1f}s"]
    if p.get("pressure_pct") is not None:
        lines.append(f"C1 pressure: {p['pressure_pct']:+.2f}% over {p['pressure_window_sec']}s | side vol {round((p.get('side_volume_share') or 0) * 100)}%")
    lines.append("")
    for t in p.get("trades", []):
        import datetime as _dt

        mmss = _dt.datetime.fromtimestamp(t["ts"], tz=_dt.UTC).strftime("%M:%S")
        e = "🟩" if t["side"] == "buy" else "🟥"
        lines.append(f"{e} {short_usd(t['quote'])} @ {mmss} | {t['move_pct']:+g}%")
    return "\n".join(lines)
