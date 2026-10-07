"""Competition JSON API, mounted beside the existing /api router.

Same contract as app/core/api.py: Basic auth is applied by the middleware in app/main.py, POSTs
additionally need `X-PaperLab: 1`, and errors come back as `{"ok": false, "error": ...}`.

Everything here reads the real `CompetitionService` / `CompetitionEngine`. There is no mock data
path: with no season run the endpoints return `ran: false` and the dashboard says so.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Body, Query, Request

from app.competition.service import PROFILE_LABELS, PROFILES
from app.core.engine import EngineError

router = APIRouter(prefix="/api/competition")

MAX_PAGE = 500


def _svc(request: Request) -> Any:
    svc = getattr(request.app.state, "competition", None)
    if svc is None:
        raise EngineError("competition service not started", 503)
    return svc


def _stored(svc: Any, run_id: str | None) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    """The requested season, or the most recent one. Returns (run_id, run row)."""
    if run_id:
        row = svc.storage.competition_run(run_id)
        return (run_id, row) if row else (None, None)
    runs = svc.storage.competition_runs(limit=1)
    if not runs:
        return None, None
    rid = runs[0]["run_id"]
    return rid, svc.storage.competition_run(rid)


@router.get("")
async def overview(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    """Season header, status counts and competition-wide cost totals."""
    svc = _svc(request)
    rid, row = _stored(svc, run_id or None)
    payload: dict[str, Any] = {
        "ok": True,
        "ran": row is not None,
        "progress": svc.progress.to_dict(),
        "profiles": [{"id": k, "label": PROFILE_LABELS.get(k, k)} for k in PROFILES],
        "data": svc.data_report(list(svc.settings.symbols)),
        "strategies": sorted(svc.classes),
        "defaults": {"starting_balance": 20.0, "max_leverage": 20, "profile": "realistic",
                     "symbols": list(svc.settings.symbols)},
    }
    if row is None:
        return payload
    comps = svc.storage.competition_competitors(rid)
    payload["run_id"] = rid
    payload["season"] = {
        "competition_id": row.get("competition_id"), "season_id": row.get("season_id"),
        "label": row.get("label"), "fingerprint": row.get("fingerprint"),
        "status": row.get("status"), "created_ts": row.get("created_ts"),
        "finished_ts": row.get("finished_ts"), "start_ms": row.get("start_ms"),
        "end_ms": row.get("end_ms"), "symbols": row.get("symbols"), "bars": row.get("bars"),
        "timeframe": "1m", "venue": "BINANCE_USDM (simulated)",
        "starting_balance": row.get("starting_balance"), "max_leverage": row.get("max_leverage"),
        "execution_profile": row.get("execution_profile"),
        "execution_profile_label": PROFILE_LABELS.get(row.get("execution_profile") or "", ""),
        "error": row.get("error"),
    }
    payload["states"] = _state_counts(comps)
    payload["totals"] = _totals(comps)
    payload["banner"] = ("Ranking is not qualification. A bot can rank #1 and still fail on "
                         "insufficient trades, drawdown, liquidation, weak expectancy, execution "
                         "costs, or validation stages that have not been run.")
    return payload


def _state_counts(comps: list[dict[str, Any]]) -> dict[str, int]:
    """Every state is present, including the zeroes, so the summary cards never shift around."""
    out = {s: 0 for s in ("COMPETING", "QUALIFIED", "WATCHLIST", "INSUFFICIENT_SAMPLE",
                          "FAILED", "DISQUALIFIED", "SHADOW_LIVE", "LIVE_CANDIDATE")}
    for c in comps:
        st = c.get("state")
        if st in out:
            out[st] += 1
    return out


def _totals(comps: list[dict[str, Any]]) -> dict[str, float]:
    keys = ("trades", "fees_paid", "slippage_cost", "latency_cost", "spread_cost", "impact_cost",
            "funding_paid", "liquidation_count", "gross_pnl", "net_profit")
    out = {k: 0.0 for k in keys}
    for c in comps:
        m = c.get("metrics") or {}
        for k in keys:
            out[k] += float(m.get(k) or 0.0)
    out["trades"] = int(out["trades"])
    out["liquidation_count"] = int(out["liquidation_count"])
    out["bots"] = len(comps)
    return out


@router.get("/leaderboard")
async def leaderboard(request: Request, run_id: str = Query(""),
                      sort: str = Query("score")) -> dict[str, Any]:
    """Ranked rows. `rank` and `qualification` are separate fields on purpose."""
    svc = _svc(request)
    rid, row = _stored(svc, run_id or None)
    if row is None:
        return {"ok": True, "ran": False, "rows": []}
    comps = svc.storage.competition_competitors(rid)
    rows = [_row(c) for c in comps if c.get("metrics")]
    keyfns = {
        "score": lambda r: r.get("score"),
        "net": lambda r: r["metrics"].get("net_profit"),
        "return": lambda r: r["metrics"].get("net_return_pct"),
        "expectancy": lambda r: r["metrics"].get("expectancy_r"),
        "profit_factor": lambda r: r["metrics"].get("profit_factor"),
        "drawdown": lambda r: -(r["metrics"].get("max_drawdown_pct") or 1e9),
        "trades": lambda r: r["metrics"].get("trades"),
    }
    fn = keyfns.get(sort, keyfns["score"])
    rows.sort(key=lambda r: (fn(r) is not None, fn(r) if fn(r) is not None else 0), reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    skipped = [{"strategy_id": c["strategy_id"], "name": c.get("name"), "skipped": c.get("skipped")}
               for c in comps if c.get("skipped")]
    return {"ok": True, "ran": True, "run_id": rid, "sort": sort, "rows": rows, "skipped": skipped}


def _row(c: dict[str, Any]) -> dict[str, Any]:
    m = c.get("metrics") or {}
    q = c.get("qualification") or {}
    return {
        "strategy_id": c["strategy_id"], "version": c.get("version"), "name": c.get("name"),
        "state": c.get("state"), "score": c.get("score"),
        "reasons": q.get("reasons") or [],
        "metrics": m,
    }


@router.get("/bots/{strategy_id}")
async def bot_detail(request: Request, strategy_id: str, run_id: str = Query("")) -> dict[str, Any]:
    """One competitor's isolated account. Never includes another bot's ledger."""
    svc = _svc(request)
    rid, row = _stored(svc, run_id or None)
    if row is None:
        raise EngineError("no competition has been run yet", 404)
    c = svc.storage.competition_competitor(rid, strategy_id)
    if c is None:
        raise EngineError(f"unknown competitor {strategy_id!r} in this season", 404)
    fills = c.pop("fills", None) or []
    c["trade_count"] = len(fills)
    c["equity"] = c.get("equity") or []
    c["run_id"] = rid
    c["starting_balance"] = row.get("starting_balance")
    c["execution_levels"] = _level_mix(fills)
    return {"ok": True, "bot": c}


def _level_mix(fills: list[dict[str, Any]]) -> dict[str, int]:
    """How many fills came from each execution level.

    Historical seasons run on klines, so this is normally all L3. The dashboard shows the mix
    rather than a label, because calling an OHLCV-modelled fill an order-book fill would be a lie.
    """
    out = {"1": 0, "2": 0, "3": 0}
    for f in fills:
        lvl = str(f.get("execution_level") or "")
        if lvl in out:
            out[lvl] += 1
    return out


@router.get("/bots/{strategy_id}/trades")
async def bot_trades(request: Request, strategy_id: str, run_id: str = Query(""),
                     offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=MAX_PAGE),
                     symbol: str = Query(""), side: str = Query(""), role: str = Query(""),
                     level: str = Query(""), outcome: str = Query("")) -> dict[str, Any]:
    """Server-side paginated ledger. A multi-year season has far too many fills to ship at once."""
    svc = _svc(request)
    rid, row = _stored(svc, run_id or None)
    if row is None:
        raise EngineError("no competition has been run yet", 404)
    c = svc.storage.competition_competitor(rid, strategy_id)
    if c is None:
        raise EngineError(f"unknown competitor {strategy_id!r} in this season", 404)
    fills = c.get("fills") or []
    if symbol:
        fills = [f for f in fills if f.get("symbol") == symbol]
    if side:
        fills = [f for f in fills if (f.get("side") or "").upper() == side.upper()]
    if role:
        fills = [f for f in fills if (f.get("liquidity_role") or "") == role.upper()]
    if level:
        fills = [f for f in fills if str(f.get("execution_level") or "") == str(level)]
    if outcome == "win":
        fills = [f for f in fills if (f.get("realized_pnl") or 0) > 0]
    elif outcome == "loss":
        fills = [f for f in fills if (f.get("realized_pnl") or 0) < 0]
    total = len(fills)
    page = fills[offset:offset + limit]
    return {"ok": True, "run_id": rid, "strategy_id": strategy_id, "total": total,
            "offset": offset, "limit": limit, "rows": page}


@router.get("/bots/{strategy_id}/qualification")
async def bot_qualification(request: Request, strategy_id: str,
                            run_id: str = Query("")) -> dict[str, Any]:
    """Every gate, plus the stages that have NOT been run.

    An unrun stage is reported as NOT_RUN, never as a pass. `judge()` already refuses to reach
    QUALIFIED without them; this endpoint makes that visible instead of implicit.
    """
    svc = _svc(request)
    rid, row = _stored(svc, run_id or None)
    if row is None:
        raise EngineError("no competition has been run yet", 404)
    c = svc.storage.competition_competitor(rid, strategy_id)
    if c is None:
        raise EngineError(f"unknown competitor {strategy_id!r} in this season", 404)
    q = c.get("qualification") or {}
    evaluated = set(q.get("evaluated_stages") or [])
    stages = [{"name": s, "status": "RAN" if s in evaluated else "NOT_RUN"}
              for s in ("season", "walk_forward", "monte_carlo", "stress")]
    stages.append({"name": "shadow_live",
                   "status": "RAN" if c.get("state") in ("SHADOW_LIVE", "LIVE_CANDIDATE") else "NOT_RUN"})
    return {"ok": True, "strategy_id": strategy_id, "state": c.get("state"),
            "gates": q.get("gates") or [], "reasons": q.get("reasons") or [], "stages": stages,
            "score_breakdown": c.get("score_breakdown")}


@router.get("/execution-comparison")
async def execution_comparison(request: Request, base: str = Query(""),
                               against: str = Query("")) -> dict[str, Any]:
    """Two seasons side by side, per strategy.

    Defaults to the newest realistic season against the newest legacy one, which is the comparison
    that shows how unrealistic execution manufactured winners.
    """
    svc = _svc(request)
    runs = svc.storage.competition_runs(limit=50)
    def newest(profile: str) -> str | None:
        for r in runs:
            if r.get("execution_profile") == profile:
                return r["run_id"]
        return None

    new_id = against or newest("realistic")
    old_id = base or newest("legacy")
    if not new_id or not old_id:
        return {"ok": True, "available": False,
                "reason": "needs one season on the realistic profile and one on the legacy profile",
                "runs": [{"run_id": r["run_id"], "profile": r.get("execution_profile"),
                          "label": r.get("label"), "created_ts": r.get("created_ts")} for r in runs]}
    old = {c["strategy_id"]: c for c in svc.storage.competition_competitors(old_id)}
    new = {c["strategy_id"]: c for c in svc.storage.competition_competitors(new_id)}
    old_rank = {c["strategy_id"]: c.get("rank") for c in svc.storage.competition_competitors(old_id)}
    new_rank = {c["strategy_id"]: c.get("rank") for c in svc.storage.competition_competitors(new_id)}
    fields = ("net_profit", "net_return_pct", "trades", "fees_paid", "slippage_cost",
              "max_drawdown_pct", "liquidation_count", "gross_pnl")
    rows = []
    for sid in sorted(set(old) | set(new)):
        o = (old.get(sid) or {}).get("metrics") or {}
        n = (new.get(sid) or {}).get("metrics") or {}
        if not o and not n:
            continue
        row: dict[str, Any] = {
            "strategy_id": sid,
            "name": (new.get(sid) or old.get(sid) or {}).get("name"),
            "old_state": (old.get(sid) or {}).get("state"),
            "new_state": (new.get(sid) or {}).get("state"),
            "old_rank": old_rank.get(sid), "new_rank": new_rank.get(sid),
        }
        for f in fields:
            row["old_" + f] = o.get(f)
            row["new_" + f] = n.get(f)
        row["rank_delta"] = ((old_rank.get(sid) or 0) - (new_rank.get(sid) or 0)) or 0
        ret_o, ret_n = o.get("net_return_pct"), n.get("net_return_pct")
        row["flipped"] = bool(ret_o is not None and ret_n is not None and (ret_o > 0) != (ret_n > 0))
        rows.append(row)
    return {"ok": True, "available": True, "base_run_id": old_id, "against_run_id": new_id,
            "base_profile": "legacy", "against_profile": "realistic", "rows": rows}


@router.get("/seasons")
async def seasons(request: Request, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    svc = _svc(request)
    rows = svc.storage.competition_runs(limit=limit)
    for r in rows:
        comps = svc.storage.competition_competitors(r["run_id"])
        ranked = [c for c in comps if c.get("metrics")]
        leader = min(ranked, key=lambda c: c.get("rank") or 10**9, default=None)
        r["bots"] = len(ranked)
        r["qualified"] = sum(1 for c in ranked
                             if c.get("state") in ("QUALIFIED", "SHADOW_LIVE", "LIVE_CANDIDATE"))
        r["leader"] = {"strategy_id": leader["strategy_id"], "name": leader.get("name"),
                       "state": leader.get("state")} if leader else None
        r["profile_label"] = PROFILE_LABELS.get(r.get("execution_profile") or "", "")
    return {"ok": True, "seasons": rows}


@router.post("/run")
async def run_competition(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Start a season in the background and return its run id immediately.

    Never blocks: a month of 1m bars takes minutes and would exceed the platform request timeout.
    Poll `/api/competition` for `progress`.
    """
    svc = _svc(request)
    if svc.running:
        raise EngineError("a competition is already running", 409)
    data = svc.data_report(list(svc.settings.symbols))
    symbols = [s for s in (body.get("symbols") or svc.settings.symbols) if s]
    if not symbols:
        raise EngineError("no symbols selected", 400)
    start_ms = int(body.get("start_ms") or data.get("overlap_start_ms") or 0)
    end_ms = int(body.get("end_ms") or data.get("overlap_end_ms") or 0)
    if not start_ms or not end_ms or end_ms <= start_ms:
        raise EngineError("no stored candle window to replay; let the feed backfill first", 400)
    profile = str(body.get("profile") or "realistic")
    if profile not in PROFILES:
        raise EngineError(f"unknown execution profile {profile!r}", 400)
    only = [str(s).upper() for s in (body.get("strategies") or []) if s] or None
    season = svc.build_season(
        symbols=symbols, start_ms=start_ms, end_ms=end_ms,
        starting_balance=float(body.get("starting_balance") or 20.0),
        max_leverage=int(body.get("max_leverage") or 20),
        profile=profile, label=str(body.get("label") or ""),
        min_closed_trades=body.get("min_closed_trades"))
    run_id = await svc.start(season, profile, only)
    return {"ok": True, "run_id": run_id, "profile": profile,
            "symbols": symbols, "start_ms": start_ms, "end_ms": end_ms}


@router.post("/cancel")
async def cancel_competition(request: Request) -> dict[str, Any]:
    svc = _svc(request)
    return {"ok": True, "cancelled": svc.cancel()}


# ---- multi-year validation (walk-forward / Monte Carlo / stress) ---------------------------------

def _validation_run(svc: Any, run_id: str | None) -> tuple[str | None, dict[str, Any] | None]:
    if run_id:
        row = svc.storage.validation_run(run_id)
        return (run_id, row) if row else (None, None)
    runs = svc.storage.validation_runs(limit=1)
    if not runs:
        return None, None
    return runs[0]["run_id"], runs[0]


@router.get("/validation")
async def validation_overview(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    """Latest validation run: stage, progress and state counts.

    `stage` is the coarse pipeline phase the dashboard renders; `progress` carries the competitor
    counter so the GUI can show "47 / 81" without loading every result.
    """
    svc = _svc(request)
    rid, row = _validation_run(svc, run_id or None)
    if row is None:
        return {"ok": True, "ran": False,
                "stages": ["DOWNLOADING_DATA", "PREPARING", "RUNNING_WALK_FORWARD",
                           "MONTE_CARLO", "STRESS_TESTING", "COMPLETE", "FAILED", "CANCELLED"]}
    comps = svc.storage.validation_competitors(rid)
    states: dict[str, int] = {}
    for c in comps:
        states[c["state"]] = states.get(c["state"], 0) + 1
    prog = row.get("progress") or {}
    total = row.get("total_competitors") or 0
    done = len(comps)
    return {
        "ok": True, "ran": True, "run_id": rid,
        "status": row.get("status"), "stage": row.get("stage"),
        "created_ts": row.get("created_ts"), "finished_ts": row.get("finished_ts"),
        "symbols": row.get("symbols"), "first_month": row.get("first_month"),
        "last_month": row.get("last_month"), "windows": row.get("windows"),
        "starting_balance": row.get("starting_balance"), "leverages": row.get("leverages"),
        "dataset_fingerprint": row.get("dataset_fingerprint"),
        "config_fingerprint": row.get("config_fingerprint"),
        "total_competitors": total, "completed": done,
        "pct": (done / total) if total else 0.0,
        "current": prog.get("last"), "elapsed_s": prog.get("elapsed_s"),
        "states": states, "error": row.get("error"),
    }


@router.get("/validation/leaderboard")
async def validation_leaderboard(request: Request, run_id: str = Query(""),
                                 sort: str = Query("score")) -> dict[str, Any]:
    """Out-of-sample leaderboard. Every number here comes from the master OOS ledger."""
    svc = _svc(request)
    rid, row = _validation_run(svc, run_id or None)
    if row is None:
        return {"ok": True, "ran": False, "rows": []}
    rows = []
    for c in svc.storage.validation_competitors(rid):
        full = svc.storage.validation_competitor(rid, c["key"]) or {}
        m = full.get("oos_metrics") or {}
        w = full.get("walk_forward") or {}
        mcr = full.get("monte_carlo") or {}
        sr = full.get("stress") or {}
        q = full.get("qualification") or {}
        rows.append({
            "key": c["key"], "strategy_id": c["strategy_id"], "leverage": c["leverage"],
            "name": c.get("name"), "state": c["state"], "score": c.get("score"),
            "skipped": (full.get("skipped") or ""), "ran": bool(full.get("oos_metrics")),
            "oos_trades": m.get("trades"), "oos_net": m.get("net_profit"),
            "oos_return_pct": m.get("net_return_pct"), "oos_expectancy_r": m.get("expectancy_r"),
            "oos_profit_factor": m.get("profit_factor"), "oos_max_dd": m.get("max_drawdown_pct"),
            "fees": m.get("fees_paid"), "slippage": m.get("slippage_cost"),
            "funding": m.get("funding_paid"),
            "avg_effective_leverage": m.get("avg_effective_leverage"),
            "max_effective_leverage": m.get("max_effective_leverage"),
            "profitable_windows": w.get("profitable_windows"),
            "active_windows": w.get("active_windows"), "total_windows": w.get("total_windows"),
            "profitable_ratio": w.get("profitable_ratio"), "active_ratio": w.get("active_ratio"),
            "symbol_concentration": w.get("symbol_concentration"),
            "mc_ran": bool(mcr.get("ran")), "mc_ruin": mcr.get("ruin_probability"),
            "stress_ran": bool(sr.get("ran")), "stress_survives": sr.get("survives"),
            "stress_worst": sr.get("worst_scenario"),
            "reasons": q.get("reasons") or [],
            "stages": q.get("evaluated_stages") or [],
        })
    keys = {
        "score": lambda r: r.get("score"),
        "oos_return": lambda r: r.get("oos_return_pct"),
        "oos_trades": lambda r: r.get("oos_trades"),
        "expectancy": lambda r: r.get("oos_expectancy_r"),
        "windows": lambda r: r.get("profitable_ratio"),
    }
    fn = keys.get(sort, keys["score"])
    # A competitor that never ran has no score. Sorting on `is None` with reverse=True would put
    # those rows FIRST, so the leaderboard would open with a dozen blank entries.
    rows.sort(key=lambda r: (fn(r) is not None, fn(r) if fn(r) is not None else 0), reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    qualified = [r["key"] for r in rows
                 if r["state"] in ("QUALIFIED", "SHADOW_LIVE", "LIVE_CANDIDATE")]
    return {"ok": True, "ran": True, "run_id": rid, "rows": rows, "qualified": qualified,
            "failure_reasons": _failure_summary(rows)}


FAILURE_LABELS = {
    "min_net_profit": "negative_net_after_costs",
    "min_expectancy_r": "negative_expectancy",
    "min_profit_factor": "profit_factor_below_threshold",
    "max_drawdown_pct": "excessive_drawdown",
    "min_profitable_oos_ratio": "too_few_profitable_windows",
    "min_active_oos_ratio": "too_few_active_windows",
    "max_symbol_concentration": "symbol_concentration",
    "max_profit_concentration": "single_trade_concentration",
    "max_ruin_probability": "monte_carlo_ruin",
    "stress_survives": "stress_failure",
}


def _failure_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    """How many competitors failed for each reason, so a run answers why nobody passed."""
    out: dict[str, int] = {}

    def bump(k: str) -> None:
        out[k] = out.get(k, 0) + 1

    for r in rows:
        if r["state"] in ("QUALIFIED", "SHADOW_LIVE", "LIVE_CANDIDATE"):
            continue
        if r["state"] == "INSUFFICIENT_SAMPLE":
            bump("insufficient_trades")
            continue
        if r["state"] == "DISQUALIFIED":
            bump("liquidation")
            continue
        for reason in r.get("reasons") or []:
            name = reason.split(" ")[0]
            bump(FAILURE_LABELS.get(name, name))
    return out


@router.get("/validation/bots/{key}")
async def validation_bot(request: Request, key: str, run_id: str = Query("")) -> dict[str, Any]:
    """One competitor's full validation record: windows, Monte Carlo, stress, gates."""
    svc = _svc(request)
    rid, row = _validation_run(svc, run_id or None)
    if row is None:
        raise EngineError("no validation run yet", 404)
    full = svc.storage.validation_competitor(rid, key)
    if full is None:
        raise EngineError(f"unknown competitor {key!r} in this validation run", 404)
    return {"ok": True, "run_id": rid, "bot": full}


@router.get("/validation/leverage-comparison")
async def leverage_comparison(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    """5x / 10x / 20x side by side for each strategy.

    Answers whether leverage amplifies a real edge or merely changes which trades clear Binance's
    minimum notional. Diagnostic only: the highest ceiling is never treated as the best.
    """
    svc = _svc(request)
    rid, row = _validation_run(svc, run_id or None)
    if row is None:
        return {"ok": True, "ran": False, "rows": []}
    by_strategy: dict[str, dict[str, Any]] = {}
    for c in svc.storage.validation_competitors(rid):
        full = svc.storage.validation_competitor(rid, c["key"]) or {}
        m = full.get("oos_metrics") or {}
        w = full.get("walk_forward") or {}
        mcr = full.get("monte_carlo") or {}
        sr = full.get("stress") or {}
        rejects = m.get("rejects") if isinstance(m.get("rejects"), dict) else {}
        entry = by_strategy.setdefault(c["strategy_id"],
                                       {"strategy_id": c["strategy_id"], "name": c.get("name"),
                                        "variants": {}})
        entry["variants"][str(c["leverage"])] = {
            "key": c["key"], "state": c["state"], "score": c.get("score"),
            "oos_return_pct": m.get("net_return_pct"), "oos_net": m.get("net_profit"),
            "trades": m.get("trades"), "expectancy_r": m.get("expectancy_r"),
            "profit_factor": m.get("profit_factor"), "max_dd": m.get("max_drawdown_pct"),
            "liquidations": m.get("liquidation_count"),
            "avg_effective_leverage": m.get("avg_effective_leverage"),
            "rejects": sum(rejects.values()) if rejects else None,
            "below_min_notional": rejects.get("below_min_notional"),
            "mc_ruin": mcr.get("ruin_probability") if mcr.get("ran") else None,
            "stress_survives": sr.get("survives") if sr.get("ran") else None,
            "profitable_ratio": w.get("profitable_ratio"),
        }
    return {"ok": True, "ran": True, "run_id": rid,
            "leverages": row.get("leverages") or [],
            "rows": sorted(by_strategy.values(), key=lambda r: r["strategy_id"])}


# ---- specialist arena (discovery) ---------------------------------------------------------------
# Read-only here as well: arena runs are produced offline (scripts/run_arena.py), where the market
# archive lives, and shipped with the deploy. Nothing on this router starts one.

@router.get("/arena")
async def arena(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    from app.core.arena_view import arena_payload
    svc = _svc(request)
    p = arena_payload(svc.storage, run_id or None)
    if p is None:
        return {"ok": True, "ran": False}
    return {"ok": True, "ran": True, **p}


@router.get("/arena/bots/{key}")
async def arena_bot(request: Request, key: str, run_id: str = Query("")) -> dict[str, Any]:
    from app.core.arena_view import arena_bot_payload
    svc = _svc(request)
    p = arena_bot_payload(svc.storage, run_id or None, key)
    if p is None:
        raise EngineError(f"unknown arena bot {key!r}", 404)
    return {"ok": True, "bot": p}


# ---- CONTROL vs +JEV (read-only; experiments run server-side via scripts/run_jev_experiment.py) --

@router.get("/jev")
async def jev(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    from app.core.jev_view import jev_payload
    svc = _svc(request)
    j = getattr(request.app.state, "jev", None)
    return {"ok": True, **jev_payload(svc.storage, run_id or None, j.health() if j else None)}


@router.get("/jev/pairs/{pair_id:path}")
async def jev_pair(request: Request, pair_id: str, run_id: str = Query("")) -> dict[str, Any]:
    from app.core.jev_view import jev_pair_payload
    svc = _svc(request)
    p = jev_pair_payload(svc.storage, run_id or None, pair_id)
    if p is None:
        raise EngineError(f"unknown pair {pair_id!r}", 404)
    return {"ok": True, **p}


# ---- live shadow (read-only; the worker runs in its own process, see app/live) --------------------

@router.get("/shadow")
async def shadow(request: Request) -> dict[str, Any]:
    from app.core.shadow_view import shadow_payload
    svc = _svc(request)
    return shadow_payload(svc.storage, getattr(request.app.state, "shadow", None))


@router.get("/shadow/activity")
async def shadow_activity(request: Request, limit: int = Query(100), before: int = Query(0)) -> dict[str, Any]:
    from app.core.shadow_view import activity_payload
    svc = _svc(request)
    return activity_payload(svc.storage, limit, before or None)


# ---- frozen v2 candidates (read-only; validation runs offline, scripts/run_candidate_validation.py) --

@router.get("/candidates")
async def candidates(request: Request, key: str = Query(""), run_id: str = Query("")) -> dict[str, Any]:
    from app.core.candidates_view import candidates_payload
    from app.core.shadow_view import shadow_payload
    svc = _svc(request)
    shadow = shadow_payload(svc.storage, getattr(request.app.state, "shadow", None))
    return candidates_payload(svc.storage, shadow, run_id or None, key or None)


# ---- V3 aggressive intraday Jev arena (read-only; runs offline, scripts/run_v3_arena.py) ---------------

@router.get("/v3")
async def v3_arena(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    from app.core.v3_view import v3_payload
    svc = _svc(request)
    return v3_payload(svc.storage, run_id or None)


@router.get("/v3/bots/{key}")
async def v3_bot(request: Request, key: str, run_id: str = Query("")) -> dict[str, Any]:
    from app.core.v3_view import v3_bot_payload
    svc = _svc(request)
    return v3_bot_payload(svc.storage, key, run_id or None)


# ---- V3.1 aggressive edge (read-only; runs offline, scripts/run_v31_arena.py) ---------------------------

@router.get("/v31")
async def v31_arena(request: Request, run_id: str = Query("")) -> dict[str, Any]:
    from app.core.v31_view import v31_payload
    svc = _svc(request)
    return v31_payload(svc.storage, run_id or None)


@router.get("/v31/bots/{key}")
async def v31_bot(request: Request, key: str, run_id: str = Query("")) -> dict[str, Any]:
    from app.core.v31_view import v31_bot_payload
    svc = _svc(request)
    return v31_bot_payload(svc.storage, key, run_id or None)
