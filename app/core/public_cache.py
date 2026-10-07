"""A short shared cache for the public program feeds (/api/public/competition/v6 ... v14).

Each feed takes up to about 4 seconds to build. External dashboards (the Lovable front end) refresh every program every
15 seconds, so each open tab would keep a CPU core busy. Within the TTL (10 s) every viewer gets the same finished
response. Concurrent requests for a missing entry wait for ONE build (single flight), not one build each.

Only exact program paths without a query string are cached, and only successful responses. Bot pages, candles,
activity, the private API and every non-GET request pass straight through. These feeds are public and identical for
every viewer, so sharing them leaks nothing.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

PATTERN = re.compile(r"/api/public/competition/v(6|7|8|9|11|12|13|14)")
TTL_S = 10.0


class PublicCache:
    def __init__(self, app: Any, ttl: float = TTL_S, clock: Any = time.monotonic):
        self.app, self.ttl, self.clock = app, float(ttl), clock
        self.store: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    def _fresh(self, key: str) -> list[dict[str, Any]] | None:
        hit = self.store.get(key)
        return hit[1] if hit is not None and self.clock() - hit[0] < self.ttl else None

    @staticmethod
    async def _replay(messages: list[dict[str, Any]], send: Any) -> None:
        for m in messages:
            await send(m)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or scope.get("method") != "GET" or scope.get("query_string") \
                or not PATTERN.fullmatch(path):
            await self.app(scope, receive, send)
            return
        hit = self._fresh(path)
        if hit is not None:
            await self._replay(hit, send)
            return
        lock = self.locks.setdefault(path, asyncio.Lock())
        async with lock:
            hit = self._fresh(path)
            if hit is None:
                messages: list[dict[str, Any]] = []

                async def capture(message: dict[str, Any]) -> None:
                    messages.append(message)
                await self.app(scope, receive, capture)
                if messages and messages[0].get("type") == "http.response.start" and messages[0].get("status") == 200:
                    self.store[path] = (self.clock(), messages)
                hit = messages
        await self._replay(hit, send)
