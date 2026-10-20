"""Reference prices of underlying stocks for tokenised equities (TradingView quote session).

TradingView's public quote websocket speaks a framed protocol: every packet is
``~m~<len>~m~<json>`` and heartbeats are ``~m~<len>~m~~h~<n>`` which must be echoed. We
open one quote session, add listings on demand (``NASDAQ:AAPL``), and publish
``stocks.quotes`` snapshots ``{listing: {status, source, listing, price, currency, session,
quote_time_ms, earnings}}`` whenever a quote changes.
"""

from __future__ import annotations

import asyncio
import random
import string
import time
from typing import Any

import orjson

from scryner.collectors.base import WsClient
from scryner.core.bus import MemoryBus, RedisBus
from scryner.core.config import StocksConfig
from scryner.core.logging import get_logger

FIELDS = ["lp", "ch", "chp", "current_session", "lp_time", "earnings_release_next_date", "earnings_publication_type_next", "currency_code", "short_name", "description", "rtc", "rch", "rchp"]


def _frame(payload: dict[str, Any]) -> str:
    body = orjson.dumps(payload).decode()
    return f"~m~{len(body)}~m~{body}"


def _parse(raw: str) -> list[Any]:
    out: list[Any] = []
    i = 0
    while i < len(raw):
        if not raw.startswith("~m~", i):
            break
        j = raw.index("~m~", i + 3)
        ln = int(raw[i + 3 : j])
        body = raw[j + 3 : j + 3 + ln]
        i = j + 3 + ln
        if body.startswith("~h~"):
            out.append(("heartbeat", body))
        else:
            try:
                out.append(("msg", orjson.loads(body)))
            except orjson.JSONDecodeError:
                continue
    return out


def _listing_to_tv(listing: str) -> str:
    # "NASDAQ-SNDK" → "NASDAQ:SNDK"
    return listing.replace("-", ":", 1)


class StockQuotes:
    def __init__(self, cfg: StocksConfig, bus: MemoryBus | RedisBus) -> None:
        self.cfg = cfg
        self.bus = bus
        self.log = get_logger("stocks")
        self.session_id = "qs_" + "".join(random.choices(string.ascii_lowercase, k=12))
        self.wanted: set[str] = set()
        self.added: set[str] = set()
        self.quotes: dict[str, dict[str, Any]] = {}
        self._ws: WsClient | None = None

    async def run(self) -> None:
        self._ws = WsClient(
            f"{self.cfg.tradingview_ws_url}?from=screener%2F&type=chart", self._on_message, name="tradingview", on_open=self._on_open,
            ping_interval=None, decode=lambda raw: raw if isinstance(raw, str) else raw.decode(), extra_headers={"Origin": "https://www.tradingview.com"},
        )
        await asyncio.gather(self._ws.run(), self._cmd_loop(), self._sync_loop())

    async def _on_open(self, ws: WsClient) -> None:
        await ws.send(_frame({"m": "set_auth_token", "p": ["unauthorized_user_token"]}))
        await ws.send(_frame({"m": "quote_create_session", "p": [self.session_id]}))
        await ws.send(_frame({"m": "quote_set_fields", "p": [self.session_id, *FIELDS]}))
        self.added = set()

    async def _on_message(self, raw: str) -> None:
        for kind, body in _parse(raw):
            if kind == "heartbeat":
                if self._ws:
                    await self._ws.send(f"~m~{len(body)}~m~{body}")
                continue
            if not isinstance(body, dict) or body.get("m") != "qsd":
                continue
            p = body.get("p") or []
            if len(p) < 2 or not isinstance(p[1], dict):
                continue
            sym = p[1].get("n", "")
            v = p[1].get("v") or {}
            listing = sym.replace(":", "-", 1)
            q = self.quotes.setdefault(listing, {"status": "live", "source": "tradingview_live_quote", "listing": listing, "price": None, "currency": "USD", "session": None, "quote_time_ms": None, "earnings": None})
            changed = False
            if "lp" in v and v["lp"] is not None:
                q["price"], changed = float(v["lp"]), True
            if "currency_code" in v:
                q["currency"] = v["currency_code"]
            if "current_session" in v:
                q["session"] = v["current_session"]
            if v.get("lp_time"):
                q["quote_time_ms"] = int(v["lp_time"]) * 1000
            if v.get("earnings_release_next_date"):
                from datetime import UTC, datetime

                q["earnings"] = {"date": datetime.fromtimestamp(int(v["earnings_release_next_date"]), tz=UTC).strftime("%Y-%m-%d"),
                                 "time": 1, "publication_type": v.get("earnings_publication_type_next")}
            if changed:
                await self.bus.publish("stocks.quotes", {"quotes": {listing: q}, "ts": time.time()})

    async def _cmd_loop(self) -> None:
        sub = self.bus.subscribe("cmd.stocks")
        try:
            async for _t, payload in sub:
                if payload.get("type") == "subscribe":
                    self.wanted = {str(x) for x in payload.get("listings", [])}
        finally:
            sub.close()

    async def _sync_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self._ws is None or not self._ws.connected:
                continue
            add = self.wanted - self.added
            remove = self.added - self.wanted
            if add:
                await self._ws.send(_frame({"m": "quote_add_symbols", "p": [self.session_id, *[_listing_to_tv(x) for x in add]]}))
                self.added |= add
            if remove:
                await self._ws.send(_frame({"m": "quote_remove_symbols", "p": [self.session_id, *[_listing_to_tv(x) for x in remove]]}))
                self.added -= remove
