"""Renata V12-VOL forward test (app/lab12/forward.py): one public, read-only route. Paper only; nothing here can trade.

    GET /api/public/v12vol      the two paper books, open and closed trades, metrics vs the pre-registered rules
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

public_router = APIRouter(prefix="/api/public/v12vol")


@public_router.get("")
async def v12vol_state(request: Request) -> dict[str, Any]:
    svc = getattr(request.app.state, "v12vol", None)
    if svc is None:
        raise HTTPException(503, "Renata V12-VOL is not running")
    return {**(await asyncio.to_thread(svc.public_state)), "read_only": True}
