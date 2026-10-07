"""V3.1 AGGRESSIVE EDGE payloads for the API, the dashboards and the report (public and private share this
one allow-listed builder). Everything is rebuilt field by field; nothing here is secret -- no key, no
account, no Jev state payload -- and nothing here can start, change or trade anything.
"""
from __future__ import annotations

from typing import Any

from app.core.storage_candidates import finite

VENUE_LABEL = "BYBIT LINEAR costs & filters · BINANCE USD-M 1m price tape"
RUN_FIELDS = ("run_id", "created_ts", "finished_ts", "status", "stage", "label", "dataset_role", "config_fingerprint")
CONFIG_FIELDS = ("protocol", "dataset_role", "observe_from", "trade_from", "trade_to", "coins", "timeframes",
                 "starting_balance", "capacity_balances", "leverage_ceiling", "fees", "max_fee_share_of_r", "edge",
                 "maker", "field_min_pairs", "field_target_pairs", "field_coins_per_slot", "random_seeds", "gates",
                 "strategy_fingerprints", "jev_fingerprints", "edge_model_fingerprint", "venue", "price_tape",
                 "rules_source", "dataset_fingerprint", "risk_profile", "tier_risk", "participation_per_day",
                 "participation_min_trades")
DETAIL_FIELDS = ("identity", "key", "role", "pair_id", "family", "fingerprint", "window", "balance", "metrics",
                 "activity", "funnel", "elapsed_s", "state", "failure_mode", "score")
V3_FROZEN = {"run_id": "v3-e39e94890b", "advanced_set": "NONE", "doc": "docs/V3_RESULTS_FREEZE.md"}


def _pick(d: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any]:
    d = d or {}
    return {k: d.get(k) for k in fields if k in d}


def runs_by_role(storage: Any) -> dict[str, dict[str, Any] | None]:
    runs = storage.v31_runs(30)
    out: dict[str, dict[str, Any] | None] = {"DEVELOPMENT": None, "TEST": None}
    for role in out:
        mine = [r for r in runs if r.get("dataset_role") == role]
        out[role] = next((r for r in mine if r.get("status") == "complete"), None) or (mine[0] if mine else None)
    return out


def _run_block(storage: Any, run: dict[str, Any] | None) -> dict[str, Any] | None:
    if run is None:
        return None
    summary = run.get("summary") or {}
    cfg = run.get("config") or {}
    out = {"run": _pick(run, RUN_FIELDS), "config": _pick(cfg, CONFIG_FIELDS),
           "progress": {**(run.get("progress") or {}), "bots_done": len(storage.v31_bot_keys(run["run_id"]))},
           "field": run.get("field") or {}, "evidence": run.get("evidence") or {}}
    if summary:
        out["summary"] = {k: v for k, v in summary.items() if k not in ("field",)}
    else:
        rows = storage.v31_bots(run["run_id"])
        out["partial"] = [{"key": r["key"], "role": r["role"], "coin": r["identity"]["coin"],
                           "tf": r["identity"]["timeframe"], "strategy_id": r["identity"]["strategy_id"],
                           "trades": (r.get("metrics") or {}).get("trades"),
                           "net_pnl": (r.get("metrics") or {}).get("net_profit"),
                           "signals": (r.get("activity") or {}).get("signals"),
                           "executed": (r.get("funnel") or {}).get("executed")}
                          for r in rows if r["role"] != "RANDOM"][:600]
    return out


def v31_payload(storage: Any, run_id: str | None = None) -> dict[str, Any]:
    if run_id:
        run = storage.v31_run(run_id)
        roles = {"DEVELOPMENT": run, "TEST": None} if (run or {}).get("dataset_role") != "TEST" else {"DEVELOPMENT": None, "TEST": run}
    else:
        roles = runs_by_role(storage)
    dev, test = _run_block(storage, roles["DEVELOPMENT"]), _run_block(storage, roles["TEST"])
    out = {"ok": True, "venue_label": VENUE_LABEL, "v3": V3_FROZEN, "dev": dev, "test": test,
           "note": None if (dev or test) else "no V3.1 run yet"}
    return finite(out)


def v31_bot_payload(storage: Any, key: str, run_id: str | None = None) -> dict[str, Any]:
    if run_id:
        run = storage.v31_run(run_id)
    else:
        roles = runs_by_role(storage)
        run = roles["TEST"] if (roles["TEST"] and key in storage.v31_bot_keys(roles["TEST"]["run_id"])) else roles["DEVELOPMENT"]
    if run is None:
        return {"ok": False, "error": "no V3.1 run"}
    rows = storage.v31_bots(run["run_id"], heavy=True, keys=[key])
    if not rows:
        return {"ok": False, "error": f"no bot {key} in {run['run_id']}"}
    rec = rows[0]
    pair = next((p for p in ((run.get("summary") or {}).get("pairs") or []) if p.get("pair_id") == rec.get("pair_id")), None)
    siblings = [b["key"] for b in storage.v31_bots(run["run_id"]) if b.get("pair_id") == rec.get("pair_id")
                and b["key"] != key and b.get("role") in ("CONTROL", "JEV", "TAKE", "CAPACITY")]
    out = {"ok": True, "run_id": run["run_id"], "dataset_role": run.get("dataset_role"), "venue_label": VENUE_LABEL,
           "bot": _pick(rec, DETAIL_FIELDS), "analysis": rec.get("analysis"), "pair": pair, "siblings": siblings,
           "trades": rec.get("trades") or [], "decisions": rec.get("decisions") or [], "equity": rec.get("equity") or []}
    return finite(out)
