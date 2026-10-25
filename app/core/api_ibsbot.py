"""IBS bot routes (app/ibsbot/service.py).

public_router: GET /api/public/ibs-bot, read-only (no account, no amounts, no keys; no live controls anywhere public).
router: the private dashboard (Basic auth on everything under /api/, plus the X-PaperLab header on POST, from
app/main.py's middleware): the full view, switch on (typed phrase) and switch off (sure=true).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

public_router = APIRouter(prefix="/api/public")
router = APIRouter(prefix="/api/ibs-bot")


def _bot(request: Request) -> Any:
    bot = getattr(request.app.state, "ibs_bot", None)
    if bot is None:
        raise HTTPException(503, "the IBS bot is starting")
    return bot


def _bad(bot: Any, exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": bot.providers.redact(str(exc))[:300]}, status_code=status)


@public_router.get("/ibs-bot")
async def public(request: Request) -> dict[str, Any]:
    return _bot(request).public_view()


@router.get("")
async def private(request: Request) -> dict[str, Any]:
    return _bot(request).private_view()


@router.post("/start")
async def start(request: Request, body: dict[str, Any] = Body(default={})) -> Any:
    bot = _bot(request)
    try:
        return {"ok": True, "bot": await bot.arm(body.get("network", "mainnet"), body.get("capital_qqq"), body.get("capital_tqqq"),
                                                 body.get("max_loss"), body.get("confirm", ""))}
    except ValueError as exc:
        return _bad(bot, exc)
    except Exception as exc:
        return _bad(bot, Exception(f"{type(exc).__name__}: {exc}"))


@router.post("/stop")
async def stop(request: Request, body: dict[str, Any] = Body(default={})) -> Any:
    """Switch OFF: sells the bot's QQQ/TQQQ at market (at the next open if the market is closed)."""
    bot = _bot(request)
    if body.get("sure") is not True:
        return _bad(bot, ValueError("confirm: send sure=true to switch the bot off and sell its positions"))
    try:
        return {"ok": True, "bot": await bot.disarm("switched off by the operator")}
    except Exception as exc:
        return _bad(bot, Exception(f"{type(exc).__name__}: {exc}"), 409)
