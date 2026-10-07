"""The dashboard's one credential check, shared by the HTTP middleware and the private WebSocket.

User `admin`, password DASHBOARD_PASSWORD, compared in constant time. ALLOW_NO_AUTH (a loopback-only
escape hatch, refused on Railway by app/config.py) accepts everything.
"""
from __future__ import annotations

import base64
import hmac


def basic_ok(raw: bytes | str | None, password: str, allow_no_auth: bool = False) -> bool:
    if allow_no_auth:
        return True
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "ignore")
    raw = raw or b""
    if not raw.lower().startswith(b"basic "):
        return False
    try:
        decoded = base64.b64decode(raw[6:].strip()).decode("utf-8")
    except Exception:
        return False
    user, _, pw = decoded.partition(":")
    return hmac.compare_digest(user.encode(), b"admin") and hmac.compare_digest(pw.encode(), password.encode())
