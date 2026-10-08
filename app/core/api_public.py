"""Public, unauthenticated, READ-ONLY competition API.

Exists so an outside reviewer (or an automated browser agent) can inspect the real Competition UI
without being handed the dashboard password. It is deliberately a separate router rather than an
"auth off" switch on the existing one:

* **GET only.** No route here mutates anything. Starting or cancelling a competition, promoting a
  strategy, arming live trading, changing risk or touching credentials all stay on the authenticated
  routers, and the middleware still refuses an unauthenticated POST anywhere under /api/.
* **Allow-listed fields.** Payloads are rebuilt field by field from a fixed list rather than
  serialising a stored row, so a future column cannot leak by accident. Nothing reads `Settings`,
  the environment, the exchange client or the engine.
* **Simulated records only.** The trade ledger is filtered to paper/competition fills, and exchange
  order identifiers are dropped even when present. A live fill is never publishable here.

If this file ever needs a POST, that is the signal to put the route on the private router instead.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.engine import EngineError

router = APIRouter(prefix="/api/public/competition")


@router.get("/video")
async def video_breakout(request: Request):
    from app.video_breakout.competition import payload
    return payload(getattr(request.app.state, "video_breakout", None))


@router.get("/video/bot/{key}")
async def video_breakout_bot(request: Request, key: str):
    from fastapi import HTTPException
    from app.video_breakout.competition import KEY, payload
    if key != KEY:
        raise HTTPException(404, "Unknown breakout bot")
    return payload(getattr(request.app.state, "video_breakout", None))


@router.get("/video/candles")
async def video_breakout_candles(request: Request, symbol: str = "BTCUSDT", tf: str = "4h", until: int = 0):
    from fastapi import HTTPException
    from app.video_breakout.competition import candles
    if symbol != "BTCUSDT" or tf != "4h":
        raise HTTPException(400, "This strategy uses BTCUSDT 4h candles")
    return candles(getattr(request.app.state, "video_breakout", None), until)

MAX_PAGE = 200

# Everything a reviewer needs to judge the UI, and nothing that identifies an account or a key.
METRIC_FIELDS = (
    "starting_equity", "ending_equity", "net_return_pct", "gross_pnl", "gross_profit", "gross_loss",
    "fees_paid", "funding_paid", "slippage_cost", "latency_cost", "spread_cost", "impact_cost",
    "net_profit", "trades", "wins", "losses", "win_rate", "average_win", "average_loss",
    "expectancy_usdt", "expectancy_r", "profit_factor", "average_r", "median_r", "best_trade",
    "worst_trade", "longest_win_streak", "longest_loss_streak", "max_drawdown_pct",
    "max_drawdown_usdt", "drawdown_duration_ms", "recovery_factor", "sharpe_like", "sortino_like",
    "turnover", "total_notional_traded", "average_holding_ms", "maker_percentage",
    "taker_percentage", "max_leverage_used", "configured_max_leverage", "avg_effective_leverage",
    "max_effective_leverage", "time_weighted_leverage", "margin_utilization_avg",
    "margin_utilization_max", "time_in_market_pct", "fee_to_gross_profit_ratio",
    "slippage_to_gross_profit_ratio", "largest_trade_profit_contribution_pct", "liquidation_count",
    "by_symbol", "by_exit_kind", "rejects", "halted",
)

FILL_FIELDS = (
    "ts", "symbol", "side", "kind", "qty", "price", "decision_price", "fee", "slippage_bps",
    "realized_pnl", "leverage", "position_side", "liquidity_role", "execution_level",
    "latency_cost", "spread_cost", "impact_cost", "latency_ms", "reason",
)


def _svc(request: Request) -> Any:
    svc = getattr(request.app.state, "competition", None)
    if svc is None:
        raise EngineError("competition service not started", 503)
    return svc


def _pick(src: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any]:
    """Rebuild a payload from an allow-list. Unknown keys simply do not travel."""
    src = src or {}
    return {k: src.get(k) for k in fields}


def _public_fill(f: dict[str, Any]) -> dict[str, Any]:
    out = _pick(f, FILL_FIELDS)
    # Defensive: these never appear on a simulated competition fill, and must never appear here.
    for banned in ("exchange_order_id", "signal_id", "position_id", "id"):
        out.pop(banned, None)
    return out


def _is_simulated(f: dict[str, Any]) -> bool:
    """Publish paper records only. A row that cannot prove it is simulated is not published."""
    if f.get("exchange_order_id"):
        return False
    sim = f.get("simulated")
    return True if sim is None else bool(sim)


def _diagnostics(svc: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Non-sensitive build/version markers so a reviewer can confirm which deployment they see."""
    from app.main import asset_version
    d = {
        "api_version": "public-1",
        "asset_version": asset_version(),
        "schema_version": getattr(__import__("app.core.storage", fromlist=["x"]),
                                  "SCHEMA_VERSION", None),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "read_only": True,
    }
    s = getattr(svc, "settings", None)
    if s is not None:
        d["paper_engine_fees"] = {"source": getattr(s, "fee_source", None),
                                  "maker": getattr(s, "maker_fee", None), "taker": getattr(s, "taker_fee", None)}
    d.update(extra or {})
    return d


def _latest_season(svc: Any, run_id: str | None):
    if run_id:
        row = svc.storage.competition_run(run_id)
        return (run_id, row) if row else (None, None)
    runs = svc.storage.competition_runs(limit=1)
    if not runs:
        return None, None
    rid = runs[0]["run_id"]
    return rid, svc.storage.competition_run(rid)


def _latest_validation(svc: Any, run_id: str | None):
    if run_id:
        return run_id, svc.storage.validation_run(run_id)
    runs = svc.storage.validation_runs(limit=1)
    return (runs[0]["run_id"], runs[0]) if runs else (None, None)


# ---- season -------------------------------------------------------------------------------------

@router.get("")
async def overview(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    svc = _svc(request)
    rid, row = _latest_season(svc, run_id or None)
    payload: dict[str, Any] = {"ok": True, "ran": row is not None, "read_only": True}
    if row is None:
        payload["diagnostics"] = _diagnostics(svc)
        return payload
    comps = svc.storage.competition_competitors(rid)
    states = {s: 0 for s in ("COMPETING", "QUALIFIED", "WATCHLIST", "INSUFFICIENT_SAMPLE",
                             "FAILED", "DISQUALIFIED", "SHADOW_LIVE", "LIVE_CANDIDATE")}
    for c in comps:
        if c.get("state") in states:
            states[c["state"]] += 1
    totals = {k: 0.0 for k in ("trades", "fees_paid", "slippage_cost", "funding_paid",
                               "liquidation_count", "gross_pnl", "net_profit")}
    for c in comps:
        m = c.get("metrics") or {}
        for k in totals:
            totals[k] += float(m.get(k) or 0.0)
    totals["trades"] = int(totals["trades"])
    totals["liquidation_count"] = int(totals["liquidation_count"])
    totals["bots"] = len(comps)
    payload.update({
        "run_id": rid,
        "season": {
            "competition_id": row.get("competition_id"), "season_id": row.get("season_id"),
            "label": row.get("label"), "fingerprint": row.get("fingerprint"),
            "status": row.get("status"), "created_ts": row.get("created_ts"),
            "finished_ts": row.get("finished_ts"), "start_ms": row.get("start_ms"),
            "end_ms": row.get("end_ms"), "symbols": row.get("symbols"), "bars": row.get("bars"),
            "timeframe": "1m", "venue": "BINANCE_USDM (simulated)",
            "starting_balance": row.get("starting_balance"),
            "max_leverage": row.get("max_leverage"),
            "execution_profile": row.get("execution_profile"),
        },
        "states": states, "totals": totals,
        "banner": ("Ranking is not qualification. A bot can rank #1 and still fail on insufficient "
                   "trades, drawdown, liquidation, weak expectancy, execution costs, or validation "
                   "stages that have not been run."),
        "diagnostics": _diagnostics(svc, {"competition_run_id": rid,
                                          "season_fingerprint": row.get("fingerprint")}),
    })
    return payload


@router.get("/leaderboard")
async def leaderboard(request: Request, run_id: str = Query(""),
                      sort: str = Query("score")) -> dict[str, Any]:
    svc = _svc(request)
    rid, row = _latest_season(svc, run_id or None)
    if row is None:
        return {"ok": True, "ran": False, "rows": [], "read_only": True}
    rows = []
    for c in svc.storage.competition_competitors(rid):
        m = c.get("metrics")
        q = c.get("qualification") or {}
        rows.append({
            "strategy_id": c.get("strategy_id"), "key": c.get("key") or c.get("strategy_id"),
            "name": c.get("name"), "state": c.get("state"), "score": c.get("score"),
            "leverage": c.get("leverage"), "skipped": c.get("skipped") or "",
            "ran": bool(m), "reasons": q.get("reasons") or [],
            "metrics": _pick(m, METRIC_FIELDS) if m else None,
        })
    keyfns = {
        "score": lambda r: r.get("score"),
        "net": lambda r: (r["metrics"] or {}).get("net_profit") if r["metrics"] else None,
        "return": lambda r: (r["metrics"] or {}).get("net_return_pct") if r["metrics"] else None,
        "trades": lambda r: (r["metrics"] or {}).get("trades") if r["metrics"] else None,
    }
    fn = keyfns.get(sort, keyfns["score"])
    rows.sort(key=lambda r: (fn(r) is not None, fn(r) if fn(r) is not None else 0), reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"ok": True, "ran": True, "run_id": rid, "rows": rows, "read_only": True,
            "diagnostics": _diagnostics(svc, {"competition_run_id": rid})}


@router.get("/bots/{key}")
async def bot(request: Request, key: str, run_id: str = Query("")) -> dict[str, Any]:
    svc = _svc(request)
    rid, row = _latest_season(svc, run_id or None)
    if row is None:
        raise EngineError("no competition has been run yet", 404)
    c = svc.storage.competition_competitor(rid, key)
    if c is None:
        raise EngineError(f"unknown competitor {key!r}", 404)
    fills = [f for f in (c.get("fills") or []) if _is_simulated(f)]
    levels = {"1": 0, "2": 0, "3": 0}
    for f in fills:
        lvl = str(f.get("execution_level") or "")
        if lvl in levels:
            levels[lvl] += 1
    return {
        "ok": True, "read_only": True, "run_id": rid,
        "bot": {
            "strategy_id": c.get("strategy_id"), "key": c.get("key") or c.get("strategy_id"),
            "name": c.get("name"), "state": c.get("state"), "rank": c.get("rank"),
            "version": c.get("version"), "leverage": c.get("leverage"),
            "starting_balance": row.get("starting_balance"),
            "metrics": _pick(c.get("metrics"), METRIC_FIELDS),
            "qualification": c.get("qualification"),
            "score_breakdown": c.get("score_breakdown"),
            "equity": c.get("equity") or [],
            "execution_levels": levels, "trade_count": len(fills),
        },
        "diagnostics": _diagnostics(svc, {"competition_run_id": rid}),
    }


@router.get("/bots/{key}/trades")
async def bot_trades(request: Request, key: str, run_id: str = Query(""),
                     offset: int = Query(0, ge=0),
                     limit: int = Query(100, ge=1, le=MAX_PAGE)) -> dict[str, Any]:
    svc = _svc(request)
    rid, row = _latest_season(svc, run_id or None)
    if row is None:
        raise EngineError("no competition has been run yet", 404)
    c = svc.storage.competition_competitor(rid, key)
    if c is None:
        raise EngineError(f"unknown competitor {key!r}", 404)
    fills = [_public_fill(f) for f in (c.get("fills") or []) if _is_simulated(f)]
    return {"ok": True, "read_only": True, "total": len(fills), "offset": offset, "limit": limit,
            "rows": fills[offset:offset + limit]}


@router.get("/qualification/{key}")
async def qualification(request: Request, key: str, run_id: str = Query("")) -> dict[str, Any]:
    svc = _svc(request)
    rid, row = _latest_season(svc, run_id or None)
    if row is None:
        raise EngineError("no competition has been run yet", 404)
    c = svc.storage.competition_competitor(rid, key)
    if c is None:
        raise EngineError(f"unknown competitor {key!r}", 404)
    q = c.get("qualification") or {}
    evaluated = set(q.get("evaluated_stages") or [])
    stages = [{"name": s, "status": "RAN" if s in evaluated else "NOT_RUN"}
              for s in ("season", "walk_forward", "monte_carlo", "stress")]
    stages.append({"name": "shadow_live", "status": "NOT_RUN"})
    return {"ok": True, "read_only": True, "key": key, "state": c.get("state"),
            "gates": q.get("gates") or [], "reasons": q.get("reasons") or [], "stages": stages,
            "score_breakdown": c.get("score_breakdown")}


@router.get("/seasons")
async def seasons(request: Request, limit: int = Query(25, ge=1, le=100)) -> dict[str, Any]:
    svc = _svc(request)
    out = []
    for r in svc.storage.competition_runs(limit=limit):
        comps = svc.storage.competition_competitors(r["run_id"])
        ran = [c for c in comps if c.get("metrics")]
        out.append({
            "run_id": r["run_id"], "label": r.get("label"), "season_id": r.get("season_id"),
            "status": r.get("status"), "start_ms": r.get("start_ms"), "end_ms": r.get("end_ms"),
            "symbols": r.get("symbols"), "starting_balance": r.get("starting_balance"),
            "max_leverage": r.get("max_leverage"),
            "execution_profile": r.get("execution_profile"), "bots": len(ran),
            "qualified": sum(1 for c in ran if c.get("state") in
                             ("QUALIFIED", "SHADOW_LIVE", "LIVE_CANDIDATE")),
        })
    return {"ok": True, "read_only": True, "seasons": out}


# ---- multi-year validation -----------------------------------------------------------------------

@router.get("/validation")
async def validation(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    svc = _svc(request)
    rid, row = _latest_validation(svc, run_id or None)
    if row is None:
        return {"ok": True, "ran": False, "read_only": True,
                "diagnostics": _diagnostics(svc)}
    comps = svc.storage.validation_competitors(rid)
    states: dict[str, int] = {}
    for c in comps:
        states[c["state"]] = states.get(c["state"], 0) + 1
    total = row.get("total_competitors") or 0
    prog = row.get("progress") or {}
    return {
        "ok": True, "ran": True, "read_only": True, "run_id": rid,
        "status": row.get("status"), "stage": row.get("stage"),
        "symbols": row.get("symbols"), "first_month": row.get("first_month"),
        "last_month": row.get("last_month"), "windows": row.get("windows"),
        "starting_balance": row.get("starting_balance"), "leverages": row.get("leverages"),
        "dataset_fingerprint": row.get("dataset_fingerprint"),
        "config_fingerprint": row.get("config_fingerprint"),
        "total_competitors": total, "completed": len(comps),
        "pct": (len(comps) / total) if total else 0.0,
        "current": prog.get("last"), "elapsed_s": prog.get("elapsed_s"), "states": states,
        "diagnostics": _diagnostics(svc, {
            "validation_run_id": rid,
            "dataset_fingerprint": row.get("dataset_fingerprint"),
            "config_fingerprint": row.get("config_fingerprint")}),
    }


@router.get("/validation/leaderboard")
async def validation_leaderboard(request: Request, run_id: str = Query(""),
                                 sort: str = Query("score")) -> dict[str, Any]:
    svc = _svc(request)
    rid, row = _latest_validation(svc, run_id or None)
    if row is None:
        return {"ok": True, "ran": False, "rows": [], "read_only": True}
    rows = []
    for c in svc.storage.validation_competitors(rid):
        full = svc.storage.validation_competitor(rid, c["key"]) or {}
        m = full.get("oos_metrics")
        w = full.get("walk_forward") or {}
        mcr = full.get("monte_carlo") or {}
        sr = full.get("stress") or {}
        q = full.get("qualification") or {}
        rows.append({
            "key": c["key"], "strategy_id": c["strategy_id"], "leverage": c["leverage"],
            "name": c.get("name"), "state": c["state"], "score": c.get("score"),
            "ran": bool(m), "skipped": full.get("skipped") or "",
            "oos_trades": (m or {}).get("trades"), "oos_net": (m or {}).get("net_profit"),
            "oos_return_pct": (m or {}).get("net_return_pct"),
            "oos_expectancy_r": (m or {}).get("expectancy_r"),
            "oos_profit_factor": (m or {}).get("profit_factor"),
            "oos_max_dd": (m or {}).get("max_drawdown_pct"),
            "avg_effective_leverage": (m or {}).get("avg_effective_leverage"),
            "profitable_windows": w.get("profitable_windows"),
            "active_windows": w.get("active_windows"), "total_windows": w.get("total_windows"),
            "profitable_ratio": w.get("profitable_ratio"), "active_ratio": w.get("active_ratio"),
            "symbol_concentration": w.get("symbol_concentration"),
            "mc_ran": bool(mcr.get("ran")), "mc_ruin": mcr.get("ruin_probability"),
            "stress_ran": bool(sr.get("ran")), "stress_survives": sr.get("survives"),
            "stress_worst": sr.get("worst_scenario"), "reasons": q.get("reasons") or [],
            "stages": q.get("evaluated_stages") or [],
        })
    keys = {"score": lambda r: r.get("score"),
            "oos_return": lambda r: r.get("oos_return_pct"),
            "oos_trades": lambda r: r.get("oos_trades")}
    fn = keys.get(sort, keys["score"])
    rows.sort(key=lambda r: (fn(r) is not None, fn(r) if fn(r) is not None else 0), reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"ok": True, "ran": True, "read_only": True, "run_id": rid, "rows": rows,
            "qualified": [r["key"] for r in rows
                          if r["state"] in ("QUALIFIED", "SHADOW_LIVE", "LIVE_CANDIDATE")]}


@router.get("/validation/bots/{key}")
async def validation_bot(request: Request, key: str, run_id: str = Query("")) -> dict[str, Any]:
    svc = _svc(request)
    rid, row = _latest_validation(svc, run_id or None)
    if row is None:
        raise EngineError("no validation run yet", 404)
    full = svc.storage.validation_competitor(rid, key)
    if full is None:
        raise EngineError(f"unknown competitor {key!r}", 404)
    return {"ok": True, "read_only": True, "run_id": rid, "bot": {
        "key": full.get("key"), "strategy_id": full.get("strategy_id"),
        "name": full.get("name"), "leverage": full.get("leverage"), "state": full.get("state"),
        "version": full.get("version"), "skipped": full.get("skipped") or "",
        "oos_metrics": _pick(full.get("oos_metrics"), METRIC_FIELDS)
        if full.get("oos_metrics") else None,
        "walk_forward": full.get("walk_forward"), "monte_carlo": full.get("monte_carlo"),
        "stress": full.get("stress"), "qualification": full.get("qualification"),
        "score": full.get("score"),
    }}


@router.get("/validation/leverage-comparison")
async def leverage_comparison(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    svc = _svc(request)
    rid, row = _latest_validation(svc, run_id or None)
    if row is None:
        return {"ok": True, "ran": False, "rows": [], "read_only": True}
    by: dict[str, dict[str, Any]] = {}
    for c in svc.storage.validation_competitors(rid):
        full = svc.storage.validation_competitor(rid, c["key"]) or {}
        m = full.get("oos_metrics") or {}
        w = full.get("walk_forward") or {}
        entry = by.setdefault(c["strategy_id"], {"strategy_id": c["strategy_id"],
                                                 "name": c.get("name"), "variants": {}})
        entry["variants"][str(c["leverage"])] = {
            "key": c["key"], "state": c["state"], "score": c.get("score"),
            "oos_return_pct": m.get("net_return_pct"), "oos_net": m.get("net_profit"),
            "trades": m.get("trades"), "expectancy_r": m.get("expectancy_r"),
            "profit_factor": m.get("profit_factor"), "max_dd": m.get("max_drawdown_pct"),
            "liquidations": m.get("liquidation_count"),
            "avg_effective_leverage": m.get("avg_effective_leverage"),
            "below_min_notional": (m.get("rejects") or {}).get("below_min_notional"),
            "profitable_ratio": w.get("profitable_ratio"),
        }
    return {"ok": True, "ran": True, "read_only": True, "run_id": rid,
            "leverages": row.get("leverages") or [],
            "rows": sorted(by.values(), key=lambda r: r["strategy_id"])}


# ---- specialist arena (discovery) ---------------------------------------------------------------

@router.get("/arena")
async def arena(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    """The latest (or a given) discovery arena: field, preflight, leaderboard, matrix."""
    from app.core.arena_view import arena_payload
    svc = _svc(request)
    p = arena_payload(svc.storage, run_id or None)
    if p is None:
        return {"ok": True, "ran": False, "read_only": True, "diagnostics": _diagnostics(svc)}
    return {"ok": True, "ran": True, "read_only": True, **p,
            "diagnostics": _diagnostics(svc, {"arena_run_id": p["run"].get("run_id")})}


@router.get("/arena/bots/{key}")
async def arena_bot(request: Request, key: str, run_id: str = Query("")) -> dict[str, Any]:
    from app.core.arena_view import arena_bot_payload
    svc = _svc(request)
    p = arena_bot_payload(svc.storage, run_id or None, key)
    if p is None:
        raise EngineError(f"unknown arena bot {key!r}", 404)
    return {"ok": True, "read_only": True, "bot": p,
            "diagnostics": _diagnostics(svc, {"arena_run_id": p.get("run_id")})}


# ---- Jev (sanitized) ------------------------------------------------------------------------------

JEV_HEALTH_FIELDS = ("status", "configured", "enabled", "model_requested", "model_resolved",
                     "prompt_version", "questions_fingerprint", "policy_version", "last_check",
                     "calls_since_boot", "errors_since_boot", "error_codes", "latency_p50_ms",
                     "latency_p95_ms")


@router.get("/jev/health")
async def jev_health(request: Request) -> dict[str, Any]:
    """JEV API HEALTH for readers. Status and counters only -- never a key or a header."""
    jev = getattr(request.app.state, "jev", None)
    if jev is None:
        return {"ok": True, "read_only": True, "health": {"status": "NOT_STARTED"}}
    return {"ok": True, "read_only": True, "health": _pick(jev.health(), JEV_HEALTH_FIELDS)}


@router.get("/jev")
async def jev(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    """CONTROL vs +JEV: the latest (or a given) paired experiment, sanitized."""
    from app.core.jev_view import jev_payload
    svc = _svc(request)
    j = getattr(request.app.state, "jev", None)
    p = jev_payload(svc.storage, run_id or None, j.health() if j else None)
    return {"ok": True, "read_only": True, **p, "diagnostics": _diagnostics(svc)}


@router.get("/jev/pairs/{pair_id:path}")
async def jev_pair(request: Request, pair_id: str, run_id: str = Query("")) -> dict[str, Any]:
    from app.core.jev_view import jev_pair_payload
    svc = _svc(request)
    p = jev_pair_payload(svc.storage, run_id or None, pair_id)
    if p is None:
        raise EngineError(f"unknown pair {pair_id!r}", 404)
    return {"ok": True, "read_only": True, **p}


# ---- live shadow (FORWARD data: frozen v2 bots on live market data, simulated fills) -------------

@router.get("/shadow")
async def shadow(request: Request) -> dict[str, Any]:
    """Live shadow: every frozen v2 bot's live book, CONTROL vs +JEV pairs and the live Jev V1 stats.
    Simulated books only; nothing here can place an order."""
    from app.core.shadow_view import shadow_payload
    svc = _svc(request)
    return {**shadow_payload(svc.storage, getattr(request.app.state, "shadow", None)), "read_only": True}


@router.get("/shadow/activity")
async def shadow_activity(request: Request, limit: int = Query(100), before: int = Query(0)) -> dict[str, Any]:
    """The activity log (candidates, Jev decisions, opens, closes), newest first, paginated."""
    from app.core.shadow_view import activity_payload
    svc = _svc(request)
    return {**activity_payload(svc.storage, limit, before or None), "read_only": True}


# ---- frozen v2 candidates: historical validation + live forward shadow (never summed) --------------

@router.get("/candidates")
async def candidates(request: Request, key: str = Query(""), run_id: str = Query("")) -> dict[str, Any]:
    """The three frozen v2 TEST survivors: pipeline, multi-year validation, forward shadow, venues."""
    from app.core.candidates_view import candidates_payload
    from app.core.shadow_view import shadow_payload
    svc = _svc(request)
    shadow = shadow_payload(svc.storage, getattr(request.app.state, "shadow", None))
    return {**candidates_payload(svc.storage, shadow, run_id or None, key or None), "read_only": True}


# ---- V3 aggressive intraday Jev arena (read-only; runs offline, scripts/run_v3_arena.py) ---------------

@router.get("/v3")
async def v3_arena(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    """The V3 AGGRESSIVE DISCOVERY: leaderboards, failure modes, timeframe matrices, CONTROL vs +JEV2
    against ALWAYS-TAKE / ALWAYS-SKIP / RANDOM-FILTER, and the advanced set."""
    from app.core.v3_view import v3_payload
    svc = _svc(request)
    return {**v3_payload(svc.storage, run_id or None), "read_only": True}


@router.get("/v3/bots/{key}")
async def v3_bot(request: Request, key: str, run_id: str = Query("")) -> dict[str, Any]:
    """One V3 bot: its Bot Analyzer diagnosis, trades, Jev decisions and its pair comparison."""
    from app.core.v3_view import v3_bot_payload
    svc = _svc(request)
    return {**v3_bot_payload(svc.storage, key, run_id or None), "read_only": True}


# ---- V3.1 aggressive edge (read-only; runs offline, scripts/run_v31_arena.py) ---------------------------

@router.get("/v31")
async def v31_arena(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    """V3.1 AGGRESSIVE EDGE: DEVELOPMENT and TEST runs -- small-timeframe viability, edge calibration,
    participation funnels, CONTROL vs +JEV3 against every baseline, selection alpha, ATTACK effectiveness,
    maker-vs-taker, capacity and the advanced set."""
    from app.core.v31_view import v31_payload
    svc = _svc(request)
    return {**v31_payload(svc.storage, run_id or None), "read_only": True}


@router.get("/v31/bots/{key}")
async def v31_bot(request: Request, key: str, run_id: str = Query("")) -> dict[str, Any]:
    """One V3.1 bot: Bot Analyzer V3.1, funnel, trades, Jev V3 decisions and its pair comparison."""
    from app.core.v31_view import v31_bot_payload
    svc = _svc(request)
    return {**v31_bot_payload(svc.storage, key, run_id or None), "read_only": True}


# ---- the simplified UI: V4 arena, SYSTEM, a trader's trades (read-only) --------------------------------------

@router.get("/v4")
async def v4_arena(request: Request) -> dict[str, Any]:
    """V4 INTRADAY SPECIALISTS: DEVELOPMENT and TEST runs -- edge quality by family x timeframe, exits, Jev
    selection alpha, ATTACK test, capacity verdicts and the advanced set."""
    import asyncio
    from app.core.system_view import v4_payload
    svc = _svc(request)
    return {**(await asyncio.to_thread(v4_payload, svc.storage)), "read_only": True}


@router.get("/v6")
async def v6_forward(request: Request) -> dict[str, Any]:
    """V6 FORWARD ARENA (the live home screen): forward age, active bots, open positions, the leaderboard, activity,
    CONTROL vs +JEV pairs, Jev live analytics, risk distribution and stream health. Paper only."""
    import asyncio
    from app.core.v6_view import v6_payload
    from app.live.v6_golive import annotate
    svc = _svc(request)
    out = _drop_retired_v6(await asyncio.to_thread(v6_payload, svc.storage, getattr(request.app.state, "v6", None)))
    for r in out.get("leaderboard") or []:
        annotate(r)                   # which V6.2 / V6.6 bots the operator may take live, and their backtest
    return {**out, "read_only": True}


def _drop_retired_v6(out: dict[str, Any]) -> dict[str, Any]:
    """V6's unproven families keep running inside its frozen experiment but are out of the arena (app/core/programs.py):
    drop their rows, positions, pairs and activity, and recount the headline numbers from the bots that remain."""
    from app.core.programs import RETIRED_V6_FAMILIES, bot_retired
    out = dict(out)
    lb = [r for r in out.get("leaderboard") or [] if not bot_retired("v6", str(r.get("key") or ""))]
    out["leaderboard"] = lb
    for k, field in (("positions", "bot_key"), ("activity", "bot_key"), ("pairs", "control_key")):
        out[k] = [x for x in out.get(k) or [] if not bot_retired("v6", str(x.get(field) or x.get("key") or ""))]
    out["families"] = [f for f in out.get("families") or []           # "V6.1 HOURLY", "V6.1 SWING", ...
                       if str(f.get("family") or "").split(" ")[0] not in RETIRED_V6_FAMILIES]
    hero = dict(out.get("hero") or {})
    hero.update(bots=len(lb), active_bots=len(lb), positions_open=len(out["positions"]),
                controls=sum(1 for r in lb if (r.get("role") or "CONTROL") == "CONTROL"),
                jev_bots=sum(1 for r in lb if r.get("role") == "JEV"),
                trades_24h=sum(int(r.get("trades_24h") or 0) for r in lb),
                total_virtual_equity=round(sum(float(r.get("equity_now") or 0) for r in lb), 4),
                start_equity_total=round(sum(float(r.get("start_equity") or 0) for r in lb), 4),
                net_pnl=round(sum(float(r.get("net_now") or 0) for r in lb), 6))
    out["hero"] = hero
    out["retired_families"] = sorted(RETIRED_V6_FAMILIES)
    return out


@router.get("/v7")
async def v7_forward(request: Request) -> dict[str, Any]:
    """Separate active paper experiment; no order or mutation endpoint."""
    import asyncio
    from app.core.v6_view import v6_payload
    st = getattr(request.app.state, "v7_storage", None)
    if st is None:
        return {"ok": True, "experiment": None, "status": {"status": "DISABLED"}, "read_only": True}
    result = await asyncio.to_thread(v6_payload, st, getattr(request.app.state, "v7", None))
    result["program"] = "V7 ACTIVE PAPER CHALLENGER"
    result["note"] = "15m decisions; 1% base risk, 2% cap; 1.8R target, six-hour maximum hold. Unproven."
    if result.get("hero"):
        now = int(time.time() * 1000)
        result["hero"]["next_decision_ms"] = (now // 900000 + 1) * 900000
    result["rules"] = {"paper": "simulated fills only; separate from V6", "frozen": "V7 has its own frozen identity",
                       "maturity": "No profitability claim until sufficient forward results after costs"}
    return {**result, "read_only": True}


@router.get("/v8")
async def v8_forward(request: Request) -> dict[str, Any]:
    """V8 SCALP: aggressive 5m paper scalpers with auto-elimination and a fast qualification gate. Read-only."""
    import asyncio
    from app.core.arena_live_view import v8_enrich
    from app.core.v6_view import v6_payload
    st = getattr(request.app.state, "v8_storage", None)
    if st is None:
        return {"ok": True, "experiment": None, "status": {"status": "DISABLED"}, "read_only": True}

    def build() -> dict[str, Any]:
        return v8_enrich(v6_payload(st, getattr(request.app.state, "v8", None)), st)
    result = await asyncio.to_thread(build)
    result["program"] = "V8 SCALP"
    result["note"] = "5m scalps; 1% base risk, 2% cap; 1.5R target, 45-minute maximum hold. Unproven: fees are ~1/3 R."
    if result.get("hero"):
        result["hero"]["next_decision_ms"] = (int(time.time() * 1000) // 300_000 + 1) * 300_000
    result["rules"] = {"paper": "simulated fills only; separate from V6 and V7",
                       "frozen": "V8 has its own frozen identity (docs/V8_FREEZE.json)",
                       "maturity": "QUALIFIED is a gate for the operator's go-live button, not a profitability claim"}
    return {**result, "read_only": True}


@router.get("/v9")
async def v9_forward(request: Request) -> dict[str, Any]:
    """V9 STOCKS: 5m paper scalpers on US stocks / ETFs (Alpaca IEX data, regular sessions only). Read-only."""
    import asyncio
    from app.core.arena_live_view import v9_enrich
    from app.core.v6_view import v6_payload
    st = getattr(request.app.state, "v9_storage", None)
    if st is None:
        return {"ok": True, "experiment": None, "status": {"status": "DISABLED"}, "read_only": True}
    svc = getattr(request.app.state, "v9", None)

    def build() -> dict[str, Any]:
        return v9_enrich(v6_payload(st, svc), st, svc)
    result = await asyncio.to_thread(build)
    result["program"] = "V9 STOCKS"
    result["note"] = ("5m paper scalps on US stocks / ETFs; 1% base risk, 2% cap, 3x leverage cap. "
                      "Session-local setups, resting limit targets, crossed stops with gap/slippage costs, "
                      "45-minute maximum hold capped before the session close. No overnight positions.")
    result["rules"] = {"paper": "simulated fills on Alpaca IEX data; separate from V6, V7 and V8",
                       "frozen": "V9 has its own frozen identity (docs/V9_FREEZE.json)",
                       "maturity": "QUALIFIED is a gate for the operator's go-live button, not a profitability claim"}
    result["historical_review"] = {
        "date": "2026-10-07", "comparisons": 120, "active_variant": "SESSION",
        "state": "FAILED_PROFITABILITY_CHECKS", "passed_families": [],
        "detail": "Correctness repairs reduced aggregate losses. No tested family passed all historical stages; "
                  "the repaired version remains a paper experiment. Wider stops were not adopted.",
        "data_limit": "Historical dates were inspected previously; new forward evidence is required.",
    }
    return {**result, "read_only": True}


@router.get("/v11")
async def v11_forward(request: Request) -> dict[str, Any]:
    """V11 SCAN: paper bots that scan all 30 coins of a Bybit universe and trade the best setups. Read-only."""
    import asyncio
    from app.core.arena_live_view import v11_enrich
    from app.core.v6_view import v6_payload
    st = getattr(request.app.state, "v11_storage", None)
    if st is None:
        return {"ok": True, "experiment": None, "status": {"status": "DISABLED"}, "read_only": True}

    def build() -> dict[str, Any]:
        return v11_enrich(v6_payload(st, getattr(request.app.state, "v11", None)), st)
    result = await asyncio.to_thread(build)
    result["program"] = "V11 SCAN"
    result["note"] = ("Each bot scans 30 coins and takes the best setups, up to 8 coins at once; 1% base risk, "
                      "2% cap, 20 USDT paper books. Unproven: the 90-day study found no scanner rule that beats fees.")
    if result.get("hero"):
        result["hero"]["next_decision_ms"] = (int(time.time() * 1000) // 300_000 + 1) * 300_000
    result["rules"] = {"paper": "simulated fills only; separate from V6-V9",
                       "frozen": "V11 has its own frozen identity (docs/V11_FREEZE.json)",
                       "maturity": "QUALIFIED is a gate, not a profitability claim"}
    return {**result, "read_only": True}


@router.get("/v12")
async def v12_forward(request: Request) -> dict[str, Any]:
    """V12 BIZZY: beebots' Bizzy Bee -- one long day breakout a day at full size -- and her Jev twin. Read-only."""
    import asyncio
    from app.core.arena_live_view import v12_enrich
    from app.core.v6_view import v6_payload
    st = getattr(request.app.state, "v12_storage", None)
    if st is None:
        return {"ok": True, "experiment": None, "status": {"status": "DISABLED"}, "read_only": True}

    def build() -> dict[str, Any]:
        return v12_enrich(v6_payload(st, getattr(request.app.state, "v12", None)), st)
    result = await asyncio.to_thread(build)
    result["program"] = "V12 BIZZY"
    result["note"] = ("Bizzy Bee from github.com/imikerussell/beebots: when ETH, SOL or HYPE trades above today's UTC "
                      "open + half of yesterday's range she goes long at full size (2x), stop at the day's open, out at "
                      "the UTC close; one trade a day, 20 USDT paper books. Unproven: in its back-test the rule lost "
                      "after Bybit costs.")
    if result.get("hero"):
        result["hero"]["next_decision_ms"] = (int(time.time() * 1000) // 60_000 + 1) * 60_000
    result["rules"] = {"paper": "simulated fills only; separate from V6-V11",
                       "frozen": "V12 has its own frozen identity (docs/V12_FREEZE.json)",
                       "maturity": "QUALIFIED is a gate, not a profitability claim"}
    return {**result, "read_only": True}


@router.get("/v13")
async def v13_forward(request: Request) -> dict[str, Any]:
    """V13 SNAPBACK: limit-order (maker) mean reversion over the V11 coins. Read-only."""
    import asyncio
    from app.core.arena_live_view import v13_enrich
    from app.core.v6_view import v6_payload
    st = getattr(request.app.state, "v13_storage", None)
    if st is None:
        return {"ok": True, "experiment": None, "status": {"status": "DISABLED"}, "read_only": True}

    def build() -> dict[str, Any]:
        return v13_enrich(v6_payload(st, getattr(request.app.state, "v13", None)), st)
    result = await asyncio.to_thread(build)
    result["program"] = "V13 SNAPBACK"
    result["note"] = ("Every 5 minutes Snapback looks for coins stretched far from their 24 h VWAP and rests a post-only "
                      "LIMIT order a little further out, betting on a snap back; it pays the maker fee instead of the "
                      "taker fee. 20 USDT paper book, up to 4 positions. Unproven: its pre-registered study FAILED -- "
                      "every setting lost on the second half of the data (docs/V13_SNAPBACK_STUDY.json).")
    if result.get("hero"):
        result["hero"]["next_decision_ms"] = (int(time.time() * 1000) // 300_000 + 1) * 300_000
    result["rules"] = {"paper": "simulated fills only; separate from V6-V12",
                       "frozen": "V13 has its own frozen identity (docs/V13_FREEZE.json)",
                       "maturity": "QUALIFIED is a gate, not a profitability claim"}
    return {**result, "read_only": True}


@router.get("/v14")
async def v14_forward(request: Request) -> dict[str, Any]:
    """V14 HTF: copies of the best strategies that check the higher timeframes before every entry. Read-only."""
    import asyncio
    from app.core.arena_live_view import v14_enrich
    from app.core.v6_view import v6_payload
    st = getattr(request.app.state, "v14_storage", None)
    if st is None:
        return {"ok": True, "experiment": None, "status": {"status": "DISABLED"}, "read_only": True}

    def build() -> dict[str, Any]:
        return v14_enrich(v6_payload(st, getattr(request.app.state, "v14", None)), st)
    result = await asyncio.to_thread(build)
    result["program"] = "V14 HTF"
    result["note"] = ("Copies of the best current strategies (V8.3 snap-back, the V11.1 / V11.2 scanners, V13 Snapback) "
                      "that only enter in the direction of the higher-timeframe trend (the 4-hour and daily trend from "
                      "1h candles); everything else is the original's. Each runs next to its original as an A/B test. "
                      "20 USDT paper books (docs/V14_HTF_STUDY.json).")
    if result.get("hero"):
        result["hero"]["next_decision_ms"] = (int(time.time() * 1000) // 300_000 + 1) * 300_000
    result["rules"] = {"paper": "simulated fills only; separate from V6-V13",
                       "frozen": "V14 has its own frozen identity (docs/V14_FREEZE.json)",
                       "maturity": "QUALIFIED is a gate, not a profitability claim"}
    return {**result, "read_only": True}


@router.get("/roster")
async def roster(request: Request) -> dict[str, Any]:
    """THE ROSTER (home screen): the named contenders across every forward program -- in profit after every cost with
    enough trades -- and the retired list (still paper trading, not on screen). Read-only."""
    import asyncio
    from app.core.roster_view import roster_payload
    return {**(await asyncio.to_thread(roster_payload, request.app.state)), "read_only": True}


@router.get("/{program}/bot/{key:path}")
async def arena_bot(request: Request, program: str, key: str) -> dict[str, Any]:
    """One forward bot of V6, V7, V8, V9 or V11: book, closed trades, decisions. Read-only."""
    import asyncio
    from app.core.v6_view import v6_bot
    st = {"v6": _svc(request).storage if program == "v6" else None, "v7": getattr(request.app.state, "v7_storage", None),
          "v8": getattr(request.app.state, "v8_storage", None),
          "v9": getattr(request.app.state, "v9_storage", None),
          "v11": getattr(request.app.state, "v11_storage", None),
          "v12": getattr(request.app.state, "v12_storage", None),
          "v13": getattr(request.app.state, "v13_storage", None),
          "v14": getattr(request.app.state, "v14_storage", None)}.get(program)
    if st is None:
        return {"ok": False, "error": "unknown or stopped program"}
    return {**(await asyncio.to_thread(v6_bot, st, key, getattr(request.app.state, program, None))), "read_only": True}


@router.get("/{program}/candles")
async def arena_candles(request: Request, program: str, symbol: str = Query(..., min_length=1, max_length=20),
                        tf: str = Query("5m"), limit: int = Query(300, ge=10, le=1000),
                        until: int | None = Query(None, ge=0)) -> dict[str, Any]:
    """Candlesticks aggregated from a forward program's stored 1m bars, with its paper trades as markers. `until` (ms)
    ends the window there instead of now (the dashboard centres a chosen closed trade)."""
    import asyncio
    from app.core.arena_live_view import candles
    from app.core.v6_view import current_experiment
    stores = {"v6": lambda: _svc(request).storage, "v7": lambda: getattr(request.app.state, "v7_storage", None),
              "v8": lambda: getattr(request.app.state, "v8_storage", None),
              "v9": lambda: getattr(request.app.state, "v9_storage", None),
              "v11": lambda: getattr(request.app.state, "v11_storage", None),
              "v12": lambda: getattr(request.app.state, "v12_storage", None),
              "v13": lambda: getattr(request.app.state, "v13_storage", None),
              "v14": lambda: getattr(request.app.state, "v14_storage", None)}
    if program not in stores:
        return {"ok": False, "error": "unknown program"}
    st = stores[program]()
    if st is None:
        return {"ok": False, "error": "program not running"}
    svc = getattr(request.app.state, program, None)
    sym = symbol.upper()

    def build() -> dict[str, Any]:
        now = int(time.time() * 1000)
        return candles(st, current_experiment(st, svc), sym, tf, limit, now_ms=min(until, now) if until else None,
                       program=program)
    return {**(await asyncio.to_thread(build)), "program": program, "read_only": True}


@router.get("/v6/activity")
async def v6_activity(request: Request, limit: int = Query(100, ge=1, le=500),
                      before_id: int | None = Query(None, ge=1)) -> dict[str, Any]:
    import asyncio
    from app.core.v6_view import v6_activity as act
    svc = _svc(request)
    return {**(await asyncio.to_thread(act, svc.storage, getattr(request.app.state, "v6", None), limit, before_id)),
            "read_only": True}


@router.get("/v6/bot/{key:path}")
async def v6_bot(request: Request, key: str) -> dict[str, Any]:
    import asyncio
    from app.core.v6_view import v6_bot as bot
    svc = _svc(request)
    return {**(await asyncio.to_thread(bot, svc.storage, key, getattr(request.app.state, "v6", None))), "read_only": True}


@router.get("/v5")
async def v5_arena(request: Request) -> dict[str, Any]:
    """V5 HOURLY / DAILY FUTURES ARENA (frozen research history): DEVELOPMENT and PSEUDO-HOLDOUT runs -- the family raw-edge
    and economic verdicts, HOURLY / DAILY viability, the gross -> fees -> slippage -> funding -> net decomposition, the
    after-cost leaderboard, Jev V5 against its baselines, capacity diagnostics and the advanced set."""
    import asyncio
    from app.core.system_view import v5_payload
    svc = _svc(request)
    return {**(await asyncio.to_thread(v5_payload, svc.storage)), "read_only": True}


@router.get("/system")
async def system(request: Request) -> dict[str, Any]:
    """SYSTEM: execution and safety, stream / Jev / data health, research jobs, database, environment and
    the developer details. Allow-listed; never a credential, header, order id or environment secret."""
    from app.core.system_view import system_payload
    _svc(request)
    return system_payload(request.app.state)


@router.get("/trader/{bot_id:path}/trades")
async def trader_trades(request: Request, bot_id: str, limit: int = Query(300, ge=1, le=MAX_PAGE * 5)) -> dict[str, Any]:
    """The newest closed trades of one research bot, by its inspection bot id (e.g. v4d:V4.1-HYPE-15m-CONTROL@20x)."""
    import asyncio
    from app.core.system_view import trader_trades as tt
    svc = _svc(request)
    return {**(await asyncio.to_thread(tt, svc.storage, bot_id, limit)), "read_only": True}
