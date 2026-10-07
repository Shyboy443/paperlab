"""Realtime push endpoints, GET/upgrade only. Bus in app/core/events.py, producers in
app/core/realtime.py.

    WS  /api/ws              private dashboard (primary transport)
    WS  /api/public/ws       public inspection page (primary transport)
    GET /api/stream          private, server-sent events (fallback transport)
    GET /api/public/stream   public, server-sent events (fallback transport)

WebSocket protocol (JSON text messages):
    client -> {"type": "hello", "authorization": "Basic ...", "last_id": 123}   first message, <= 10 s
    server -> {"id": 124, "event": "fill", "data": {...}}                        live and backlog events
              {"event": "hello" | "state" | "health" | "ping" | "unauthorized", "data": {...}}
                                                                                 per-client, no id

The private socket authenticates with the SAME check as every /api route (user admin +
DASHBOARD_PASSWORD), but in its first message: a browser cannot attach an Authorization header to a
WebSocket, and a credential must never travel in a URL. It also refuses a cross-origin browser
handshake, so another site cannot open it with the visitor's session. The public socket needs no
credential and only ever carries events published as public.
"""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, Request, WebSocket
from fastapi.responses import StreamingResponse
from starlette.websockets import WebSocketDisconnect

from app.core.auth import basic_ok
from app.core.engine import EngineError
from app.core.events import HEARTBEAT_S, _body, last_event_id, ws_message

router = APIRouter()

SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
HELLO_TIMEOUT_S = 10.0


def _realtime(request: Request) -> Any:
    rt = getattr(request.app.state, "realtime", None)
    if rt is None:
        raise EngineError("realtime stream not started", 503)
    return rt


def _last_id(request: Request, last_id: str) -> int | None:
    return last_event_id(request.headers.get("last-event-id") or last_id)


# ---- server-sent events (fallback) --------------------------------------------------------------------

@router.get("/api/public/prices")
async def display_price_snapshot(request: Request) -> dict[str, Any]:
    feed = _realtime(request).display_prices
    return feed.snapshot() if feed is not None else {"connected": False, "quotes": {}, "display_only": True}

@router.get("/api/stream")
async def private_stream(request: Request, last_id: str = Query("")) -> StreamingResponse:
    rt = _realtime(request)
    return StreamingResponse(rt.bus.stream(_last_id(request, last_id), public_only=False, greeting=rt.greeting),
                             media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/api/public/stream")
async def public_stream(request: Request, last_id: str = Query("")) -> StreamingResponse:
    rt = _realtime(request)
    return StreamingResponse(rt.bus.stream(_last_id(request, last_id), public_only=True,
                                           greeting=rt.public_greeting),
                             media_type="text/event-stream", headers=SSE_HEADERS)


# ---- WebSocket (primary) --------------------------------------------------------------------------------

@router.websocket("/api/ws")
async def private_ws(ws: WebSocket) -> None:
    await serve_ws(ws, private=True)


@router.websocket("/api/public/ws")
async def public_ws(ws: WebSocket) -> None:
    await serve_ws(ws, private=False)


def same_origin(ws: WebSocket) -> bool:
    """A browser always sends Origin on a WebSocket handshake; a non-browser client may not."""
    origin = ws.headers.get("origin")
    if not origin:
        return True
    return urlsplit(origin).netloc.lower() == (ws.headers.get("host") or "").lower()


async def _close(ws: WebSocket, code: int) -> None:
    try:
        await ws.close(code=code)
    except Exception:
        pass


async def _drain_client(ws: WebSocket) -> None:
    """Read (and ignore) client frames until it disconnects; the socket is push-only."""
    while True:
        msg = await ws.receive()
        if msg.get("type") == "websocket.disconnect":
            return


async def serve_ws(ws: WebSocket, private: bool) -> None:
    rt = getattr(ws.app.state, "realtime", None)
    if rt is None:
        await _close(ws, 1013)
        return
    if private and not same_origin(ws):
        await _close(ws, 4403)
        return
    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=HELLO_TIMEOUT_S)
        if not isinstance(first, dict):
            raise ValueError("hello must be an object")
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError, KeyError):
        await _close(ws, 4400)
        return
    if private:
        settings = ws.app.state.settings
        if not basic_ok(first.get("authorization"), settings.dashboard_password, settings.allow_no_auth):
            try:
                await ws.send_text(ws_message("unauthorized", '{"ok":false,"error":"unauthorized"}'))
            except Exception:
                pass
            await _close(ws, 4401)
            return
    bus = rt.bus
    sub = bus.subscribe(public_only=not private, transport="websocket")
    recv: asyncio.Task | None = None
    get: asyncio.Task | None = None
    try:
        backlog = bus.backlog(last_event_id(first.get("last_id")), not private)
        for eid, channel, payload in backlog:
            await ws.send_text(ws_message(channel, payload, eid))
        await ws.send_text(ws_message("hello", _body({"public": not private, "resumed": bool(backlog),
                                                      "transport": "websocket"})))
        for channel, data in (rt.greeting() if private else rt.public_greeting()):
            await ws.send_text(ws_message(channel, _body(data)))
        recv = asyncio.create_task(_drain_client(ws))
        while True:
            if get is None:
                get = asyncio.create_task(sub.queue.get())
            done, _ = await asyncio.wait({get, recv}, timeout=HEARTBEAT_S, return_when=asyncio.FIRST_COMPLETED)
            if recv in done:
                break
            if get in done:
                eid, channel, payload = get.result()
                get = None
                await ws.send_text(ws_message(channel, payload, eid))
            else:
                await ws.send_text(ws_message("ping", _body({})))
    except (WebSocketDisconnect, RuntimeError, ConnectionError):
        pass
    finally:
        bus.unsubscribe(sub, "websocket")
        for t in (get, recv):
            if t is not None and not t.done():
                t.cancel()
        await _close(ws, 1000)
