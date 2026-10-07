"""The public program feeds are built once per TTL and shared; everything else is never cached."""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.public_cache import PublicCache


def make():
    calls = {"n": 0}
    now = {"t": 0.0}

    async def feed(request):
        calls["n"] += 1
        return JSONResponse({"build": calls["n"]})
    app = Starlette(routes=[Route("/api/public/competition/v8", feed, methods=["GET", "POST"]),
                            Route("/api/public/competition/v8/bot/X", feed)])
    app.add_middleware(PublicCache, ttl=10.0, clock=lambda: now["t"])
    return TestClient(app), calls, now


def test_feed_is_built_once_per_ttl_and_shared():
    c, calls, now = make()
    assert c.get("/api/public/competition/v8").json() == {"build": 1}
    now["t"] = 9.0
    assert c.get("/api/public/competition/v8").json() == {"build": 1} and calls["n"] == 1
    now["t"] = 10.5
    assert c.get("/api/public/competition/v8").json() == {"build": 2}


def test_queries_bot_pages_and_posts_bypass_the_cache():
    c, calls, _ = make()
    c.get("/api/public/competition/v8")
    c.get("/api/public/competition/v8?x=1")
    c.get("/api/public/competition/v8/bot/X")
    c.post("/api/public/competition/v8")
    assert calls["n"] == 4
