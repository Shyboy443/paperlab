"""MACD desk routes: public and read-only (no order, execution or mutation routes)."""
import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api/public/macd")


def service(request):
    svc = getattr(request.app.state, "macd", None)
    if svc is None:
        raise HTTPException(503, "MACD desk is starting")
    return svc


@router.get("")
async def summary(request: Request):
    return await asyncio.to_thread(service(request).summary)


@router.get("/chart")
async def chart(request: Request, symbol: str = Query("ZECUSDT", max_length=16), around: int = Query(0, ge=0, le=10**14)):
    try:
        return await asyncio.to_thread(service(request).chart, symbol, around)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/backtest")
async def backtest(request: Request, symbol: str = Query("ZECUSDT", max_length=16)):
    try:
        return await asyncio.to_thread(service(request).backtest, symbol)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/perp-monthly")
async def perp_monthly(request: Request):
    """ZEC perpetual MACD account, 2026 by month (paper study, read-only)."""
    return await asyncio.to_thread(service(request).perp_monthly)
