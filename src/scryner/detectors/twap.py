"""Hyperliquid TWAP indexer (the "Active TWAPs" widget source).

Hyperliquid executes TWAP orders on-chain; each sub-fill carries ``twapId`` and the explorer
WS exposes every ``twapOrder`` action. We subscribe to ``explorerBlock`` (all L1 actions),
pick ``twapOrder`` actions, keep the active ones in a snapshot/patch map and poll market
prices. The published state mirrors the reference channel:

    __meta__  {kind: meta, source, status, count, active_count, last_success_ts}
    __bans__  {kind: bans, tokens, prefixes, min_usd_per_30s_by_token_side, min_usd_per_minute_by_token}
    t:<hash>  {kind: twap, ...}     p:<hash> {kind: presence}     m:<market_id> {kind: market, price}

A TWAP expires when ``expires_ms`` passes or when a ``twapCancel`` action for the same id is
seen. Perp assets use ``market_id = asset index``, spot assets ``10000 + spot index``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import orjson

from scryner.collectors.base import RestClient, WsClient
from scryner.core.config import TwapConfig
from scryner.core.logging import get_logger
from scryner.core.models import make_key


class TwapIndexer:
    def __init__(self, cfg: TwapConfig, on_patch: Any, on_snapshot: Any = None) -> None:
        self.cfg = cfg
        self.on_patch = on_patch  # async callable(seq, updates: dict, delete: list)
        self.on_snapshot = on_snapshot  # async callable() → publish a full snapshot
        self.log = get_logger("twap")
        self.rest = RestClient(cfg.info_url.rsplit("/", 1)[0], rate_per_sec=10)
        self.state: dict[str, dict[str, Any]] = {}
        self.seq = 0
        self.perp_names: list[str] = []
        self.spot_pairs: list[dict[str, Any]] = []
        self.spot_tokens: dict[int, dict[str, Any]] = {}
        self.prices: dict[str, float] = {}
        self._ws: WsClient | None = None
        self.last_success_ts = 0.0

    # ------------------------------------------------------------------ metadata
    async def load_meta(self) -> None:
        meta, _ = await self.rest.post("/info", json={"type": "metaAndAssetCtxs"})
        self.perp_names = [a["name"] for a in meta.get("universe", [])]
        smeta, _ = await self.rest.post("/info", json={"type": "spotMetaAndAssetCtxs"})
        self.spot_pairs = smeta.get("universe", [])
        self.spot_tokens = {t["index"]: t for t in smeta.get("tokens", [])}

    async def load_prices(self) -> None:
        mids = await self.rest.post("/info", json={"type": "allMids"})
        updates: dict[str, Any] = {}
        for coin, px in mids.items():
            try:
                price = float(px)
            except (TypeError, ValueError):
                continue
            self.prices[coin] = price
            mid = self._market_id(coin)
            if mid is not None:
                k = f"m:{mid}"
                rec = {"kind": "market", "market_id": mid, "price": price}
                if self.state.get(k) != rec:
                    self.state[k] = rec
                    updates[k] = rec
        self.last_success_ts = time.time()
        if updates:
            await self._patch(updates, [])

    def _market_id(self, coin: str) -> int | None:
        if coin.startswith("@"):
            try:
                return 10_000 + int(coin[1:])
            except ValueError:
                return None
        try:
            return self.perp_names.index(coin)
        except ValueError:
            return None

    def _asset_info(self, asset: int) -> tuple[str, str, str, str, str, str]:
        """→ (market, token, pair, display_symbol, display_pair, chart_key)"""
        if asset >= 10_000:
            idx = asset - 10_000
            pair = self.spot_pairs[idx] if idx < len(self.spot_pairs) else {"name": f"@{idx}", "tokens": [0, 0]}
            base = self.spot_tokens.get(pair["tokens"][0], {}).get("name", pair["name"])
            quote = self.spot_tokens.get(pair["tokens"][1], {}).get("name", "USDC")
            name = pair["name"]
            native = name if name.startswith("@") else f"{base}/{quote}"
            return "spot", base, f"{name}/{quote}" if name.startswith("@") else native, base, f"{base}/{quote}", make_key("hyperliquid", native, "spot")
        name = self.perp_names[asset] if asset < len(self.perp_names) else str(asset)
        return "perp", name, f"{name}/USDC", name, f"{name}/USDC", make_key("hyperliquid", f"{name}/USDC", "futures")

    # ------------------------------------------------------------------ bans / filters
    def _banned(self, token: str, side: str, amount_usd: float, duration_min: int) -> bool:
        t = token.lower()
        if t in {x.lower() for x in self.cfg.banned_tokens}:
            return True
        if any(t.startswith(p.lower()) for p in self.cfg.banned_prefixes):
            return True
        per_min = amount_usd / max(1, duration_min)
        threshold = self.cfg.min_usd_per_minute_by_token.get(t, self.cfg.min_usd_per_minute_default)
        if per_min < threshold:
            return True
        side_thr = self.cfg.min_usd_per_30s_by_token_side.get(t, {}).get(side.lower())
        return bool(side_thr is not None and per_min / 2 < side_thr)

    def bans_record(self) -> dict[str, Any]:
        return {"kind": "bans", "source": "config", "tokens": list(self.cfg.banned_tokens), "prefixes": list(self.cfg.banned_prefixes),
                "min_usd_per_30s_by_token_side": self.cfg.min_usd_per_30s_by_token_side,
                "min_usd_per_minute_by_token": self.cfg.min_usd_per_minute_by_token}

    # ------------------------------------------------------------------ explorer stream
    async def bootstrap(self) -> None:
        """Load currently active TWAPs from hypurrscan (``/twap/{token}``, same tx format)."""
        if not self.cfg.bootstrap_from_hypurrscan:
            return
        client = RestClient(self.cfg.hypurrscan_url, rate_per_sec=12)
        tokens = list(self.perp_names) + [t.get("name", "") for t in self.spot_tokens.values()]
        loaded = 0
        try:
            for tok in tokens:
                if not tok:
                    continue
                try:
                    txs = await client.get(f"/twap/{tok}", retries=0)
                except Exception:
                    continue
                if isinstance(txs, list) and txs:
                    now_ms = int(time.time() * 1000)
                    fresh = [tx for tx in txs if int(tx.get("time", 0)) + int(((tx.get("action") or {}).get("twap") or {}).get("m", 0)) * 60_000 > now_ms]
                    if fresh:
                        await self._on_message(fresh)
                        loaded += len(fresh)
        finally:
            await client.close()
        self.log.info("twap_bootstrap", loaded=loaded, tokens=len(tokens))
        if self.on_snapshot is not None:
            await self.on_snapshot()

    async def run(self) -> None:
        await self.load_meta()
        self.state["__bans__"] = self.bans_record()
        await self.load_prices()
        await self.bootstrap()
        self._ws = WsClient(self.cfg.explorer_ws_url, self._on_message, name="hl-explorer", on_open=self._on_open, ping_interval=None,
                            app_ping=lambda: orjson.dumps({"method": "ping"}).decode(), app_ping_interval=30)
        tasks = [asyncio.create_task(self._ws.run()), asyncio.create_task(self._price_loop()), asyncio.create_task(self._expiry_loop()),
                 asyncio.create_task(self._meta_loop())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            await self.rest.close()

    async def _on_open(self, ws: WsClient) -> None:
        # explorerTxs (rpc.hyperliquid.xyz) streams every L1 transaction as a bare JSON list
        await ws.send_json({"method": "subscribe", "subscription": {"type": "explorerTxs"}})

    async def _on_message(self, msg: Any) -> None:
        if isinstance(msg, dict):
            data = msg.get("data")
            txs = data if isinstance(data, list) else []
        elif isinstance(msg, list):
            txs = msg
        else:
            return
        updates: dict[str, Any] = {}
        deletes: list[str] = []
        for tx in txs:
            if not isinstance(tx, dict):
                continue
            action = tx.get("action") or {}
            atype = action.get("type")
            height = int(tx.get("block", 0) or 0)
            if atype == "twapOrder":
                rec = self._twap_record(tx, action, height)
                if rec is not None:
                    updates[f"t:{rec['hash']}"] = rec
                    updates[f"p:{rec['hash']}"] = {"kind": "presence", "id": rec["hash"]}
            elif atype == "twapCancel":
                asset = int(action.get("a", -1))
                user = str(tx.get("user", "")).lower()
                # cancel references the on-chain twap id; we match the user's latest TWAP on that asset
                for k, v in sorted(((k, v) for k, v in self.state.items() if k.startswith("t:")), key=lambda kv: -kv[1].get("time_ms", 0)):
                    if v.get("asset") == asset and str(v.get("user", "")).lower() == user:
                        deletes.extend([k, f"p:{v['hash']}"])
                        break
        for k, v in updates.items():
            self.state[k] = v
        for k in deletes:
            self.state.pop(k, None)
        if updates or deletes:
            self.last_success_ts = time.time()
            await self._patch(updates, deletes)

    def _twap_record(self, tx: dict[str, Any], action: dict[str, Any], height: int) -> dict[str, Any] | None:
        tw = action.get("twap") or {}
        try:
            asset = int(tw.get("a"))
            is_buy = bool(tw.get("b"))
            size = float(tw.get("s"))
            minutes = int(tw.get("m"))
        except (TypeError, ValueError):
            return None
        market, token, pair, dsym, dpair, chart_key = self._asset_info(asset)
        price = self.prices.get(token if market == "perp" else pair.split("/")[0]) or self.prices.get(token) or 0.0
        amount_usd = size * price
        side = "BUY" if is_buy else "SELL"
        if self._banned(token, side, amount_usd, minutes):
            return None
        time_ms = int(tx.get("time", time.time() * 1000))
        h = str(tx.get("hash", ""))
        return {
            "kind": "twap", "id": h, "hash": h, "time_ms": time_ms, "expires_ms": time_ms + minutes * 60_000, "duration_min": minutes,
            "user": tx.get("user", ""), "block": height, "market_id": asset, "market": market, "market_label": "Spot" if market == "spot" else "Perp",
            "side": side, "direction": side.lower(), "token": token, "pair": pair, "display_symbol": dsym, "display_pair": dpair,
            "chart_key": chart_key, "amount": round(amount_usd, 2), "amount_text": f"{amount_usd:.2f}", "reduce_only": bool(tw.get("r")),
            "randomize": bool(tw.get("t")), "exchange": "hyperliquid", "market_type": "spot" if market == "spot" else "futures",
            "source_route_id": "hyperliquid_spot" if market == "spot" else "hyperliquid_futures", "twap_id": None, "asset": asset,
            "size": size,
        }

    async def _price_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.poll_sec)
            try:
                await self.load_prices()
            except Exception as exc:
                self.log.warning("twap_price_error", error=str(exc)[:200])

    async def _meta_loop(self) -> None:
        while True:
            await asyncio.sleep(600)
            try:
                await self.load_meta()
            except Exception as exc:
                self.log.warning("twap_meta_error", error=str(exc)[:200])

    async def _expiry_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now_ms = int(time.time() * 1000)
            deletes = [k for k, v in self.state.items() if k.startswith("t:") and v.get("expires_ms", 0) <= now_ms]
            for k in deletes:
                v = self.state.pop(k, None)
                if v:
                    self.state.pop(f"p:{v['hash']}", None)
            if deletes:
                await self._patch({}, deletes + [f"p:{k[2:]}" for k in deletes])

    async def _patch(self, updates: dict[str, Any], deletes: list[str]) -> None:
        count = sum(1 for k in self.state if k.startswith("t:"))
        meta = {"kind": "meta", "source": "hyperliquid_explorer", "status": "live" if self._ws is None or self._ws.connected else "reconnecting",
                "count": count, "active_count": count, "last_success_ts": self.last_success_ts}
        self.state["__meta__"] = meta
        updates = {"__meta__": meta, **updates}
        self.seq += 1
        await self.on_patch(self.seq, updates, deletes)

    def snapshot(self) -> dict[str, Any]:
        return dict(self.state)
