"""Telegram Bot API client (long polling, no extra dependencies).

Responsibilities:
* ``/start login_<token>`` → confirm the login token (:class:`AuthService.confirm`)
* ``/start ref_<code>`` → remember the referral for the next login
* outgoing notifications for users who connected their own bot are sent from the browser
  (reference behaviour); this bot additionally serves server-side notifications
  (team signals, admin broadcasts) via :meth:`send_message`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from scryner.api.auth import AuthService
from scryner.core.config import TelegramConfig
from scryner.core.logging import get_logger


class TelegramBot:
    def __init__(self, cfg: TelegramConfig, auth: AuthService) -> None:
        self.cfg = cfg
        self.auth = auth
        self.log = get_logger("tgbot")
        self.base = f"https://api.telegram.org/bot{cfg.bot_token}"
        self._session: aiohttp.ClientSession | None = None
        self.username: str = cfg.bot_username

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.cfg.bot_poll_timeout_sec + 15), trust_env=True)
        return self._session

    async def call(self, method: str, **params: Any) -> Any:
        s = await self.session()
        async with s.post(f"{self.base}/{method}", json=params) as resp:
            data = await resp.json(content_type=None)
            if not data.get("ok"):
                raise RuntimeError(f"telegram {method}: {data}")
            return data["result"]

    async def send_message(self, chat_id: int | str, text: str, **kw: Any) -> None:
        await self.call("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True, **kw)

    async def run(self) -> None:
        if not self.cfg.bot_token:
            self.log.warning("telegram_bot_disabled", reason="no bot_token")
            return
        me = await self.call("getMe")
        self.username = me.get("username", self.username)
        self.log.info("telegram_bot_started", username=self.username)
        offset = 0
        while True:
            try:
                updates = await self.call("getUpdates", offset=offset, timeout=self.cfg.bot_poll_timeout_sec, allowed_updates=["message"])
            except (TimeoutError, aiohttp.ClientError, RuntimeError) as exc:
                self.log.warning("telegram_poll_error", error=str(exc)[:200])
                await asyncio.sleep(3)
                continue
            for u in updates:
                offset = max(offset, int(u["update_id"]) + 1)
                try:
                    await self._handle(u.get("message") or {})
                except Exception as exc:
                    self.log.warning("telegram_handle_error", error=str(exc)[:200])

    async def _handle(self, msg: dict[str, Any]) -> None:
        text = str(msg.get("text") or "")
        chat = msg.get("chat") or {}
        frm = msg.get("from") or {}
        if not text.startswith("/start"):
            if text.startswith("/id"):
                await self.send_message(chat["id"], f"chat_id: {chat['id']}")
            return
        arg = text.split(maxsplit=1)[1].strip() if " " in text else ""
        if arg.startswith("login_"):
            token = arg[len("login_"):]
            ok = await self.auth.confirm(token, int(frm.get("id")), frm.get("username"), frm.get("first_name"))
            await self.send_message(chat["id"], "✅ Вход подтверждён. Вернитесь в браузер." if ok else "⚠️ Токен входа недействителен или истёк. Обновите страницу входа.")
        elif arg.startswith("ref_"):
            await self.send_message(chat["id"], "Реферальный код сохранён. Откройте сайт и войдите через бота.")
        else:
            await self.send_message(chat["id"], f"Scryner. Для входа откройте сайт и нажмите «Войти через Telegram». Ваш chat_id: {chat['id']}")
