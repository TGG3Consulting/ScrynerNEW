"""Exchange collectors: catalog, trades, order books, open interest, 24h tickers."""

from __future__ import annotations

from collections.abc import Callable

from scryner.core.config import ExchangeConfig

from .base import Collector, CollectorSink

_REGISTRY: dict[str, Callable[[ExchangeConfig, CollectorSink], Collector]] = {}


def register(name: str) -> Callable[[type[Collector]], type[Collector]]:
    def deco(cls: type[Collector]) -> type[Collector]:
        _REGISTRY[name] = cls
        return cls

    return deco


def create_collector(cfg: ExchangeConfig, sink: CollectorSink) -> Collector:
    import importlib

    # Import lazily so that optional exchange modules do not slow down startup.
    for mod in ("apex", "aster", "binance", "bitget", "bybit", "coinbase", "gateio", "htx", "hyperliquid",
                "kraken", "kucoin", "lighter", "mexc", "okx", "upbit"):
        importlib.import_module(f"{__name__}.{mod}")

    factory = _REGISTRY.get(cfg.source_route_id) or _REGISTRY.get(cfg.exchange)
    if factory is None:
        raise ValueError(f"no collector registered for {cfg.source_route_id}")
    return factory(cfg, sink)


__all__ = ["Collector", "CollectorSink", "create_collector", "register"]
