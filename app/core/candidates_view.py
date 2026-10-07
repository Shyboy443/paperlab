"""The three frozen v2 candidates for the API and the report: pipeline, HISTORICAL validation and
LIVE FORWARD shadow evidence -- side by side, never added together, each labelled with its venue.

Everything is allow-listed; the heavy ledger and daily equity only travel on the per-candidate
detail request.
"""
from __future__ import annotations

from typing import Any

from app.competition.candidate_validation import CANDIDATES, SOURCE_TEST_RUN, VENUE

VENUES = {
    "BINANCE_USDM": "BINANCE USD-M",
    "BYBIT_LINEAR": "BYBIT LINEAR",
}
# The only real-money path PaperLab has configured (production EXCHANGE=bybit, LIVE_OVERRIDE with
# Bybit keys). See docs/VENUE_AUDIT.md. Binance evidence is STRATEGY robustness; Bybit execution
# validation is required before any live promotion.
TARGET_LIVE_VENUE = "BYBIT_LINEAR"
DEV_RUN = "78b995a21d07"

RESULT_FIELDS = ("key", "venue", "window", "metrics", "halted", "halt_ts", "halt", "capacity", "signals", "trade_stats",
                 "cost_efficiency", "trades_per_month", "windows", "years", "robustness", "regimes",
                 "headroom", "stress", "monte_carlo", "gates", "stages", "verdict", "manifest")
METRIC_FIELDS = ("starting_equity", "ending_equity", "net_return_pct", "gross_pnl", "fees_paid", "slippage_cost",
                 "funding_paid", "net_profit", "trades", "wins", "losses", "win_rate", "expectancy_r",
                 "profit_factor", "average_win", "average_loss", "max_drawdown_pct", "drawdown_duration_ms",
                 "longest_loss_streak", "avg_effective_leverage", "max_effective_leverage", "max_leverage_used",
                 "margin_utilization_avg", "margin_utilization_max", "liquidation_count", "time_in_market_pct")


def _pick(d: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any]:
    d = d or {}
    return {k: d.get(k) for k in fields if k in d}


def latest_run(storage: Any, run_id: str | None = None) -> dict[str, Any] | None:
    for r in storage.candidate_runs(50):
        if run_id and r["run_id"] != run_id:
            continue
        if run_id or r.get("status") in ("complete", "running"):
            return r
    return None


def _forward_for(shadow: dict[str, Any] | None, key: str) -> dict[str, Any]:
    """Live forward shadow evidence for one candidate, from the CURRENT forward experiment only."""
    if not shadow:
        return {"status": "NO LIVE SHADOW DATA"}
    bot = next((b for b in shadow.get("bots") or [] if b.get("key") == key), None)
    fx = shadow.get("forward_experiment") or {}
    fwd = (bot or {}).get("forward") or {"trades": 0, "net": 0.0}
    return {"venue": VENUE, "venue_label": VENUES[VENUE] + " live shadow",
            "experiment_id": fx.get("experiment_id"), "started_ts": fx.get("started_ts"),
            "elapsed_ms": fx.get("elapsed_ms"), "sessions": fx.get("sessions"),
            "book": (fx.get("continuity") or {}).get("mode") or "PER_SESSION",
            "live": bool(bot and bot.get("live")), "mode": (bot or {}).get("mode"),
            "session_equity": (bot or {}).get("equity"), "session_net": (bot or {}).get("net"),
            "open_positions": (bot or {}).get("open_positions") or [],
            "trades": fwd.get("trades", 0), "net": fwd.get("net", 0.0), "win_rate": fwd.get("win_rate"),
            "expectancy_r": fwd.get("expectancy_r"), "profit_factor": fwd.get("profit_factor"),
            "status": "COLLECTING"}


def pipeline(key: str, result: dict[str, Any] | None, run: dict[str, Any] | None,
             forward: dict[str, Any]) -> list[dict[str, Any]]:
    stages = (result or {}).get("stages") or {}
    running = bool(run and run.get("status") == "running" and not result)

    def hist(stage: str) -> str:
        if running:
            return "RUNNING" if stage == "MULTI_YEAR" else "PENDING"
        return stages.get(stage) or "PENDING"
    verdict = (result or {}).get("verdict")
    if verdict == "FAIL":
        qual = "NOT ELIGIBLE"
        qual_note = "failed the multi-year validation"
    elif verdict == "PASS":
        qual = "PENDING"
        qual_note = "needs forward shadow evidence and Bybit execution validation; live always needs an operator"
    else:
        qual = "PENDING"
        qual_note = "multi-year validation not finished"
    return [
        {"stage": "DEVELOPMENT", "status": "COMPLETED", "note": f"run {DEV_RUN}, 2025-11..2026-04 (design data)"},
        {"stage": "TEST", "status": "PASS", "note": f"run {SOURCE_TEST_RUN}, 2026-05..2026-06: ADVANCE"},
        {"stage": "MULTI-YEAR", "status": hist("MULTI_YEAR"), "note": "2021-01..2025-10, BINANCE USD-M"},
        {"stage": "MONTE CARLO", "status": hist("MONTE_CARLO"), "note": "10,000 paths, ruin <= 5%"},
        {"stage": "STRESS", "status": hist("STRESS"), "note": "every single-factor stress keeps net > 0"},
        {"stage": "FORWARD", "status": forward.get("status", "COLLECTING"),
         "note": f"{forward.get('trades', 0)} closed trades on the live shadow ({VENUES[VENUE]})"},
        {"stage": "QUALIFICATION", "status": qual, "note": qual_note},
    ]


def candidates_payload(storage: Any, shadow: dict[str, Any] | None = None, run_id: str | None = None,
                       key: str | None = None) -> dict[str, Any]:
    run = latest_run(storage, run_id)
    results = {r["key"]: r for r in storage.candidate_results(run["run_id"])} if run else {}
    out = []
    for k in CANDIDATES:
        res = results.get(k)
        fwd = _forward_for(shadow, k)
        man = (res or {}).get("manifest") or (((run or {}).get("protocol") or {}).get("manifests") or {}).get(k) or {}
        row = {"key": k, "strategy_id": man.get("strategy_id") or k.split("-")[0],
               "coin": man.get("coin") or k.split("-")[1].replace("USDT", ""),
               "timeframe": man.get("timeframe") or k.split("-")[2].split("@")[0],
               "label": f"{k.split('-')[0]} · {k.split('-')[1].replace('USDT', '')} · {k.split('-')[2].split('@')[0]}",
               "manifest": man, "pipeline": pipeline(k, res, run, fwd),
               "historical": None, "forward": fwd,
               "venues": {"historical": {"venue": VENUE, "label": VENUES[VENUE] + " (historical data and fees)"},
                          "forward": {"venue": VENUE, "label": VENUES[VENUE] + " (live shadow)"},
                          "target_live": {"venue": TARGET_LIVE_VENUE, "label": VENUES[TARGET_LIVE_VENUE],
                                          "note": "configured real-money venue; Bybit execution validation "
                                                  "required before any live promotion"}}}
        if res:
            h = _pick(res, RESULT_FIELDS)
            h["metrics"] = _pick(res.get("metrics"), METRIC_FIELDS)
            h.pop("manifest", None)
            if key == k:
                h["ledger"] = res.get("ledger") or []
                h["daily_equity"] = res.get("daily_equity") or []
            else:
                eq = res.get("daily_equity") or []
                step = max(1, len(eq) // 120)
                h["equity_preview"] = eq[::step] + (eq[-1:] if eq and (len(eq) - 1) % step else [])
            row["historical"] = h
        out.append(row)
    return {"ok": True, "run": _pick(run, ("run_id", "created_ts", "finished_ts", "status", "label", "first_month",
                                           "last_month", "venue", "source_test_run", "protocol_fingerprint",
                                           "progress", "summary", "dataset")) if run else None,
            "protocol": (run or {}).get("protocol"),
            "target_live_venue": {"venue": TARGET_LIVE_VENUE, "label": VENUES[TARGET_LIVE_VENUE]},
            "candidates": out,
            "note": "HISTORICAL VALIDATION and LIVE FORWARD SHADOW are separate evidence. Their PnLs are never added."}
