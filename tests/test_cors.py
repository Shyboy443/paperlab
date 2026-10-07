"""Cross-origin access is read-only and public-only: GET/HEAD on /api/public/* and /public/* from Lovable domains or
listed origins; never credentials, never custom headers, never the private API."""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.cors import PublicCORS, allowed_origin


def make(env=None):
    async def ok(request):
        return JSONResponse({"ok": True})
    app = Starlette(routes=[Route("/api/public/competition/roster", ok, methods=["GET", "POST"]),
                            Route("/api/mirror/providers", ok, methods=["GET"])])
    app.add_middleware(PublicCORS, env=env or {})
    return TestClient(app)


LOV = "https://my-app.lovable.app"


def test_allowed_origins():
    assert allowed_origin(LOV, {}) and allowed_origin("https://id-preview--abc.lovable.app", {})
    assert allowed_origin("https://x.lovableproject.com", {})
    assert not allowed_origin("https://evil.com", {}) and not allowed_origin("https://lovable.app.evil.com", {})
    assert not allowed_origin("http://my-app.lovable.app", {})                 # https only
    assert allowed_origin("https://trade.example.com", {"CORS_ALLOWED_ORIGINS": "https://trade.example.com"})
    assert not allowed_origin(LOV, {"CORS_ALLOW_LOVABLE": "false"})


def test_public_get_gets_cors_headers_without_credentials():
    r = make().get("/api/public/competition/roster", headers={"Origin": LOV})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == LOV
    assert "access-control-allow-credentials" not in r.headers


def test_preflight_allows_get_only_and_no_custom_headers():
    c = make()
    ok = c.options("/api/public/competition/roster", headers={"Origin": LOV, "Access-Control-Request-Method": "GET"})
    assert ok.status_code == 204 and ok.headers["access-control-allow-methods"] == "GET, HEAD"
    post = c.options("/api/public/competition/roster", headers={"Origin": LOV, "Access-Control-Request-Method": "POST"})
    assert post.status_code == 403 and "access-control-allow-origin" not in post.headers
    auth = c.options("/api/public/competition/roster", headers={"Origin": LOV, "Access-Control-Request-Method": "GET",
                                                                 "Access-Control-Request-Headers": "authorization"})
    assert auth.status_code == 403 and "access-control-allow-origin" not in auth.headers


def test_private_and_unknown_origins_get_nothing():
    c = make()
    assert "access-control-allow-origin" not in c.get("/api/mirror/providers", headers={"Origin": LOV}).headers
    assert "access-control-allow-origin" not in c.get("/api/public/competition/roster",
                                                      headers={"Origin": "https://evil.com"}).headers
    assert "access-control-allow-origin" not in c.post("/api/public/competition/roster", headers={"Origin": LOV}).headers
