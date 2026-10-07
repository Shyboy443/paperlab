"""V3 AGGRESSIVE ARENA payloads for the API, the dashboards and the report (public and private share
this one allow-listed builder). Everything is rebuilt field by field; nothing here is secret -- no
key, no account, no Jev state payload -- and nothing here can start, change or trade anything.
"""
from __future__ import annotations

from typing import Any

from app.core.storage_candidates import finite

VENUE_LABEL = "BYBIT LINEAR costs & filters · BINANCE USD-M 1m price tape"
RUN_FIELDS = ("run_id", "created_ts", "finished_ts", "status", "stage", "label", "dataset_role", "config_fingerprint")
CONFIG_FIELDS = ("protocol", "dataset_role", "trade_from", "trade_to", "coins", "scan_timeframes", "field_timeframes",
                 "starting_balance", "leverage_ceiling", "fees", "cost_gate_min_ratio", "attack_min_edge_to_cost",
                 "max_fee_share_of_r", "random_seeds", "permutations", "min_active_jev_bots", "gates",
                 "strategy_fingerprints", "jev_fingerprints", "venue", "price_tape", "rules_source",
                 "dataset_fingerprint", "risk_profile", "participation")
DETAIL_FIELDS = ("identity", "key", "role", "pair_id", "family", "fingerprint", "experimental", "window", "metrics",
                 "activity", "elapsed_s", "state", "failure_mode", "score")


def _pick(d: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any]:
    d = d or {}
    return {k: d.get(k) for k in fields if k in d}


def latest_run(storage: Any, run_id: str | None = None) -> dict[str, Any] | None:
    if run_id:
        return storage.v3_run(run_id)
    runs = storage.v3_runs(20)
    return next((r for r in runs if r.get("status") in ("complete", "running", "insufficient_competitors")), None) \
        or (runs[0] if runs else None)


def v2_result(storage: Any) -> dict[str, Any]:
    """The V2 multi-year verdict stays visible next to V3: failure is evidence the filter works."""
    try:
        runs = [r for r in storage.candidate_runs(10) if r.get("status") == "complete"]
    except Exception:
        runs = []
    if not runs:
        return {"passed": None, "candidates": None}
    run = runs[0]
    res = storage.candidate_results(run["run_id"])
    return {"run_id": run["run_id"], "passed": sum(1 for r in res if r.get("verdict") == "PASS"),
            "candidates": len(res), "label": "V2 MULTI-YEAR", "window": f"{run.get('first_month')}..{run.get('last_month')}"}


def v3_payload(storage: Any, run_id: str | None = None) -> dict[str, Any]:
    run = latest_run(storage, run_id)
    if run is None:
        return {"ok": True, "run": None, "v2": v2_result(storage), "venue_label": VENUE_LABEL,
                "note": "no V3 discovery run yet"}
    summary = run.get("summary") or {}
    cfg = run.get("config") or {}
    progress = run.get("progress") or {}
    n_bots = len(storage.v3_bot_keys(run["run_id"]))
    out = {"ok": True, "run": _pick(run, RUN_FIELDS), "config": _pick(cfg, CONFIG_FIELDS),
           "progress": {**progress, "bots_done": n_bots}, "field": run.get("field") or {},
           "venue_label": VENUE_LABEL, "v2": v2_result(storage)}
    if summary:
        out["summary"] = {k: v for k, v in summary.items() if k not in ("field",)}
    else:
        # a run still in progress: show the rows that exist, without the analysis
        rows = storage.v3_bots(run["run_id"])
        out["partial"] = [{"key": r["key"], "role": r["role"], "coin": r["identity"]["coin"],
                           "tf": r["identity"]["timeframe"], "strategy_id": r["identity"]["strategy_id"],
                           "trades": (r.get("metrics") or {}).get("trades"),
                           "net_pnl": (r.get("metrics") or {}).get("net_profit"),
                           "signals": (r.get("activity") or {}).get("signals")} for r in rows]
    return finite(out)


def v3_bot_payload(storage: Any, key: str, run_id: str | None = None) -> dict[str, Any]:
    run = latest_run(storage, run_id)
    if run is None:
        return {"ok": False, "error": "no V3 run"}
    rows = storage.v3_bots(run["run_id"], heavy=True, keys=[key])
    if not rows:
        return {"ok": False, "error": f"no bot {key} in {run['run_id']}"}
    rec = rows[0]
    pair = next((p for p in ((run.get("summary") or {}).get("pairs") or []) if p.get("pair_id") == rec.get("pair_id")), None)
    siblings = [b["key"] for b in storage.v3_bots(run["run_id"]) if b.get("pair_id") == rec.get("pair_id")
                and b["key"] != key and b.get("role") in ("CONTROL", "JEV", "TAKE")]
    out = {"ok": True, "run_id": run["run_id"], "venue_label": VENUE_LABEL, "bot": _pick(rec, DETAIL_FIELDS),
           "analysis": rec.get("analysis"), "pair": pair, "siblings": siblings,
           "trades": rec.get("trades") or [], "decisions": rec.get("decisions") or [],
           "equity": rec.get("equity") or []}
    return finite(out)
