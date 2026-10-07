"""One definition of what an arena run looks like to a reader.

The private dashboard, the public API and the server-rendered report all show the same arena, so
they all build it here. Every published field comes from an allow-list: a stored row is never
serialised wholesale, so a future column cannot leak by accident. Metric fields reuse
`api_public.METRIC_FIELDS` -- one list of what is safe, not three.

An arena run is simulated by construction (a historical replay against production exchange rules),
so it never carries an exchange order id, a key or an account.
"""
from __future__ import annotations

import calendar
from typing import Any

from app.competition.cost_efficiency import cost_efficiency, jev_eligible
from app.core.api_public import METRIC_FIELDS, _pick

# The discovery -> live path. ADVANCE is the only state discovery can hand out.
PIPELINE = ("DISCOVERY", "ADVANCE", "MULTI-YEAR VALIDATION", "QUALIFIED", "SHADOW_LIVE",
            "LIVE_CANDIDATE", "MANUAL GO LIVE")

ARENA_BOT_FIELDS = (
    "key", "strategy_id", "name", "symbol", "coin", "timeframe", "max_leverage", "profile",
    "params_version", "version", "state", "rank", "reasons", "signals", "entry_states",
    "entry_quality", "avg_risk_pct", "max_risk_pct", "avg_position_leverage",
    "max_position_leverage", "partial_fills", "rejects", "halted", "fee_source", "elapsed_s",
)
GATE_FIELDS = ("name", "ok", "actual", "threshold", "comparison")
TRADE_FIELDS = ("side", "qty", "entry", "exit", "entry_ts", "exit_ts", "pnl", "fees", "net", "r",
                "exit_kind")
RUN_FIELDS = ("run_id", "label", "status", "created_ts", "finished_ts", "first_month", "last_month",
              "symbols", "timeframes", "active_bots", "min_active_bots", "not_entered", "advanced",
              "config_fingerprint")
CONFIG_FIELDS = (
    "arena_version", "months", "min_active_bots", "max_bots", "starting_balance", "profile",
    "leverage_ceiling", "leverage_policy", "params_version", "seed", "min_net_profit",
    "min_expectancy_r", "min_profit_factor", "max_drawdown_pct", "max_liquidations", "min_trades",
    "min_bars_history", "fees", "fee_source", "execution", "min_notional_safety_multiplier",
    "max_fee_share_of_r", "risk_profile", "rules", "dataset_role", "cost_gate_min_ratio",
    "shadow_cost_rejects", "only_keys", "strategy_fingerprints",
)
SYMBOL_FIELDS = (
    "symbol", "bars", "reference_price", "min_notional", "min_qty", "step", "min_qty_notional",
    "binding_minimum", "exchange_min", "floor", "safety", "risk_pct", "risk_usd", "ceiling_fee",
    "ceiling_leverage", "ceiling", "min_stop_pct", "max_stop_pct", "min_risk_pct_needed",
    "tradeable", "reason",
)
STRATEGY_FIELDS = ("strategy_id", "name", "native_timeframe", "supported_timeframes",
                   "declared_timeframes", "context_timeframes")


def window_days(first_month: str | None, last_month: str | None) -> float | None:
    """Calendar days covered by a run's months, e.g. 2026-07..2026-08 -> 62."""
    try:
        y0, m0 = (int(x) for x in str(first_month).split("-"))
        y1, m1 = (int(x) for x in str(last_month).split("-"))
    except (TypeError, ValueError):
        return None
    days, y, m = 0, y0, m0
    while (y, m) <= (y1, m1):
        days += calendar.monthrange(y, m)[1]
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return float(days)


def latest_run_id(storage: Any, run_id: str | None = None) -> str | None:
    if run_id:
        return run_id if storage.arena_run(run_id) else None
    runs = storage.arena_runs(limit=1)
    return runs[0]["run_id"] if runs else None


def bot_row(b: dict[str, Any], days: float | None = None) -> dict[str, Any]:
    out = _pick(b, ARENA_BOT_FIELDS)
    out["gates"] = [_pick(g, GATE_FIELDS) for g in (b.get("gates") or [])]
    m = b.get("metrics")
    out["metrics"] = _pick(m, METRIC_FIELDS) if m else None
    ce = cost_efficiency(m, b.get("trades_ledger"), days)
    ok, why = jev_eligible(m, ce)
    out["cost_efficiency"] = ce
    out["jev_eligible"] = ok
    out["jev_eligibility_reasons"] = why
    return out


def matrix(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Strategy x symbol. A cell lists every bot of that pair (one per timeframe/version)."""
    strategies = sorted({r["strategy_id"] for r in rows})
    symbols = sorted({r["symbol"] for r in rows})
    cells: dict[str, dict[str, list[dict[str, Any]]]] = {s: {} for s in strategies}
    for r in rows:
        m = r.get("metrics") or {}
        cells[r["strategy_id"]].setdefault(r["symbol"], []).append({
            "key": r["key"], "timeframe": r["timeframe"], "state": r["state"],
            "net_return_pct": m.get("net_return_pct"), "profit_factor": m.get("profit_factor"),
            "expectancy_r": m.get("expectancy_r"), "max_drawdown_pct": m.get("max_drawdown_pct"),
            "trades": m.get("trades"), "net_profit": m.get("net_profit")})
    names = {r["strategy_id"]: r.get("name") for r in rows}
    return {"strategies": strategies, "symbols": symbols, "names": names, "cells": cells}


def arena_payload(storage: Any, run_id: str | None = None) -> dict[str, Any] | None:
    rid = latest_run_id(storage, run_id)
    if rid is None:
        return None
    run = storage.arena_run(rid, heavy=True) or {}
    days = window_days(run.get("first_month"), run.get("last_month"))
    rows = [bot_row(b, days) for b in storage.arena_bots(rid, heavy=True)]
    pre = run.get("preflight") or {}
    config = run.get("config") or {}
    grouped: dict[str, list[str]] = {}
    for ne in pre.get("not_entered") or []:
        grouped.setdefault(str(ne.get("reason") or "unknown"), []).append(str(ne.get("key")))
    return {
        "run": _pick(run, RUN_FIELDS),
        "summary": run.get("summary") or {},
        "config": _pick(config, CONFIG_FIELDS),
        "pipeline": list(PIPELINE),
        "symbols": [_pick(r, SYMBOL_FIELDS) for r in pre.get("symbols") or []],
        "strategies": [_pick(r, STRATEGY_FIELDS) for r in pre.get("strategies") or []],
        "not_entered": [{"reason": k, "count": len(v), "keys": v}
                        for k, v in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0]))],
        "eligible_not_selected": list(pre.get("eligible_not_selected") or []),
        "bots": rows,
        "matrix": matrix(rows),
        "filters": {"coins": sorted({r["coin"] for r in rows if r.get("coin")}),
                    "timeframes": [tf for tf in ("5m", "15m", "30m")
                                   if any(r["timeframe"] == tf for r in rows)],
                    "strategies": sorted({r["strategy_id"] for r in rows}),
                    "states": sorted({r["state"] for r in rows if r.get("state")})},
        "runs": [_run_summary(storage, r) for r in storage.arena_runs(limit=10)],
        "cost_diagnostics": cost_diagnostics(rows),
    }


def _run_summary(storage: Any, r: dict[str, Any]) -> dict[str, Any]:
    """A run for the selector: what it is (DEVELOPMENT / TEST / discovery) as well as when."""
    cfg = (storage.arena_run(r["run_id"], heavy=True) or {}).get("config") or {}
    return {**_pick(r, RUN_FIELDS), "dataset_role": cfg.get("dataset_role") or "",
            "params_version": cfg.get("params_version") or ""}


def cost_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Arena-wide: where does the money go, and which bots have an edge worth protecting?"""
    classes: dict[str, int] = {}
    for r in rows:
        c = (r.get("cost_efficiency") or {}).get("class") or "NO_TRADES"
        classes[c] = classes.get(c, 0) + 1
    fd = sorted((r for r in rows if (r.get("cost_efficiency") or {}).get("class") == "FEE_DESTROYED"),
                key=lambda r: -(r["cost_efficiency"].get("gross_edge") or 0))
    ce = [r.get("cost_efficiency") or {} for r in rows]
    return {"classes": classes,
            "fee_destroyed": [{"key": r["key"], "gross_edge": r["cost_efficiency"]["gross_edge"],
                               "total_costs": r["cost_efficiency"]["total_costs"],
                               "net": r["cost_efficiency"]["net"], "trades": r["cost_efficiency"]["trades"],
                               "cost_to_edge": r["cost_efficiency"].get("cost_to_edge")} for r in fd],
            "jev_eligible": [r["key"] for r in rows if r.get("jev_eligible")],
            "total_gross": sum(c.get("gross_edge") or 0 for c in ce),
            "total_costs": sum(c.get("total_costs") or 0 for c in ce),
            "total_trades": sum(c.get("trades") or 0 for c in ce)}


def arena_bot_payload(storage: Any, run_id: str | None, key: str) -> dict[str, Any] | None:
    rid = latest_run_id(storage, run_id)
    if rid is None:
        return None
    b = storage.arena_bot(rid, key)
    if not b:
        return None
    run = storage.arena_run(rid) or {}
    out = bot_row(b, window_days(run.get("first_month"), run.get("last_month")))
    out["run_id"] = rid
    out["equity"] = [[int(t), float(v)] for t, v in (b.get("equity") or [])]
    out["trades"] = [_pick(t, TRADE_FIELDS) for t in (b.get("trades_ledger") or [])]
    return out
