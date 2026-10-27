"""PUBLIC, read-only: what the server runs (app/core/lineup.py), for the dashboard's home page. GET only, no secrets."""
from __future__ import annotations

from typing import Any

import asyncio

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/public")


@router.get("/lineup")
async def lineup(request: Request) -> dict[str, Any]:
    lu = getattr(request.app.state, "lineup", None)
    return {"mode": lu.mode if lu is not None else "starting", "kept": ["ensemble", "worker2", "worker3", "ibs_bot"]}


@router.get("/worker2")
async def worker2(request: Request) -> dict[str, Any]:
    """Worker 2's paper book (afternoon session momentum): curve, months, trades, the current event and its positions."""
    svc = getattr(request.app.state, "worker2", None)
    if svc is None:
        raise HTTPException(503, "the Worker 2 paper book is not running")
    return {**await asyncio.to_thread(svc.public), "read_only": True}


@router.get("/worker3")
async def worker3(request: Request) -> dict[str, Any]:
    """Worker 3's paper book (30-day-high long/short): curve, months, closed positions, today's book."""
    svc = getattr(request.app.state, "worker3", None)
    if svc is None:
        raise HTTPException(503, "the Worker 3 paper book is not running")
    return {**await asyncio.to_thread(svc.public), "read_only": True}
