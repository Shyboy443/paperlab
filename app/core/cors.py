"""Read-only cross-origin access to the PUBLIC API, so an external front end (a Lovable app, for example) can show real
PaperLab data from the browser.

Scope, deliberately narrow:
- Only paths under /api/public/ and /public/. Everything else stays same-origin, so the private dashboard API, the live
  mirror, keys and every POST are untouched.
- Only GET and HEAD. A preflight asking for any other method, or for any custom header (Authorization included),
  gets no CORS headers, and the browser refuses the request.
- Never Access-Control-Allow-Credentials: a browser sends no cookies or saved logins with these requests.

Allowed origins:
- Lovable's domains (https://*.lovable.app, *.lovableproject.com, *.lovable.dev), unless CORS_ALLOW_LOVABLE=false;
- any exact origin listed in CORS_ALLOWED_ORIGINS (comma-separated; for a custom domain).
The public WebSocket (/api/public/ws) already accepts any origin; the private one stays same-origin.
"""
from __future__ import annotations

import os
import re
from typing import Any

LOVABLE = re.compile(r"https://([a-z0-9-]+\.)*(lovable\.app|lovableproject\.com|lovable\.dev)")
PUBLIC_PREFIXES = ("/api/public/", "/public/")
METHODS = ("GET", "HEAD")


def allowed_origin(origin: str, env: dict[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    origin = (origin or "").strip().rstrip("/").lower()
    if not origin:
        return False
    extra = {o.strip().rstrip("/").lower() for o in (env.get("CORS_ALLOWED_ORIGINS") or "").split(",") if o.strip()}
    if origin in extra:
        return True
    return str(env.get("CORS_ALLOW_LOVABLE", "true")).lower() not in ("0", "false", "no", "off") and \
        LOVABLE.fullmatch(origin) is not None


class PublicCORS:
    def __init__(self, app: Any, env: dict[str, str] | None = None):
        self.app = app
        self.env = env

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        origin = headers.get(b"origin", b"").decode("latin-1")
        if not origin or not allowed_origin(origin, self.env):
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        if method == "OPTIONS" and b"access-control-request-method" in headers:
            wanted = headers[b"access-control-request-method"].decode("latin-1").upper()
            asked = headers.get(b"access-control-request-headers", b"").strip()
            ok = wanted in METHODS and not asked
            out = [(b"vary", b"Origin"), (b"content-length", b"0")]
            if ok:
                out += [(b"access-control-allow-origin", origin.encode("latin-1")),
                        (b"access-control-allow-methods", b"GET, HEAD"), (b"access-control-max-age", b"600")]
            await send({"type": "http.response.start", "status": 204 if ok else 403, "headers": out})
            await send({"type": "http.response.body", "body": b""})
            return
        if method not in METHODS:
            await self.app(scope, receive, send)
            return

        async def send_with_cors(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                hdrs = [(k, v) for k, v in message.get("headers", []) if k.lower() not in
                        (b"access-control-allow-origin", b"vary")]
                hdrs += [(b"access-control-allow-origin", origin.encode("latin-1")), (b"vary", b"Origin")]
                message = {**message, "headers": hdrs}
            await send(message)
        await self.app(scope, receive, send_with_cors)
