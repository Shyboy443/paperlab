"""pump.fun recorder: one public, GET-only, read-only summary (research data collection, paper only)."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/public/pumpfun")


@router.get("")
async def summary(request: Request) -> dict[str, Any]:
    svc = getattr(request.app.state, "pumpfun", None)
    if svc is None:
        raise HTTPException(503, "the pump.fun recorder is not running")
    return await asyncio.to_thread(svc.summary)
