"""Deterministic tests of the metrics engine on synthetic trade flow."""

from __future__ import annotations

import math

import numpy as np
import pytest

from scryner.core.config import MetricsConfig, TickConfig
from scryner.core.models import Bar, Instrument, OIUpdate, TickerUpdate, Trade, make_key
from scryner.metrics import bars as B
from scryner.metrics.bars import RingBars
from scryner.metrics.engine import MetricsEngine

T0 = 1_792_488_000  # aligned to 5 min and 1 h? 1792488000 % 3600 == 0


def inst(pair: str = "BTCUSDT") -> Instrument:
    return Instrument(
        key=make_key("binance", pair, "futures"), exchange="binance", pair=pair, market_type="futures",
        source_route_id="binance_futures", source_title="Binance Futures", native_symbol=pair,
        base_asset=pair.replace("USDT", ""), quote_currency="USDT", volume_currency="USDT", tick_size=0.1,
    )


def make_engine(on_tick=None) -> MetricsEngine:
    return MetricsEngine(MetricsConfig(), TickConfig(), bars_1s_capacity=1800, bars_1m_capacity=2000, on_tick=on_tick)


def test_ring_gap_fill_and_windows() -> None:
    ring = RingBars(60, 10)
    ring.append(Bar(ts=T0, o=1, h=2, l=0.5, c=1.5, total_quote=10, trades_count=3, had_trade=True))
    ring.append(Bar(ts=T0 + 180, o=1.5, h=1.6, l=1.4, c=1.55, total_quote=5, trades_count=1, had_trade=True))
    v = ring.view()
    assert [int(x) for x in v[:, B.TS]] == [T0, T0 + 60, T0 + 120, T0 + 180]
    assert v[1, B.C] == 1.5 and v[1, B.TOTAL] == 0  # gap filled flat
    assert len(ring.since(T0 + 120)) == 2
    assert ring.last(1)[0, B.C] == 1.55


def test_ring_wraps() -> None:
    ring = RingBars(60, 4)
    for i in range(10):
        ring.append(Bar(ts=T0 + i * 60, o=i, h=i, l=i, c=i, had_trade=True))
    v = ring.view()
    assert [int(x) for x in v[:, B.TS]] == [T0 + 360, T0 + 420, T0 + 480, T0 + 540]
    assert [int(x) for x in ring.last(2)[:, B.C]] == [8, 9]


def test_aggregate_and_true_range() -> None:
    ring = RingBars(60, 100)
    for i in range(10):
        ring.append(Bar(ts=T0 + i * 60, o=100 + i, h=101 + i, l=99 + i, c=100.5 + i, total_quote=1, trades_count=1, had_trade=True))
    agg = B.aggregate(ring.view(), 5)
    assert len(agg) == 2
    assert agg[0, B.O] == 100 and agg[0, B.C] == 104.5 and agg[0, B.H] == 105 and agg[0, B.L] == 99
    assert agg[0, B.TOTAL] == 5
    tr = B.true_range(agg)
    assert tr[0] == pytest.approx(6.0)


def test_reset_window_sums_and_price_change() -> None:
    eng = make_engine()
    i = inst()
    eng.add_instrument(i)
    eng.on_trade(Trade(i.key, T0 + 1, 100.0, 1.0, 100.0, "buy"))
    eng.on_trade(Trade(i.key, T0 + 2, 101.0, 2.0, 202.0, "sell"))
    m = eng.compute(i.key, T0 + 3)
    assert m is not None
    assert m.buy_sum == 100.0 and m.sell_sum == 202.0
    assert m.price_change_pct == pytest.approx(1.0)
    assert m.big_trade_volume == 202.0 and m.big_trade_side == "sell"
    assert m.tpm == 2
    # crossing the 5-minute boundary resets accumulators
    eng.on_trade(Trade(i.key, T0 + 301, 102.0, 1.0, 102.0, "buy"))
    m2 = eng.compute(i.key, T0 + 302)
    assert m2 is not None
    assert m2.buy_sum == 102.0 and m2.sell_sum == 0.0
    assert m2.price_change_pct == pytest.approx((102 - 101) / 101 * 100)
    # rolling 5m: minute T0 fell out of the window [T0+60, T0+300] (5 minute buckets incl. partial)
    assert m2.buy_sum_5m == pytest.approx(102.0) and m2.sell_sum_5m == pytest.approx(0.0)
    m3 = eng.compute(i.key, T0 + 200)
    assert m3 is not None and m3.buy_sum_5m == pytest.approx(202.0) and m3.sell_sum_5m == pytest.approx(202.0)


def test_oi_percentages() -> None:
    eng = make_engine()
    i = inst()
    eng.add_instrument(i)
    eng.on_trade(Trade(i.key, T0 + 1, 100.0, 1.0, 100.0, "buy"))
    eng.on_oi(OIUpdate(i.key, T0 + 1, 1000.0))
    eng.on_oi(OIUpdate(i.key, T0 + 100, 1050.0))
    m = eng.compute(i.key, T0 + 101)
    assert m is not None
    assert m.oi_percentage == pytest.approx(5.0)
    assert m.oi2_percentage == pytest.approx(5.0)
    assert m.oi_value == pytest.approx(1050.0 * 100.0)


def test_rolling_metrics_natr_spike_dvol() -> None:
    eng = make_engine()
    i = inst()
    eng.add_instrument(i)
    # 2 hours of 1m history: price oscillates ±1 around 100, 10 trades/min, 1000 quote/min
    bars = []
    for k in range(120):
        p = 100 + (1 if k % 2 else -1)
        bars.append(Bar(ts=T0 - 120 * 60 + k * 60, o=100, h=101, l=99, c=p, buy_quote=500, sell_quote=500,
                        total_quote=1000, trades_count=10, buy_trades=5, sell_trades=5, had_trade=True, oi_open=1000, oi_close=1000))
    eng.seed_bars_1m(i.key, bars)
    eng.on_ticker(TickerUpdate(i.key, T0, volume_24h=288_000, trades_24h=2880, price_change_24h_pct=2.5))
    # current 5 minutes: volume spike x3
    for k in range(5):
        for j in range(30):
            ts = T0 + k * 60 + j * 2
            eng.on_trade(Trade(i.key, ts, 100 + (j % 3) * 0.1, 1, 100.0, "buy" if j % 2 else "sell"))
    now = T0 + 299
    m = eng.compute(i.key, now)
    assert m is not None
    w5 = m.windows[1]
    assert w5.volume == pytest.approx(5 * 30 * 100.0)  # 15 000 in the last 5 min (4 closed + partial)
    # spike: mean of completed 5m buckets in previous hour = 5000; current rolling 5m = 15000 → 3.0
    assert m.spike_ratio == pytest.approx(3.0)
    assert m.dvol5_pct == pytest.approx(15_000 / 288_000 * 100)
    assert m.dtpl5_pct == pytest.approx(150 / 2880 * 100)
    # NATR: ATR(14) on 5m bars with h=101, l=99 (range 2) and close alternating → TR = 2 ⇒ natr ≈ 2/close*100
    assert m.natr5 == pytest.approx(2.0 / m.last_price * 100, rel=0.05)
    assert m.price_change_24h_pct == 2.5  # from ticker until 24h of own bars exist
    assert m.range5_pct == pytest.approx((100.2 - 100.0) / 100.0 * 100)
    packed = eng.packed_graph_metrics(m)
    assert len(packed) == 28 and packed[9] == pytest.approx(15_000.0) and packed[6] == float(m.tpm)
    assert packed[1] is None and packed[13] is None


def test_tick_threshold_and_tick_event() -> None:
    ticks = []
    eng = make_engine(on_tick=ticks.append)
    i = inst()
    eng.add_instrument(i)
    eng.on_ticker(TickerUpdate(i.key, T0, volume_24h=1_750_000))
    eng.on_trade(Trade(i.key, T0 + 1.2, 100.0, 1.0, 100.0, "buy"))
    eng.compute(i.key, T0 + 1.5)  # refreshes threshold
    st = eng.get(i.key)
    assert st is not None
    expected = 0.0857142857 * 1_750_000 ** 0.56
    assert st.tick_threshold_base == pytest.approx(expected)
    assert 265 < st.tick_threshold_base < 275  # reference: 1.757M futures → 269.09
    # a second with 500 quote volume exceeds ≈161 → tick event when the second closes
    eng.on_trade(Trade(i.key, T0 + 5.1, 100.0, 2.0, 200.0, "buy"))
    eng.on_trade(Trade(i.key, T0 + 5.6, 100.5, 3.0, 301.5, "sell"))
    eng.tick_clock(T0 + 6.0)
    assert len(ticks) == 1
    ev = ticks[0]
    assert ev.buy == 200.0 and ev.sell == 301.5 and ev.price_open == 100.0 and ev.price == 100.5
    assert ev.price_move_pct == pytest.approx(0.5)
    assert ev.volume_24h == 1_750_000


def test_threshold_clamps() -> None:
    eng = make_engine()
    for ex, vol, expect, mt in (("binance", 2000.0, 100.0, "futures"), ("htx", 2000.0, 50.0, "futures"),
                                ("binance", 9.8e9, 25_000.0, "futures"), ("binance", 1_750_000.0, 0.0514285714 * 1_750_000 ** 0.56, "spot"),
                                ("aster", 1_750_000.0, 100.0, "futures"),  # 89.5 → clamped to the 100 floor
                                ("aster", 1.0e8, 0.0285714286 * 1.0e8 ** 0.56, "futures")):
        i = Instrument(key=make_key(ex, f"X{vol}", mt), exchange=ex, pair=f"X{vol}", market_type=mt,
                       source_route_id=f"{ex}_futures", source_title=ex, native_symbol="X", base_asset="X",
                       quote_currency="USDT", volume_currency="USDT")
        st = eng.add_instrument(i)
        eng.on_ticker(TickerUpdate(i.key, T0, volume_24h=vol))
        eng.refresh_tick_threshold(st, T0)
        assert st.tick_threshold_base == pytest.approx(expect)


def test_volume_24h_from_own_bars_when_full_day() -> None:
    eng = make_engine()
    i = inst()
    eng.add_instrument(i)
    bars = [Bar(ts=T0 - 1440 * 60 + k * 60, o=1, h=1, l=1, c=1, total_quote=2.0, trades_count=1, had_trade=True) for k in range(1440)]
    eng.seed_bars_1m(i.key, bars)
    eng.on_ticker(TickerUpdate(i.key, T0, volume_24h=999.0))
    m = eng.compute(i.key, T0 + 1)
    assert m is not None
    assert m.volume_24h == pytest.approx(2880.0)
    assert not math.isnan(m.volume_24h)
    assert np.isfinite(m.day.range_pct or 0.0)
