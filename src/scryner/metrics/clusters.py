"""Volume-at-price clusters (footprint) per instrument and hour.

Trades are bucketed by hour and by price level (``tick_size``). Taker buys accumulate on the
ask side, taker sells on the bid side (a market buy lifts the ask). Snapshots aggregate
levels into ``price_step`` groups requested by the client (``zoom``).
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from scryner.core.models import Trade
from scryner.core.timeutil import bucket


@dataclass(slots=True)
class ClusterColumn:
    start_ts: int
    end_ts: int
    levels: dict[float, list[float]] = field(default_factory=dict)  # price -> [ask, bid, count]
    asks_sum: float = 0.0
    bids_sum: float = 0.0
    anchor: float | None = None

    def add(self, price_level: float, trade: Trade) -> None:
        row = self.levels.get(price_level)
        if row is None:
            row = [0.0, 0.0, 0.0]
            self.levels[price_level] = row
        if trade.side == "buy":
            row[0] += trade.qty
            self.asks_sum += trade.qty
        else:
            row[1] += trade.qty
            self.bids_sum += trade.qty
        row[2] += 1
        if self.anchor is None:
            self.anchor = price_level


class ClusterStore:
    def __init__(self, timeframe_sec: int = 3600, keep_columns: int = 24, max_instruments: int = 5000) -> None:
        self.timeframe_sec = timeframe_sec
        self.keep_columns = keep_columns
        self.max_instruments = max_instruments
        self._data: OrderedDict[str, OrderedDict[int, ClusterColumn]] = OrderedDict()
        self._increment: dict[str, float] = {}

    def set_increment(self, key: str, tick_size: float) -> None:
        self._increment[key] = tick_size if tick_size > 0 else 0.0

    def _level(self, key: str, price: float) -> float:
        inc = self._increment.get(key, 0.0)
        if inc <= 0:
            return price
        return round(math.floor(price / inc + 1e-9) * inc, 12)

    def on_trade(self, t: Trade) -> None:
        cols = self._data.get(t.key)
        if cols is None:
            if len(self._data) >= self.max_instruments:
                self._data.popitem(last=False)
            cols = OrderedDict()
            self._data[t.key] = cols
        start = bucket(t.ts, self.timeframe_sec)
        col = cols.get(start)
        if col is None:
            col = ClusterColumn(start_ts=start, end_ts=start + self.timeframe_sec)
            cols[start] = col
            while len(cols) > self.keep_columns:
                cols.popitem(last=False)
        col.add(self._level(t.key, t.price), t)

    @staticmethod
    def _iso(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def snapshot(self, key: str, price_step: float | None, zoom: int = 1, columns: int = 1) -> list[dict[str, object]]:
        cols = self._data.get(key)
        if not cols:
            return []
        base_inc = self._increment.get(key, 0.0) or price_step or 0.0
        step = price_step if price_step and price_step > 0 else base_inc * max(1, zoom)
        out: list[dict[str, object]] = []
        for col in list(cols.values())[-columns:]:
            grouped: dict[float, list[float]] = {}
            for price, (ask, bid, cnt) in col.levels.items():
                lvl = round(math.floor(price / step + 1e-9) * step, 12) if step > 0 else price
                g = grouped.setdefault(lvl, [0.0, 0.0, 0.0])
                g[0] += ask
                g[1] += bid
                g[2] += cnt
            items = [
                {"Price": p, "AskSize": v[0], "BidSize": v[1], "TradeCount": int(v[2])}
                for p, v in sorted(grouped.items())
            ]
            out.append({
                "StartTime": self._iso(col.start_ts),
                "EndTime": self._iso(col.end_ts),
                "PriceIncrement": step,
                "AsksSum": col.asks_sum,
                "BidsSum": col.bids_sum,
                "Items": items,
                "Aggregated": step != base_inc,
                "PriceAnchor": col.anchor,
            })
        return out

    def increment(self, key: str) -> float:
        return self._increment.get(key, 0.0)
