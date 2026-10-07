"""PRIVATE live-mirror routes (/api/mirror/*): providers, testnet verification, go-live and stop.

Behind the dashboard's Basic auth; every POST also needs the X-PaperLab header (the app's CSRF guard). Nothing here
is reachable from /api/public. Keys are never accepted or returned: they live in the server environment.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/mirror")


def _mirror(request: Request) -> Any:
    m = getattr(request.app.state, "mirror", None)
    if m is None:
        raise HTTPException(503, "live mirror is not running")
    return m


@router.get("/providers")
async def providers(request: Request) -> dict[str, Any]:
    m = _mirror(request)
    rows = await m.providers.status(m.storage)
    tg = getattr(request.app.state, "telegram", None)
    if tg is not None:
        h = tg.health()                                              # presence + counters only: never the token
        for row in rows:
            if row["exchange"] == "telegram":
                row["telegram"] = {k: h.get(k) for k in ("configured", "linked", "sent", "replies", "errors",
                                                         "last_error", "queued", "dropped", "filters", "last_sent_ts")}
    return {"ok": True, "providers": rows, "max_amount_usdt": m.providers.max_amount(),
            "checks": m.storage.provider_checks(limit=10)}


@router.post("/telegram/test")
async def telegram_test(request: Request) -> Any:
    """The operator's own button: one test message to the linked chat, so they can see the bot reaches them."""
    import asyncio
    tg = getattr(request.app.state, "telegram", None)
    if tg is None:
        raise HTTPException(503, "telegram notifications are not running")
    out = await asyncio.to_thread(tg.send_test)
    return out if out.get("ok") else JSONResponse(out, status_code=400)


@router.post("/providers/{exchange}/testnet/verify")
async def verify(request: Request, exchange: str) -> dict[str, Any]:
    m = _mirror(request)
    if exchange not in ("bybit", "binance"):
        raise HTTPException(400, "exchange must be bybit or binance")
    if m.providers.keys(exchange, "testnet") is None:
        return JSONResponse({"ok": False, "error": f"{exchange} testnet keys are not configured on the server"}, status_code=400)
    return await m.verify_testnet(exchange)


def _provider(exchange: str, network: str) -> None:
    from app.live.providers import PROVIDERS
    if (exchange, network) not in PROVIDERS:
        raise HTTPException(404, "unknown provider")


def _in_use(m: Any, exchange: str, network: str, stock_trend: Any = None) -> bool:
    if exchange == 'alpaca' and stock_trend is not None and stock_trend.uses_provider(network):
        return True
    return any(x["exchange"] == exchange and x["network"] == network and (x["status"] == "ARMED" or x.get("position"))
               for x in m.mirrors.values())


@router.post("/providers/{exchange}/{network}/keys")
async def save_keys(request: Request, exchange: str, network: str) -> Any:
    """Keys typed into the dashboard: checked with a read-only account call, then kept ENCRYPTED on the server.
    The response never echoes them."""
    m = _mirror(request)
    _provider(exchange, network)
    if _in_use(m, exchange, network, getattr(request.app.state, 'stock_trend', None)):
        return JSONResponse({"ok": False, "error": "stop the live mirrors on this provider before changing its keys"}, status_code=409)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "JSON body required")
    out = await m.providers.save_keys(exchange, network, str(body.get("key") or ""), str(body.get("secret") or ""))
    return out if out.get("ok") else JSONResponse(out, status_code=400)


@router.post("/providers/{exchange}/{network}/keys/clear")
async def clear_keys(request: Request, exchange: str, network: str) -> Any:
    m = _mirror(request)
    _provider(exchange, network)
    if _in_use(m, exchange, network, getattr(request.app.state, 'stock_trend', None)):
        return JSONResponse({"ok": False, "error": "stop the live mirrors on this provider before removing its keys"}, status_code=409)
    return {"ok": True, "removed": m.providers.forget_keys(exchange, network)}


@router.get("/mirrors")
async def mirrors(request: Request) -> dict[str, Any]:
    m = _mirror(request)
    return {"ok": True, "mirrors": m.overview(), "log": m.storage.mirror_logs(limit=60)}


@router.post("/start")
async def start(request: Request) -> dict[str, Any]:
    from app.live.mirror import MirrorError
    m = _mirror(request)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "JSON body required")
    try:
        return {"ok": True, "mirror": await m.arm(body)}
    except MirrorError as exc:                 # the reason is shown to the operator
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/{mirror_id}/stop")
async def stop(request: Request, mirror_id: str) -> dict[str, Any]:
    from app.live.mirror import MirrorError
    m = _mirror(request)
    try:
        return {"ok": True, "mirror": await m.disarm(mirror_id, "operator STOP")}
    except MirrorError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
