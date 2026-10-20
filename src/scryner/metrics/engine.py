"""Metrics engine: turns trades / OI / ticker updates into bars, rolling metrics, tick events.

One :class:`MetricsEngine` serves one source (``exchange_market``) and keeps a
:class:`InstrumentState` per instrument. All formulas (see
``docs/analysis/GODSEYE_REALTIME_PROTOCOL.md``):

* ``buy_sum`` / ``sell_sum``: quote volume since the last *reset boundary*
  (``reset_interval_min``, default 5 min, aligned to wall clock).
* ``buy_sum_5m`` / ``sell_sum_5m``: rolling 5 minutes (closed 1m bars + current 1m bar).
* ``tpm``: trades in the last 60 s (closed 1s bars + current second).
* ``oi_percentage``: OI change since the reset boundary; ``oi2_percentage``: since the
  daily boundary (``oi2_reset_interval_min``).
* ``price_change_pct``: since the reset boundary; ``price_change_24h_pct``: from the ticker
  feed, or from our own 1m bars once 24 h of history exists.
* ``range5_pct``: (high − low) / low over the last 5 minutes.
* ``natr5``: ATR(14) over 5-minute bars / last close × 100.
* ``spike_ratio``: rolling 5m volume / mean volume of the completed 5-minute buckets in the
  previous hour.
* ``dvol5_pct``: rolling 5m volume / 24h volume × 100; ``dtpl5_pct``: 5m trades / 24h trades.
* ``tick_threshold_base = clamp(coef · volume_24h^0.56, 100, 25000)`` with coef 3/35 for
  futures, 1.8/35 for spot, 1/35 for Aster (exact fit of the reference); a tick event is
  emitted when ``buy + sell`` of a closed second ≥ threshold × multiplier.
* window metrics for 1m/5m/15m/1h: ``[range, natr, volume, oi_change, dvol, dtpl, tpm]``
  packed like the original ``graph_metrics`` (only the slots the original fills).
* ``graph_activity``: for each window metric, how many of the last ``activity_lookback``
  recompute ticks the metric exceeded its own running mean (our definition; the original's
  is undocumented).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from scryner.core.config import MetricsConfig, TickConfig
from scryner.core.models import (
    Bar,
    Instrument,
    InstrumentMetrics,
    OIUpdate,
    TickerUpdate,
    TickEvent,
    Trade,
    WindowMetrics,
)
from scryner.core.timeutil import bucket

from . import bars as B
from .bars import RingBars

TickSink = Callable[[TickEvent], None]
BarSink = Callable[[str, int, Bar], None]

WINDOW_SLOTS = 7
ACTIVITY_SLOTS = 6  # [range, -, volume, -, -, tpm] + oi/dvol/dtpl per the original's 24-slot layout


@dataclass(slots=True)
class ResetState:
    boundary: int = 0
    buy: float = 0.0
    sell: float = 0.0
    price_open: float | None = None
    oi_open: float | None = None
    big_trade: float = 0.0
    big_side: str | None = None


@dataclass(slots=True)
class InstrumentState:
    inst: Instrument
    ring_1s: RingBars
    ring_1m: RingBars
    cur_1s: Bar | None = None
    cur_1m: Bar | None = None
    reset: ResetState = field(default_factory=ResetState)
    day_reset: ResetState = field(default_factory=ResetState)
    last_price: float | None = None
    last_price_ts: float | None = None
    fair_price: float | None = None
    oi: float | None = None
    oi_value: float | None = None
    oi_ts: float | None = None
    ticker_volume_24h: float | None = None
    ticker_trades_24h: int | None = None
    ticker_change_24h: float | None = None
    ticker_ts: float | None = None
    tick_threshold_base: float = 0.0
    tick_threshold_ts: float = 0.0
    tick_multiplier: float = 1.0
    activity_hist: list[deque[float]] = field(default_factory=list)
    activity: list[int | None] = field(default_factory=list)
    dirty: bool = False
    metrics: InstrumentMetrics | None = None


class MetricsEngine:
    def __init__(
        self,
        cfg: MetricsConfig,
        tick_cfg: TickConfig,
        bars_1s_capacity: int,
        bars_1m_capacity: int,
        on_tick: TickSink | None = None,
        on_bar_close: BarSink | None = None,
    ) -> None:
        self.cfg = cfg
        self.tick_cfg = tick_cfg
        self._cap_1s = bars_1s_capacity
        self._cap_1m = bars_1m_capacity
        self._on_tick = on_tick
        self._on_bar_close = on_bar_close
        self.states: dict[str, InstrumentState] = {}
        self.reset_sec = cfg.reset_interval_min * 60
        self.day_sec = cfg.oi2_reset_interval_min * 60
        self.windows = list(cfg.windows_sec)

    # ------------------------------------------------------------------ registry
    def add_instrument(self, inst: Instrument) -> InstrumentState:
        st = self.states.get(inst.key)
        if st is None:
            st = InstrumentState(
                inst=inst,
                ring_1s=RingBars(1, self._cap_1s),
                ring_1m=RingBars(60, self._cap_1m),
                activity_hist=[deque(maxlen=self.cfg.activity_lookback_windows) for _ in range(len(self.windows) * ACTIVITY_SLOTS)],
                activity=[None] * (len(self.windows) * ACTIVITY_SLOTS),
            )
            self.states[inst.key] = st
        else:
            st.inst = inst
        return st

    def remove_instrument(self, key: str) -> None:
        self.states.pop(key, None)

    def get(self, key: str) -> InstrumentState | None:
        return self.states.get(key)

    def seed_bars_1m(self, key: str, bars: list[Bar]) -> None:
        st = self.states.get(key)
        if st is None:
            return
        for b in bars:
            st.ring_1m.append(b)

    # ------------------------------------------------------------------ ingestion
    def on_trade(self, t: Trade) -> None:
        st = self.states.get(t.key)
        if st is None:
            return
        self._roll(st, t.ts)
        if st.cur_1s is None:
            st.cur_1s = self._new_bar(st, bucket(t.ts, 1), st.oi, st.oi_value)
        if st.cur_1m is None:
            st.cur_1m = self._new_bar(st, bucket(t.ts, 60), st.oi, st.oi_value)
        st.cur_1s.apply_trade(t)
        st.cur_1m.apply_trade(t)
        r = st.reset
        if r.price_open is None:
            r.price_open = t.price
        if t.side == "buy":
            r.buy += t.quote
        else:
            r.sell += t.quote
        if t.quote > r.big_trade:
            r.big_trade = t.quote
            r.big_side = t.side
        st.last_price = t.price
        st.last_price_ts = t.ts
        st.dirty = True

    def on_oi(self, u: OIUpdate) -> None:
        st = self.states.get(u.key)
        if st is None:
            return
        self._roll(st, u.ts)
        st.oi = u.oi
        st.oi_ts = u.ts
        if u.oi_value is not None:
            st.oi_value = u.oi_value
        elif st.last_price is not None:
            st.oi_value = u.oi * st.last_price * st.inst.contract_multiplier
        if st.reset.oi_open is None:
            st.reset.oi_open = u.oi
        if st.day_reset.oi_open is None:
            st.day_reset.oi_open = u.oi
        if st.cur_1s is None:
            st.cur_1s = self._new_bar(st, bucket(u.ts, 1), None, None)
        if st.cur_1m is None:
            st.cur_1m = self._new_bar(st, bucket(u.ts, 60), None, None)
        st.cur_1s.apply_oi(u.oi, st.oi_value)
        st.cur_1m.apply_oi(u.oi, st.oi_value)
        st.dirty = True

    def on_ticker(self, u: TickerUpdate) -> None:
        st = self.states.get(u.key)
        if st is None:
            return
        if u.volume_24h is not None:
            st.ticker_volume_24h = u.volume_24h
        if u.trades_24h is not None:
            st.ticker_trades_24h = u.trades_24h
        if u.price_change_24h_pct is not None:
            st.ticker_change_24h = u.price_change_24h_pct
        if u.fair_price is not None:
            st.fair_price = u.fair_price
        if u.last_price is not None and st.last_price is None:
            st.last_price = u.last_price
            st.last_price_ts = u.ts
        st.ticker_ts = u.ts
        st.dirty = True

    def set_tick_multiplier(self, key: str, multiplier: float) -> None:
        st = self.states.get(key)
        if st is not None:
            st.tick_multiplier = max(0.0, multiplier)

    # ------------------------------------------------------------------ clock
    def tick_clock(self, now: float) -> None:
        """Advance all instruments to ``now`` (closes bars, resets windows)."""
        for st in self.states.values():
            self._roll(st, now)

    def _new_bar(self, st: InstrumentState, ts: int, oi: float | None, oi_value: float | None) -> Bar:
        p = st.last_price if st.last_price is not None else (st.ring_1m.last_close if not math.isnan(st.ring_1m.last_close) else 0.0)
        b = Bar(ts=ts, o=p, h=p, l=p, c=p)
        if oi is not None:
            b.apply_oi(oi, oi_value)
        return b

    def _roll(self, st: InstrumentState, now: float) -> None:
        sec = bucket(now, 1)
        if st.cur_1s is not None and sec > st.cur_1s.ts:
            self._close_1s(st, st.cur_1s)
            st.cur_1s = None
        minute = bucket(now, 60)
        if st.cur_1m is not None and minute > st.cur_1m.ts:
            self._close_1m(st, st.cur_1m)
            st.cur_1m = None
        rb = bucket(now, self.reset_sec)
        if rb != st.reset.boundary:
            st.reset = ResetState(boundary=rb, price_open=st.last_price, oi_open=st.oi)
            st.dirty = True
        db = bucket(now, self.day_sec)
        if db != st.day_reset.boundary:
            st.day_reset = ResetState(boundary=db, price_open=st.last_price, oi_open=st.oi)
            st.dirty = True

    def _close_1s(self, st: InstrumentState, bar: Bar) -> None:
        st.ring_1s.append(bar)
        if self._on_tick is not None and bar.had_trade and st.tick_threshold_base > 0:
            vol = bar.buy_quote + bar.sell_quote
            threshold = st.tick_threshold_base * st.tick_multiplier
            if vol >= threshold:
                self._on_tick(TickEvent(
                    key=st.inst.key, ts=float(bar.ts), buy=bar.buy_quote, sell=bar.sell_quote,
                    price=bar.c, price_open=bar.o, price_high=bar.h, price_low=bar.l,
                    price_ts=st.last_price_ts or float(bar.ts), window_sec=1.0,
                    tick_threshold=threshold, tick_threshold_base=st.tick_threshold_base,
                    tick_threshold_multiplier=st.tick_multiplier, volume_24h=self._volume_24h(st),
                ))
        st.dirty = True

    def _close_1m(self, st: InstrumentState, bar: Bar) -> None:
        st.ring_1m.append(bar)
        if self._on_bar_close is not None:
            self._on_bar_close(st.inst.key, 60, bar)
        st.dirty = True

    # ------------------------------------------------------------------ metrics
    def _volume_24h(self, st: InstrumentState) -> float | None:
        if st.ring_1m.last_ts is not None and len(st.ring_1m) >= 1440:
            rows = st.ring_1m.last(1440)
            v = float(np.nansum(rows[:, B.TOTAL]))
            if st.cur_1m is not None:
                v += st.cur_1m.total_quote
            return v
        return st.ticker_volume_24h

    def _trades_24h(self, st: InstrumentState) -> float | None:
        if st.ring_1m.last_ts is not None and len(st.ring_1m) >= 1440:
            rows = st.ring_1m.last(1440)
            v = float(np.nansum(rows[:, B.TRADES]))
            if st.cur_1m is not None:
                v += st.cur_1m.trades_count
            return v
        return float(st.ticker_trades_24h) if st.ticker_trades_24h is not None else None

    def refresh_tick_threshold(self, st: InstrumentState, now: float) -> None:
        if now - st.tick_threshold_ts < self.tick_cfg.threshold_refresh_sec and st.tick_threshold_base > 0:
            return
        vol = self._volume_24h(st)
        if vol is None or vol <= 0:
            return
        coef = self.tick_cfg.threshold_coef_by_exchange.get(st.inst.exchange)
        if coef is None:
            coef = self.tick_cfg.threshold_coef_spot if st.inst.market_type == "spot" else self.tick_cfg.threshold_coef_futures
        base = coef * vol ** self.tick_cfg.threshold_power
        lo = self.tick_cfg.threshold_min_by_exchange.get(st.inst.exchange, self.tick_cfg.threshold_min)
        st.tick_threshold_base = float(min(self.tick_cfg.threshold_max, max(lo, base)))
        st.tick_threshold_ts = now

    def compute(self, key: str, now: float) -> InstrumentMetrics | None:
        st = self.states.get(key)
        if st is None:
            return None
        self._roll(st, now)
        self.refresh_tick_threshold(st, now)
        m = InstrumentMetrics(key=key, ts=now)
        m.last_price, m.last_price_ts, m.fair_price = st.last_price, st.last_price_ts, st.fair_price
        m.buy_sum, m.sell_sum = st.reset.buy, st.reset.sell
        m.big_trade_volume, m.big_trade_side = st.reset.big_trade, st.reset.big_side  # type: ignore[assignment]
        m.oi, m.oi_value = st.oi, st.oi_value
        m.volume_24h = self._volume_24h(st)
        t24 = self._trades_24h(st)
        m.trades_24h = int(t24) if t24 is not None else None
        m.tick_threshold_base, m.tick_threshold_multiplier = st.tick_threshold_base, st.tick_multiplier
        m.tick_volume_updated_ts = st.ticker_ts
        m.coi_oi, m.coi_oi_ts = st.oi, st.oi_ts

        if st.last_price and st.reset.price_open:
            m.price_change_pct = (st.last_price - st.reset.price_open) / st.reset.price_open * 100.0
        if st.oi is not None and st.reset.oi_open:
            m.oi_percentage = (st.oi - st.reset.oi_open) / st.reset.oi_open * 100.0
        if st.oi is not None and st.day_reset.oi_open:
            m.oi2_percentage = (st.oi - st.day_reset.oi_open) / st.day_reset.oi_open * 100.0

        # last second
        if st.cur_1s is not None and st.cur_1s.ts == bucket(now, 1):
            m.buy_volume_1s, m.sell_volume_1s = st.cur_1s.buy_quote, st.cur_1s.sell_quote
        # tpm: closed 1s bars in the last 60 s + current second
        rows_1s = st.ring_1s.since(bucket(now, 1) - 59)
        tpm = float(np.sum(rows_1s[:, B.TRADES])) if len(rows_1s) else 0.0
        if st.cur_1s is not None:
            tpm += st.cur_1s.trades_count
        m.tpm = int(tpm)

        # rolling windows from 1m bars (+ current partial minute)
        cur = st.cur_1m
        m.windows = []
        for w in self.windows:
            wm = self._window(st, w, now, cur)
            m.windows.append(wm)
        w5 = m.windows[self.windows.index(300)] if 300 in self.windows else self._window(st, 300, now, cur)
        m.range5_pct, m.natr5, m.dvol5_pct, m.dtpl5_pct = w5.range_pct, w5.natr, w5.dvol_pct, w5.dtpl_pct
        vol5 = w5.volume or 0.0
        buy5, sell5 = self._side_sums(st, 300, now, cur)
        m.buy_sum_5m, m.sell_sum_5m = buy5, sell5
        m.spike_ratio = self._spike_ratio(st, now, vol5)
        # day window
        m.day = self._day_window(st, m)
        m.price_change_24h_pct = m.day.range_pct if False else self._change_24h(st)
        # activity
        self._update_activity(st, m)
        m.activity = list(st.activity)
        st.metrics = m
        st.dirty = False
        return m

    def _window_rows(self, st: InstrumentState, window_sec: int, now: float) -> np.ndarray:
        start = bucket(now, 60) - window_sec + 60  # closed minutes fully inside the window
        return st.ring_1m.since(start)

    def _side_sums(self, st: InstrumentState, window_sec: int, now: float, cur: Bar | None) -> tuple[float, float]:
        rows = self._window_rows(st, window_sec, now)
        buy = float(np.nansum(rows[:, B.BUY])) if len(rows) else 0.0
        sell = float(np.nansum(rows[:, B.SELL])) if len(rows) else 0.0
        if cur is not None:
            buy += cur.buy_quote
            sell += cur.sell_quote
        return buy, sell

    def _window(self, st: InstrumentState, window_sec: int, now: float, cur: Bar | None) -> WindowMetrics:
        wm = WindowMetrics()
        rows = self._window_rows(st, window_sec, now)
        highs: list[float] = []
        lows: list[float] = []
        vol = 0.0
        trades = 0.0
        oi_open: float | None = None
        if len(rows):
            traded = rows[rows[:, B.HAD_TRADE] > 0]
            if len(traded):
                highs.append(float(np.max(traded[:, B.H])))
                lows.append(float(np.min(traded[:, B.L])))
            vol += float(np.nansum(rows[:, B.TOTAL]))
            trades += float(np.nansum(rows[:, B.TRADES]))
            first_oi = rows[:, B.OI_O]
            valid = first_oi[~np.isnan(first_oi)]
            if len(valid):
                oi_open = float(valid[0])
        if cur is not None:
            if cur.had_trade:
                highs.append(cur.h)
                lows.append(cur.l)
            vol += cur.total_quote
            trades += cur.trades_count
            if oi_open is None and cur.oi_open is not None:
                oi_open = cur.oi_open
        if highs and lows and min(lows) > 0:
            wm.range_pct = (max(highs) - min(lows)) / min(lows) * 100.0
        wm.volume = vol
        if window_sec == 60:
            wm.tpm = float(st.metrics.tpm) if st.metrics else None
        if st.oi is not None and oi_open:
            wm.oi_change_pct = (st.oi - oi_open) / oi_open * 100.0
        if window_sec == 300:
            v24 = self._volume_24h(st)
            t24 = self._trades_24h(st)
            wm.dvol_pct = vol / v24 * 100.0 if v24 else None
            wm.dtpl_pct = trades / t24 * 100.0 if t24 else None
            wm.natr = self._natr(st, now)
        return wm

    def _natr(self, st: InstrumentState, now: float) -> float | None:
        period = self.cfg.natr_period
        group = self.cfg.natr_bar_sec // 60
        need = (period + 1) * group
        start = bucket(now, self.cfg.natr_bar_sec) - need * 60
        rows = st.ring_1m.since(start)
        rows = rows[rows[:, B.TS] < bucket(now, self.cfg.natr_bar_sec)]  # completed 5m buckets only
        if len(rows) < need:
            return None
        agg = B.aggregate(rows, group)
        if len(agg) < period + 1:
            return None
        tr = B.true_range(agg)[-period:]
        atr = float(np.mean(tr))
        close = st.last_price or float(agg[-1, B.C])
        return atr / close * 100.0 if close else None

    def _spike_ratio(self, st: InstrumentState, now: float, vol5: float) -> float | None:
        look = self.cfg.spike_lookback_sec
        start = bucket(now, 300) - look
        rows = st.ring_1m.since(start)
        rows = rows[rows[:, B.TS] < bucket(now, 300)]
        if len(rows) < 5:
            return None
        agg = B.aggregate(rows, 5)
        if len(agg) == 0:
            return None
        mean = float(np.mean(agg[:, B.TOTAL]))
        if mean <= 0:
            return None
        return vol5 / mean

    def _day_window(self, st: InstrumentState, m: InstrumentMetrics) -> WindowMetrics:
        wm = WindowMetrics()
        wm.volume = m.volume_24h
        if len(st.ring_1m) >= 1440:
            rows = st.ring_1m.last(1440)
            traded = rows[rows[:, B.HAD_TRADE] > 0]
            if len(traded):
                hi, lo = float(np.max(traded[:, B.H])), float(np.min(traded[:, B.L]))
                if lo > 0:
                    wm.range_pct = (hi - lo) / lo * 100.0
        wm.oi_change_pct = m.oi2_percentage
        return wm

    def _change_24h(self, st: InstrumentState) -> float | None:
        if len(st.ring_1m) >= 1440 and st.last_price:
            rows = st.ring_1m.last(1440)
            o = float(rows[0, B.O])
            if o > 0:
                return (st.last_price - o) / o * 100.0
        return st.ticker_change_24h

    def _update_activity(self, st: InstrumentState, m: InstrumentMetrics) -> None:
        # slots per window: [range, volume, oi, dvol, dtpl, tpm]
        for wi, wm in enumerate(m.windows):
            values = [wm.range_pct, wm.volume, wm.oi_change_pct, wm.dvol_pct, wm.dtpl_pct, wm.tpm]
            for si, v in enumerate(values):
                idx = wi * ACTIVITY_SLOTS + si
                if v is None:
                    st.activity[idx] = None
                    continue
                hist = st.activity_hist[idx]
                hist.append(abs(v))
                if len(hist) < 2:
                    st.activity[idx] = 0
                    continue
                mean = sum(hist) / len(hist)
                st.activity[idx] = sum(1 for x in hist if x > mean)

    # ------------------------------------------------------------------ packing
    def packed_graph_metrics(self, m: InstrumentMetrics) -> list[float | None]:
        out: list[float | None] = []
        for wi, wm in enumerate(m.windows):
            w = self.windows[wi]
            p = wm.packed()
            # replicate the original's filled slots: 1m: range, volume, oi, tpm; 5m: all but tpm;
            # 15m/1h: range, volume, oi
            if w == 60:
                p = [p[0], None, p[2], p[3], None, None, float(m.tpm)]
            elif w == 300:
                p = [p[0], p[1], p[2], p[3], p[4], p[5], None]
            else:
                p = [p[0], None, p[2], p[3], None, None, None]
            out.extend(p)
        return out

    def packed_day_metrics(self, m: InstrumentMetrics) -> list[float | None]:
        return [m.day.range_pct, None, m.day.volume, m.day.oi_change_pct, None, None, None]

    def packed_activity(self, m: InstrumentMetrics) -> list[int | None]:
        return list(m.activity)
