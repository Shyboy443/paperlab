"""V10 SCOUT routes.

    GET /api/public/scout        public, read-only: per-symbol attention, spike vs normal, tone, 24 h series and the
                                 scout's health. Aggregates only -- no post or headline text.
    GET /api/scout/items         PRIVATE (dashboard login): the latest posts / headlines behind a symbol's numbers.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request

public_router = APIRouter(prefix="/api/public/scout")
router = APIRouter(prefix="/api/scout")


def _scout(request: Request) -> Any:
    return getattr(request.app.state, "scout", None)


@public_router.get("")
async def scout_summary(request: Request) -> dict[str, Any]:
    s = _scout(request)
    if s is None:
        return {"ok": True, "health": {"state": "DISABLED", "enabled": False}, "symbols": [], "read_only": True}
    return {**(await asyncio.to_thread(s.summary)), "read_only": True}


@router.get("/items")
async def scout_items(request: Request, symbol: str | None = Query(None, max_length=10),
                      limit: int = Query(40, ge=1, le=200)) -> dict[str, Any]:
    s = _scout(request)
    if s is None:
        return {"ok": False, "error": "the scout is not running"}
    rows = await asyncio.to_thread(s.store.items, symbol.upper() if symbol else None, limit)
    tones = await asyncio.to_thread(s.store.tones, [r["id"] for r in rows])
    return {"ok": True, "items": [{"source": r["source"], "channel": r["channel"], "kind": r["kind"],
                                   "created_ms": r["created_ms"], "symbols": [x for x in (r["symbols"] or "").split(",") if x],
                                   "text": (r["text"] or "")[:400], "url": r["url"], "score": r["score"],
                                   "tone": r["tone"], "jev_tone": {k[1]: v for k, v in tones.items() if k[0] == r["id"]}}
                                  for r in rows]}
