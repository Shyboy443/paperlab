"""Live shadow analytics: forward results per bot, CONTROL vs +JEV per pair, and the live Jev V1
verdict -- computed from the shadow_* tables only, i.e. from genuinely unseen FORWARD data.

The verdict never rests on pair counts. It needs, together: enough eligible controls, enough
RESOLVED decisions (the candidate's real or counterfactual trade has closed), discrimination (AUC
with an interval), calibration, the economic result against BOTH the control and the always-skip
baseline, how many trades Jev actually let through, and the quality of the controls themselves.
"""
from __future__ import annotations

from typing import Any, Sequence

from app.competition.jev_experiment import auc_with_ci

MIN_PAIRS = 10
MIN_RESOLVED = 100          # resolved live decisions before any verdict
MIN_TAKEN = 20              # trades Jev let through before "it improves results" can be said
RETIRE_BAND = 0.03          # AUC within 0.5 +- this, with an interval spanning 0.5 -> retire V1
BUCKETS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0001))

# The replay experiment on CONTAMINATED data (Jul-Aug 2026), kept visible as history.
HISTORICAL_V1 = {"run_id": "527331f5d0c7", "verdict": "NO EDGE", "skip_rate": 0.997, "auc": 0.512,
                 "auc_ci": [0.498, 0.526], "decisions": 9188, "pairs": 30,
                 "null_note": "always-skip baseline slightly better (+2.92 vs +2.89 USDT per pair)",
                 "period": "2026-07..2026-08 (replay, contaminated window)"}


def forward_experiment(config: dict[str, Any], arena_config_fingerprint: str | None) -> tuple[str, dict[str, Any]]:
    """The identity of a FORWARD EXPERIMENT: sessions with the same identity are one experiment.

    Everything that can change what a bot does or what it is charged is in it -- the strategy
    sources, the arena configuration they were frozen with (risk, sizing, cost gate, execution),
    the fee schedule, the venue and the Jev prompt/policy/model. A deploy that changes none of
    these continues the experiment; a change to any of them starts a new one."""
    import hashlib
    import json as _json
    jev = config.get("jev") or {}
    market = str(config.get("market_data") or "")
    venue = config.get("venue") or ("BINANCE_USDM" if "Binance" in market else "UNKNOWN")
    ident = {"venue": venue, "source_run_id": config.get("source_run_id"),
             "arena_config_fingerprint": config.get("arena_config_fingerprint") or arena_config_fingerprint,
             "strategy_fingerprints": config.get("strategy_fingerprints") or {}, "fees": config.get("fees") or {},
             "execution": config.get("execution") or {}, "cost_gate_min_ratio": config.get("cost_gate_min_ratio"),
             "profile": config.get("profile"), "starting_balance": config.get("starting_balance"),
             "leverage_ceiling": config.get("leverage_ceiling"),
             "jev": {k: jev.get(k) for k in ("model", "prompt_version", "policy_version")}}
    fid = "fx-" + hashlib.sha256(_json.dumps(ident, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return fid, ident


def pct(vals: Sequence[float], q: float) -> float | None:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def trade_stats(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    nets = [float(t.get("net") or 0.0) for t in trades]
    rs = [float(t.get("r") or 0.0) for t in trades]
    gross_w = sum(x for x in nets if x > 0)
    gross_l = -sum(x for x in nets if x < 0)
    return {"trades": n, "net": sum(nets), "gross": sum(float(t.get("gross") or 0.0) for t in trades),
            "fees": sum(float(t.get("fees") or 0.0) for t in trades),
            "wins": sum(1 for x in nets if x > 0), "win_rate": (sum(1 for x in nets if x > 0) / n) if n else None,
            "expectancy_r": (sum(rs) / n) if n else None,
            "profit_factor": (gross_w / gross_l) if gross_l > 0 else (None if not gross_w else 999.0),
            "first_ts": min((t.get("entry_ts") or 0 for t in trades), default=None),
            "last_ts": max((t.get("exit_ts") or 0 for t in trades), default=None)}


def forward_by_bot(trades: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        if not t.get("counterfactual"):
            by.setdefault(t["bot_key"], []).append(t)
    return {k: trade_stats(v) for k, v in by.items()}


def decision_stats(ds: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(ds)
    lat = [d["latency_ms"] for d in ds if d.get("latency_ms") is not None and not d.get("error_code")]
    slip = [d["latency_slippage_bps"] for d in ds if d.get("latency_slippage_bps") is not None]
    errors = [d for d in ds if d.get("error_code")]
    timeouts = [d for d in ds if d.get("timed_out")]
    acts: dict[str, int] = {}
    for d in ds:
        acts[d.get("final_action") or "?"] = acts.get(d.get("final_action") or "?", 0) + 1
    notional_slip = sum((d.get("latency_slippage_bps") or 0.0) * (d.get("notional") or 0.0) / 1e4
                        for d in ds if d.get("final_action") in ("TAKE", "REDUCE"))
    return {"decisions": n, "errors": len(errors), "timeouts": len(timeouts),
            "failure_rate": (len(errors) / n) if n else None, "timeout_rate": (len(timeouts) / n) if n else None,
            "error_codes": _count(d.get("error_code") for d in errors),
            "latency_p50_ms": pct(lat, 0.50), "latency_p95_ms": pct(lat, 0.95), "latency_p99_ms": pct(lat, 0.99),
            "latency_slippage_bps_mean": (sum(slip) / len(slip)) if slip else None,
            "latency_slippage_bps_p95": pct(slip, 0.95), "latency_slippage_usdt_taken": notional_slip,
            "actions": acts, "skip_rate": (acts.get("SKIP", 0) / n) if n else None,
            "cost_usd": sum(float(d.get("cost_usd") or 0.0) for d in ds),
            "resolved": sum(1 for d in ds if d.get("outcome_r") is not None)}


def _count(xs: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in xs:
        out[str(x)] = out.get(str(x), 0) + 1
    return out


def calibration(ds: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for lo, hi in BUCKETS:
        sel = [d for d in ds if d.get("take_probability") is not None and lo <= d["take_probability"] < hi
               and d.get("outcome_r") is not None]
        rs = [float(d["outcome_r"]) for d in sel]
        rows.append({"bucket": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": len(sel),
                     "mean_take_probability": (sum(d["take_probability"] for d in sel) / len(sel)) if sel else None,
                     "win_rate": (sum(1 for r in rs if r > 0) / len(rs)) if rs else None,
                     "mean_r": (sum(rs) / len(rs)) if rs else None})
    return rows


def pairs_table(pair_ids: Sequence[str], trades: Sequence[dict[str, Any]],
                ds: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for pid in pair_ids:
        ctl = [t for t in trades if t.get("pair_id") == pid and t.get("role") == "CONTROL" and not t.get("counterfactual")]
        jev = [t for t in trades if t.get("pair_id") == pid and t.get("role") == "JEV" and not t.get("counterfactual")]
        dd = [d for d in ds if d.get("pair_id") == pid]
        c, j = trade_stats(ctl), trade_stats(jev)
        out.append({"pair_id": pid, "control_key": pid.split(":", 1)[-1], "control": c, "jev": j,
                    "edge_delta": j["net"] - c["net"], "decisions": len(dd),
                    "skipped": sum(1 for d in dd if d.get("final_action") == "SKIP"),
                    "taken": sum(1 for d in dd if d.get("final_action") in ("TAKE", "REDUCE"))})
    return out


def verdict(n_pairs: int, pairs: Sequence[dict[str, Any]], ds: Sequence[dict[str, Any]],
            n_eligible: int | None = None) -> dict[str, Any]:
    """`n_eligible` = JEV-eligible controls; `n_pairs` = CONTROL vs +JEV pairs actually running (fewer
    when Jev is disabled or not configured). A verdict needs both at the minimum."""
    resolved = [d for d in ds if d.get("outcome_r") is not None and d.get("take_probability") is not None]
    a = auc_with_ci([(float(d["take_probability"]), int(float(d["outcome_r"]) > 0)) for d in resolved])
    ctl_net = sum(p["control"]["net"] for p in pairs)
    jev_net = sum(p["jev"]["net"] for p in pairs)
    taken = sum(p["jev"]["trades"] for p in pairs)
    ctl_exp = [p["control"]["expectancy_r"] for p in pairs if p["control"]["expectancy_r"] is not None]
    n_eligible = n_pairs if n_eligible is None else n_eligible
    facts = {"pairs": n_pairs, "eligible_controls": n_eligible, "resolved_decisions": len(resolved), "auc": a,
             "control_net": ctl_net, "jev_net": jev_net, "always_skip_net": 0.0,
             "jev_minus_control": jev_net - ctl_net, "jev_minus_always_skip": jev_net,
             "trades_taken": taken, "controls_forward_expectancy_r": (sum(ctl_exp) / len(ctl_exp)) if ctl_exp else None,
             "min_pairs": MIN_PAIRS, "min_resolved": MIN_RESOLVED, "min_taken": MIN_TAKEN}
    if n_eligible < MIN_PAIRS:
        running = (f"{n_pairs} CONTROL vs +JEV pairs running and collecting evidence" if n_pairs
                   else "no pairs running (Jev disabled or not configured here)")
        return {"verdict": "NO VERDICT", "status": "INSUFFICIENT JEV-ELIGIBLE CONTROLS",
                "why": f"{n_eligible} JEV-eligible controls, {MIN_PAIRS} required for a verdict; {running}",
                **facts}
    if n_pairs < MIN_PAIRS:
        return {"verdict": "NO VERDICT", "status": "PAIRS NOT RUNNING",
                "why": f"{n_eligible} eligible controls but only {n_pairs} pairs running", **facts}
    if len(resolved) < MIN_RESOLVED:
        return {"verdict": "NO VERDICT", "status": "COLLECTING",
                "why": f"{len(resolved)} resolved live decisions, {MIN_RESOLVED} required", **facts}
    lo, hi, auc = a.get("low"), a.get("high"), a.get("auc")
    if auc is not None and lo is not None and lo <= 0.5 <= hi and abs(auc - 0.5) <= RETIRE_BAND:
        return {"verdict": "NO EDGE", "status": "RETIRE JEV V1",
                "why": f"AUC {auc:.3f} [{lo:.3f}, {hi:.3f}] on unseen live data: no discrimination", **facts}
    edge = (lo is not None and lo > 0.5 and jev_net > ctl_net and jev_net > 0.0 and taken >= MIN_TAKEN)
    if edge:
        return {"verdict": "EDGE (PROVISIONAL)", "status": "EVIDENCE",
                "why": "AUC interval above 0.5, beats its controls AND the always-skip baseline, "
                       f"with {taken} trades taken", **facts}
    reasons = []
    if lo is None or lo <= 0.5:
        reasons.append("AUC interval includes 0.5")
    if jev_net <= ctl_net:
        reasons.append("does not beat its controls")
    if jev_net <= 0.0:
        reasons.append("does not beat always-skip")
    if taken < MIN_TAKEN:
        reasons.append(f"only {taken} trades taken")
    return {"verdict": "NO EDGE", "status": "NO EDGE", "why": "; ".join(reasons), **facts}
