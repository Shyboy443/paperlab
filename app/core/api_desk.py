"""Trading Desk routes (app/desk). Paper money only.

    GET  /api/public/desk                 public, read-only: the group chat, the paper ledger, session list, health
    GET  /api/public/desk/session/{id}    public, read-only: one session's Markdown decision log
    GET  /api/public/desk/candles         public, read-only: 1h/4h candles of a coin in the desk's universe
    GET  /api/desk/dm/{agent}             PRIVATE: a direct-message thread with one agent
    POST /api/desk/dm                     PRIVATE: message an agent {agent, text}; agents can act (paper only)
    POST /api/desk/chat                   PRIVATE: write in the group chat {text}; the @mentioned agent answers
    POST /api/desk/run                    PRIVATE: run a desk session now
    GET  /api/desk/settings               PRIVATE
    POST /api/desk/settings               PRIVATE: schedule, universe, mode, risk %, start balance, alerts

Private routes sit behind the dashboard's Basic auth, and every POST also needs the X-PaperLab header (app/main.py).
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

public_router = APIRouter(prefix="/api/public/desk")
router = APIRouter(prefix="/api/desk")


def _desk(request: Request) -> Any:
    d = getattr(request.app.state, "desk", None)
    if d is None:
        raise HTTPException(503, "the trading desk is not running")
    return d


@public_router.get("")
async def desk_state(request: Request, limit: int = Query(150, ge=10, le=500)) -> dict[str, Any]:
    return {**(await asyncio.to_thread(_desk(request).public_state, limit)), "read_only": True}


@public_router.get("/session/{sid}")
async def desk_session(request: Request, sid: str) -> dict[str, Any]:
    if len(sid) > 40:
        raise HTTPException(400, "bad session id")
    row = await asyncio.to_thread(_desk(request).ledger.session, sid)
    if not row:
        raise HTTPException(404, "no such session")
    return {"ok": True, "session": row, "read_only": True}


@public_router.get("/candles")
async def desk_candles(request: Request, coin: str = Query(..., min_length=2, max_length=10),
                       tf: str = Query("1h"), limit: int = Query(72, ge=10, le=300)) -> dict[str, Any]:
    desk = _desk(request)
    coin = coin.upper().replace("USDT", "")
    if coin not in desk.settings["universe"] or tf not in ("1h", "4h"):
        raise HTTPException(400, "coin must be in the desk's universe and tf 1h or 4h")
    src, rows = await asyncio.to_thread(desk.market.candles, coin, tf, limit, None, False)
    return {"ok": True, "coin": coin, "tf": tf, "source": src,
            "candles": [[c["t"], c["o"], c["h"], c["l"], c["c"]] for c in rows], "read_only": True}


@router.get("/dm/{agent}")
async def dm_thread(request: Request, agent: str) -> dict[str, Any]:
    desk = _desk(request)
    return {"ok": True, "messages": await asyncio.to_thread(desk.ledger.messages, "dm:" + agent, 100)}


@router.post("/dm")
async def dm_send(request: Request) -> dict[str, Any]:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "send {agent, text}")
    try:
        msg = await asyncio.to_thread(_desk(request).dm, str(body.get("agent") or ""), str(body.get("text") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True, "message": msg}


@router.post("/chat")
async def chat_send(request: Request) -> dict[str, Any]:
    """The operator writes in the group chat; the @mentioned agent (default: the Chief) answers and can act."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "send {text, agent?}")
    try:
        msg = await asyncio.to_thread(_desk(request).chat, str(body.get("text") or ""), body.get("agent") or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True, "message": msg}


@router.post("/run")
async def run_now(request: Request) -> dict[str, Any]:
    return _desk(request).start_session("manual")


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    return {"ok": True, "settings": _desk(request).settings}


@router.post("/settings")
async def set_settings(request: Request) -> dict[str, Any]:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "send a JSON object")
    try:
        return {"ok": True, "settings": await asyncio.to_thread(_desk(request).update_settings, body)}
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from None
