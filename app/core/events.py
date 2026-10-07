"""Realtime event bus: one in-process bus, two transports, two audiences.

    publish(channel, data, public=False)      from the event loop OR any worker thread

    WebSocket (primary)                       GET /api/ws         private: everything
                                              GET /api/public/ws  public: events published public=True
    Server-sent events (fallback)             GET /api/stream / GET /api/public/stream

Both transports carry the same events in the same order with the same ids, and both resume: a
reconnecting client gives its last event id and is replayed what it missed from a short backlog,
then sent a fresh greeting (a full state snapshot for the private dashboard). Commands still go over
the authenticated REST POSTs; the push channel is read-only.

Channels:
    state         dashboard state as per-second deltas (private; full snapshot on connect)
    fill/signal/note/equity   paper engine writes as they are stored (private)
    jev           live Jev decisions (public, sanitized)
    shadow        live shadow events: candidates, opens, closes (public)
    shadow_state  live shadow books and market, every couple of seconds while anything moves (public)
    competition   season / validation / arena / Jev / candidate run progress (public)
    health        stream, Jev API and live shadow health, every 10 s (public subset)
"""
from __future__ import annotations

import asyncio
import itertools
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

HEARTBEAT_S = 15.0
Greeting = Callable[[], list[tuple[str, dict[str, Any]]]]


@dataclass(eq=False)
class Subscription:
    queue: asyncio.Queue
    public_only: bool


class EventBus:
    def __init__(self, replay: int = 500):
        self._seq = itertools.count(1)
        self._lock = threading.Lock()
        self._recent: deque[tuple[int, str, str, bool]] = deque(maxlen=replay)
        self._subs: set[Subscription] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.published = 0
        self.by_transport = {"websocket": 0, "sse": 0}

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def clients(self) -> int:
        return len(self._subs)

    @property
    def private_clients(self) -> int:
        return sum(1 for s in list(self._subs) if not s.public_only)

    # -- producers ----------------------------------------------------------------------------
    def publish(self, channel: str, data: dict[str, Any], public: bool = False, replay: bool = True) -> int:
        """Thread-safe. Returns the event id.

        `replay=False` keeps the event out of the reconnect backlog: state deltas are useless without
        their chain, and a reconnecting client is sent a full snapshot anyway."""
        payload = json.dumps({"ts": int(time.time() * 1000), **data}, separators=(",", ":"), default=str)
        with self._lock:
            eid = next(self._seq)
            if replay:
                self._recent.append((eid, channel, payload, public))
            self.published += 1
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._fanout, eid, channel, payload, public)
            except RuntimeError:
                pass
        return eid

    def _fanout(self, eid: int, channel: str, payload: str, public: bool) -> None:
        for sub in list(self._subs):
            if sub.public_only and not public:
                continue
            try:
                sub.queue.put_nowait((eid, channel, payload))
            except asyncio.QueueFull:          # a stalled client loses events, never blocks others
                pass

    # -- consumers (transport-agnostic) ----------------------------------------------------------
    def subscribe(self, public_only: bool, transport: str = "") -> Subscription:
        """Register BEFORE reading the backlog or building a greeting, so nothing falls between."""
        sub = Subscription(asyncio.Queue(maxsize=1000), public_only)
        self._subs.add(sub)
        if transport in self.by_transport:
            self.by_transport[transport] += 1
        return sub

    def unsubscribe(self, sub: Subscription, transport: str = "") -> None:
        if sub in self._subs:
            self._subs.discard(sub)
            if transport in self.by_transport:
                self.by_transport[transport] = max(0, self.by_transport[transport] - 1)

    def backlog(self, last_id: int | None, public_only: bool) -> list[tuple[int, str, str]]:
        if last_id is None:
            return []
        with self._lock:
            return [(eid, ch, p) for eid, ch, p, public in self._recent
                    if eid > last_id and (public or not public_only)]

    # -- SSE framing -------------------------------------------------------------------------------
    async def stream(self, last_id: int | None, public_only: bool, greeting: Greeting | None = None
                     ) -> AsyncIterator[str]:
        """SSE frames for one client: backlog after `last_id`, a hello, the greeting (e.g. a full
        state snapshot), then live events and heartbeats. Greeting frames carry no id: they are
        per-client and must not move Last-Event-ID."""
        sub = self.subscribe(public_only, "sse")
        try:
            yield "retry: 3000\n\n"
            backlog = self.backlog(last_id, public_only)
            for eid, channel, payload in backlog:
                yield f"id: {eid}\nevent: {channel}\ndata: {payload}\n\n"
            hello = {"ts": int(time.time() * 1000), "public": public_only, "resumed": bool(backlog),
                     "transport": "sse"}
            yield f"event: hello\ndata: {json.dumps(hello)}\n\n"
            for channel, data in (greeting() if greeting is not None else []):
                yield f"event: {channel}\ndata: {_body(data)}\n\n"
            while True:
                try:
                    eid, channel, payload = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_S)
                    yield f"id: {eid}\nevent: {channel}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            self.unsubscribe(sub, "sse")


def _body(data: dict[str, Any]) -> str:
    return json.dumps({"ts": int(time.time() * 1000), **data}, separators=(",", ":"), default=str)


def ws_message(channel: str, payload: str, eid: int | None = None) -> str:
    """One WebSocket text message. `payload` is already JSON; it is embedded, not re-encoded."""
    head = f'{{"id":{eid},' if eid is not None else "{"
    return f'{head}"event":{json.dumps(channel)},"data":{payload}}}'


def last_event_id(raw: Any) -> int | None:
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None
