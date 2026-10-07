"""What a reader sees of a CONTROL vs +JEV experiment. One builder for the dashboard, the public API
and the static report, every field from an allow-list.

Nothing here can carry a credential: the ledger has no column for one, and payloads are rebuilt
field by field. The decision inspector publishes a SUMMARY of the state Jev saw -- relative market
features only, which is also all Jev ever received.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.api_public import _pick

RUN_FIELDS = ("run_id", "label", "status", "created_ts", "finished_ts", "arena_run_id",
              "config_fingerprint", "model", "prompt_version", "policy_version", "first_month",
              "last_month", "pairs")
SUMMARY_FIELDS = (
    "pairs", "jev_bots", "control_bots", "candidate_signals", "jev_calls", "cache_hits",
    "api_errors", "error_rate", "error_codes", "accepted", "reduced", "skipped", "attack",
    "acceptance_rate", "skipped_losing", "skipped_winning", "total_input_tokens", "total_cost_usd",
    "cost_per_decision_usd", "ai_cost_vs_gross_profit", "latency_avg_ms", "latency_p95_ms",
    "models_resolved", "improved", "worsened", "unchanged", "improved_keys", "worsened_keys",
    "sign_test_p", "mean_edge_delta_usdt", "median_edge_delta_usdt", "mean_edge_delta_return_pp",
    "median_edge_delta_return_pp", "total_control_net", "total_jev_net", "best_pair",
    "best_edge_delta_usdt", "worst_pair", "worst_edge_delta_usdt", "calibration",
    "price_per_m_input_usd", "null_mean_edge_delta_usdt", "pairs_jev_profitable", "pairs_jev_losing",
    "auc", "spearman_take_p_vs_r", "quintiles", "take_probability_quartiles", "risk_state_counts",
    "regime_counts", "allowed_outcomes", "skipped_outcomes", "verdict", "verdict_detail")
SIDE_FIELDS = ("key", "state", "trades", "net_profit", "net_return_pct", "gross_pnl", "fees_paid",
               "slippage_cost", "funding_paid", "expectancy_r", "profit_factor", "max_drawdown_pct",
               "liquidation_count", "win_rate", "avg_effective_leverage")
STATS_FIELDS = ("reviewed", "accepted", "reduced", "skipped", "attack", "errors", "error_codes",
                "acceptance_rate", "traded", "risk_rejected_after_resize", "skipped_losing",
                "skipped_winning", "skipped_shadow_net", "decision_attribution_estimate", "api_calls",
                "cache_hits", "cache_hit_rate", "latency_avg_ms", "latency_p95_ms", "input_tokens",
                "cost_usd", "models_resolved")
PAIR_FIELDS = ("pair_id", "control_key", "jev_key", "strategy_id", "name", "symbol", "coin",
               "timeframe", "edge_delta_usdt", "edge_delta_return_pp", "economic_net_after_ai",
               "verdict")
DECISION_FIELDS = ("signal_ts", "side", "take_probability", "setup_quality", "risk_state", "regime",
                   "final_action", "final_level", "risk_multiplier", "result", "outcome_kind",
                   "outcome_net", "outcome_r", "outcome_exit", "error_code", "source",
                   "request_latency_ms", "model_resolved", "policy_version")
STATE_SUMMARY = {"signal": ("side", "stop_distance_pct", "target_distance_pct", "reward_risk", "setup"),
                 "market": ("ret_5", "ret_20", "atr_pct", "atr_pct_rank_100", "rsi_14",
                            "ema20_vs_ema50", "volume_ratio_20"),
                 "execution": ("fees_vs_target", "last_funding_rate"),
                 "bot_health": ("drawdown", "recent_expectancy_r", "losing_streak")}
HEALTH_FIELDS = ("status", "configured", "enabled", "model_requested", "model_resolved",
                 "prompt_version", "policy_version", "last_check", "calls_since_boot",
                 "errors_since_boot", "error_codes", "latency_p50_ms", "latency_p95_ms")


def pair_row(p: dict[str, Any]) -> dict[str, Any]:
    out = _pick(p, PAIR_FIELDS)
    out["control"] = _pick(p.get("control"), SIDE_FIELDS)
    out["jev"] = _pick(p.get("jev"), SIDE_FIELDS)
    out["jev_stats"] = _pick(p.get("jev_stats"), STATS_FIELDS)
    return out


def latest_run_id(storage: Any, run_id: str | None = None) -> str | None:
    if run_id:
        return run_id if storage.jev_run(run_id) else None
    for r in storage.jev_runs(limit=10):
        if r.get("status") == "complete":
            return r["run_id"]
    runs = storage.jev_runs(limit=1)
    return runs[0]["run_id"] if runs else None


def jev_payload(storage: Any, run_id: str | None = None, health: dict[str, Any] | None = None) -> dict[str, Any]:
    rid = latest_run_id(storage, run_id)
    out: dict[str, Any] = {"health": _pick(health, HEALTH_FIELDS) if health else None,
                           "runs": [_pick(r, RUN_FIELDS) for r in storage.jev_runs(limit=10)]}
    if rid is None:
        return {**out, "ran": False}
    run = storage.jev_run(rid) or {}
    summary = run.get("summary") or {}
    return {**out, "ran": True, "run": _pick(run, RUN_FIELDS), "summary": _pick(summary, SUMMARY_FIELDS),
            "pairs": [pair_row(p) for p in summary.get("pairs_detail") or []]}


def state_summary(state_json: str | None) -> dict[str, Any]:
    try:
        st = json.loads(state_json or "{}")
    except ValueError:
        return {}
    return {block: {k: (st.get(block) or {}).get(k) for k in keys} for block, keys in STATE_SUMMARY.items()}


def jev_pair_payload(storage: Any, run_id: str | None, pid: str, limit: int = 500) -> dict[str, Any] | None:
    rid = latest_run_id(storage, run_id)
    if rid is None:
        return None
    run = storage.jev_run(rid) or {}
    pairs = {p["pair_id"]: p for p in (run.get("summary") or {}).get("pairs_detail") or []}
    p = pairs.get(pid)
    if p is None:
        return None
    rows = storage.jev_decisions(rid, p["jev_key"], limit=limit)
    decisions = []
    for d in rows:
        item = _pick(d, DECISION_FIELDS)
        item["state"] = state_summary(d.get("state_json"))
        decisions.append(item)
    control = storage.jev_bot(rid, p["control_key"]) or {}
    jev = storage.jev_bot(rid, p["jev_key"]) or {}
    from app.competition.jev_experiment import calibration
    return {"run_id": rid, "run": _pick(run, RUN_FIELDS), "pair": pair_row(p),
            "decisions": decisions, "decisions_total": len(rows),
            "calibration": calibration(rows),
            "control_equity": [[int(t), float(v)] for t, v in (control.get("equity") or [])],
            "jev_equity": [[int(t), float(v)] for t, v in (jev.get("equity") or [])]}
