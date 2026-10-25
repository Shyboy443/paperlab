"""PRIVATE routes: linked exchange accounts and the desks' live switches (MACD, Donchian).

Mounted under /api/ (not /api/public/), so BasicAuthMiddleware requires the dashboard password on every request and the
X-PaperLab header on every POST. Responses never contain a key or a secret (accounts are presence + fingerprint only).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api")


def _accounts(request: Request):
    a = getattr(request.app.state, "accounts", None)
    if a is None:
        raise HTTPException(503, "linked accounts are unavailable")
    return a


def _live(request: Request):
    live = getattr(request.app.state, "macd_live", None)
    if live is None:
        raise HTTPException(503, "the MACD live executor is not running")
    return live


def _bad(exc: Exception, request: Request) -> JSONResponse:
    """A refused request: 400 with {ok: false, error} (the dashboard shows `error`). Secrets are redacted."""
    providers = getattr(getattr(request.app.state, "mirror", None), "providers", None)
    text = str(exc)
    if providers is not None:
        text = providers.redact(text)
    return JSONResponse({"ok": False, "error": text}, status_code=400)


@router.get("/accounts")
async def accounts_list(request: Request) -> dict[str, Any]:
    return {"accounts": _accounts(request).public()}


@router.get("/accounts/balances")
async def accounts_balances(request: Request) -> dict[str, Any]:
    """Live USDT balance per linked account (read-only, cached 30 s)."""
    return {"ok": True, "balances": await _accounts(request).balances()}


@router.post("/accounts")
async def accounts_add(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        row = await _accounts(request).add(body.get("label", ""), body.get("exchange", "binance"), body.get("network", ""),
                                           body.get("api_key", ""), body.get("secret", ""))
    except ValueError as exc:
        return _bad(exc, request)
    return {"ok": True, "account": row}


@router.post("/accounts/import")
async def accounts_import(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Link the key saved in Providers & Live (no key in the request or the response)."""
    try:
        row = await _accounts(request).import_shared(body.get("exchange", "binance"), body.get("network", ""), body.get("label", ""))
    except ValueError as exc:
        return _bad(exc, request)
    return {"ok": True, "account": row}


@router.post("/accounts/{account_id}/remove")
async def accounts_remove(request: Request, account_id: str) -> dict[str, Any]:
    try:
        _accounts(request).remove(account_id)
    except ValueError as exc:
        return _bad(exc, request)
    return {"ok": True}


@router.post("/accounts/{account_id}/verify")
async def accounts_verify(request: Request, account_id: str) -> dict[str, Any]:
    """A real TESTNET round trip on a linked testnet account (unlocks mainnet for every live desk)."""
    live = _live(request)
    try:
        return await live.call(lambda: live.verify(account_id))
    except ValueError as exc:
        return _bad(exc, request)


DESKS = {"macd": "macd_live", "donchian": "donchian_live",
         "macd-stx": "macd_stx_live", "macd-dash": "macd_dash_live", "macd-xlm": "macd_xlm_live",
         "ensemble": "ensemble_live", "worker2": "worker2_live",
         **{f"zec-{k}": f"zec_{k}_live" for k in ('emarsi', 'momentum', 'emacross', 'macross', 'scalpslayer', 'wilder')}}


def _desk(request: Request, desk: str):
    live = getattr(request.app.state, DESKS.get(desk, ""), None)
    if live is None:
        raise HTTPException(404 if desk not in DESKS else 503, f"no live switch for '{desk}'")
    return live


@router.get("/{desk}/live")
async def desk_live_view(request: Request, desk: str) -> dict[str, Any]:
    return _desk(request, desk).view()


@router.post("/{desk}/live/start")
async def desk_live_start(request: Request, desk: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    live = _desk(request, desk)
    try:
        return await live.call(lambda: live.arm(body.get("account_id", ""), body.get("amount"), body.get("max_loss"),
                                                body.get("confirm", ""), body.get("symbol") or None))
    except ValueError as exc:
        return _bad(exc, request)


@router.post("/{desk}/live/stop")
async def desk_live_stop(request: Request, desk: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Switch OFF: closes the desk's live position. The dashboard asks "are you sure?" and sends sure=true."""
    if body.get("sure") is not True:
        return _bad(ValueError("confirm: send sure=true to stop live trading and close the position"), request)
    live = _desk(request, desk)
    if hasattr(live, "request_abort"):
        live.request_abort()                     # a maker fill in progress stops waiting; the close then goes at market
    try:
        return await live.call(lambda: live.disarm("switched off by the operator"))
    except ValueError as exc:
        return _bad(exc, request)
