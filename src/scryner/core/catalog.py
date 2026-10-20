"""Instrument catalog: in-memory registry with a content hash (``catalog_version``)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

import orjson

from .models import Instrument

# Tokenised stock tickers are identified by exchange suffixes/prefixes; the collectors set
# ``asset_class`` from exchange metadata when available, this list is the fallback.
STOCK_SUFFIXES = ("STOCK",)


def catalog_record(inst: Instrument) -> dict[str, object]:
    return {
        "exchange": inst.exchange,
        "pair": inst.pair,
        "market_type": inst.market_type,
        "display_symbol": inst.display_symbol or "",
        "display_pair": inst.display_pair,
        "display_name": inst.display_name or "",
        "stock_scope": inst.stock_scope,
        "quote_currency": inst.quote_currency,
        "volume_currency": inst.volume_currency,
        "contract_multiplier": inst.contract_multiplier,
        "source_route_id": inst.source_route_id,
        "source_title": inst.source_title,
        "coi_base": inst.coi_base,
    }


class Catalog:
    def __init__(self) -> None:
        self._items: dict[str, Instrument] = {}
        self._version: str = ""
        self._dirty = True

    def upsert(self, inst: Instrument) -> bool:
        prev = self._items.get(inst.key)
        if prev is not None and catalog_record(prev) == catalog_record(inst) and prev.tick_size == inst.tick_size:
            return False
        self._items[inst.key] = inst
        self._dirty = True
        return True

    def upsert_many(self, items: Iterable[Instrument]) -> int:
        return sum(1 for inst in items if self.upsert(inst))

    def deactivate_missing(self, source_route_id: str, present: set[str]) -> list[str]:
        removed = []
        for key, inst in list(self._items.items()):
            if inst.source_route_id == source_route_id and key not in present and inst.is_active:
                inst.is_active = False
                removed.append(key)
                self._dirty = True
        return removed

    def get(self, key: str) -> Instrument | None:
        return self._items.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def __len__(self) -> int:
        return len(self._items)

    def keys(self, source_route_id: str | None = None, active_only: bool = True) -> list[str]:
        return [
            k for k, v in self._items.items()
            if (source_route_id is None or v.source_route_id == source_route_id) and (v.is_active or not active_only)
        ]

    def items(self, active_only: bool = True) -> list[Instrument]:
        return [v for v in self._items.values() if v.is_active or not active_only]

    def by_coi_base(self) -> dict[str, list[Instrument]]:
        out: dict[str, list[Instrument]] = {}
        for inst in self._items.values():
            if inst.is_active:
                out.setdefault(inst.coi_base, []).append(inst)
        return out

    @property
    def version(self) -> str:
        if self._dirty:
            payload = orjson.dumps(
                {k: catalog_record(v) for k, v in sorted(self._items.items()) if v.is_active},
                option=orjson.OPT_SORT_KEYS,
            )
            self._version = "v1-" + hashlib.sha256(payload).hexdigest()
            self._dirty = False
        return self._version

    def to_dict(self, source_route_id: str | None = None) -> dict[str, dict[str, object]]:
        return {
            k: catalog_record(v)
            for k, v in self._items.items()
            if v.is_active and (source_route_id is None or v.source_route_id == source_route_id)
        }
