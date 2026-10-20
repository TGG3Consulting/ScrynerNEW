from __future__ import annotations

from scryner.core.config import BotConfig, DensityConfig
from scryner.core.models import BookUpdate, Trade
from scryner.detectors.bots import BotDetector, bot_text
from scryner.detectors.density import DensityDetector

T0 = 1_792_488_000.0
KEY = "bitget:SQQQUSDT:futures"


def test_density_wall_detected_and_impact() -> None:
    out: list[dict] = []
    cfg = DensityConfig(min_size_usd_default=100_000, min_lifetime_sec=5, neighbour_levels=10, dominance_ratio=5, cooldown_sec=60, impact_windows_sec=[10, 20])
    det = DensityDetector(cfg, "bitget", out.append)
    bids = [(37.70 - i * 0.01, 100.0) for i in range(10)]
    bids[3] = (37.67, 6000.0)  # 226k USD wall
    asks = [(37.80 + i * 0.01, 100.0) for i in range(10)]
    det.on_book(BookUpdate(KEY, T0, bids, asks, True))
    det.on_trade(Trade(KEY, T0, 37.75, 1, 37.75, "buy"))
    det.on_clock(T0 + 1)
    assert not [e for e in out if e["event"] == "density.message"]
    det.on_clock(T0 + 6)
    msgs = [e for e in out if e["event"] == "density.message"]
    assert len(msgs) == 1
    m = msgs[0]
    assert m["side"] == "bid" and m["price"] == 37.67 and 220_000 < m["size_usd"] < 230_000
    assert m["metric_line"].startswith("🟩 226") and "@ 37.67" in m["metric_line"]
    # price drops below the wall → weak impact on a bid wall
    det.on_trade(Trade(KEY, T0 + 15, 37.60, 1, 37.6, "sell"))
    det.on_clock(T0 + 17)
    imp = [e for e in out if e["event"] == "density.impact"]
    assert imp and imp[0]["window_sec"] == 10 and imp[0]["label"] == "weak"
    # wall disappears → removed
    det.on_book(BookUpdate(KEY, T0 + 18, [(37.67, 0.0)], [], False))
    det.on_clock(T0 + 19)
    det.on_clock(T0 + 22)
    assert any(e["event"] == "density.removed" for e in out)


def test_bot_p1_strict_timer() -> None:
    out: list[dict] = []
    det = BotDetector(BotConfig(min_series=5, min_tick_volume_usd=10), out.append)
    key = "bybit:DRIFTUSDT:spot"
    for i in range(8):
        det.on_trade(Trade(key, T0 + i * 5.0, 0.5 + (i % 2) * 0.0005, 398, 199.0, "sell"))
    det.on_clock(T0 + 36)
    p1 = [e for e in out if e["pattern"] == "P1"]
    assert len(p1) == 1
    e = p1[0]
    assert e["side"] == "sell" and abs(e["avg_interval_sec"] - 5.0) < 0.01 and e["rating"] >= 3
    e.update({"source_title": "Bybit Spot", "display_pair": "DRIFTUSDT"})
    txt = bot_text(e)
    assert txt.startswith("P1 🟥 [BYBIT SPOT]") and "DRIFTUSDT R" in txt and "🟨 ~5.0s" in txt and "$199 @" in txt


def test_bot_c1_micro_pressure() -> None:
    out: list[dict] = []
    det = BotDetector(BotConfig(min_series=5, c1_min_trades=8, min_tick_volume_usd=10, p1_size_tolerance=0.01), out.append)
    key = "kucoin:JCTUSDTM:futures"
    price = 1.0
    for i in range(12):
        price -= 0.001
        det.on_trade(Trade(key, T0 + i * 2.3 + (i % 3) * 0.7, price, 300 + i * 37, 300.0 + i * 37, "sell"))
    det.on_clock(T0 + 40)
    c1 = [e for e in out if e["pattern"] == "C1"]
    assert len(c1) == 1 and c1[0]["side"] == "sell" and c1[0]["pressure_pct"] < 0 and c1[0]["side_volume_share"] == 1.0
