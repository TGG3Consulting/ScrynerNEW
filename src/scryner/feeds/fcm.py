"""Firebase Cloud Messaging (HTTP v1) push sender using a service-account JSON.

Access tokens are minted with a signed JWT (RS256) exchanged at Google's OAuth endpoint; no
Firebase SDK dependency. Returns ``True`` when the message was accepted.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any

import aiohttp

from scryner.core.config import FcmConfig
from scryner.core.logging import get_logger

log = get_logger("fcm")
_token_cache: dict[str, tuple[str, float]] = {}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


async def _access_token(cfg: FcmConfig) -> str | None:
    if not cfg.credentials_path:
        return None
    cached = _token_cache.get(cfg.credentials_path)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    sa = json.loads(await asyncio.to_thread(Path(cfg.credentials_path).read_text, encoding="utf-8"))
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        log.error("fcm_missing_dependency", package="cryptography")
        return None
    now = int(time.time())
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({"iss": sa["client_email"], "scope": "https://www.googleapis.com/auth/firebase.messaging",
                              "aud": sa["token_uri"], "iat": now, "exp": now + 3600}).encode())
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr]
    assertion = f"{header}.{claims}.{_b64(sig)}"
    async with aiohttp.ClientSession(trust_env=True) as s, s.post(sa["token_uri"], data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}) as resp:
        data = await resp.json()
    token = data.get("access_token")
    if token:
        _token_cache[cfg.credentials_path] = (token, time.time() + int(data.get("expires_in", 3600)))
    return token


async def send_push(cfg: FcmConfig, device_token: str, title: str, body: str, data: dict[str, Any] | None = None) -> bool:
    token = await _access_token(cfg)
    if not token or not cfg.project_id:
        log.warning("fcm_not_configured")
        return False
    msg = {"message": {"token": device_token, "notification": {"title": title, "body": body}, "data": {k: str(v) for k, v in (data or {}).items()},
                       "android": {"priority": "high"}}}
    url = f"https://fcm.googleapis.com/v1/projects/{cfg.project_id}/messages:send"
    async with aiohttp.ClientSession(trust_env=True) as s, s.post(url, json=msg, headers={"Authorization": f"Bearer {token}"}) as resp:
        if resp.status >= 300:
            log.warning("fcm_send_failed", status=resp.status, body=(await resp.text())[:200])
            return False
    return True
