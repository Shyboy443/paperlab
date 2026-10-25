"""MACD coin bots (STX / DASH / XLM): their 2-year backtests with monthly and weekly returns. Public, GET-only, read-only.
The live switches themselves are private (/api/macd-<coin>/live, app/core/api_live_accounts.py)."""
from __future__ import annotations

from typing import Any

import asyncio

from fastapi import APIRouter, HTTPException, Request

from app.macdtrio.live import backtests
from app.zecbots.live import backtests as zec_backtests

router = APIRouter(prefix="/api/public")


@router.get("/macd-coins")
async def coins() -> dict[str, Any]:
    return {"coins": backtests(), "read_only": True, "historical_only": True,
            "note": "hindsight picks: the pre-registered year-1 / year-2 selection passed no coin (docs/MACD_TRIO_STUDY.md)"}


@router.get("/macd-chop")
async def chop_test(request: Request) -> dict[str, Any]:
    """The choppy-market filter's forward paper test: CONTROL vs FILTER, each coin and the three together, weekly."""
    svc = getattr(request.app.state, "macd_chop", None)
    if svc is None:
        raise HTTPException(503, "the choppy-filter paper test is not running")
    return await asyncio.to_thread(svc.summary)


@router.get("/ensemble")
async def ensemble(request: Request) -> dict[str, Any]:
    """The Ensemble paper book (rotation + ML + 60-day momentum): curve, months, today's weights and positions."""
    svc = getattr(request.app.state, "ensemble", None)
    if svc is None:
        raise HTTPException(503, "the Ensemble paper book is not running")
    return {**await asyncio.to_thread(svc.public), "read_only": True}


@router.get("/zec-bots")
async def zec_bots() -> dict[str, Any]:
    """The six ZEC bots: 2-year perpetual backtests (long-only, all costs) with monthly and weekly returns."""
    return {"bots": zec_backtests(), "read_only": True, "historical_only": True,
            "note": "ZEC was the best coin of another study, picked with hindsight; all six trade the same coin (docs/ZEC_BOTS_STUDY.md)"}
