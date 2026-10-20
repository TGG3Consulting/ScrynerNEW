"""FastAPI gateway: REST + WebSocket channels, authentication, user features.

Routes follow the reference contract (``docs/analysis/GODSEYE_ANALYSIS.md`` §6 and
``GODSEYE_REALTIME_PROTOCOL.md``). Static client files are served from ``web/dist`` when
present.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from pathlib import Path
from typing import Any

import orjson
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from scryner.core.bus import MemoryBus, RedisBus
from scryner.core.config import ScrynerConfig
from scryner.core.logging import get_logger
from scryner.metrics.records import simple_metrics_array
from scryner.storage import Storage
from scryner.worker import SourceWorker

from .auth import AuthService, User
from .channels import (
    AlertsChannel,
    ChartChannel,
    FeedChannel,
    StockQuoteChannel,
    TickChannel,
    TopChannel,
    TwapChannel,
    WsConn,
    render_bot,
    render_density,
    render_feed,
)
from .state import GatewayState

log = get_logger("api")

LOGIN_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Scryner — вход</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>
body{margin:0;background:#0c0c0c;color:#e6e6e6;font-family:ui-monospace,monospace;display:flex;min-height:100vh;align-items:center;justify-content:center}
.card{max-width:420px;padding:32px;border:1px solid #222;border-radius:12px;background:#121212;text-align:center}
a.btn{display:inline-block;margin:16px 0;padding:14px 22px;background:#2aabee;color:#fff;border-radius:8px;text-decoration:none;font-weight:700}
code{display:block;margin-top:8px;color:#9aa;font-size:12px}#st{min-height:20px;color:#8c8}</style></head>
<body><main class="card" data-auth-status-url="/auth/pending/{token}" data-auth-next="{next}">
<h1>Scryner</h1><p>Вход через Telegram-бота</p>
<a class="btn" href="https://t.me/{bot}?start=login_{token}" target="_blank" rel="noopener">ВОЙТИ ЧЕРЕЗ TELEGRAM</a>
<div id="st"></div><code>/start login_{token}</code></main>
<script>
const st=document.getElementById('st');const url='/auth/pending/{token}';
async function poll(){try{const r=await fetch(url,{cache:'no-store'});const p=await r.json().catch(()=>({}));
if(r.ok&&p.status==='authenticated'){st.textContent='Telegram confirmed. Opening...';location.href=p.redirect_url||'{next}';return;}
if(r.status===410||p.status==='expired'){st.textContent='Login token expired. Refresh the page.';return;}
if(r.status===409||p.status==='completed'){st.textContent='Login was already completed in another tab.';return;}
if(r.status===404){st.textContent='Login token not found. Refresh and try again.';return;}}catch(e){}
setTimeout(poll,2000);}setTimeout(poll,1200);</script></body></html>"""


class Gateway:
    def __init__(self, cfg: ScrynerConfig, bus: MemoryBus | RedisBus, storage: Storage, workers: dict[str, SourceWorker]) -> None:
        self.cfg = cfg
        self.bus = bus
        self.storage = storage
        self.workers = workers
        self.state = GatewayState(cfg, bus, workers)
        self.auth = AuthService(storage, cfg.api, trial_days=cfg.api.trial_days, admin_telegram_ids=tuple(cfg.api.admin_telegram_ids))
        self.app = FastAPI(title="Scryner", docs_url=None, redoc_url=None, openapi_url=None)
        self.app.add_middleware(GZipMiddleware, minimum_size=512)
        self.chart_subs: dict[int, set[str]] = {}
        self.stock_subs: dict[int, set[str]] = {}
        self.feed_clients: dict[str, int] = {}
        self.team_hubs: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self._routes()

    # ------------------------------------------------------------------ helpers
    def _no_store(self, payload: Any, status: int = 200) -> Response:
        return Response(content=orjson.dumps(payload), media_type="application/json", status_code=status, headers={"cache-control": "no-store"})

    async def _current_user(self, request: Request) -> User | None:
        token = request.cookies.get(self.cfg.api.cookie_name)
        user = await self.auth.user_by_session(token)
        if user is None and self.cfg.api.dev_auto_login and self.cfg.app.env == "dev":
            user = await self.auth.get_or_create_by_telegram(self.cfg.api.dev_auto_login_telegram_id, "dev", "Dev")
        return user

    async def require_user(self, request: Request) -> User:
        user = await self._current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="auth required")
        return user

    async def require_admin(self, request: Request) -> User:
        user = await self.require_user(request)
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="admin required")
        return user

    async def _ws_user(self, ws: WebSocket) -> User | None:
        token = ws.cookies.get(self.cfg.api.cookie_name)
        user = await self.auth.user_by_session(token)
        if user is None and self.cfg.api.dev_auto_login and self.cfg.app.env == "dev":
            user = await self.auth.get_or_create_by_telegram(self.cfg.api.dev_auto_login_telegram_id, "dev", "Dev")
        return user

    async def _resubscribe_charts(self) -> None:
        by_source: dict[str, set[str]] = {sid: set() for sid in self.cfg.enabled_sources()}
        for keys in self.chart_subs.values():
            for k in keys:
                rec = self.state.records.get(k) or self.state.catalog_record(k) or {}
                sid = rec.get("source_route_id")
                if sid:
                    by_source.setdefault(sid, set()).add(k)
        for sid, keys in by_source.items():
            await self.bus.publish(f"cmd.{sid}", {"type": "chart_subscribe", "keys": sorted(keys)})

    async def _request_stock_listings(self) -> None:
        listings: set[str] = set()
        for s in self.stock_subs.values():
            listings |= s
        await self.bus.publish("cmd.stocks", {"type": "subscribe", "listings": sorted(listings)})

    def _history_from_worker(self, key: str, timeframe: str, limit: int, include_partial: bool) -> dict[str, Any] | None:
        w = self.state.worker_for(key)
        if w is None:
            return None
        if timeframe == "1s":
            bars = w.history_1s(key, limit)
            interval, retention = 1, self.cfg.storage.retention.bars_1s_hours
        else:
            bars = w.history_1m(key, limit)
            interval, retention = 60, self.cfg.storage.retention.bars_1m_hours
        out: dict[str, Any] = {"key": key, "interval_sec": interval, "retention_hours": retention, "bars": bars, "source_route_id": w.sid}
        if include_partial:
            out["partial"] = w.partial(key, timeframe)
        return out

    async def _history_from_storage(self, key: str, limit: int) -> dict[str, Any] | None:
        bars = await self.storage.read_bars_1m(key, int(time.time()) - self.cfg.storage.retention.bars_1m_hours * 3600, limit)
        if not bars:
            return None
        cat = self.state.catalog_record(key) or {}
        return {"key": key, "interval_sec": 60, "retention_hours": self.cfg.storage.retention.bars_1m_hours, "bars": [b.to_1m_dict() for b in bars],
                "partial": None, "source_route_id": cat.get("source_route_id")}

    # ------------------------------------------------------------------ routes
    def _routes(self) -> None:
        app = self.app
        cfg = self.cfg
        state = self.state
        require_user = self.require_user
        require_admin = self.require_admin

        # ---------------------------------------------------------- auth
        @app.get("/auth/login", response_class=HTMLResponse)
        async def auth_login(next: str = "/", ref: str | None = None) -> HTMLResponse:
            if not cfg.telegram.bot_username:
                return HTMLResponse("<h1>Telegram bot is not configured (telegram.bot_username)</h1>", status_code=503)
            token = await self.auth.start_login(next, ref)
            html = LOGIN_PAGE.replace("{token}", token).replace("{next}", next).replace("{bot}", cfg.telegram.bot_username)
            return HTMLResponse(html, headers={"cache-control": "no-store"})

        @app.get("/auth/pending/{token}")
        async def auth_pending(token: str, request: Request) -> Response:
            status, _ = await self.auth.token_status(token)
            if status == "not_found":
                return self._no_store({"status": "not_found"}, 404)
            if status == "expired":
                return self._no_store({"status": "expired"}, 410)
            if status == "completed":
                return self._no_store({"status": "completed"}, 409)
            if status == "authenticated":
                done = await self.auth.complete(token, request.headers.get("user-agent"))
                if done is None:
                    return self._no_store({"status": "pending", "poll_after_ms": 2000})
                session, next_url = done
                resp = self._no_store({"status": "authenticated", "redirect_url": next_url})
                resp.set_cookie(cfg.api.cookie_name, session, max_age=cfg.api.session_ttl_days * 86400, httponly=True, samesite="lax", secure=cfg.app.env == "prod")
                return resp
            return self._no_store({"status": "pending", "poll_after_ms": 2000})

        @app.post("/auth/logout")
        async def auth_logout(request: Request) -> Response:
            token = request.cookies.get(cfg.api.cookie_name)
            if token:
                await self.auth.logout(token)
            resp = self._no_store({"ok": True})
            resp.delete_cookie(cfg.api.cookie_name)
            return resp

        @app.get("/r/{code}")
        async def referral_link(code: str) -> Response:
            await self.auth.referral_click(code)
            return RedirectResponse(f"/auth/login?next=/&ref={code}", status_code=303)

        @app.post("/api/auth/ping")
        async def auth_ping(request: Request, user: User = Depends(require_user)) -> Response:
            await self.auth.touch(user.id)
            return self._no_store({"ok": True, "tracked_at": int(time.time()), "bypass": user.is_admin, "welcome_notice": await self._welcome_notice(user)})

        @app.get("/api/auth/settings-configs")
        async def settings_configs(user: User = Depends(require_user)) -> Response:
            items = await self.auth.list_configs(user.id)
            return self._no_store({"max_items": cfg.api.max_saved_configs, "count": len(items), "items": items})

        @app.post("/api/auth/settings-configs")
        async def settings_configs_save(request: Request, user: User = Depends(require_user)) -> Response:
            body = await request.json()
            try:
                item = await self.auth.save_config(user.id, str(body.get("title") or "config"), body.get("payload") or {}, cfg.api.max_saved_configs, body.get("id"))
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return self._no_store(item)

        @app.delete("/api/auth/settings-configs/{config_id}")
        async def settings_configs_delete(config_id: str, user: User = Depends(require_user)) -> Response:
            await self.auth.delete_config(user.id, config_id)
            return self._no_store({"ok": True})

        @app.get("/api/auth/me")
        async def auth_me(user: User = Depends(require_user)) -> Response:
            stats = await self.auth.referral_stats(user.referral_code)
            return self._no_store({
                "user_id": user.id, "nickname": user.nickname, "role": user.role, "plan": user.plan, "plan_until": user.plan_until,
                "plan_active": user.plan_active(time.time()), "modes": user.modes(), "referral_code": user.referral_code,
                "referral_link": f"{cfg.api.public_url.rstrip('/')}/r/{user.referral_code}", "referral": stats, "created_at": user.created_at,
                "telegram_username": user.telegram_username, "bonus_usdt": user.bonus_usdt,
            })

        @app.post("/api/auth/nickname")
        async def auth_nickname(request: Request, user: User = Depends(require_user)) -> Response:
            body = await request.json()
            nick = str(body.get("nickname") or "").strip()
            if not (3 <= len(nick) <= 24) or not all(c.isalnum() or c in "_-" for c in nick):
                raise HTTPException(status_code=400, detail="nickname must be 3-24 chars: letters, digits, _ and -")
            await self.auth.set_nickname(user.id, nick)
            return self._no_store({"ok": True, "nickname": nick})

        @app.get("/api/auth/welcome-notice/{revision}/content")
        async def welcome_content(revision: int, user: User = Depends(require_user)) -> Response:
            rows = await self.storage.fetch("SELECT html FROM welcome_notices WHERE revision=?", (revision,))
            if not rows:
                raise HTTPException(status_code=404, detail="Not Found")
            return self._no_store({"revision": revision, "html": rows[0][0]})

        @app.post("/api/auth/welcome-notice/{revision}/ack")
        async def welcome_ack(revision: int, user: User = Depends(require_user)) -> Response:
            await self.storage.execute("INSERT OR REPLACE INTO welcome_acks (user_id, revision, ts) VALUES (?,?,?)", (user.id, revision, time.time()))
            return self._no_store({"ok": True})

        # ---------------------------------------------------------- user settings (alerts, telegram, bans, favorites…)
        @app.get("/api/settings/{name}")
        async def get_setting(name: str, user: User = Depends(require_user)) -> Response:
            return self._no_store({"name": name, "payload": await self.auth.get_setting(user.id, name)})

        @app.put("/api/settings/{name}")
        async def put_setting(name: str, request: Request, user: User = Depends(require_user)) -> Response:
            await self.auth.set_setting(user.id, name, await request.json())
            return self._no_store({"ok": True})

        # ---------------------------------------------------------- client / help / feedback
        @app.get("/api/client_release")
        async def client_release(route: str | None = None, cv: str | None = None) -> Response:
            speech = await self.storage.kv_get("speech_text") or ""
            return self._no_store({"version": cfg.api.client_version, "poll_ms": cfg.api.client_release_poll_ms, "speech_text": speech})

        @app.get("/api/help-content/{key}")
        async def help_content(key: str) -> Response:
            rows = await self.storage.fetch("SELECT key, html, updated_at, updated_by FROM help_content WHERE key=?", (key,))
            if not rows:
                return self._no_store({"key": key, "html": "", "updated_at": 0, "updated_by": "", "is_default": True})
            r = rows[0]
            return self._no_store({"key": r[0], "html": r[1], "updated_at": r[2], "updated_by": r[3], "is_default": False})

        @app.put("/api/help-content/{key}")
        async def help_content_put(key: str, request: Request, user: User = Depends(require_admin)) -> Response:
            body = await request.json()
            await self.storage.execute("INSERT OR REPLACE INTO help_content (key, html, updated_at, updated_by) VALUES (?,?,?,?)",
                                       (key, str(body.get("html", "")), time.time(), user.nickname))
            return self._no_store({"ok": True})

        @app.post("/api/v1/feedback/reports")
        async def feedback(request: Request, user: User = Depends(require_user)) -> Response:
            payload = await request.json()
            rid = secrets.token_hex(8)
            await self.storage.execute("INSERT INTO feedback_reports (id, user_id, ts, payload) VALUES (?,?,?,?)", (rid, user.id, time.time(), orjson.dumps(payload).decode()))
            return self._no_store({"ok": True, "id": rid})

        # ---------------------------------------------------------- admin
        @app.get("/api/admin/welcome-notices")
        async def admin_notices(user: User = Depends(require_admin)) -> Response:
            rows = await self.storage.fetch("SELECT revision, html, created_at, active FROM welcome_notices ORDER BY revision DESC")
            return self._no_store({"items": [{"revision": r[0], "html": r[1], "created_at": r[2], "active": bool(r[3])} for r in rows]})

        @app.post("/api/admin/welcome-notices")
        async def admin_notice_create(request: Request, user: User = Depends(require_admin)) -> Response:
            body = await request.json()
            rows = await self.storage.fetch("SELECT COALESCE(MAX(revision),0) FROM welcome_notices")
            rev = int(rows[0][0]) + 1
            await self.storage.execute("INSERT INTO welcome_notices (revision, html, created_at, active) VALUES (?,?,?,1)", (rev, str(body.get("html", "")), time.time()))
            return self._no_store({"revision": rev})

        @app.post("/api/admin/speech")
        async def admin_speech(request: Request, user: User = Depends(require_admin)) -> Response:
            body = await request.json()
            await self.storage.kv_set("speech_text", str(body.get("text", "")))
            return self._no_store({"ok": True})

        @app.delete("/api/pairs/{key}")
        async def admin_pair_delete(key: str, user: User = Depends(require_admin)) -> Response:
            state.records.pop(key, None)
            return self._no_store({"ok": True})

        @app.post("/api/pairs/{key}/reset")
        async def admin_pair_reset(key: str, user: User = Depends(require_admin)) -> Response:
            w = state.worker_for(key)
            if w is not None:
                st = w.engine.get(key)
                if st is not None:
                    w.engine.remove_instrument(key)
                    w.engine.add_instrument(st.inst)
            return self._no_store({"ok": True})

        # ---------------------------------------------------------- market data
        @app.get("/api/all/v1/snapshot")
        async def all_snapshot(request: Request, user: User = Depends(require_user)) -> Response:
            q = request.query_params
            from .channels import _csv, _flag

            keys = state.select_keys(market_scope=q.get("market_scope", "all"), exclude_stocks=_flag(q.get("exclude_stocks")),
                                     source_futures=_csv(q.get("source_futures")), source_spot=_csv(q.get("source_spot")), route=q.get("route"))
            return self._no_store({"type": "snapshot", "version": 3, "seq": state.seq, "data": {k: state.records[k] for k in keys}})

        @app.get("/api/all/v1/keys")
        async def all_keys(request: Request, user: User = Depends(require_user)) -> Response:
            q = request.query_params
            from .channels import _csv, _flag

            keys = state.select_keys(market_scope=q.get("market_scope", "all"), exclude_stocks=_flag(q.get("exclude_stocks")),
                                     source_futures=_csv(q.get("source_futures")), source_spot=_csv(q.get("source_spot")), route=q.get("route"))
            return self._no_store({"keys": keys, "count": len(keys)})

        @app.get("/api/all/v1/summary_filter_metadata")
        @app.get("/api/v2/summary_filter_metadata")
        async def filter_metadata(window: str = "24h", include_partial: int = 1, route: str | None = None, user: User = Depends(require_user)) -> Response:
            fields = ("volume_24h", "tpm", "spike_ratio", "dvol5_pct", "natr5", "oi_percentage", "oi2_percentage", "buy_sum", "sell_sum", "price_change_pct")
            keys = state.select_keys(route=route)
            meta: dict[str, Any] = {}
            for f in fields:
                vals = [state.records[k].get(f) for k in keys if state.records[k].get(f) is not None]
                meta[f] = {"min": min(vals) if vals else None, "max": max(vals) if vals else None, "count": len(vals)}
            sources = sorted({state.records[k].get("source_route_id") for k in keys if state.records[k].get("source_route_id")})
            return self._no_store({"window": window, "fields": meta, "sources": sources, "count": len(keys), "updated_at": time.time()})

        @app.get("/api/simple/v1/pairs")
        @app.get("/api/v2/pairs")
        async def simple_pairs(route: str = "all_in_one", source: str | None = None, catalog_version: str | None = None, user: User = Depends(require_user)) -> Response:
            sources = {source} if source else None
            catalog = state.all_catalog(sources)
            version = "v1-" + __import__("hashlib").sha256(orjson.dumps(catalog, option=orjson.OPT_SORT_KEYS)).hexdigest()
            metrics = {k: simple_metrics_array(state.records[k]) for k in catalog if k in state.records}
            return self._no_store({"catalog_version": version, "catalog": None if catalog_version == version else catalog, "metrics": metrics, "coi": state.coi_snapshot})

        @app.get("/api/graph/v1/candidates")
        async def candidates(sort: str = "range5m", limit: int = 96, metric_filters: str | None = None, route: str | None = None, user: User = Depends(require_user)) -> Response:
            filters: dict[str, float] = {}
            if metric_filters:
                try:
                    filters = {k: float(v) for k, v in orjson.loads(metric_filters).items()}
                except (orjson.JSONDecodeError, ValueError, AttributeError):
                    filters = {}
            limit = min(limit, cfg.api.candidates_limit_max)
            keys = state.select_keys(route=route)
            items = []
            for k in keys:
                r = state.records[k]
                gm = r.get("graph_metrics") or [None] * 28
                ga = r.get("graph_activity") or [None] * 24
                metrics = _graph_metric_map(gm)
                if any((metrics.get(f) or 0) < thr for f, thr in filters.items()):
                    continue
                items.append({
                    "key": k, "exchange": r.get("exchange"), "pair": r.get("pair"), "market_type": r.get("market_type"), "display_symbol": r.get("display_symbol") or "",
                    "display_pair": r.get("display_pair"), "display_name": r.get("display_name") or "", "quote_currency": r.get("quote_currency"),
                    "contract_multiplier": r.get("contract_multiplier"), "source_route_id": r.get("source_route_id"), "volume_5m": metrics.get("volume5m"),
                    "range5_pct": r.get("range5_pct"), "natr5": r.get("natr5"), "dvol5_pct": r.get("dvol5_pct"), "dtpl5_pct": r.get("dtpl5_pct"),
                    "oi_percentage": r.get("oi_percentage"), "tpm": r.get("tpm"), "sort_value": metrics.get(sort) if sort in metrics else r.get(sort),
                    "graph_metrics": gm, "simple_24h_metrics": r.get("simple_24h_metrics"), "metric_activity": _activity_map(ga, metrics), "stock_scope": r.get("stock_scope"),
                })
            items.sort(key=lambda it: (it.get("sort_value") or 0), reverse=True)
            total = len(items)
            items = items[:limit]
            return self._no_store({"type": "graph_candidates", "sort": sort, "metric_filters": filters, "min_volume_5m": filters.get("volume5m", 0.0),
                                   "limit": limit, "count": len(items), "total_matched": total, "updated_at": time.time(), "items": items})

        @app.post("/api/graph/v1/history_batch")
        async def history_batch(request: Request, route: str | None = None, user: User = Depends(require_user)) -> Response:
            body = await request.json()
            items = body.get("items") or []
            include_partial = bool(body.get("include_partial", 1))
            max_items = cfg.api.history_max_items
            out: dict[str, Any] = {}
            errors: dict[str, str] = {}
            accepted = 0
            for it in items[:max_items]:
                key = str(it.get("key") or "")
                tf = str(it.get("timeframe") or "1m")
                limit = int(it.get("limit") or (cfg.api.history_1s_limit if tf == "1s" else cfg.api.history_1m_limit))
                limit = min(limit, cfg.api.history_1s_limit if tf == "1s" else cfg.api.history_1m_limit)
                res = self._history_from_worker(key, tf, limit, include_partial or bool(it.get("include_partial")))
                if res is None and tf == "1m":
                    res = await self._history_from_storage(key, limit)
                if res is None:
                    errors[f"{key}|{tf}"] = "unknown key"
                    continue
                accepted += 1
                out[f"{key}|{tf}"] = res
            return self._no_store({"type": "graph_history_batch", "count": len(out), "received": len(items), "max_items": max_items, "accepted": accepted,
                                   "omitted": max(0, len(items) - max_items), "truncated": len(items) > max_items, "error_count": len(errors), "limit_reason": "",
                                   "items": out, "errors": errors})

        @app.get("/api/history/{kind}/{key:path}")
        async def history_one(kind: str, key: str, limit: int | None = None, include_partial: int = 1, user: User = Depends(require_user)) -> Response:
            tf = "1s" if kind == "seconds" else "1m"
            lim = min(limit or (cfg.api.history_1s_limit if tf == "1s" else cfg.api.history_1m_limit), cfg.api.history_1s_limit if tf == "1s" else cfg.api.history_1m_limit)
            res = self._history_from_worker(key, tf, lim, bool(include_partial))
            if res is None and tf == "1m":
                res = await self._history_from_storage(key, lim)
            if res is None:
                raise HTTPException(status_code=404, detail="Not Found")
            return self._no_store(res)

        @app.post("/api/marketstore/clusters/snapshot_batch")
        async def clusters_batch(request: Request, route: str | None = None, user: User = Depends(require_user)) -> Response:
            body = await request.json()
            items = body.get("items") or []
            out: dict[str, Any] = {}
            errors: dict[str, str] = {}
            for it in items[: cfg.api.history_max_items]:
                rid = str(it.get("request_id") or it.get("key"))
                res = self._cluster_snapshot(it)
                if res is None:
                    errors[rid] = "unknown key"
                else:
                    out[rid] = res
            return self._no_store({"type": "marketstore_cluster_batch", "count": len(out), "received": len(items), "max_items": cfg.api.history_max_items,
                                   "accepted": len(out), "omitted": max(0, len(items) - cfg.api.history_max_items), "truncated": len(items) > cfg.api.history_max_items,
                                   "error_count": len(errors), "limit_reason": "", "items": out, "errors": errors})

        @app.get("/api/marketstore/clusters/snapshot/{key:path}")
        async def cluster_one(key: str, timeframe: str = "1h", zoom: int = 1, price_step: float | None = None, user: User = Depends(require_user)) -> Response:
            res = self._cluster_snapshot({"key": key, "timeframe": timeframe, "zoom": zoom, "price_step": price_step})
            if res is None:
                raise HTTPException(status_code=404, detail="Not Found")
            return self._no_store(res)

        @app.get("/api/chart-alerts/v1")
        async def chart_alerts(route: str | None = None, user: User = Depends(require_user)) -> Response:
            saved = await self.auth.get_setting(user.id, f"chart_alerts:{route or 'all'}") or {}
            return self._no_store({"type": "chart_signal_alerts", "max_active": cfg.api.chart_alerts.max_active, "max_active_per_key": cfg.api.chart_alerts.max_active_per_key,
                                   "alerts": saved.get("alerts", [])})

        @app.put("/api/chart-alerts/v1")
        async def chart_alerts_put(request: Request, route: str | None = None, user: User = Depends(require_user)) -> Response:
            body = await request.json()
            alerts = list(body.get("alerts") or [])
            if len(alerts) > cfg.api.chart_alerts.max_active:
                raise HTTPException(status_code=409, detail=f"max {cfg.api.chart_alerts.max_active} active alerts")
            per_key: dict[str, int] = {}
            for a in alerts:
                k = str(a.get("key"))
                per_key[k] = per_key.get(k, 0) + 1
                if per_key[k] > cfg.api.chart_alerts.max_active_per_key:
                    raise HTTPException(status_code=409, detail=f"max {cfg.api.chart_alerts.max_active_per_key} alerts per key")
            await self.auth.set_setting(user.id, f"chart_alerts:{route or 'all'}", {"alerts": alerts})
            return self._no_store({"ok": True, "count": len(alerts)})

        @app.get("/api/v2/lighter/market-id/{symbol}")
        async def lighter_market_id(symbol: str, route: str | None = None, user: User = Depends(require_user)) -> Response:
            cat = state.catalogs.get("lighter_futures", {})
            for key, rec in cat.items():
                if rec.get("pair", "").upper() == symbol.upper():
                    return self._no_store({"symbol": symbol, "market_id": key.split(":")[1] if False else (state.records.get(key) or {}).get("base_asset_id") or (rec.get("base_asset_id")), "key": key})
            raise HTTPException(status_code=404, detail="Not Found")

        # ---------------------------------------------------------- teams
        @app.get("/api/teams")
        async def teams(route: str | None = None, user: User = Depends(require_user)) -> Response:
            rows = await self.storage.fetch("SELECT t.id, t.title, t.owner_id, t.invite_code FROM teams t JOIN team_members m ON m.team_id=t.id WHERE m.user_id=?", (user.id,))
            prefs = await self.auth.get_setting(user.id, "team_prefs") or {"voice": 0, "mobile": 0, "signal_mode": "simple"}
            history: list[dict[str, Any]] = []
            for r in rows:
                sig = await self.storage.fetch("SELECT payload FROM team_signals WHERE team_id=? ORDER BY ts DESC LIMIT 50", (r[0],))
                history.extend(orjson.loads(s[0]) for s in sig)
            return self._no_store({"teams": [{"id": r[0], "title": r[1], "owner_id": r[2], "invite_code": r[3] if r[2] == user.id else None} for r in rows],
                                   "preferences": prefs, "history": history, "user_id": user.id})

        @app.post("/api/teams/actions/{action}")
        async def team_action(action: str, request: Request, route: str | None = None, user: User = Depends(require_user)) -> Response:
            body = await request.json()
            now = time.time()
            if action == "create":
                tid, code = secrets.token_hex(8), secrets.token_hex(4).upper()
                await self.storage.execute("INSERT INTO teams (id, title, owner_id, invite_code, created_at) VALUES (?,?,?,?,?)", (tid, str(body.get("title") or "Team"), user.id, code, now))
                await self.storage.execute("INSERT INTO team_members (team_id, user_id, joined_at) VALUES (?,?,?)", (tid, user.id, now))
                return self._no_store({"ok": True, "id": tid, "invite_code": code})
            if action == "join":
                rows = await self.storage.fetch("SELECT id FROM teams WHERE invite_code=?", (str(body.get("invite_code") or "").upper(),))
                if not rows:
                    raise HTTPException(status_code=404, detail="team not found")
                await self.storage.execute("INSERT OR REPLACE INTO team_members (team_id, user_id, joined_at) VALUES (?,?,?)", (rows[0][0], user.id, now))
                return self._no_store({"ok": True, "id": rows[0][0]})
            if action == "leave":
                await self.storage.execute("DELETE FROM team_members WHERE team_id=? AND user_id=?", (str(body.get("id")), user.id))
                return self._no_store({"ok": True})
            if action == "preferences":
                await self.auth.set_setting(user.id, "team_prefs", {k: body.get(k) for k in ("voice", "mobile", "signal_mode")})
                return self._no_store({"ok": True})
            if action == "signal":
                tid = str(body.get("team_id"))
                member = await self.storage.fetch("SELECT 1 FROM team_members WHERE team_id=? AND user_id=?", (tid, user.id))
                if not member:
                    raise HTTPException(status_code=403, detail="not a member")
                sig = {"id": secrets.token_hex(8), "team_id": tid, "user_id": user.id, "nickname": user.nickname, "ts": now, **{k: v for k, v in body.items() if k != "team_id"}}
                await self.storage.execute("INSERT INTO team_signals (id, team_id, user_id, ts, payload) VALUES (?,?,?,?,?)", (sig["id"], tid, user.id, now, orjson.dumps(sig).decode()))
                for q in self.team_hubs.get(tid, []):
                    if not q.full():
                        q.put_nowait(sig)
                return self._no_store({"ok": True, "id": sig["id"]})
            raise HTTPException(status_code=404, detail="Not Found")

        # ---------------------------------------------------------- mobile (FCM)
        @app.post("/api/mobile-alerts/{action}")
        async def mobile(action: str, request: Request, user: User = Depends(require_user)) -> Response:
            body = await request.json() if await request.body() else {}
            now = time.time()
            if action == "code":
                code = f"{secrets.randbelow(10**8):08d}"
                await self.storage.execute("INSERT INTO mobile_devices (code, user_id, fcm_token, created_at, paired_at) VALUES (?,?,?,?,?)", (code, user.id, None, now, None))
                return self._no_store({"code": code, "ttl_sec": cfg.fcm.pairing_code_ttl_sec})
            if action == "pair":  # called by the Android app with the code and its FCM token
                rows = await self.storage.fetch("SELECT user_id, created_at FROM mobile_devices WHERE code=?", (str(body.get("code")),))
                if not rows or now - rows[0][1] > cfg.fcm.pairing_code_ttl_sec:
                    raise HTTPException(status_code=410, detail="code expired")
                await self.storage.execute("UPDATE mobile_devices SET fcm_token=?, paired_at=? WHERE code=?", (str(body.get("fcm_token")), now, str(body.get("code"))))
                return self._no_store({"ok": True})
            if action == "devices":
                rows = await self.storage.fetch("SELECT code, paired_at FROM mobile_devices WHERE user_id=? AND paired_at IS NOT NULL", (user.id,))
                return self._no_store({"devices": [{"code": r[0], "paired_at": r[1]} for r in rows]})
            if action == "unpair":
                await self.storage.execute("DELETE FROM mobile_devices WHERE code=? AND user_id=?", (str(body.get("code")), user.id))
                return self._no_store({"ok": True})
            if action == "send":  # browser → server → FCM
                from scryner.feeds.fcm import send_push

                rows = await self.storage.fetch("SELECT fcm_token FROM mobile_devices WHERE user_id=? AND fcm_token IS NOT NULL", (user.id,))
                sent = 0
                for r in rows:
                    if await send_push(cfg.fcm, r[0], str(body.get("title") or "Scryner"), str(body.get("text") or ""), body.get("data") or {}):
                        sent += 1
                return self._no_store({"ok": True, "sent": sent})
            raise HTTPException(status_code=404, detail="Not Found")

        # ---------------------------------------------------------- websockets
        async def ws_guard(ws: WebSocket) -> User | None:
            user = await self._ws_user(ws)
            if user is None:
                await ws.close(code=4401)
                return None
            await ws.accept()
            return user

        @app.websocket("/ws/all/v1")
        @app.websocket("/ws/v3")
        async def ws_all(ws: WebSocket) -> None:
            if await ws_guard(ws) is None:
                return
            conn = WsConn(ws)
            ch = TopChannel(state, conn, patch_interval=cfg.api.patch_interval_sec)
            if ch.limit == 0 and ws.url.path.endswith("/all/v1"):
                ch.limit = cfg.api.top_limit_default
            ch.limit = min(ch.limit, cfg.api.top_limit_max) if ch.limit else 0
            await ch.run(wait_subscribe=ws.url.path.endswith("/all/v1"))

        @app.websocket("/ws/all/alerts/v1")
        async def ws_alerts(ws: WebSocket) -> None:
            if await ws_guard(ws) is None:
                return
            conn = WsConn(ws, gzip_frames=ws.query_params.get("frame_encoding") == "gzip")
            await AlertsChannel(state, conn, cfg.api.patch_interval_sec, cfg.api.alerts_limit_max).run()

        @app.websocket("/ws/tick")
        async def ws_tick(ws: WebSocket) -> None:
            if await ws_guard(ws) is None:
                return
            await TickChannel(state, WsConn(ws)).run()

        @app.websocket("/ws/active_twaps")
        async def ws_twaps(ws: WebSocket) -> None:
            if await ws_guard(ws) is None:
                return
            await TwapChannel(state, WsConn(ws)).run()

        def feed_counter(name: str) -> Any:
            return lambda: self.feed_clients.get(name, 0)

        async def run_feed(ws: WebSocket, name: str, history: Any, render: Any, kinds: tuple[str, ...], density: bool = False) -> None:
            if await ws_guard(ws) is None:
                return
            self.feed_clients[name] = self.feed_clients.get(name, 0) + 1
            try:
                size = cfg.density.history_size if density else (cfg.bots.history_size if name == "bots" else cfg.telegram.history_size)
                await FeedChannel(state, WsConn(ws), name, history, render, kinds, feed_counter(name), size, density_mode=density).run()
            finally:
                self.feed_clients[name] -= 1

        @app.websocket("/ws/density/feed")
        async def ws_density(ws: WebSocket) -> None:
            await run_feed(ws, "density", state.densities, render_density, ("density", "density_impact", "density_removed"), density=True)

        @app.websocket("/ws/summary_bots")
        async def ws_bots(ws: WebSocket) -> None:
            await run_feed(ws, "bots", state.bots, render_bot, ("bots",))

        @app.websocket("/ws/summary_news")
        async def ws_news(ws: WebSocket) -> None:
            await run_feed(ws, "news", state.news["news"], render_feed, ("feed.news",))

        @app.websocket("/ws/summary_onchain")
        async def ws_onchain(ws: WebSocket) -> None:
            await run_feed(ws, "onchain", state.news["onchain"], render_feed, ("feed.onchain",))

        @app.websocket("/ws/summary_listings")
        async def ws_listings(ws: WebSocket) -> None:
            await run_feed(ws, "listings", state.news["listings"], render_feed, ("feed.listings",))

        @app.websocket("/ws/graph_charts/v1")
        async def ws_charts(ws: WebSocket) -> None:
            if await ws_guard(ws) is None:
                return
            cid = id(ws)
            ch = ChartChannel(state, WsConn(ws), resubscribe=lambda: self._chart_resub(cid, ch))
            try:
                await ch.run()
            finally:
                self.chart_subs.pop(cid, None)
                await self._resubscribe_charts()

        @app.websocket("/ws/stock/reference-prices/v1")
        async def ws_stocks(ws: WebSocket) -> None:
            if await ws_guard(ws) is None:
                return
            cid = id(ws)

            async def req(listings: set[str]) -> None:
                self.stock_subs[cid] = listings
                await self._request_stock_listings()

            try:
                await StockQuoteChannel(state, WsConn(ws), req).run()
            finally:
                self.stock_subs.pop(cid, None)
                await self._request_stock_listings()

        @app.websocket("/ws/team-signals")
        async def ws_team(ws: WebSocket) -> None:
            user = await ws_guard(ws)
            if user is None:
                return
            conn = WsConn(ws)
            rows = await self.storage.fetch("SELECT team_id FROM team_members WHERE user_id=?", (user.id,))
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
            tids = [r[0] for r in rows]
            for tid in tids:
                self.team_hubs.setdefault(tid, []).append(queue)
            reader = asyncio.create_task(conn.recv_loop())
            try:
                await conn.send({"type": "hello", "teams": tids})
                while not conn.closed:
                    try:
                        sig = await asyncio.wait_for(queue.get(), timeout=30)
                    except TimeoutError:
                        continue
                    await conn.send({"type": "signal", **sig})
            finally:
                for tid in tids:
                    lst = self.team_hubs.get(tid, [])
                    if queue in lst:
                        lst.remove(queue)
                reader.cancel()

        # ---------------------------------------------------------- health + static client
        @app.get("/healthz")
        async def healthz() -> Response:
            return self._no_store({"ok": True, "records": len(state.records), "sources": sorted(state.catalogs), "uptime_sec": int(time.time() - state.started_at)})

        web_dir = Path(cfg.api.web_dir)
        if web_dir.exists():
            app.mount("/static", StaticFiles(directory=str(web_dir / "static")), name="static") if (web_dir / "static").exists() else None

            @app.get("/{page:path}", response_class=HTMLResponse)
            async def spa(page: str, request: Request) -> Response:
                user = await self._current_user(request)
                if user is None and not page.startswith("auth/"):
                    return RedirectResponse(f"/auth/login?next={request.url.path}", status_code=303)
                index = web_dir / "index.html"
                return HTMLResponse(index.read_text(encoding="utf-8"), headers={"cache-control": "no-store"})

    async def _chart_resub(self, cid: int, ch: ChartChannel) -> None:
        if ch.keys:
            self.chart_subs[cid] = set(ch.keys)
        else:
            self.chart_subs.pop(cid, None)
        await self._resubscribe_charts()

    def _cluster_snapshot(self, it: dict[str, Any]) -> dict[str, Any] | None:
        key = str(it.get("key") or "")
        w = self.state.worker_for(key)
        rec = self.state.records.get(key) or {}
        if w is None:
            return None
        step = it.get("price_step")
        zoom = int(it.get("zoom") or 1)
        cols = w.clusters.snapshot(key, float(step) if step else None, zoom=zoom, columns=int(it.get("columns") or 1))
        from datetime import UTC, datetime

        return {
            "key": key, "source": "marketstore_clusters", "exchange": rec.get("exchange"), "market": rec.get("market_type"), "symbol": rec.get("pair"),
            "TimeFrame": it.get("timeframe", "1h"), "timeframe": it.get("timeframe", "1h"), "ZoomIndex": zoom, "zoom": zoom,
            "PriceIncrement": w.clusters.increment(key), "updated_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "Columns": cols, "source_route_id": w.sid,
        }

    async def _welcome_notice(self, user: User) -> dict[str, Any] | None:
        rows = await self.storage.fetch("SELECT revision FROM welcome_notices WHERE active=1 ORDER BY revision DESC LIMIT 1")
        if not rows:
            return None
        rev = int(rows[0][0])
        ack = await self.storage.fetch("SELECT 1 FROM welcome_acks WHERE user_id=? AND revision=?", (user.id, rev))
        return None if ack else {"revision": rev}

    # ------------------------------------------------------------------ serve
    async def serve(self, stop: asyncio.Event) -> None:
        await self.auth.init()
        await self.state.start()
        config = uvicorn.Config(self.app, host=self.cfg.api.host, port=self.cfg.api.port, log_level="warning", ws_ping_interval=self.cfg.api.ws_ping_sec,
                                ws_ping_timeout=self.cfg.api.ws_ping_sec * 2, proxy_headers=True, forwarded_allow_ips="*")
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        task = asyncio.create_task(server.serve())
        log.info("gateway_listening", host=self.cfg.api.host, port=self.cfg.api.port)
        await stop.wait()
        server.should_exit = True
        await task
        await self.state.stop()


GRAPH_METRIC_NAMES = [
    "range1m", "natr1m", "volume1m", "oi1m", "dvol1m", "dtpl1m", "tpm1m",
    "range5m", "natr5m", "volume5m", "oi5m", "dvol5m", "dtpl5m", "tpm5m",
    "range15m", "natr15m", "volume15m", "oi15m", "dvol15m", "dtpl15m", "tpm15m",
    "range1h", "natr1h", "volume1h", "oi1h", "dvol1h", "dtpl1h", "tpm1h",
]
ACTIVITY_NAMES = ["range", "volume", "oi", "dvol", "dtpl", "tpm"]


def _graph_metric_map(gm: list[Any]) -> dict[str, Any]:
    return {name: gm[i] if i < len(gm) else None for i, name in enumerate(GRAPH_METRIC_NAMES)}


def _activity_map(ga: list[Any], metrics: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    windows = ["1m", "5m", "15m", "1h"]
    for wi, w in enumerate(windows):
        for si, name in enumerate(ACTIVITY_NAMES):
            idx = wi * len(ACTIVITY_NAMES) + si
            v = ga[idx] if idx < len(ga) else None
            if v is not None:
                out[f"{name}{w}"] = v
    return out


__all__ = ["Gateway", "JSONResponse", "Query", "os"]
