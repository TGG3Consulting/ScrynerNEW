"""Telegram channel reader (MTProto via Telethon) for news / onchain / listings feeds.

Produces messages in the reference format (``event: telegram.message`` with ``message``,
``chat`` and ``sender`` blocks) and publishes them to ``feed.<kind>`` on the bus.
Requires ``telegram.api_id`` / ``api_hash`` and an authorised session file
(``<session_name>.session``; create it once interactively with ``python -m scryner.feeds.telegram_channels``).
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from typing import Any

from scryner.core.bus import MemoryBus, RedisBus
from scryner.core.config import TelegramConfig
from scryner.core.logging import get_logger


def _media(m: Any) -> dict[str, Any]:
    media = getattr(m, "media", None)
    if media is None:
        return {"kind": "", "has_media": False, "visual": False, "mime_type": "", "size_bytes": 0, "data_url": ""}
    kind = type(media).__name__.replace("MessageMedia", "").lower()
    doc = getattr(media, "document", None)
    mime = getattr(doc, "mime_type", "") if doc else ("image/jpeg" if kind == "photo" else "")
    size = int(getattr(doc, "size", 0) or 0) if doc else 0
    return {"kind": kind, "has_media": True, "visual": kind in ("photo", "document") and mime.startswith(("image/", "video/")), "mime_type": mime, "size_bytes": size, "data_url": ""}


def message_payload(m: Any, chat: Any, sender: Any, kind: str) -> dict[str, Any]:
    from datetime import datetime

    now = datetime.now(tz=UTC)
    chat_username = getattr(chat, "username", None)
    return {
        "event": "telegram.message",
        "captured_at": now.isoformat(),
        "source_type": "message",
        "feed": kind,
        "message": {
            "id": m.id, "date": m.date.isoformat() if m.date else None, "edit_date": m.edit_date.isoformat() if m.edit_date else None,
            "grouped_id": m.grouped_id, "text": m.message or "", "raw_text": m.raw_text or "", "post": bool(getattr(m, "post", False)),
            "post_author": getattr(m, "post_author", None), "views": getattr(m, "views", 0) or 0, "forwards": getattr(m, "forwards", 0) or 0,
            "media": _media(m),
        },
        "chat": {
            "id": getattr(chat, "id", None), "title": getattr(chat, "title", None), "username": chat_username,
            "kind": "megagroup" if getattr(chat, "megagroup", False) else "channel", "verified": bool(getattr(chat, "verified", False)),
            "broadcast": bool(getattr(chat, "broadcast", False)), "megagroup": bool(getattr(chat, "megagroup", False)), "photo": None,
        },
        "sender": {
            "id": getattr(sender, "id", None), "name": getattr(sender, "title", None) or " ".join(filter(None, [getattr(sender, "first_name", None), getattr(sender, "last_name", None)])),
            "username": getattr(sender, "username", None), "kind": "bot" if getattr(sender, "bot", False) else ("channel" if hasattr(sender, "broadcast") else "user"),
            "verified": bool(getattr(sender, "verified", False)), "bot": bool(getattr(sender, "bot", False)), "photo": None,
        },
    }


class TelegramChannels:
    def __init__(self, cfg: TelegramConfig, bus: MemoryBus | RedisBus) -> None:
        self.cfg = cfg
        self.bus = bus
        self.log = get_logger("tgchannels")
        self.kinds: dict[str, str] = {}
        for ch in cfg.news_channels:
            self.kinds[ch.lower().lstrip("@")] = "news"
        for ch in cfg.onchain_channels:
            self.kinds[ch.lower().lstrip("@")] = "onchain"
        for ch in cfg.listings_channels:
            self.kinds[ch.lower().lstrip("@")] = "listings"

    async def run(self) -> None:
        if not self.cfg.api_id or not self.cfg.api_hash:
            self.log.warning("telegram_channels_disabled", reason="no api_id/api_hash")
            return
        from telethon import TelegramClient, events

        client = TelegramClient(self.cfg.session_name, self.cfg.api_id, self.cfg.api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            self.log.error("telegram_channels_unauthorized", hint="run `python -m scryner.feeds.telegram_channels` once to sign in")
            await client.disconnect()
            return
        entities = []
        for name, kind in self.kinds.items():
            try:
                ent = await client.get_entity(name)
                entities.append(ent)
                # preload history so the feed is not empty after a restart
                async for m in client.iter_messages(ent, limit=self.cfg.history_size):
                    await self.bus.publish(f"feed.{kind}", message_payload(m, ent, ent, kind))
            except Exception as exc:
                self.log.warning("telegram_channel_error", channel=name, error=str(exc)[:200])
        self.log.info("telegram_channels_started", channels=len(entities))

        @client.on(events.NewMessage(chats=entities))
        async def on_new(event: Any) -> None:
            chat = await event.get_chat()
            sender = await event.get_sender()
            uname = (getattr(chat, "username", "") or "").lower()
            kind = self.kinds.get(uname, "news")
            await self.bus.publish(f"feed.{kind}", message_payload(event.message, chat, sender or chat, kind))

        await client.run_until_disconnected()


if __name__ == "__main__":  # interactive sign-in helper
    from scryner.core.config import load_config

    cfg = load_config()
    from telethon import TelegramClient

    async def _login() -> None:
        c = TelegramClient(cfg.telegram.session_name, cfg.telegram.api_id, cfg.telegram.api_hash)
        await c.start()
        me = await c.get_me()
        print("signed in as", me.username or me.id)
        await c.disconnect()

    asyncio.run(_login())
