from __future__ import annotations

from scryner.core.models import Trade
from scryner.metrics.clusters import ClusterStore
from scryner.metrics.coi import CoiAggregator

T0 = 1_792_488_000


def test_cluster_levels_and_zoom() -> None:
    cs = ClusterStore(timeframe_sec=3600, keep_columns=2)
    key = "kraken:PF_TAOUSD:futures"
    cs.set_increment(key, 0.01)
    cs.on_trade(Trade(key, T0 + 10, 269.861, 10, 2698.6, "buy"))
    cs.on_trade(Trade(key, T0 + 11, 269.869, 5, 1349.3, "sell"))
    cs.on_trade(Trade(key, T0 + 12, 270.12, 1, 270.1, "buy"))
    cols = cs.snapshot(key, price_step=None, zoom=1)
    assert len(cols) == 1
    items = cols[0]["Items"]
    assert [it["Price"] for it in items] == [269.86, 270.12]
    assert items[0]["AskSize"] == 10 and items[0]["BidSize"] == 5 and items[0]["TradeCount"] == 2
    assert cols[0]["AsksSum"] == 11 and cols[0]["BidsSum"] == 5 and cols[0]["Aggregated"] is False
    cols = cs.snapshot(key, price_step=0.5)
    assert [it["Price"] for it in cols[0]["Items"]] == [269.5, 270.0]
    assert cols[0]["Aggregated"] is True
    # next hour opens a new column; only 2 kept
    cs.on_trade(Trade(key, T0 + 3601, 271, 1, 271, "buy"))
    cs.on_trade(Trade(key, T0 + 7201, 272, 1, 272, "buy"))
    assert len(cs.snapshot(key, None, columns=5)) == 2


def test_coi_deltas_and_counts() -> None:
    c = CoiAggregator(sample_sec=1)
    base = "crypto::BTC"
    for t in range(0, 4000, 10):
        c.update(base, "binance:BTCUSDT:futures", 1_000_000 + t * 10, T0 + t)
        if t >= 2000:
            c.update(base, "bybit:BTCUSDT:futures", 500_000 + (t - 2000) * 5, T0 + t)
    snap = c.snapshot(T0 + 3990)
    deltas, counts, total, _ts = snap[base]
    assert total == 2
    assert counts[0] == 2 and counts[3] == 1  # bybit has no 1h-old sample
    assert deltas[0] == (60 * 10 + 60 * 5)  # 1m: binance +600, bybit +300
    assert deltas[3] == 3600 * 10  # 1h: binance only
    assert counts[4] == 0  # no 24h history
