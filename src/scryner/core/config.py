"""Application configuration.

Loaded from a YAML file (path from ``SCRYNER_CONFIG`` or ``config/scryner.yaml``) and
overridable by environment variables with the ``SCRYNER_`` prefix (nested keys are
separated by ``__``, e.g. ``SCRYNER_BUS__REDIS_URL``).  Every tunable lives here: the
code base contains no hard-coded thresholds.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MarketType = Literal["futures", "spot"]


class AppConfig(BaseModel):
    env: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True
    instance_id: str = "scryner-1"


class BusConfig(BaseModel):
    backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"
    stream_maxlen: int = 100_000
    consumer_group: str = "scryner"


class RetentionConfig(BaseModel):
    bars_1m_hours: int = 216
    bars_1s_hours: int = 72
    bars_1s_memory_minutes: int = 30
    clusters_days: int = 30
    events_days: int = 7


class StorageConfig(BaseModel):
    backend: Literal["sqlite", "timescale"] = "sqlite"
    sqlite_path: str = "data/scryner.db"
    timescale_dsn: str = "postgresql://scryner:scryner@localhost:5432/scryner"
    retention: RetentionConfig = RetentionConfig()


class ExchangeConfig(BaseModel):
    """Per-source settings. ``source_route_id`` is ``<exchange>_<market>``."""

    enabled: bool = True
    exchange: str
    market_type: MarketType
    title: str
    port: int
    ws_url: str = ""
    rest_url: str = ""
    streams_per_connection: int = 100
    oi_poll_sec: float = 15.0
    ticker_poll_sec: float = 60.0
    catalog_refresh_sec: float = 600.0
    depth_levels: int = 50
    depth_enabled: bool = True
    quote_whitelist: list[str] = Field(default_factory=lambda: ["USDT", "USDC", "USD"])
    rest_rate_limit_per_sec: float = 5.0
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def source_route_id(self) -> str:
        return f"{self.exchange}_{self.market_type}"


class MetricsConfig(BaseModel):
    windows_sec: list[int] = Field(default_factory=lambda: [60, 300, 900, 3600])
    day_sec: int = 86_400
    natr_period: int = 14
    natr_bar_sec: int = 300
    reset_interval_min: int = 5
    oi2_reset_interval_min: int = 1440
    spike_lookback_sec: int = 3600
    recompute_sec: float = 1.0
    publish_sec: float = 4.0
    activity_lookback_windows: int = 60
    backfill_minutes: int = 180  # 1m klines fetched at startup so NATR/spike are available immediately


class TickConfig(BaseModel):
    """tick_threshold_base = clamp(coef · volume_24h ** power, min, max).

    Coefficients reverse-engineered exactly from the reference service: futures 3/35,
    spot 1.8/35 (= 0.6 × futures), Aster 1/35; power 0.56; clamp [100, 25000], HTX min 50.
    """

    window_sec: float = 1.0
    threshold_power: float = 0.56
    threshold_coef_futures: float = 0.0857142857
    threshold_coef_spot: float = 0.0514285714
    threshold_coef_by_exchange: dict[str, float] = Field(default_factory=lambda: {"aster": 0.0285714286})
    threshold_min: float = 100.0
    threshold_max: float = 25_000.0
    threshold_min_by_exchange: dict[str, float] = Field(default_factory=lambda: {"htx": 50.0})
    threshold_refresh_sec: float = 60.0


class DensityConfig(BaseModel):
    # Calibrated to the reference density feed: walls from ~$40k, impact "good" only on a
    # notable price reaction (observed +0.2% moves were labelled "weak").
    min_size_usd_default: float = 40_000.0
    min_size_usd_by_exchange: dict[str, float] = Field(default_factory=dict)
    neighbour_levels: int = 10
    dominance_ratio: float = 5.0
    min_lifetime_sec: float = 20.0
    max_distance_pct: float = 3.0
    iceberg_refill_ratio: float = 0.8
    impact_windows_sec: list[int] = Field(default_factory=lambda: [300, 900])
    impact_good_pct: float = 0.3
    cooldown_sec: float = 600.0
    history_size: int = 80


class BotConfig(BaseModel):
    # Calibrated to the reference service's live bot feed (observed P1/C1 output):
    # series as short as 3, intervals up to ~60s, ~30% size spread, loose timing.
    min_series: int = 3
    max_gap_sec: float = 60.0
    p1_interval_tolerance: float = 0.5
    p1_size_tolerance: float = 0.3
    c1_window_sec: float = 300.0
    c1_min_trades: int = 8
    c1_min_side_share: float = 0.8
    c1_require_direction: bool = False  # reference flags C1 even when price moves against the dominant side
    s1_min_pairs: int = 4
    s1_size_tolerance: float = 0.1
    min_tick_volume_usd: float = 50.0
    min_tick_move_pct: float = 0.0
    history_size: int = 80


class TwapConfig(BaseModel):
    info_url: str = "https://api.hyperliquid.xyz/info"
    explorer_ws_url: str = "wss://rpc.hyperliquid.xyz/ws"
    hypurrscan_url: str = "https://api.hypurrscan.io"
    bootstrap_from_hypurrscan: bool = True
    poll_sec: float = 2.0
    banned_tokens: list[str] = Field(default_factory=lambda: ["bnb", "btc", "eth", "paxg"])
    banned_prefixes: list[str] = Field(default_factory=lambda: ["cash:", "vntl:", "xyz:"])
    min_usd_per_minute_default: float = 0.0
    min_usd_per_minute_by_token: dict[str, float] = Field(default_factory=dict)
    min_usd_per_30s_by_token_side: dict[str, dict[str, float]] = Field(default_factory=dict)


class ChartAlertsConfig(BaseModel):
    max_active: int = 30
    max_active_per_key: int = 5


class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    client_release_poll_ms: int = 15_000
    client_version: str = "dev"
    top_limit_default: int = 50
    top_limit_max: int = 200
    alerts_limit_max: int = 20_000
    history_max_items: int = 64
    history_1m_limit: int = 360
    history_1s_limit: int = 1800
    ws_ping_sec: float = 25.0
    patch_interval_sec: float = 4.0
    cookie_name: str = "scryner_session"
    session_ttl_days: int = 30
    login_token_ttl_sec: int = 600
    chart_alerts: ChartAlertsConfig = ChartAlertsConfig()
    trial_days: int = 3
    admin_telegram_ids: list[int] = Field(default_factory=list)
    dev_auto_login: bool = False
    dev_auto_login_telegram_id: int = 1
    public_url: str = "http://localhost:8080"
    max_saved_configs: int = 20
    candidates_limit_max: int = 96
    web_dir: str = "web/dist"


class TelegramConfig(BaseModel):
    bot_token: str = ""
    bot_username: str = ""
    bot_poll_timeout_sec: int = 25
    api_id: int = 0
    api_hash: str = ""
    session_name: str = "scryner_reader"
    news_channels: list[str] = Field(default_factory=lambda: ["markettwits"])
    onchain_channels: list[str] = Field(default_factory=lambda: ["OnChainIntrigue", "lookonchain"])
    listings_channels: list[str] = Field(default_factory=list)
    history_size: int = 80


class FcmConfig(BaseModel):
    project_id: str = ""
    credentials_path: str = ""
    pairing_code_ttl_sec: int = 180


class StocksConfig(BaseModel):
    reference_source: str = "tradingview"
    tradingview_ws_url: str = "wss://data.tradingview.com/socket.io/websocket"
    poll_sec: float = 5.0


class ScrynerConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCRYNER_", env_nested_delimiter="__", extra="ignore")

    app: AppConfig = AppConfig()
    bus: BusConfig = BusConfig()
    storage: StorageConfig = StorageConfig()
    exchanges: dict[str, ExchangeConfig] = Field(default_factory=dict)
    metrics: MetricsConfig = MetricsConfig()
    ticks: TickConfig = TickConfig()
    density: DensityConfig = DensityConfig()
    bots: BotConfig = BotConfig()
    twap: TwapConfig = TwapConfig()
    api: ApiConfig = ApiConfig()
    telegram: TelegramConfig = TelegramConfig()
    fcm: FcmConfig = FcmConfig()
    stocks: StocksConfig = StocksConfig()

    @model_validator(mode="after")
    def _check_ports(self) -> ScrynerConfig:
        seen: dict[int, str] = {}
        for name, ex in self.exchanges.items():
            if ex.port in seen:
                raise ValueError(f"exchange port {ex.port} used by both {seen[ex.port]} and {name}")
            seen[ex.port] = name
        return self

    def enabled_sources(self) -> dict[str, ExchangeConfig]:
        return {k: v for k, v in self.exchanges.items() if v.enabled}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def default_config_path() -> Path:
    env = os.environ.get("SCRYNER_CONFIG")
    if env:
        return Path(env)
    for candidate in ("config/scryner.yaml", "config/scryner.example.yaml"):
        p = Path(candidate)
        if p.exists():
            return p
    return Path("config/scryner.yaml")


def load_config(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> ScrynerConfig:
    """Load YAML config, apply dict overrides, then environment variables."""
    p = Path(path) if path else default_config_path()
    data: dict[str, Any] = {}
    if p.exists():
        with p.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"config root must be a mapping: {p}")
        data = loaded
    if overrides:
        data = _deep_merge(data, overrides)
    return ScrynerConfig(**data)
