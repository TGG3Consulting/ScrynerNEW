"""Users, sessions and Telegram deep-link login.

Flow (same as the reference):
1. ``GET /auth/login`` renders the login page with ``token`` (32 chars) and the deep link
   ``https://t.me/<bot>?start=login_<token>``; the token is stored as *pending*.
2. The Telegram bot receives ``/start login_<token>`` from a user, calls
   :meth:`AuthService.confirm` with the Telegram id; the token becomes *authenticated*.
3. The page polls ``GET /auth/pending/<token>`` every 2 s; on *authenticated* the response
   sets the session cookie and returns ``redirect_url``; the token is then *completed*.

Users get a trial plan on first login (``plans.trial_days``). All state lives in the storage
(tables ``users``, ``sessions``, ``login_tokens``, ``user_settings``).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any

import orjson

from scryner.core.config import ApiConfig
from scryner.storage import Storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, telegram_id INTEGER UNIQUE, telegram_username TEXT, nickname TEXT, role TEXT NOT NULL DEFAULT 'user',
    plan TEXT NOT NULL DEFAULT 'trial', plan_until REAL, referral_code TEXT UNIQUE, referred_by TEXT, created_at REAL NOT NULL,
    last_seen REAL, bonus_usdt REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, user_agent TEXT
);
CREATE TABLE IF NOT EXISTS login_tokens (
    token TEXT PRIMARY KEY, status TEXT NOT NULL, created_at REAL NOT NULL, next_url TEXT, user_id TEXT, referral TEXT
);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY (user_id, name)
);
CREATE TABLE IF NOT EXISTS user_configs (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS referral_events (
    id TEXT PRIMARY KEY, code TEXT NOT NULL, kind TEXT NOT NULL, ts REAL NOT NULL, user_id TEXT, amount REAL
);
CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, owner_id TEXT NOT NULL, invite_code TEXT UNIQUE, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS team_members (team_id TEXT NOT NULL, user_id TEXT NOT NULL, joined_at REAL NOT NULL, PRIMARY KEY (team_id, user_id));
CREATE TABLE IF NOT EXISTS team_signals (
    id TEXT PRIMARY KEY, team_id TEXT NOT NULL, user_id TEXT NOT NULL, ts REAL NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mobile_devices (
    code TEXT PRIMARY KEY, user_id TEXT NOT NULL, fcm_token TEXT, created_at REAL NOT NULL, paired_at REAL
);
CREATE TABLE IF NOT EXISTS welcome_notices (revision INTEGER PRIMARY KEY, html TEXT NOT NULL, created_at REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS welcome_acks (user_id TEXT NOT NULL, revision INTEGER NOT NULL, ts REAL NOT NULL, PRIMARY KEY (user_id, revision));
CREATE TABLE IF NOT EXISTS help_content (key TEXT PRIMARY KEY, html TEXT NOT NULL, updated_at REAL NOT NULL, updated_by TEXT);
CREATE TABLE IF NOT EXISTS feedback_reports (id TEXT PRIMARY KEY, user_id TEXT, ts REAL NOT NULL, payload TEXT NOT NULL);
"""

MODES_BY_PLAN = {"trial": ["SIMPLE", "ADVANCED", "GRAPH", "TICK", "TRADES"], "pro": ["SIMPLE", "ADVANCED", "GRAPH", "TICK", "TRADES"], "free": ["SIMPLE"]}


@dataclass(slots=True)
class User:
    id: str
    telegram_id: int | None
    telegram_username: str | None
    nickname: str
    role: str
    plan: str
    plan_until: float | None
    referral_code: str
    referred_by: str | None
    created_at: float
    bonus_usdt: float = 0.0

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def plan_active(self, now: float) -> bool:
        return self.plan_until is None or self.plan_until > now

    def modes(self) -> list[str]:
        return MODES_BY_PLAN.get(self.plan, MODES_BY_PLAN["free"])


def _user(row: tuple[Any, ...]) -> User:
    return User(id=row[0], telegram_id=row[1], telegram_username=row[2], nickname=row[3], role=row[4], plan=row[5], plan_until=row[6],
                referral_code=row[7], referred_by=row[8], created_at=row[9], bonus_usdt=row[11] or 0.0)


USER_COLS = "id, telegram_id, telegram_username, nickname, role, plan, plan_until, referral_code, referred_by, created_at, last_seen, bonus_usdt"


class AuthService:
    def __init__(self, storage: Storage, cfg: ApiConfig, trial_days: int = 3, admin_telegram_ids: tuple[int, ...] = ()) -> None:
        self.storage = storage
        self.cfg = cfg
        self.trial_days = trial_days
        self.admin_ids = set(admin_telegram_ids)

    async def init(self) -> None:
        for stmt in SCHEMA.split(";"):
            s = stmt.strip()
            if s:
                await self.storage.execute(s)

    # ------------------------------------------------------------------ login tokens
    async def start_login(self, next_url: str, referral: str | None = None) -> str:
        token = secrets.token_urlsafe(24)[:32]
        await self.storage.execute("INSERT INTO login_tokens (token, status, created_at, next_url, user_id, referral) VALUES (?,?,?,?,?,?)",
                                   (token, "pending", time.time(), next_url, None, referral))
        return token

    async def token_status(self, token: str) -> tuple[str, dict[str, Any] | None]:
        rows = await self.storage.fetch("SELECT token, status, created_at, next_url, user_id FROM login_tokens WHERE token=?", (token,))
        if not rows:
            return "not_found", None
        _t, status, created, next_url, user_id = rows[0]
        if status == "pending" and time.time() - created > self.cfg.login_token_ttl_sec:
            return "expired", None
        return status, {"next_url": next_url, "user_id": user_id}

    async def confirm(self, token: str, telegram_id: int, username: str | None, first_name: str | None = None) -> bool:
        status, _info = await self.token_status(token)
        if status != "pending":
            return False
        user = await self.get_or_create_by_telegram(telegram_id, username, first_name)
        rows = await self.storage.fetch("SELECT referral FROM login_tokens WHERE token=?", (token,))
        ref = rows[0][0] if rows else None
        if ref and user.referred_by is None and ref != user.referral_code:
            await self.storage.execute("UPDATE users SET referred_by=? WHERE id=? AND referred_by IS NULL", (ref, user.id))
            await self.storage.execute("INSERT INTO referral_events (id, code, kind, ts, user_id, amount) VALUES (?,?,?,?,?,?)",
                                       (secrets.token_hex(8), ref, "registration", time.time(), user.id, None))
        await self.storage.execute("UPDATE login_tokens SET status='authenticated', user_id=? WHERE token=?", (user.id, token))
        return True

    async def complete(self, token: str, user_agent: str | None) -> tuple[str, str] | None:
        """Turn an authenticated token into a session: returns (session_token, next_url)."""
        status, info = await self.token_status(token)
        if status != "authenticated" or not info or not info.get("user_id"):
            return None
        session = secrets.token_urlsafe(32)
        now = time.time()
        await self.storage.execute("INSERT INTO sessions (token, user_id, created_at, expires_at, user_agent) VALUES (?,?,?,?,?)",
                                   (session, info["user_id"], now, now + self.cfg.session_ttl_days * 86400, user_agent))
        await self.storage.execute("UPDATE login_tokens SET status='completed' WHERE token=?", (token,))
        return session, info.get("next_url") or "/"

    # ------------------------------------------------------------------ users
    async def get_or_create_by_telegram(self, telegram_id: int, username: str | None, first_name: str | None = None) -> User:
        rows = await self.storage.fetch(f"SELECT {USER_COLS} FROM users WHERE telegram_id=?", (telegram_id,))
        if rows:
            u = _user(rows[0])
            if username and username != u.telegram_username:
                await self.storage.execute("UPDATE users SET telegram_username=? WHERE id=?", (username, u.id))
            return u
        now = time.time()
        uid = "GEU-" + secrets.token_hex(16).upper()
        nickname = f"Trader_{secrets.token_hex(8).upper()}"
        code = secrets.token_hex(4).upper()
        role = "admin" if telegram_id in self.admin_ids else "user"
        await self.storage.execute(
            "INSERT INTO users (id, telegram_id, telegram_username, nickname, role, plan, plan_until, referral_code, referred_by, created_at, last_seen, bonus_usdt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, telegram_id, username, nickname, role, "trial", now + self.trial_days * 86400, code, None, now, now, 0.0),
        )
        rows = await self.storage.fetch(f"SELECT {USER_COLS} FROM users WHERE id=?", (uid,))
        return _user(rows[0])

    async def user_by_session(self, session: str | None) -> User | None:
        if not session:
            return None
        rows = await self.storage.fetch(
            f"SELECT {USER_COLS} FROM users WHERE id=(SELECT user_id FROM sessions WHERE token=? AND expires_at>?)", (session, time.time()))
        return _user(rows[0]) if rows else None

    async def user_by_id(self, uid: str) -> User | None:
        rows = await self.storage.fetch(f"SELECT {USER_COLS} FROM users WHERE id=?", (uid,))
        return _user(rows[0]) if rows else None

    async def touch(self, uid: str) -> None:
        await self.storage.execute("UPDATE users SET last_seen=? WHERE id=?", (time.time(), uid))

    async def set_nickname(self, uid: str, nickname: str) -> None:
        await self.storage.execute("UPDATE users SET nickname=? WHERE id=?", (nickname, uid))

    async def logout(self, session: str) -> None:
        await self.storage.execute("DELETE FROM sessions WHERE token=?", (session,))

    # ------------------------------------------------------------------ settings / configs
    async def get_setting(self, uid: str, name: str) -> dict[str, Any] | None:
        rows = await self.storage.fetch("SELECT payload FROM user_settings WHERE user_id=? AND name=?", (uid, name))
        return orjson.loads(rows[0][0]) if rows else None

    async def set_setting(self, uid: str, name: str, payload: dict[str, Any]) -> None:
        await self.storage.execute("DELETE FROM user_settings WHERE user_id=? AND name=?", (uid, name))
        await self.storage.execute("INSERT INTO user_settings (user_id, name, payload, updated_at) VALUES (?,?,?,?)", (uid, name, orjson.dumps(payload).decode(), time.time()))

    async def list_configs(self, uid: str) -> list[dict[str, Any]]:
        rows = await self.storage.fetch("SELECT id, title, payload, created_at, updated_at FROM user_configs WHERE user_id=? ORDER BY updated_at DESC", (uid,))
        return [{"id": r[0], "title": r[1], "payload": orjson.loads(r[2]), "created_at": r[3], "updated_at": r[4]} for r in rows]

    async def save_config(self, uid: str, title: str, payload: dict[str, Any], max_items: int, config_id: str | None = None) -> dict[str, Any]:
        now = time.time()
        if config_id:
            await self.storage.execute("UPDATE user_configs SET title=?, payload=?, updated_at=? WHERE id=? AND user_id=?", (title, orjson.dumps(payload).decode(), now, config_id, uid))
            return {"id": config_id, "title": title, "updated_at": now}
        rows = await self.storage.fetch("SELECT COUNT(*) FROM user_configs WHERE user_id=?", (uid,))
        if rows and rows[0][0] >= max_items:
            raise ValueError("config limit reached")
        cid = secrets.token_hex(8)
        await self.storage.execute("INSERT INTO user_configs (id, user_id, title, payload, created_at, updated_at) VALUES (?,?,?,?,?,?)", (cid, uid, title, orjson.dumps(payload).decode(), now, now))
        return {"id": cid, "title": title, "updated_at": now}

    async def delete_config(self, uid: str, config_id: str) -> None:
        await self.storage.execute("DELETE FROM user_configs WHERE id=? AND user_id=?", (config_id, uid))

    # ------------------------------------------------------------------ referrals
    async def referral_stats(self, code: str) -> dict[str, Any]:
        rows = await self.storage.fetch("SELECT kind, ts, amount FROM referral_events WHERE code=?", (code,))
        day_ago = time.time() - 86400
        stats = {"invited": 0, "paid": 0, "payments": 0, "bonus": 0.0, "clicks_all": 0, "clicks_day": 0, "registrations_all": 0, "registrations_day": 0}
        paid_users: set[str] = set()
        for kind, ts, amount in rows:
            if kind == "click":
                stats["clicks_all"] += 1
                stats["clicks_day"] += int(ts >= day_ago)
            elif kind == "registration":
                stats["invited"] += 1
                stats["registrations_all"] += 1
                stats["registrations_day"] += int(ts >= day_ago)
            elif kind == "payment":
                stats["payments"] += 1
                stats["bonus"] += float(amount or 0)
        stats["paid"] = len(paid_users)
        return stats

    async def referral_click(self, code: str) -> None:
        await self.storage.execute("INSERT INTO referral_events (id, code, kind, ts, user_id, amount) VALUES (?,?,?,?,?,?)", (secrets.token_hex(8), code, "click", time.time(), None, None))
