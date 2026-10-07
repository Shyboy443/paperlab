"""Authenticated paper strategy controls; protected by the lab's CSRF middleware."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/video-breakout")


def refreshed(result):
    from app.core.roster_view import invalidate
    invalidate()
    return result


def service(request):
    svc = getattr(request.app.state, "video_breakout", None)
    if svc is None:
        raise HTTPException(503, "Bitcoin breakout service is starting")
    return svc


@router.get("")
async def status(request: Request):
    return service(request).summary()


@router.post("/start")
async def start(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("A JSON object is required")
        return refreshed(await service(request).arm(body))
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Could not start: {exc}"}, status_code=409)


@router.post("/pause")
async def pause(request: Request):
    return refreshed(await service(request).pause())


@router.post("/flatten")
async def flatten(request: Request):
    try:
        return refreshed(await service(request).flatten())
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)


@router.post("/reset")
async def reset(request: Request):
    try:
        return refreshed(await service(request).reset())
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
