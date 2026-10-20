"""Domain models for the hot path.

Plain ``dataclass(slots=True)`` objects: they are created thousands of times per second by
collectors, so no pydantic validation here. API-facing schemas live in ``scryner.api``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Side = Literal["buy", "sell"]
MarketType = Literal["futures", "spot"]
AssetClass = Literal["crypto", "stock"]


def make_key(exchange: str, pair: str, market_type: str) -> str:
    return f"{exchange}:{pair}:{market_type}"


def split_key(key: str) -> tuple[str, str, str]:
    exchange, pair, market_type = key.split(":", 2)
    return exchange, pair, market_type


@dataclass(slots=True)
class Instrument:
    key: str
    exchange: str
    pair: str
    market_type: MarketType
    source_route_id: str
    source_title: str
    native_symbol: str
    base_asset: str
    quote_currency: str
    volume_currency: str
    contract_multiplier: float = 1.0
    tick_size: float = 0.0
    step_size: float = 0.0
    display_pair: str = ""
    display_symbol: str | None = None
    display_name: str | None = None
    asset_class: AssetClass = "crypto"
    stock_scope: bool = False
    stock_symbol: str | None = None
    coi_base: str = ""
    coi_inverse: bool = False
    chain_name: str | None = None
    contract_address: str | None = None
    base_asset_id: str | None = None
    is_active: bool = True
    listed_at: float | None = None

    def __post_init__(self) -> None:
        if not self.display_pair:
            self.display_pair = self.pair
        if not self.coi_base:
            self.coi_base = f"{self.asset_class}::{self.base_asset}"


@dataclass(slots=True)
class Trade:
    key: str
    ts: float
    price: float
    qty: float
    quote: float
    side: Side
    trade_id: str = ""


@dataclass(slots=True)
class BookLevel:
    price: float
    size: float


@dataclass(slots=True)
class BookUpdate:
    key: str
    ts: float
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    is_snapshot: bool
    seq: int = 0
    prev_seq: int = 0


@dataclass(slots=True)
class OIUpdate:
    key: str
    ts: float
    oi: float
    oi_value: float | None = None


@dataclass(slots=True)
class TickerUpdate:
    key: str
    ts: float
    volume_24h: float | None = None
    price_change_24h_pct: float | None = None
    last_price: float | None = None
    fair_price: float | None = None
    trades_24h: int | None = None
    high_24h: float | None = None
    low_24h: float | None = None


@dataclass(slots=True)
class Bar:
    ts: int
    o: float
    h: float
    l: float
    c: float
    buy_quote: float = 0.0
    sell_quote: float = 0.0
    total_quote: float = 0.0
    trades_count: int = 0
    buy_trades: int = 0
    sell_trades: int = 0
    oi_open: float | None = None
    oi_high: float | None = None
    oi_low: float | None = None
    oi_close: float | None = None
    oi_value: float | None = None
    had_trade: bool = False
    max_trade_quote: float = 0.0
    max_trade_side: Side | None = None

    def apply_trade(self, t: Trade) -> None:
        if not self.had_trade:
            self.o = self.h = self.l = self.c = t.price
            self.had_trade = True
        else:
            if t.price > self.h:
                self.h = t.price
            if t.price < self.l:
                self.l = t.price
            self.c = t.price
        self.total_quote += t.quote
        self.trades_count += 1
        if t.side == "buy":
            self.buy_quote += t.quote
            self.buy_trades += 1
        else:
            self.sell_quote += t.quote
            self.sell_trades += 1
        if t.quote > self.max_trade_quote:
            self.max_trade_quote = t.quote
            self.max_trade_side = t.side

    def apply_oi(self, oi: float, oi_value: float | None) -> None:
        if self.oi_open is None:
            self.oi_open = self.oi_high = self.oi_low = oi
        else:
            if self.oi_high is None or oi > self.oi_high:
                self.oi_high = oi
            if self.oi_low is None or oi < self.oi_low:
                self.oi_low = oi
        self.oi_close = oi
        if oi_value is not None:
            self.oi_value = oi_value

    def to_1m_dict(self) -> dict[str, float | int | None]:
        return {
            "ts": self.ts, "o": self.o, "h": self.h, "l": self.l, "c": self.c,
            "buy_quote": round(self.buy_quote, 6), "sell_quote": round(self.sell_quote, 6),
            "total_quote": round(self.total_quote, 6), "trades_count": self.trades_count,
            "oi_close": self.oi_close, "oi_open": self.oi_open, "oi_high": self.oi_high, "oi_low": self.oi_low,
        }

    def to_1s_dict(self) -> dict[str, float | int | None]:
        return {
            "ts": self.ts, "o": self.o, "h": self.h, "l": self.l, "c": self.c,
            "total_quote": round(self.total_quote, 6), "buy_quote": round(self.buy_quote, 6),
            "sell_quote": round(self.sell_quote, 6), "trades_count": self.trades_count,
            "oi_close": self.oi_close, "oi_value": self.oi_value, "had_trade": int(self.had_trade),
        }


@dataclass(slots=True)
class WindowMetrics:
    """One rolling window of an instrument (1m / 5m / 15m / 1h / 24h)."""

    range_pct: float | None = None
    natr: float | None = None
    volume: float | None = None
    oi_change_pct: float | None = None
    dvol_pct: float | None = None
    dtpl_pct: float | None = None
    tpm: float | None = None

    def packed(self) -> list[float | None]:
        return [self.range_pct, self.natr, self.volume, self.oi_change_pct, self.dvol_pct, self.dtpl_pct, self.tpm]


@dataclass(slots=True)
class InstrumentMetrics:
    """Full metric set of one instrument, mirrors the record of ``/ws/all/v1``."""

    key: str
    ts: float
    last_price: float | None = None
    last_price_ts: float | None = None
    fair_price: float | None = None
    buy_sum: float = 0.0
    sell_sum: float = 0.0
    buy_sum_5m: float = 0.0
    sell_sum_5m: float = 0.0
    buy_volume_1s: float = 0.0
    sell_volume_1s: float = 0.0
    tpm: int = 0
    big_trade_side: Side | None = None
    big_trade_volume: float = 0.0
    price_change_pct: float = 0.0
    price_change_24h_pct: float | None = None
    range5_pct: float | None = None
    natr5: float | None = None
    spike_ratio: float | None = None
    dvol5_pct: float | None = None
    dtpl5_pct: float | None = None
    oi_percentage: float | None = None
    oi2_percentage: float | None = None
    oi_value: float | None = None
    oi: float | None = None
    volume_24h: float | None = None
    trades_24h: int | None = None
    tick_threshold_base: float = 0.0
    tick_threshold_multiplier: float = 1.0
    tick_volume_updated_ts: float | None = None
    windows: list[WindowMetrics] = field(default_factory=list)
    day: WindowMetrics = field(default_factory=WindowMetrics)
    activity: list[int | None] = field(default_factory=list)
    coi_oi: float | None = None
    coi_oi_ts: float | None = None

    @property
    def tick_threshold(self) -> float:
        return self.tick_threshold_base * self.tick_threshold_multiplier


@dataclass(slots=True)
class TickEvent:
    key: str
    ts: float
    buy: float
    sell: float
    price: float
    price_open: float
    price_high: float
    price_low: float
    price_ts: float
    window_sec: float
    tick_threshold: float
    tick_threshold_base: float
    tick_threshold_multiplier: float
    volume_24h: float | None

    @property
    def price_move_pct(self) -> float:
        return (self.price - self.price_open) / self.price_open * 100.0 if self.price_open else 0.0

    @property
    def price_range_pct(self) -> float:
        return (self.price_high - self.price_low) / self.price_low * 100.0 if self.price_low else 0.0


@dataclass(slots=True)
class DensityEvent:
    id: str
    key: str
    captured_at: float
    side: Literal["bid", "ask"]
    price: float
    size_usd: float
    distance_pct: float
    is_iceberg: bool
    impact: dict[int, float | None] = field(default_factory=dict)
    removed_at: float | None = None


@dataclass(slots=True)
class BotTrade:
    ts: float
    quote: float
    price_move_pct: float
    side: Side


@dataclass(slots=True)
class BotEvent:
    id: str
    key: str
    captured_at: float
    pattern: Literal["P1", "C1", "S1"]
    side: Side
    rating: int
    avg_interval_sec: float
    trades: list[BotTrade]
    pressure_pct: float | None = None
    pressure_window_sec: int | None = None
    side_volume_share: float | None = None


@dataclass(slots=True)
class TwapRecord:
    id: str
    hash: str
    time_ms: int
    expires_ms: int
    duration_min: int
    user: str
    block: int
    market_id: int
    market: Literal["spot", "perp"]
    side: Literal["BUY", "SELL"]
    token: str
    pair: str
    display_symbol: str
    display_pair: str
    chart_key: str
    amount: float
    reduce_only: bool
    randomize: bool
    key: str
    market_type: MarketType
    source_route_id: str
