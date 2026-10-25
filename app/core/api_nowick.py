"""No-Wick Retest desk routes: public and read-only (no order, execution or mutation routes)."""
import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api/public/nowick")


def service(request):
    svc = getattr(request.app.state, "nowick", None)
    if svc is None:
        raise HTTPException(503, "No-Wick desk is starting")
    return svc


@router.get("")
async def summary(request: Request):
    return await asyncio.to_thread(service(request).summary)


@router.get("/chart")
async def chart(request: Request, symbol: str = Query("BTCUSDT", max_length=16), tf: str = Query("5m", max_length=3),
                around: int = Query(0, ge=0, le=10**14)):
    try:
        return await asyncio.to_thread(service(request).chart, symbol, tf, around)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/backtest")
async def backtest(request: Request, tf: str = Query("5m", max_length=3), symbol: str = Query("", max_length=16)):
    try:
        return await asyncio.to_thread(service(request).backtest, tf, symbol)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
