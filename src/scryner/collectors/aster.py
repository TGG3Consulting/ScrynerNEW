"""Aster DEX (Binance-compatible API).

Futures: ``https://fapi.asterdex.com`` + ``wss://fstream.asterdex.com``;
spot: ``https://sapi.asterdex.com`` + ``wss://sstream.asterdex.com``.
"""

from __future__ import annotations

from . import register
from .binance import _BinanceLike


@register("aster_futures")
class AsterFutures(_BinanceLike):
    exchange_name = "aster"


@register("aster_spot")
class AsterSpot(_BinanceLike):
    exchange_name = "aster"
    spot_rest_prefix = "/api/v1"
