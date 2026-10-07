"""What the realtime stream pushes, and when. The transport is app/core/events.py.

Producers (all server-side; the browser never polls for any of this):

    StatePublisher     the dashboard's /api/state, as per-second DELTAS while a private client is
                       connected: only top-level blocks and strategy rows that actually changed.
                       A new client gets one full snapshot on connect, then deltas chained by seq.
    storage writes     every stored fill, signal and note (Storage.on_write) -> `fill`/`signal`/`note`
    CompetitionWatch   season progress (in memory) and the latest validation / arena / Jev run rows
                       (written by off-process jobs) -> `competition`, only on change
    health             engine, feed, stream and Jev API health every 10 s -> `health`

REST stays the source for history, detail pages, paginated tables and settings.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from app.core.events import EventBus

log = logging.getLogger("paperlab.realtime")

STATE_HZ = 1.0
WATCH_S = 2.0
HEALTH_S = 10.0


def _canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


class StatePublisher:
    """Coalesces the state payload into deltas. Runs on the event loop only (no locking needed).

    delta = {"seq", "base", "ts", "set": {changed top-level blocks}, "rows": {sid: row},
             "order": [sid, ...] | None, "unset": [keys]}

    A client applies a delta only when `base` equals the seq it holds; any gap (a dropped event, a
    reconnect) makes it ask for a fresh snapshot instead of guessing.
    """

    SKIP = ("ts", "strategies", "equity_curves")

    def __init__(self, build: Callable[[], dict[str, Any]]):
        self.build = build
        self.seq = 0
        self.snapshot: dict[str, Any] | None = None
        self._blocks: dict[str, str] = {}
        self._rows: dict[str, str] = {}
        self._order: list[str] = []
        self.last_build_ms = 0.0

    def tick(self) -> dict[str, Any] | None:
        """Rebuild once, diff against the last snapshot, return the delta (None = nothing changed)."""
        t0 = time.perf_counter()
        payload = self.build()
        self.last_build_ms = (time.perf_counter() - t0) * 1000.0
        changed: dict[str, Any] = {}
        for k, v in payload.items():
            if k in self.SKIP:
                continue
            c = _canon(v)
            if self._blocks.get(k) != c:
                self._blocks[k] = c
                changed[k] = v
        unset = [k for k in self._blocks if k not in payload]
        for k in unset:
            del self._blocks[k]
        rows: dict[str, Any] = {}
        order = []
        for row in payload.get("strategies") or []:
            sid = str(row.get("id"))
            order.append(sid)
            c = _canon(row)
            if self._rows.get(sid) != c:
                self._rows[sid] = c
                rows[sid] = row
        for sid in [s for s in self._rows if s not in order]:
            del self._rows[sid]
        order_changed = order != self._order
        self._order = order
        self.snapshot = payload
        if self.seq and not (changed or rows or unset or order_changed):
            return None
        self.seq += 1
        return {"seq": self.seq, "base": self.seq - 1, "ts": payload.get("ts"), "set": changed,
                "rows": rows, "order": order if order_changed else None, "unset": unset}

    def full(self) -> dict[str, Any]:
        return {"full": True, "seq": self.seq, "state": self.snapshot}


class Realtime:
    """Owns the bus and the producer tasks for one app instance."""

    def __init__(self, bus: EventBus | None = None):
        self.bus = bus or EventBus()
        self.engine: Any = None
        self.competition: Any = None
        self.jev: Any = None
        self.shadow: Any = None            # the live shadow service, when running
        self.v6: Any = None                # the V6 forward service, when running
        self.v7: Any = None                # the V7 challenger service
        self.v8: Any = None                # the V8 scalp service
        self.v9: Any = None                # the V9 US-stock service
        self.v11: Any = None               # the V11 multi-coin scanner service
        self.v12: Any = None               # the V12 Bizzy (beebots) service
        self.v13: Any = None               # the V13 Snapback (limit-order reversion) service
        self.v14: Any = None               # the V14 HTF (higher-timeframe copies) service
        self.display_prices: Any = None    # UI only; never read by a trading engine
        self.state: StatePublisher | None = None
        self.tasks: list[asyncio.Task] = []
        self._watch: dict[str, str] = {}
        self._health_sig = ""
        self.started_at = time.time()

    # -- wiring -------------------------------------------------------------------------------
    def attach(self, engine: Any = None, competition: Any = None, jev: Any = None) -> None:
        self.engine = engine
        self.competition = competition
        self.jev = jev
        if engine is not None:
            self.state = StatePublisher(lambda: engine.state_payload(curves=False))
            engine.storage.on_write = self.on_storage_write
            engine.realtime = self

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self.bus.bind(loop)
        self.tasks = [loop.create_task(self._state_loop(), name="rt-state"),
                      loop.create_task(self._watch_loop(), name="rt-watch"),
                      loop.create_task(self._health_loop(), name="rt-health")]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self.tasks.clear()

    # -- storage writes (engine thread = the event loop; publish is thread-safe anyway) ---------
    def on_storage_write(self, kind: str, data: dict[str, Any]) -> None:
        self.bus.publish(kind, data)

    # -- greeting for a new private client ------------------------------------------------------
    def greeting(self) -> list[tuple[str, dict[str, Any]]]:
        """A fresh full snapshot for the new client. Rebuilding here also publishes the delta to
        every existing client, so their seq chain never breaks."""
        out: list[tuple[str, dict[str, Any]]] = []
        if self.state is not None and getattr(self.engine, "state", "") not in ("", "booting"):
            try:
                delta = self.state.tick()
                if delta is not None:
                    self.bus.publish("state", delta, replay=False)
                out.append(("state", self.state.full()))
            except Exception as exc:
                log.warning("state snapshot for a new client failed: %s", str(exc)[:160])
        out.append(("health", self.health_payload(private=True)))
        if self.display_prices is not None:
            out.append(("prices", self.display_prices.snapshot()))
        return out

    def public_greeting(self) -> list[tuple[str, dict[str, Any]]]:
        out = [("health", self.health_payload(private=False))]
        if self.display_prices is not None:
            out.append(("prices", self.display_prices.snapshot()))
        return out

    # -- producers ------------------------------------------------------------------------------
    async def _state_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0 / STATE_HZ)
            if self.state is None or self.bus.private_clients == 0:
                continue
            try:
                delta = self.state.tick()
                if delta is not None:
                    self.bus.publish("state", delta, replay=False)
            except Exception as exc:
                log.warning("state tick failed: %s", str(exc)[:160])

    def competition_summary(self) -> dict[str, dict[str, Any]]:
        """Compact, public-safe summaries of everything a run can be doing. Change detection only."""
        out: dict[str, dict[str, Any]] = {}
        svc = self.competition
        if svc is None:
            return out
        p = svc.progress.to_dict()
        out["season"] = {k: p.get(k) for k in ("run_id", "status", "bots_done", "total_bots", "trades",
                                               "current_bot", "replay_ts", "error")}
        out["season"]["pct"] = p.get("pct")
        st = svc.storage
        try:
            v = st.validation_runs(1)
            if v:
                r = v[0]
                prog = r.get("progress") or {}
                out["validation"] = {"run_id": r.get("run_id"), "status": r.get("status"), "stage": r.get("stage"),
                                     "total": r.get("total_competitors"), "current": prog.get("last"),
                                     "done": prog.get("completed"), "finished_ts": r.get("finished_ts")}
            a = st.arena_runs(1)
            if a:
                r = a[0]
                out["arena"] = {"run_id": r.get("run_id"), "status": r.get("status"), "label": r.get("label"),
                                "active_bots": r.get("active_bots"), "advanced": r.get("advanced"),
                                "finished_ts": r.get("finished_ts")}
            c = st.candidate_runs(1)
            if c:
                r = c[0]
                prog = r.get("progress") or {}
                out["candidates"] = {"run_id": r.get("run_id"), "status": r.get("status"), "label": r.get("label"),
                                     "done": prog.get("done"), "total": prog.get("total"),
                                     "finished_ts": r.get("finished_ts")}
            j = st.jev_runs(1)
            if j:
                r = j[0]
                out["jev"] = {"run_id": r.get("run_id"), "status": r.get("status"), "label": r.get("label"),
                              "pairs": r.get("pairs"), "finished_ts": r.get("finished_ts")}
        except Exception as exc:
            log.debug("competition watch read failed: %s", str(exc)[:160])
        return out

    async def _watch_loop(self) -> None:
        while True:
            await asyncio.sleep(WATCH_S)
            try:
                for kind, summary in self.competition_summary().items():
                    c = _canon(summary)
                    if self._watch.get(kind) != c:
                        first = kind not in self._watch
                        self._watch[kind] = c
                        if not first:
                            self.bus.publish("competition", {"kind": kind, **summary}, public=True)
            except Exception as exc:
                log.warning("competition watch failed: %s", str(exc)[:160])

    def health_payload(self, private: bool) -> dict[str, Any]:
        """System health. The public form carries only what the public API already publishes (stream,
        Jev API health, live shadow status); engine and exchange-feed details stay private, in line
        with api_public never reading the engine."""
        out: dict[str, Any] = {
            "stream": {"status": "LIVE", "clients": self.bus.clients, "published": self.bus.published,
                       "transports": dict(self.bus.by_transport), "uptime_s": int(time.time() - self.started_at)},
        }
        if self.jev is not None:
            try:
                h = self.jev.health()
                out["jev"] = {k: h.get(k) for k in ("status", "configured", "enabled", "model_requested",
                                                    "calls_since_boot", "errors_since_boot",
                                                    "latency_p50_ms", "latency_p95_ms")}
            except Exception as exc:
                out["jev"] = {"status": "ERROR", "detail": str(exc)[:120]}
        if self.shadow is not None:
            try:
                out["shadow"] = self.shadow.health()
            except Exception as exc:
                out["shadow"] = {"status": "ERROR", "detail": str(exc)[:120]}
        for name in ("v6", "v7", "v8", "v9", "v11", "v12", "v13", "v14"):
            svc = getattr(self, name, None)
            if svc is None:
                continue
            try:
                out[name] = svc.health()
            except Exception as exc:
                out[name] = {"status": "ERROR", "detail": str(exc)[:120]}
        e = self.engine
        if e is not None:
            # SAFETY facts every viewer must see (the same ones /public/inspection.json publishes):
            # paper or live, the kill switch, the engine state / risk halt, and whether market data flows.
            try:
                feed = e.feed.status() if getattr(e, "feed", None) is not None else None
                market = (feed or {}).get("market") or {}
                out["safety"] = {
                    "execution": "PAPER" if bool(getattr(getattr(e, "router", None), "dry_run", True)) else "LIVE",
                    "kill_switch": "ENGAGED" if bool(getattr(getattr(getattr(e, "risk", None), "state", None), "killed", False)) else "off",
                    "engine_state": str(getattr(e, "state", "starting")), "risk_halt": getattr(e, "paused_reason", None) or None,
                    "market_feed": "LIVE" if market.get("connected") else "DOWN", "market_age_s": market.get("last_msg_age_s")}
            except Exception as exc:
                out["safety"] = {"status": "ERROR", "detail": str(exc)[:120]}
        if private and e is not None:
            feed = e.feed.status() if getattr(e, "feed", None) is not None else None
            market = (feed or {}).get("market") or {}
            out["engine"] = {"state": getattr(e, "state", "starting"),
                             "paused_reason": getattr(e, "paused_reason", None),
                             "state_build_ms": round(self.state.last_build_ms, 1) if self.state else None}
            out["feed"] = {"connected": bool(market.get("connected")), "age_s": market.get("last_msg_age_s")}
        return out

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(HEALTH_S)
            try:
                self.bus.publish("health", self.health_payload(private=False), public=True)
            except Exception as exc:
                log.warning("health publish failed: %s", str(exc)[:160])
