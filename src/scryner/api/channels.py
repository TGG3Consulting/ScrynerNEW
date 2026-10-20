"""WebSocket channel implementations (snapshot/patch state channels and feeds)."""

from __future__ import annotations

import asyncio
import gzip
import time
from collections.abc import Callable, Iterable
from typing import Any

import orjson
from fastapi import WebSocket, WebSocketDisconnect

from scryner.core.logging import get_logger
from scryner.detectors.bots import bot_text
from scryner.detectors.density import density_text
from scryner.metrics.records import alert_record

from .state import GatewayState

log = get_logger("ws")

SORT_FIELDS: dict[str, Callable[[dict[str, Any]], float]] = {
    "totalVolume": lambda r: (r.get("buy_sum") or 0) + (r.get("sell_sum") or 0),
    "volume24h": lambda r: r.get("volume_24h") or 0,
    "tpm": lambda r: r.get("tpm") or 0,
    "spike": lambda r: r.get("spike_ratio") or 0,
    "dvol": lambda r: r.get("dvol5_pct") or 0,
    "natr5": lambda r: r.get("natr5") or 0,
    "oi": lambda r: r.get("oi_percentage") or 0,
    "oi2": lambda r: r.get("oi2_percentage") or 0,
    "buySum": lambda r: r.get("buy_sum") or 0,
    "sellSum": lambda r: r.get("sell_sum") or 0,
    "priceChange": lambda r: r.get("price_change_pct") or 0,
}


def _csv(v: str | None) -> set[str] | None:
    if v is None or v == "":
        return None
    return {x.strip() for x in v.split(",") if x.strip()}


def _flag(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on")


def _num(v: str | None, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        return default


class WsConn:
    """Thin wrapper: JSON / gzip sending, ping handling, closed flag."""

    def __init__(self, ws: WebSocket, gzip_frames: bool = False) -> None:
        self.ws = ws
        self.gzip = gzip_frames
        self.closed = False
        self.query = dict(ws.query_params)

    async def send(self, payload: Any) -> None:
        if self.closed:
            return
        try:
            data = orjson.dumps(payload)
            if self.gzip:
                await self.ws.send_bytes(gzip.compress(data, compresslevel=3))
            else:
                await self.ws.send_text(data.decode())
        except Exception:
            self.closed = True

    async def send_text(self, text: str) -> None:
        if self.closed:
            return
        try:
            await self.ws.send_text(text)
        except Exception:
            self.closed = True

    async def recv_loop(self, on_message: Callable[[Any], Any] | None = None) -> None:
        """Read incoming frames; answers pings; calls ``on_message`` for JSON objects."""
        try:
            while not self.closed:
                raw = await self.ws.receive_text()
                if raw == "ping":
                    await self.send_text("pong")
                    continue
                try:
                    msg = orjson.loads(raw)
                except orjson.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and msg.get("ping") is not None and len(msg) == 1:
                    await self.send_text("pong")
                    continue
                if on_message is not None:
                    res = on_message(msg)
                    if asyncio.iscoroutine(res):
                        await res
        except WebSocketDisconnect:
            self.closed = True
        except Exception:
            self.closed = True


class StateChannel:
    """Base for snapshot/patch channels over a filtered, sorted selection of records."""

    def __init__(self, state: GatewayState, conn: WsConn, *, patch_interval: float, record_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self.state = state
        self.conn = conn
        self.patch_interval = patch_interval
        self.record_fn = record_fn or (lambda r: r)
        self.seq = 0
        self.sent: dict[str, dict[str, Any]] = {}
        q = conn.query
        self.market_scope = q.get("market_scope", "all")
        self.exclude_stocks = _flag(q.get("exclude_stocks"))
        self.source_futures = _csv(q.get("source_futures"))
        self.source_spot = _csv(q.get("source_spot"))
        self.route = q.get("route")
        self.limit = int(_num(q.get("limit"), 0))
        self.sort = q.get("sort", "totalVolume")
        self.desc = q.get("dir", "desc") != "asc"
        self.pinned: set[str] = set()

    def selection(self) -> list[str]:
        keys = self.state.select_keys(market_scope=self.market_scope, exclude_stocks=self.exclude_stocks,
                                      source_futures=self.source_futures, source_spot=self.source_spot, route=self.route)
        keys = [k for k in keys if self.accept(self.state.records[k])]
        sort_fn = SORT_FIELDS.get(self.sort, SORT_FIELDS["totalVolume"])
        keys.sort(key=lambda k: sort_fn(self.state.records[k]), reverse=self.desc)
        if self.limit > 0:
            top = keys[: self.limit]
            for p in self.pinned:
                if p in self.state.records and p not in top:
                    top.append(p)
            return top
        return keys

    def accept(self, rec: dict[str, Any]) -> bool:
        return True

    async def send_snapshot(self) -> None:
        self.seq += 1
        data = {k: self.record_fn(self.state.records[k]) for k in self.selection()}
        self.sent = {k: dict(v) for k, v in data.items()}
        await self.conn.send({"type": "snapshot", "version": 3, "seq": self.seq, "data": data})

    async def send_patch(self) -> None:
        current = {k: self.record_fn(self.state.records[k]) for k in self.selection()}
        updates: dict[str, Any] = {}
        for k, rec in current.items():
            old = self.sent.get(k)
            if old is None:
                updates[k] = rec
            else:
                d = {f: v for f, v in rec.items() if old.get(f) != v}
                if d:
                    updates[k] = d
        delete = [k for k in self.sent if k not in current]
        if not updates and not delete:
            return
        base = self.seq
        self.seq += 1
        self.sent = {k: dict(v) for k, v in current.items()}
        await self.conn.send({"type": "patch", "version": 3, "base_seq": base, "seq": self.seq, "updates": updates, "unset": {}, "delete": delete})

    async def run(self, wait_subscribe: bool = False) -> None:
        subscribed = asyncio.Event()
        if not wait_subscribe:
            subscribed.set()

        def on_msg(msg: Any) -> None:
            if isinstance(msg, dict) and msg.get("type") == "subscribe":
                self.pinned = set(msg.get("pinned_keys") or [])
                subscribed.set()

        reader = asyncio.create_task(self.conn.recv_loop(on_msg))
        try:
            try:
                await asyncio.wait_for(subscribed.wait(), timeout=30)
            except TimeoutError:
                return
            await self.send_snapshot()
            while not self.conn.closed:
                await asyncio.sleep(self.patch_interval)
                await self.send_patch()
        finally:
            reader.cancel()


class TopChannel(StateChannel):
    """``/ws/all/v1`` and ``/ws/v3``."""


class AlertsChannel(StateChannel):
    """``/ws/all/alerts/v1`` — compact alert records, server-side ``min_*`` pre-filter."""

    def __init__(self, state: GatewayState, conn: WsConn, patch_interval: float, limit_max: int) -> None:
        super().__init__(state, conn, patch_interval=patch_interval, record_fn=alert_record)
        q = conn.query
        self.min_vol = _num(q.get("min_vol"))
        self.mins = {
            "oi_percentage": _num(q.get("min_oi1")), "dvol5_pct": _num(q.get("min_dvol5")), "tpm": _num(q.get("min_tpm")),
            "spike_ratio": _num(q.get("min_spike")), "natr5": _num(q.get("min_natr5")), "buy_sum": _num(q.get("min_buy")), "sell_sum": _num(q.get("min_sell")),
        }
        self.active_mins = {k: v for k, v in self.mins.items() if v > 0}
        self.limit = min(self.limit or limit_max, limit_max)
        self.sort = "volume24h"

    def accept(self, rec: dict[str, Any]) -> bool:
        if (rec.get("volume_24h") or 0) < self.min_vol:
            return False
        if not self.active_mins:
            return True
        for field, thr in self.active_mins.items():
            v = rec.get(field)
            if v is not None and v >= thr:
                return True
        return False


class TwapChannel:
    """``/ws/active_twaps`` — relays the indexer's snapshot/patch map."""

    def __init__(self, state: GatewayState, conn: WsConn) -> None:
        self.state = state
        self.conn = conn
        self.seq = 0

    async def run(self) -> None:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        off = self.state.listen("twap", lambda _k, p: queue.put_nowait(p) if not queue.full() else None)
        reader = asyncio.create_task(self.conn.recv_loop())
        try:
            self.seq = self.state.twap_seq
            await self.conn.send({"type": "snapshot", "version": 3, "seq": self.seq, "data": self.state.twap})
            while not self.conn.closed:
                try:
                    p = await asyncio.wait_for(queue.get(), timeout=30)
                except TimeoutError:
                    continue
                if p.get("type") == "snapshot":
                    self.seq = self.state.twap_seq
                    await self.conn.send({"type": "snapshot", "version": 3, "seq": self.seq, "data": self.state.twap})
                    continue
                base = self.seq
                self.seq += 1
                await self.conn.send({"type": "patch", "version": 3, "base_seq": base, "seq": self.seq, "updates": p.get("updates", {}), "unset": {}, "delete": p.get("delete", [])})
        finally:
            off()
            reader.cancel()


class TickChannel:
    """``/ws/tick`` — raw tick events filtered by sources."""

    def __init__(self, state: GatewayState, conn: WsConn) -> None:
        self.state = state
        self.conn = conn
        q = conn.query
        self.source_futures = _csv(q.get("source_futures"))
        self.source_spot = _csv(q.get("source_spot"))
        self.route = q.get("route")

    def accept(self, ev: dict[str, Any]) -> bool:
        ex, mt = ev.get("exchange", ""), ev.get("market_type")
        if self.route in ("all_futures",) and mt != "futures":
            return False
        if self.route in ("all_spots",) and mt != "spot":
            return False
        if mt == "futures" and self.source_futures is not None and ex not in self.source_futures:
            return False
        if mt == "spot" and self.source_spot is not None and ex not in self.source_spot:
            return False
        return True

    async def run(self) -> None:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)

        def on_tick(_k: str, p: dict[str, Any]) -> None:
            if self.accept(p) and not queue.full():
                queue.put_nowait(p)

        off = self.state.listen("tick", on_tick)
        reader = asyncio.create_task(self.conn.recv_loop())
        try:
            while not self.conn.closed:
                try:
                    p = await asyncio.wait_for(queue.get(), timeout=30)
                except TimeoutError:
                    continue
                out = {k: v for k, v in p.items() if k not in ("key", "source_route_id")}
                await self.conn.send(out)
        finally:
            off()
            reader.cancel()


class FeedChannel:
    """``hello`` → ``history`` → live items. Used by bots/news/onchain/listings/density feeds."""

    def __init__(self, state: GatewayState, conn: WsConn, kind: str, history: Iterable[dict[str, Any]], render: Callable[[dict[str, Any]], dict[str, Any]],
                 listen_kinds: tuple[str, ...], connections: Callable[[], int], history_size: int, density_mode: bool = False) -> None:
        self.state = state
        self.conn = conn
        self.kind = kind
        self.history = list(history)[-history_size:]
        self.render = render
        self.listen_kinds = listen_kinds
        self.connections = connections
        self.density_mode = density_mode

    async def run(self) -> None:
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=5000)
        offs = [self.state.listen(k, lambda kind, p: queue.put_nowait((kind, p)) if not queue.full() else None) for k in self.listen_kinds]
        reader = asyncio.create_task(self.conn.recv_loop())
        try:
            if self.density_mode:
                await self.conn.send({"type": "history", "status": "live", "configured": True, "client_count": self.connections(), "items": [self.render(x) for x in self.history]})
            else:
                await self.conn.send({"type": "hello", "connections": self.connections(), "history_size": len(self.history)})
                await self.conn.send({"type": "history", "items": [self.render(x) for x in self.history]})
            while not self.conn.closed:
                try:
                    kind, p = await asyncio.wait_for(queue.get(), timeout=30)
                except TimeoutError:
                    continue
                if kind in ("density_impact", "density_removed"):
                    rec = self.state.density_by_id.get(p["id"])
                    if rec is not None:
                        await self.conn.send({**self.render(rec), "event": "density.update"})
                    continue
                await self.conn.send(self.render(p))
        finally:
            for off in offs:
                off()
            reader.cancel()


def render_density(p: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in p.items() if k not in ("key",)}
    out["text"] = density_text(p)
    return out


def render_bot(p: dict[str, Any]) -> dict[str, Any]:
    from datetime import UTC, datetime

    return {"captured_at": datetime.fromtimestamp(p["captured_at"], tz=UTC).isoformat(), "text": bot_text(p), "id": p.get("id"), "key": p.get("key"),
            "pattern": p.get("pattern"), "side": p.get("side"), "rating": p.get("rating"), "exchange": p.get("exchange"), "market_type": p.get("market_type")}


def render_feed(p: dict[str, Any]) -> dict[str, Any]:
    return p


class ChartChannel:
    """``/ws/graph_charts/v1`` — live closed/partial bars for subscribed keys."""

    def __init__(self, state: GatewayState, conn: WsConn, resubscribe: Callable[[], Any]) -> None:
        self.state = state
        self.conn = conn
        self.keys: set[str] = set()
        self.resubscribe = resubscribe

    async def run(self) -> None:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)

        def on_chart(_k: str, p: dict[str, Any]) -> None:
            for c in p.get("charts", []):
                if c["key"] in self.keys and not queue.full():
                    queue.put_nowait(c)

        async def on_msg(msg: Any) -> None:
            if isinstance(msg, dict) and msg.get("type") == "subscribe":
                items = msg.get("items") or []
                self.keys = {str(i.get("key")) for i in items if i.get("key")}
                sources = {self.state.records.get(k, {}).get("source_route_id") or (self.state.catalog_record(k) or {}).get("source_route_id") for k in self.keys}
                await self.resubscribe()
                await self.conn.send({"type": "subscribed", "count": len(self.keys), "sources": len([s for s in sources if s])})

        off = self.state.listen("chart", on_chart)
        reader = asyncio.create_task(self.conn.recv_loop(on_msg))
        try:
            while not self.conn.closed:
                try:
                    c = await asyncio.wait_for(queue.get(), timeout=30)
                except TimeoutError:
                    continue
                await self.conn.send({"type": "chart", **c})
        finally:
            self.keys = set()
            off()
            await self.resubscribe()
            reader.cancel()


class StockQuoteChannel:
    """``/ws/stock/reference-prices/v1``."""

    def __init__(self, state: GatewayState, conn: WsConn, request_listings: Callable[[set[str]], Any]) -> None:
        self.state = state
        self.conn = conn
        self.listings: set[str] = set()
        self.request_listings = request_listings
        self.seq = 0
        self.sent: dict[str, Any] = {}

    async def run(self) -> None:
        changed = asyncio.Event()

        async def on_msg(msg: Any) -> None:
            if isinstance(msg, dict) and msg.get("type") == "subscribe":
                self.listings = {str(x) for x in msg.get("listings") or []}
                await self.request_listings(self.listings)
                self.seq = 1
                data = {lst: self.state.stock_quotes[lst] for lst in self.listings if lst in self.state.stock_quotes}
                self.sent = dict(data)
                await self.conn.send({"type": "snapshot", "version": 3, "seq": self.seq, "data": data})

        off = self.state.listen("stock_quote", lambda _k, _p: changed.set())
        reader = asyncio.create_task(self.conn.recv_loop(on_msg))
        try:
            while not self.conn.closed:
                try:
                    await asyncio.wait_for(changed.wait(), timeout=30)
                except TimeoutError:
                    continue
                changed.clear()
                if not self.listings or self.seq == 0:
                    continue
                updates = {lst: self.state.stock_quotes[lst] for lst in self.listings if lst in self.state.stock_quotes and self.state.stock_quotes[lst] != self.sent.get(lst)}
                if updates:
                    base = self.seq
                    self.seq += 1
                    self.sent.update(updates)
                    await self.conn.send({"type": "patch", "version": 3, "base_seq": base, "seq": self.seq, "updates": updates, "unset": {}, "delete": []})
        finally:
            off()
            reader.cancel()


__all__ = ["AlertsChannel", "ChartChannel", "FeedChannel", "StockQuoteChannel", "TickChannel", "TopChannel", "TwapChannel", "WsConn",
           "render_bot", "render_density", "render_feed", "time"]
