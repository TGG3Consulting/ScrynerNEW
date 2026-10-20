"""Build API records (the ``/ws/all/v1`` instrument object) from engine state."""

from __future__ import annotations

from typing import Any

from scryner.core.models import Instrument, InstrumentMetrics
from scryner.core.numfmt import short_number

from .engine import MetricsEngine


def _r(v: float | None, nd: int = 6) -> float | None:
    return None if v is None else round(v, nd)


def build_record(inst: Instrument, m: InstrumentMetrics, engine: MetricsEngine, source_port: int) -> dict[str, Any]:
    tv = m.volume_24h
    return {
        "exchange": inst.exchange,
        "pair": inst.pair,
        "display_symbol": inst.display_symbol,
        "display_pair": inst.display_pair,
        "display_name": inst.display_name,
        "asset_class": inst.asset_class,
        "stock_scope": inst.stock_scope,
        "chain_name": inst.chain_name,
        "contract_address": inst.contract_address,
        "base_asset_id": inst.base_asset_id,
        "market_type": inst.market_type,
        "buy_sum": _r(m.buy_sum, 2),
        "sell_sum": _r(m.sell_sum, 2),
        "tpm": m.tpm,
        "oi_percentage": _r(m.oi_percentage),
        "oi2_percentage": _r(m.oi2_percentage),
        "oi2_reset_interval": engine.cfg.oi2_reset_interval_min,
        "reset_interval": engine.cfg.reset_interval_min,
        "big_trade_side": m.big_trade_side,
        "big_trade_volume": _r(m.big_trade_volume, 2),
        "price_change_pct": _r(m.price_change_pct),
        "oi_vol": short_number(m.oi_value) if m.oi_value else "0",
        "last_price": m.last_price,
        "fair_price": m.fair_price,
        "last_price_ts": m.last_price_ts,
        "spike_ratio": _r(m.spike_ratio),
        "buy_volume_1s": _r(m.buy_volume_1s, 2),
        "sell_volume_1s": _r(m.sell_volume_1s, 2),
        "quote_currency": inst.quote_currency,
        "volume_currency": inst.volume_currency,
        "contract_multiplier": inst.contract_multiplier,
        "tick_threshold": _r(m.tick_threshold, 4),
        "tick_threshold_base": _r(m.tick_threshold_base, 4),
        "tick_threshold_multiplier": m.tick_threshold_multiplier,
        "tick_threshold_source": "local_minute_history",
        "tick_volume_24h": _r(tv, 2),
        "tick_volume_currency": inst.volume_currency,
        "tick_volume_updated_ts": m.tick_volume_updated_ts,
        "tick_threshold_error_count": 0,
        "tick_threshold_error": "",
        "tick_threshold_error_ts": None,
        "trade_contract_error_count": 0,
        "trade_contract_error": "",
        "trade_contract_error_ts": None,
        "buy_sum_5m": _r(m.buy_sum_5m, 2),
        "sell_sum_5m": _r(m.sell_sum_5m, 2),
        "dvol5_pct": _r(m.dvol5_pct),
        "dtpl5_pct": _r(m.dtpl5_pct),
        "price_change_24h_pct": _r(m.price_change_24h_pct),
        "range5_pct": _r(m.range5_pct),
        "natr5": _r(m.natr5),
        "graph_metrics": [_r(x) for x in engine.packed_graph_metrics(m)],
        "graph_activity": engine.packed_activity(m),
        "simple_24h_metrics": [_r(x) for x in engine.packed_day_metrics(m)],
        "coi_oi": m.coi_oi,
        "coi_oi_ts": m.coi_oi_ts,
        "coi_inverse": inst.coi_inverse,
        "source_route_id": inst.source_route_id,
        "source_title": inst.source_title,
        "source_port": source_port,
        "volume_24h": _r(tv, 2),
    }


ALERT_FIELDS = (
    "exchange", "pair", "display_pair", "display_symbol", "display_name", "source_route_id", "market_type",
    "quote_currency", "buy_sum_5m", "sell_sum_5m", "buy_sum", "sell_sum", "oi_percentage", "oi2_percentage", "oi_vol",
    "spike_ratio", "dvol5_pct", "natr5", "price_change_pct", "tpm", "volume_24h", "stock_scope",
)


def alert_record(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: rec.get(k) for k in ALERT_FIELDS}


def simple_metrics_array(rec: dict[str, Any]) -> list[Any]:
    """Packed ``metrics`` entry of ``/api/simple/v1/pairs``."""
    return [rec.get("volume_24h"), rec.get("price_change_24h_pct"), rec.get("dvol5_pct"), rec.get("tpm"), rec.get("natr5"),
            rec.get("graph_metrics"), rec.get("simple_24h_metrics")]


def diff_record(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    if old is None:
        return new
    return {k: v for k, v in new.items() if old.get(k) != v}
