"""Liquidity wall (density) detector on local order books.

Per instrument we maintain an L2 book from snapshots/deltas. On every clock tick we scan the
top ``neighbour_levels`` of each side for a level whose notional (price × size in USD) is at
least ``min_size_usd`` for the exchange and at least ``dominance_ratio`` × the median
notional of its neighbours. A candidate becomes an event once it has lived
``min_lifetime_sec`` seconds, within ``max_distance_pct`` of the mid price, with a cooldown
per instrument/side/level. After publication the detector tracks the price for the
``impact_windows_sec`` windows and emits an ``impact`` update (``good`` if the price moved
towards the wall side, ``weak`` otherwise). Icebergs: a level whose visible size drops below
``iceberg_refill_ratio`` and refills repeatedly is flagged ``is_iceberg``.
"""

from __future__ import annotations

import statistics
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from scryner.core.config import DensityConfig
from scryner.core.models import BookUpdate, Trade
from scryner.core.numfmt import short_number

Sink = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class Book:
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    ts: float = 0.0
    seq: int = 0

    def apply(self, u: BookUpdate) -> None:
        if u.is_snapshot:
            self.bids = {p: s for p, s in u.bids if s > 0}
            self.asks = {p: s for p, s in u.asks if s > 0}
        else:
            for p, s in u.bids:
                if s <= 0:
                    self.bids.pop(p, None)
                else:
                    self.bids[p] = s
            for p, s in u.asks:
                if s <= 0:
                    self.asks.pop(p, None)
                else:
                    self.asks[p] = s
        self.ts = u.ts
        self.seq = u.seq

    def best(self) -> tuple[float | None, float | None]:
        bb = max(self.bids) if self.bids else None
        ba = min(self.asks) if self.asks else None
        return bb, ba


@dataclass(slots=True)
class Candidate:
    side: str
    price: float
    first_seen: float
    last_seen: float
    max_size: float
    min_size: float
    refills: int = 0
    published: bool = False
    event_id: str = ""
    published_at: float = 0.0
    ref_price: float = 0.0
    impact_done: set[int] = field(default_factory=set)


class DensityDetector:
    def __init__(self, cfg: DensityConfig, exchange: str, sink: Sink) -> None:
        self.cfg = cfg
        self.exchange = exchange
        self.sink = sink
        self.min_size = cfg.min_size_usd_by_exchange.get(exchange, cfg.min_size_usd_default)
        self.books: dict[str, Book] = {}
        self.candidates: dict[str, dict[tuple[str, float], Candidate]] = {}
        self.last_price: dict[str, float] = {}
        self.cooldown: dict[tuple[str, str, float], float] = {}
        self.active: list[tuple[str, Candidate]] = []
        self._counter = 0

    def on_book(self, u: BookUpdate) -> None:
        book = self.books.get(u.key)
        if book is None:
            book = Book()
            self.books[u.key] = book
        book.apply(u)

    def on_trade(self, t: Trade) -> None:
        self.last_price[t.key] = t.price

    def on_clock(self, now: float) -> None:
        for key, book in self.books.items():
            if not book.bids or not book.asks:
                continue
            self._scan(key, book, now)
        self._impacts(now)

    def _notional(self, price: float, size: float) -> float:
        return price * size

    def _scan(self, key: str, book: Book, now: float) -> None:
        bb, ba = book.best()
        if bb is None or ba is None:
            return
        mid = (bb + ba) / 2
        cands = self.candidates.setdefault(key, {})
        seen: set[tuple[str, float]] = set()
        for side, levels, reverse in (("bid", book.bids, True), ("ask", book.asks, False)):
            top = sorted(levels.items(), key=lambda kv: kv[0], reverse=reverse)[: self.cfg.neighbour_levels]
            if len(top) < 3:
                continue
            notionals = [self._notional(p, s) for p, s in top]
            med = statistics.median(notionals) or 1.0
            for (price, size), notional in zip(top, notionals, strict=False):
                dist = abs(price - mid) / mid * 100.0
                if notional < self.min_size or notional < self.cfg.dominance_ratio * med or dist > self.cfg.max_distance_pct:
                    continue
                ck = (side, price)
                seen.add(ck)
                c = cands.get(ck)
                if c is None:
                    cands[ck] = Candidate(side=side, price=price, first_seen=now, last_seen=now, max_size=size, min_size=size)
                    continue
                if size < c.max_size * self.cfg.iceberg_refill_ratio:
                    c.min_size = min(c.min_size, size)
                elif c.min_size < c.max_size * self.cfg.iceberg_refill_ratio:
                    c.refills += 1
                    c.min_size = size
                c.max_size = max(c.max_size, size)
                c.last_seen = now
                if not c.published and now - c.first_seen >= self.cfg.min_lifetime_sec:
                    cd_key = (key, side, price)
                    if now - self.cooldown.get(cd_key, 0.0) < self.cfg.cooldown_sec:
                        continue
                    self.cooldown[cd_key] = now
                    self._publish(key, c, notional, dist, now)
        # drop vanished candidates
        for ck in list(cands):
            if ck not in seen:
                c = cands[ck]
                if now - c.last_seen > 2.0:
                    if c.published:
                        self.sink({"event": "density.removed", "id": c.event_id, "key": key, "removed_at": now})
                        self.active = [(k, x) for k, x in self.active if x is not c]
                    cands.pop(ck, None)

    def _publish(self, key: str, c: Candidate, notional: float, dist: float, now: float) -> None:
        self._counter += 1
        c.published = True
        c.event_id = f"{int(now)}-{self._counter}-{uuid.uuid4().hex[:6]}"
        c.published_at = now
        c.ref_price = self.last_price.get(key, c.price)
        self.active.append((key, c))
        emoji = "🟩" if c.side == "bid" else "🟥"
        metric_line = f"{emoji} {short_number(notional)} @ {c.price:g} | {dist:.4f}%"
        self.sink({
            "event": "density.message", "id": c.event_id, "key": key, "captured_at": now, "side": c.side, "price": c.price,
            "size_usd": round(notional, 4), "distance_pct": round(dist, 6), "is_iceberg": c.refills >= 2, "title": "",
            "metric_line": metric_line, "text": "", "source": "", "source_type": "density_alert", "impact": {},
        })

    def _impacts(self, now: float) -> None:
        for key, c in list(self.active):
            price = self.last_price.get(key)
            if price is None or not c.ref_price:
                continue
            for w in self.cfg.impact_windows_sec:
                if w in c.impact_done or now - c.published_at < w:
                    continue
                c.impact_done.add(w)
                move = (price - c.ref_price) / c.ref_price * 100.0
                towards = move if c.side == "bid" else -move
                label = "good" if towards >= self.cfg.impact_good_pct else "weak"
                self.sink({"event": "density.impact", "id": c.event_id, "key": key, "window_sec": w, "move_pct": round(move, 4), "label": label})
            if all(w in c.impact_done for w in self.cfg.impact_windows_sec) and now - c.published_at > max(self.cfg.impact_windows_sec) + 5:
                self.active = [(k, x) for k, x in self.active if x is not c]


def density_text(payload: dict[str, Any]) -> str:
    """Render the card text exactly like the reference feed."""
    lines = [payload.get("exchange_label", ""), payload.get("display_pair", ""), payload.get("metric_line", ""), "| Price impact:"]
    for w, info in sorted((payload.get("impact") or {}).items(), key=lambda kv: int(kv[0])):
        mins = int(w) // 60
        lines.append(f"| {mins}m: {info['label']} {info['move_pct']:+.4f}%")
    return "\n".join(lines)


__all__ = ["Book", "DensityDetector", "density_text", "time"]
