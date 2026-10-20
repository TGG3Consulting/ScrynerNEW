"""Base classes shared by all exchange collectors.

* :class:`CollectorSink` — where normalised data goes (the worker).
* :class:`RestClient` — aiohttp with a token-bucket rate limit and retries.
* :class:`WsClient` — a resilient WebSocket connection: reconnect with backoff, optional
  application-level ping, text/binary frames, gzip/zlib inflate, JSON decoding.
* :class:`Collector` — orchestration: fetch catalog, open WS connections in batches,
  poll OI/tickers, keep the catalog fresh.
"""

from __future__ import annotations

import asyncio
import gzip
import random
import time
import zlib
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any, Protocol

import aiohttp
import orjson
import websockets

from scryner.core.config import ExchangeConfig
from scryner.core.logging import get_logger
from scryner.core.models import Bar, BookUpdate, Instrument, OIUpdate, TickerUpdate, Trade


class CollectorSink(Protocol):
    def on_instruments(self, source_route_id: str, instruments: list[Instrument]) -> None: ...
    def on_trade(self, t: Trade) -> None: ...
    def on_book(self, b: BookUpdate) -> None: ...
    def on_oi(self, u: OIUpdate) -> None: ...
    def on_ticker(self, u: TickerUpdate) -> None: ...


class RateLimiter:
    def __init__(self, per_sec: float, burst: int | None = None) -> None:
        self.rate = max(per_sec, 0.01)
        self.capacity = burst or max(1, int(per_sec))
        self._tokens = float(self.capacity)
        self._ts = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._ts) * self.rate)
                self._ts = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)


class RestClient:
    def __init__(self, base_url: str, rate_per_sec: float = 5.0, timeout_sec: float = 20.0, headers: dict[str, str] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.limiter = RateLimiter(rate_per_sec)
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._headers = {"User-Agent": "scryner/0.1 (+https://github.com/TGG3Consulting/ScrynerNEW)", **(headers or {})}
        self._session: aiohttp.ClientSession | None = None
        self.log = get_logger("rest", base=self.base_url)

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # trust_env: honour HTTP(S)_PROXY / NO_PROXY and SSL_CERT_FILE from the environment
            self._session = aiohttp.ClientSession(timeout=self._timeout, headers=self._headers, trust_env=True)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None,
                      retries: int = 3, absolute: bool = False, raw: bool = False) -> Any:
        url = path if absolute or path.startswith("http") else f"{self.base_url}{path}"
        last: Exception | None = None
        for attempt in range(retries + 1):
            await self.limiter.acquire()
            try:
                s = await self.session()
                async with s.request(method, url, params=params, json=json) as resp:
                    if resp.status == 429 or resp.status >= 500:
                        raise aiohttp.ClientResponseError(resp.request_info, resp.history, status=resp.status, message=await resp.text())
                    resp.raise_for_status()
                    data = await resp.read()
                    return data if raw else orjson.loads(data)
            except (TimeoutError, aiohttp.ClientError, orjson.JSONDecodeError) as exc:
                last = exc
                delay = min(30.0, (2 ** attempt) + random.random())
                self.log.warning("rest_retry", url=url, attempt=attempt, error=str(exc)[:200], delay=round(delay, 1))
                await asyncio.sleep(delay)
        raise RuntimeError(f"REST failed after retries: {url}: {last}")

    async def get(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> Any:
        return await self.request("POST", path, **kw)


MessageHandler = Callable[[Any], Awaitable[None] | None]


class WsClient:
    """One WebSocket connection with automatic reconnection.

    ``on_open`` is awaited after every (re)connect to (re)send subscriptions. Frames are decoded
    as JSON unless ``decode`` is overridden; binary frames are inflated when they look gzipped
    or zlib-compressed.
    """

    def __init__(
        self,
        url: str,
        on_message: MessageHandler,
        *,
        name: str,
        on_open: Callable[[WsClient], Awaitable[None]] | None = None,
        ping_interval: float | None = 20.0,
        app_ping: Callable[[], str | bytes | None] | None = None,
        app_ping_interval: float = 20.0,
        decode: Callable[[str | bytes], Any] | None = None,
        max_size: int = 16 * 1024 * 1024,
        extra_headers: dict[str, str] | None = None,
        reconnect_min: float = 1.0,
        reconnect_max: float = 60.0,
        open_timeout: float = 20.0,
        idle_timeout: float = 90.0,
    ) -> None:
        self.url = url
        self.on_message = on_message
        self.on_open = on_open
        self.name = name
        self.ping_interval = ping_interval
        self.app_ping = app_ping
        self.app_ping_interval = app_ping_interval
        self.decode = decode or self.default_decode
        self.max_size = max_size
        self.extra_headers = extra_headers or {}
        self.reconnect_min = reconnect_min
        self.reconnect_max = reconnect_max
        self.open_timeout = open_timeout
        self.idle_timeout = idle_timeout
        self.log = get_logger("ws", name=name)
        self._ws: websockets.ClientConnection | None = None
        self._stop = asyncio.Event()
        self.connected = False
        self.last_message_at = 0.0
        self.messages = 0
        self.reconnects = 0

    @staticmethod
    def default_decode(raw: str | bytes) -> Any:
        if isinstance(raw, bytes):
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            elif raw[:1] == b"\x78":
                try:
                    raw = zlib.decompress(raw)
                except zlib.error:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        return orjson.loads(raw)

    async def send(self, payload: Any) -> None:
        if self._ws is None:
            raise ConnectionError(f"{self.name}: not connected")
        data = payload if isinstance(payload, (str, bytes)) else orjson.dumps(payload).decode()
        await self._ws.send(data)

    async def send_json(self, payload: Any) -> None:
        await self.send(payload)

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        delay = self.reconnect_min
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url, max_size=self.max_size, additional_headers=self.extra_headers,
                    ping_interval=self.ping_interval, ping_timeout=self.ping_interval * 2 if self.ping_interval else None,
                    open_timeout=self.open_timeout, close_timeout=2, compression=None,
                ) as ws:
                    self._ws = ws
                    self.connected = True
                    self.last_message_at = time.monotonic()
                    self.log.info("ws_connected", reconnects=self.reconnects)
                    delay = self.reconnect_min
                    if self.on_open is not None:
                        await self.on_open(self)
                    pinger = asyncio.create_task(self._app_pinger()) if self.app_ping else None
                    try:
                        await self._read_loop(ws)
                    finally:
                        if pinger:
                            pinger.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log.warning("ws_error", error=f"{type(exc).__name__}: {str(exc)[:200]}")
            finally:
                self.connected = False
                self._ws = None
            if self._stop.is_set():
                break
            self.reconnects += 1
            await asyncio.sleep(delay + random.random())
            delay = min(self.reconnect_max, delay * 2)

    async def _read_loop(self, ws: websockets.ClientConnection) -> None:
        while not self._stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=self.idle_timeout)
            except TimeoutError:
                raise ConnectionError("idle timeout") from None
            self.last_message_at = time.monotonic()
            self.messages += 1
            try:
                msg = self.decode(raw)
            except Exception as exc:
                self.log.debug("ws_decode_error", error=str(exc)[:100])
                continue
            res = self.on_message(msg)
            if res is not None:
                await res

    async def _app_pinger(self) -> None:
        assert self.app_ping is not None
        while True:
            await asyncio.sleep(self.app_ping_interval)
            payload = self.app_ping()
            if payload is not None and self._ws is not None:
                try:
                    await self._ws.send(payload)
                except Exception:
                    return


def chunks(seq: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


class Collector(ABC):
    """Base orchestration for one source (``exchange_market``)."""

    rest_rate_per_sec: float = 5.0

    def __init__(self, cfg: ExchangeConfig, sink: CollectorSink) -> None:
        self.cfg = cfg
        self.sink = sink
        self.source_route_id = cfg.source_route_id
        self.log = get_logger("collector", source=self.source_route_id)
        self.rest = RestClient(cfg.rest_url, rate_per_sec=cfg.rest_rate_limit_per_sec or self.rest_rate_per_sec)
        self.instruments: dict[str, Instrument] = {}      # key -> Instrument
        self.by_native: dict[str, Instrument] = {}        # native symbol -> Instrument
        self._tasks: list[asyncio.Task[None]] = []
        self._ws_clients: list[WsClient] = []
        self._stop = asyncio.Event()
        self.stats: dict[str, int] = {"trades": 0, "books": 0, "oi": 0, "tickers": 0}

    # -------------------------------------------------------------- abstract API
    @abstractmethod
    async def fetch_instruments(self) -> list[Instrument]:
        """Load the instrument list from REST."""

    @abstractmethod
    async def open_streams(self, instruments: list[Instrument]) -> None:
        """Create WS clients for the given instruments (append to ``self._ws_clients``)."""

    async def poll_open_interest(self, instruments: list[Instrument]) -> None:
        """Fetch OI for instruments via REST (override when the exchange has no OI stream)."""
        return None

    async def poll_tickers(self, instruments: list[Instrument]) -> None:
        """Fetch 24h tickers via REST (override when the exchange has no ticker stream)."""
        return None

    async def fetch_klines_1m(self, inst: Instrument, limit: int) -> list[Bar]:
        """Historical 1-minute bars (oldest first) used to seed metrics at startup. Override per exchange."""
        return []

    # -------------------------------------------------------------- helpers
    def instrument(self, native_symbol: str) -> Instrument | None:
        return self.by_native.get(native_symbol)

    def make_instrument(self, *, native_symbol: str, pair: str, base: str, quote: str, tick_size: float, step_size: float,
                        multiplier: float = 1.0, volume_currency: str | None = None, display_pair: str | None = None,
                        display_symbol: str | None = None, display_name: str | None = None, asset_class: str = "crypto",
                        stock_symbol: str | None = None, coi_inverse: bool = False, chain_name: str | None = None,
                        contract_address: str | None = None, base_asset_id: str | None = None, listed_at: float | None = None) -> Instrument:
        from scryner.core.models import make_key

        is_stock = asset_class == "stock"
        return Instrument(
            key=make_key(self.cfg.exchange, pair, self.cfg.market_type), exchange=self.cfg.exchange, pair=pair,
            market_type=self.cfg.market_type, source_route_id=self.source_route_id, source_title=self.cfg.title,
            native_symbol=native_symbol, base_asset=base, quote_currency=quote, volume_currency=volume_currency or quote,
            contract_multiplier=multiplier, tick_size=tick_size, step_size=step_size, display_pair=display_pair or pair,
            display_symbol=display_symbol, display_name=display_name, asset_class="stock" if is_stock else "crypto",
            stock_scope=is_stock, stock_symbol=stock_symbol or (base if is_stock else None), coi_inverse=coi_inverse,
            chain_name=chain_name, contract_address=contract_address, base_asset_id=base_asset_id, listed_at=listed_at,
        )

    def quote_allowed(self, quote: str) -> bool:
        return not self.cfg.quote_whitelist or quote.upper() in {q.upper() for q in self.cfg.quote_whitelist}

    def emit_trade(self, inst: Instrument, ts: float, price: float, qty: float, side: str, trade_id: str = "", quote: float | None = None) -> None:
        if price <= 0 or qty <= 0:
            return
        q = quote if quote is not None else price * qty * inst.contract_multiplier
        self.stats["trades"] += 1
        self.sink.on_trade(Trade(key=inst.key, ts=ts, price=price, qty=qty * inst.contract_multiplier, quote=q, side="buy" if side == "buy" else "sell", trade_id=trade_id))

    def emit_book(self, inst: Instrument, ts: float, bids: list[tuple[float, float]], asks: list[tuple[float, float]], is_snapshot: bool, seq: int = 0, prev_seq: int = 0) -> None:
        self.stats["books"] += 1
        self.sink.on_book(BookUpdate(key=inst.key, ts=ts, bids=bids, asks=asks, is_snapshot=is_snapshot, seq=seq, prev_seq=prev_seq))

    def emit_oi(self, inst: Instrument, ts: float, oi: float, oi_value: float | None = None) -> None:
        if oi < 0:
            return
        self.stats["oi"] += 1
        self.sink.on_oi(OIUpdate(key=inst.key, ts=ts, oi=oi * inst.contract_multiplier, oi_value=oi_value))

    def emit_ticker(self, inst: Instrument, ts: float, **fields: Any) -> None:
        self.stats["tickers"] += 1
        self.sink.on_ticker(TickerUpdate(key=inst.key, ts=ts, **fields))

    def add_ws(self, client: WsClient) -> WsClient:
        headers = self.cfg.extra.get("ws_headers")
        if isinstance(headers, dict):
            client.extra_headers.update({str(k): str(v) for k, v in headers.items()})
        self._ws_clients.append(client)
        return client

    # -------------------------------------------------------------- lifecycle
    async def refresh_catalog(self) -> list[Instrument]:
        items = await self.fetch_instruments()
        items = [i for i in items if self.quote_allowed(i.quote_currency)]
        include = [str(x).upper() for x in self.cfg.extra.get("include_symbols", []) or []]
        if include:
            items = [i for i in items if any(sub in i.pair.upper() or sub in i.native_symbol.upper() for sub in include)]
        limit = int(self.cfg.extra.get("max_instruments", 0) or 0)
        if limit > 0:
            items = items[:limit]
        self.instruments = {i.key: i for i in items}
        self.by_native = {i.native_symbol: i for i in items}
        self.sink.on_instruments(self.source_route_id, items)
        self.log.info("catalog", count=len(items))
        return items

    async def run(self) -> None:
        instruments = await self.refresh_catalog()
        await self.open_streams(instruments)
        for c in self._ws_clients:
            self._tasks.append(asyncio.create_task(c.run(), name=f"{self.source_route_id}:{c.name}"))
        self._tasks.append(asyncio.create_task(self._oi_loop(), name=f"{self.source_route_id}:oi"))
        self._tasks.append(asyncio.create_task(self._ticker_loop(), name=f"{self.source_route_id}:ticker"))
        self._tasks.append(asyncio.create_task(self._catalog_loop(), name=f"{self.source_route_id}:catalog"))
        self._tasks.append(asyncio.create_task(self._stats_loop(), name=f"{self.source_route_id}:stats"))
        try:
            await self._stop.wait()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self._stop.set()
        for c in self._ws_clients:
            c.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.rest.close()

    def stop(self) -> None:
        self._stop.set()

    async def _oi_loop(self) -> None:
        if self.cfg.market_type != "futures":
            return
        while not self._stop.is_set():
            try:
                await self.poll_open_interest(list(self.instruments.values()))
            except Exception as exc:
                self.log.warning("oi_poll_error", error=str(exc)[:200])
            await asyncio.sleep(self.cfg.oi_poll_sec)

    async def _ticker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.poll_tickers(list(self.instruments.values()))
            except Exception as exc:
                self.log.warning("ticker_poll_error", error=str(exc)[:200])
            await asyncio.sleep(self.cfg.ticker_poll_sec)

    async def _catalog_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.cfg.catalog_refresh_sec)
            try:
                before = set(self.instruments)
                items = await self.refresh_catalog()
                new = [i for i in items if i.key not in before]
                if new:
                    self.log.info("catalog_new_instruments", count=len(new), keys=[i.key for i in new][:10])
                    await self.open_streams(new)
                    for c in self._ws_clients:
                        if not c.connected and c.messages == 0 and c.reconnects == 0:
                            self._tasks.append(asyncio.create_task(c.run(), name=f"{self.source_route_id}:{c.name}"))
            except Exception as exc:
                self.log.warning("catalog_refresh_error", error=str(exc)[:200])

    async def _stats_loop(self) -> None:
        prev = dict(self.stats)
        while not self._stop.is_set():
            await asyncio.sleep(60)
            delta = {k: self.stats[k] - prev.get(k, 0) for k in self.stats}
            prev = dict(self.stats)
            connected = sum(1 for c in self._ws_clients if c.connected)
            self.log.info("collector_stats", per_min=delta, ws_connected=connected, ws_total=len(self._ws_clients), instruments=len(self.instruments))


def f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def levels(rows: Iterable[Sequence[Any]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for r in rows:
        try:
            out.append((float(r[0]), float(r[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return out
