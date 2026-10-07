"""Operator routes for Jev. All of them sit behind the dashboard's Basic auth and CSRF header.

    GET  /api/jev/health   JEV API HEALTH
    POST /api/jev/smoke    one minimal authenticated request to OpenRouter
    POST /api/jev/decide   one typed decision for a research runner that has no key

Nothing here returns, logs or stores the OpenRouter key; every payload is built from typed fields.
`/api/jev/decide` only accepts a state for the prompt version the server itself runs, so a caller
cannot change the questions -- the prompt is part of the experiment and is versioned on the server.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from app.core.engine import EngineError

router = APIRouter(prefix="/api/jev")


def _svc(request: Request) -> Any:
    svc = getattr(request.app.state, "jev", None)
    if svc is None:
        raise EngineError("jev service not started", 503)
    return svc


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return {"ok": True, "health": _svc(request).health()}


@router.post("/smoke")
async def smoke(request: Request) -> dict[str, Any]:
    svc = _svc(request)
    check = await svc.smoke()
    return {"ok": bool(check.get("ok")), "check": check, "health": svc.health()}


@router.post("/decide")
async def decide(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    svc = _svc(request)
    state = body.get("state")
    if not isinstance(state, (dict, list, str)) or not state:
        raise EngineError("state is required", 400)
    out = await svc.decide(state, str(body.get("prompt_version") or ""),
                           str(body.get("session_id") or "") or None)
    return {"ok": True, "outcome": out.to_dict(), "model_requested": svc.config.model}
