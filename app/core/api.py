"""JSON API. Every route except /api/health sits behind Basic auth (see app/main.py); POSTs also need
the `X-PaperLab: 1` header (CSRF guard, because browsers auto-send Basic credentials)."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.engine import Engine, EngineError
from app.core.storage import EXPORTABLE

router = APIRouter(prefix="/api")


def _engine(request: Request) -> Engine:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise EngineError("engine not started", 503)
    return engine


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    engine: Engine | None = getattr(request.app.state, "engine", None)
    feed = engine.feed.status() if engine is not None and engine.feed is not None else None
    return {
        "ok": True, "mode": settings.mode, "venue": settings.venue.label, "dry_run": settings.dry_run,
        "engine_state": engine.state if engine else "starting",
        "uptime_s": int(time.time() - engine.started_at) if engine else 0,
        "feed_connected": bool(feed and feed["market"]["connected"]),
        "strategies": len(engine.strategies) if engine else 0,
        "boot_error": getattr(request.app.state, "boot_error", None),
    }


@router.get("/state")
async def state(request: Request, curves: int = Query(0)) -> dict[str, Any]:
    return _engine(request).state_payload(curves=bool(curves))


@router.get("/strategies")
async def strategies(request: Request) -> dict[str, Any]:
    e = _engine(request)
    prices = e.prices()
    snap = e.portfolio.snapshot(prices)
    return {"strategies": [e.strategy_row(sid, snap.get(sid, {}), prices) for sid in sorted(e.strategies)]}


@router.get("/strategies/{sid}")
async def strategy_detail(request: Request, sid: str) -> dict[str, Any]:
    return _engine(request).strategy_detail(sid.upper())


@router.post("/strategies/{sid}/enable")
async def strategy_enable(request: Request, sid: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return await _engine(request).set_enabled(sid.upper(), bool(body.get("enabled")))


@router.post("/strategies/{sid}/params")
async def strategy_params(request: Request, sid: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    updates = body.get("params") if isinstance(body.get("params"), dict) else body
    return await _engine(request).set_params(sid.upper(), dict(updates))


@router.post("/strategies/{sid}/allocation")
async def strategy_allocation(request: Request, sid: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    def num(key: str, cast: type) -> Any:
        v = body.get(key)
        return None if v is None or v == "" else cast(v)
    return await _engine(request).set_allocation(sid.upper(), allocation=num("allocation", float),
                                                 leverage=num("leverage", int), size_mult=num("size_mult", float))


@router.post("/strategies/{sid}/solo")
async def strategy_solo(request: Request, sid: str) -> dict[str, Any]:
    """Enable this strategy and disable all the others."""
    return await _engine(request).solo_strategy(sid.upper())


@router.post("/strategies/{sid}/promote")
async def strategy_promote(request: Request, sid: str) -> dict[str, Any]:
    """Move a strategy into the winners list (a durable label, independent of being armed)."""
    return await _engine(request).promote(sid.upper())


@router.post("/strategies/{sid}/demote")
async def strategy_demote(request: Request, sid: str) -> dict[str, Any]:
    return await _engine(request).demote(sid.upper())


@router.post("/strategies/{sid}/flatten")
async def strategy_flatten(request: Request, sid: str) -> dict[str, Any]:
    return await _engine(request).flatten(sid.upper(), "manual", "manual flatten from dashboard")


@router.post("/strategies/{sid}/halt/clear")
async def strategy_halt_clear(request: Request, sid: str) -> dict[str, Any]:
    return await _engine(request).clear_strategy_halt(sid.upper())


@router.post("/strategies/arm-all")
async def strategies_arm_all(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Enable every strategy for the paper bake-off (live routing is unaffected)."""
    return await _engine(request).arm_all(bool(body.get("enabled", True)))


@router.post("/kill")
async def kill(request: Request) -> dict[str, Any]:
    out = await _engine(request).kill()
    breakout = getattr(request.app.state, "video_breakout", None)
    if breakout is not None:
        try:
            out = {**out, "video_breakout": await breakout.flatten("lab kill switch")}
        except Exception as exc:
            out = {**out, "video_breakout": {"status": "BLOCKED", "error": str(exc)}}
    mirror = getattr(request.app.state, "mirror", None)
    if mirror is not None:                     # every live mirror stops and closes its exchange position
        out = {**out, "live_mirrors_stopped": await mirror.disarm_all("kill switch")}
    return out


@router.get("/live")
async def live_status(request: Request) -> dict[str, Any]:
    """What the ARM LIVE panel renders: current mode plus every unmet precondition."""
    e = _engine(request)
    balance: dict[str, float] = {}
    if e.client is not None and e.settings.has_keys:
        try:                                   # display only; arm_live reads it again for the real gate
            balance = await e.client.fetch_balance()
        except Exception:
            balance = {}
    picked = request.query_params.getlist("strategy") if hasattr(request, "query_params") else []
    want = {p.upper() for p in picked} or None
    checks = e.live_preflight(balance, candidates=want)
    return {"live": not e.router.dry_run, "venue": e.settings.venue.label, "wallet": balance,
            "credentials": e.credentials_status(),
            "live_strategies": sorted(e.router.live_strategies or ()),
            "exchange": e.settings.venue.exchange, "phrase": e.ARM_PHRASE,
            "ready": all(c["ok"] for c in checks), "checks": checks}


@router.post("/live/arm")
async def live_arm(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Send REAL orders. Deliberately requires the typed phrase - never call this on a user's behalf."""
    raw = body.get("strategies") or body.get("strategy")
    picks = [raw] if isinstance(raw, str) else list(raw or [])
    return await _engine(request).arm_live(str(body.get("phrase") or ""), [str(p) for p in picks])


@router.post("/live/disarm")
async def live_disarm(request: Request) -> dict[str, Any]:
    return await _engine(request).disarm_live()


@router.post("/live/credentials")
async def live_credentials(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Accept an API key pair, prove it against the venue, keep it in memory only.

    The values are never persisted and never returned: the response carries a masked fingerprint.
    """
    e = _engine(request)
    return await e.set_credentials(str(body.get("key") or ""), str(body.get("secret") or ""))


@router.post("/live/credentials/clear")
async def live_credentials_clear(request: Request) -> dict[str, Any]:
    return await _engine(request).clear_credentials()


@router.post("/engine/start")
async def engine_start(request: Request) -> dict[str, Any]:
    return await _engine(request).start_engine()


@router.post("/engine/stop")
async def engine_stop(request: Request) -> dict[str, Any]:
    return await _engine(request).stop_engine()


@router.post("/engine/resume")
async def engine_resume(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await _engine(request).resume(bool(body.get("confirm")))


@router.post("/flatten")
async def flatten_all(request: Request) -> dict[str, Any]:
    return await _engine(request).flatten(None, "manual", "flatten all from dashboard")


@router.post("/reset")
async def reset(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await _engine(request).reset_paper(bool(body.get("confirm")))


@router.post("/symbols")
async def symbols(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    raw = body.get("symbols")
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list):
        raise EngineError("symbols must be a list or comma-separated string", 400)
    return await _engine(request).change_symbols([str(s) for s in raw])


@router.get("/equity")
async def equity(request: Request, series: str = Query("TOTAL"), since_ts: int = Query(0),
                 points: int = Query(600)) -> dict[str, Any]:
    e = _engine(request)
    return {"series": series, "points": e.storage.equity_series(series.upper(), e.epoch, since_ts, min(points, 5000))}


@router.get("/candles")
async def candles(request: Request, symbol: str = Query(...), tf: str = Query("1m"),
                  limit: int = Query(300)) -> dict[str, Any]:
    e = _engine(request)
    dq = list(e.candles.get((symbol.upper(), tf), []))[-min(limit, 600):]
    return {"symbol": symbol.upper(), "tf": tf,
            "candles": [[c.open_time, c.open, c.high, c.low, c.close, c.volume, c.source] for c in dq]}


@router.get("/tape")
async def tape(request: Request, limit: int = Query(200)) -> dict[str, Any]:
    e = _engine(request)
    limit = min(limit, 1000)
    from app.core.payloads import fill_row
    return {"fills": [fill_row(f) for f in e.storage.fills_recent(limit, epoch=e.epoch)],
            "signals": e.storage.signals_recent(limit), "orders": e.storage.orders_recent(limit),
            "events": e.storage.events_recent(min(limit, 200))}


@router.get("/strategies/{sid}/trades")
async def strategy_trades(request: Request, sid: str, skip: int = Query(0), limit: int = Query(100)) -> dict[str, Any]:
    return _engine(request).trades_page(sid.upper(), max(0, skip), limit)


@router.get("/analysis")
async def analysis(request: Request, strategy: str | None = Query(None), from_: str | None = Query(None, alias="from"),
                   to: str | None = Query(None)) -> dict[str, Any]:
    """Daily performance rows plus this epoch's journal, optionally for one strategy / date range."""
    e = _engine(request)
    sid = strategy.upper() if strategy else None
    rows = e.storage.strategy_daily(e.epoch, sid, from_, to)
    totals: dict[str, Any] = {"days": len({r["day_utc"] for r in rows}), "rows": len(rows),
                              "realized": sum(r["realized"] or 0.0 for r in rows),
                              "fees": sum(r["fees"] or 0.0 for r in rows),
                              "trades": sum(r["trades"] or 0 for r in rows),
                              "wins": sum(r["wins"] or 0 for r in rows),
                              "losses": sum(r["losses"] or 0 for r in rows)}
    totals["win_rate"] = (totals["wins"] / totals["trades"]) if totals["trades"] else None
    return {"epoch": e.epoch, "strategy": sid, "from": from_, "to": to, "daily": rows, "totals": totals,
            "rejects": e.storage.reject_reason_counts(e.epoch, sid),
            "notes": e.storage.notes(e.epoch, sid, 100)}


@router.get("/notes")
async def get_notes(request: Request, strategy: str | None = Query(None), limit: int = Query(50)) -> dict[str, Any]:
    e = _engine(request)
    return {"notes": e.storage.notes(e.epoch, strategy.upper() if strategy else None, min(limit, 500))}


@router.post("/notes")
async def post_note(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    e = _engine(request)
    text = str(body.get("text") or "").strip()
    if not text:
        raise EngineError("text is required", 400)
    sid = str(body.get("strategy_id") or "").upper() or None
    if sid and sid not in e.strategies:
        raise EngineError(f"unknown strategy {sid}", 404)
    note_id = e.storage.insert_note(e.epoch, "user", text, sid, body.get("symbol"), author="user")
    return {"ok": True, "id": note_id}


@router.get("/export.csv")
async def export_csv(request: Request, table: str = Query("fills")) -> StreamingResponse:
    e = _engine(request)
    # export_csv is a generator, so its own ValueError would not surface until the response streams.
    # Validate the table name up front and answer 400 cleanly instead of failing mid-download.
    if table not in EXPORTABLE:
        raise EngineError(f"table must be one of {', '.join(EXPORTABLE)}", 400)
    gen = e.storage.export_csv(table)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = f"paperlab_{table}_{e.epoch}_{stamp}.csv"
    return StreamingResponse(gen, media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


def engine_error_handler(request: Request, exc: EngineError) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status)
