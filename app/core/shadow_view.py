"""Live shadow payloads for the API (public and private share one allow-listed builder).

Everything is rebuilt field by field. The Jev state payload never leaves the server here, and there
is nothing secret in the shadow tables to begin with: no key, no account, no order id.
"""
from __future__ import annotations

from typing import Any

import time

from app.live.analytics import (HISTORICAL_V1, calibration, decision_stats, forward_by_bot,
                                forward_experiment, pairs_table, verdict)

BOT_FIELDS = ("key", "role", "pair_id", "control_key", "strategy_id", "symbol", "coin", "timeframe",
              "params_version", "live", "warm_bars", "live_bars", "last_bar_ts", "equity", "start_equity",
              "net", "max_dd", "trades", "wins", "net_closed", "sum_r", "mode", "evaluating", "rejects", "error",
              "continuity")
POSITION_FIELDS = ("side", "qty", "entry", "mark", "upnl", "stop", "entry_ts", "leverage")
DECISION_FIELDS = ("id", "session_id", "bot_key", "pair_id", "symbol", "timeframe", "side", "signal_ts",
                   "candidate_wall_ms", "request_start_ms", "response_ms", "decision_ms", "submit_ms",
                   "latency_ms", "mid_at_candidate", "mid_at_response", "latency_slippage_bps",
                   "model_requested", "model_resolved", "prompt_version", "policy_version",
                   "take_probability", "setup_quality", "risk_state", "regime", "final_action",
                   "final_level", "risk_multiplier", "error_code", "timed_out", "cost_usd",
                   "outcome_kind", "outcome_net", "outcome_r", "outcome_exit", "resolved_ts")
CONFIG_FIELDS = ("source_run_id", "source_label", "dataset_role", "params_version", "strategy_fingerprints",
                 "fees", "execution", "cost_gate_min_ratio", "profile", "starting_balance",
                 "leverage_ceiling", "symbols", "controls", "eligible_controls", "min_pairs", "jev",
                 "warmup_hours", "go_live_ms", "market_data", "continuity")
SESSION_FIELDS = ("session_id", "created_ts", "live_ts", "ended_ts", "status", "source_run_id")
EVENT_FIELDS = ("type", "bot_key", "role", "pair_id", "symbol", "timeframe", "strategy_id", "side",
                "signal_ts", "price", "stop", "signal_quality", "edge_to_cost", "expected_move_pct",
                "outcome", "reason", "qty", "fee", "ts", "leverage", "attack_state", "risk_pct",
                "decision_id", "entry_ts", "exit_ts", "entry_price", "exit_price", "gross", "fees",
                "net", "r", "exit_kind", "counterfactual", "final_action", "final_level",
                "take_probability", "latency_ms", "latency_slippage_bps", "error_code", "timed_out",
                "status", "error", "rederived")


def _pick(d: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any]:
    d = d or {}
    return {k: d.get(k) for k in fields if k in d}


CONTINUITY_FIELDS = ("mode", "forward_from_ms", "live_from_ms", "backfill_from_ms", "rederived", "matched",
                     "diverged", "unreproduced")


def _trades(storage: Any, session_ids: list[str], counterfactual: bool | None) -> list[dict[str, Any]]:
    """An experiment's trades. A `session_end` row is a forced exit written before sessions carried
    their books (a stop closed whatever was open): it is not a trade and never counted."""
    return [t for t in storage.shadow_trades(counterfactual=counterfactual, session_ids=session_ids)
            if t.get("exit_kind") != "session_end"]


def bot_public(b: dict[str, Any], fwd: dict[str, Any] | None) -> dict[str, Any]:
    out = _pick(b, BOT_FIELDS)
    if isinstance(out.get("continuity"), dict):
        out["continuity"] = _pick(out["continuity"], CONTINUITY_FIELDS)
    out["open_positions"] = [_pick(p, POSITION_FIELDS) for p in (b.get("open_positions") or [])]
    out["forward"] = fwd or {"trades": 0, "net": 0.0}
    return out


def experiments(storage: Any, limit: int = 500) -> list[dict[str, Any]]:
    """Every live shadow session with its FORWARD EXPERIMENT id, newest first. Sessions recorded
    before experiments existed get their id computed from their own stored configuration (and
    written back), so a deploy never resets the evidence -- and never stitches incompatible runs."""
    sessions = storage.shadow_sessions(limit)
    fps: dict[str, str | None] = {}
    for sess in sessions:
        if sess.get("experiment_id"):
            continue
        cfg = sess.get("config") or {}
        rid = cfg.get("source_run_id") or sess.get("source_run_id")
        if rid not in fps:
            fps[rid] = ((storage.arena_run(rid) or {}).get("config_fingerprint")) if rid else None
        fid, ident = forward_experiment(cfg, fps[rid])
        sess["experiment_id"], sess["experiment"] = fid, ident
        try:
            storage.shadow_session_update(sess["session_id"], experiment_id=fid, experiment_json=ident)
        except Exception:
            pass
    return sessions


def experiment_summary(storage: Any, fid: str, sessions: list[dict[str, Any]], live_session: str | None,
                       now_ms: int | None = None) -> dict[str, Any]:
    """Cumulative forward evidence of ONE experiment across all of its sessions."""
    now_ms = now_ms or int(time.time() * 1000)
    mine = sorted((x for x in sessions if x.get("experiment_id") == fid), key=lambda x: x.get("created_ts") or 0)
    sids = [x["session_id"] for x in mine]
    seen = storage.shadow_session_last_seen(sids)
    observed = 0
    for x in mine:
        start = x.get("live_ts")
        if not start:
            continue
        end = now_ms if x["session_id"] == live_session else (x.get("ended_ts") or seen.get(x["session_id"]) or start)
        observed += max(0, end - start)
    first_cfg = (mine[0].get("config") or {}) if mine else {}
    # The book trades from the first session's go-live on (continuity.plan uses the same instant).
    started = (int((first_cfg.get("continuity") or {}).get("forward_from_ms") or first_cfg.get("go_live_ms") or 0)
               or min((x.get("live_ts") or x.get("created_ts") or now_ms for x in mine), default=None))
    trades = _trades(storage, sids, False)
    return {"experiment_id": fid, "identity": (mine[-1].get("experiment") if mine else None) or {},
            "started_ts": started, "elapsed_ms": (now_ms - started) if started else 0,
            "observed_ms": observed, "coverage": (observed / (now_ms - started)) if started and now_ms > started else None,
            "sessions": len(mine), "session_ids": sids,
            "signals": storage.shadow_event_count("candidate", sids),
            "closed_trades": sum(1 for t in trades if not str(t.get("bot_key", "")).endswith("+JEV")),
            "closed_trades_jev": sum(1 for t in trades if str(t.get("bot_key", "")).endswith("+JEV")),
            "rederived_trades": sum(1 for t in trades if t.get("rederived")),
            "jev_decisions": len(storage.shadow_decisions(session_ids=sids))}


def shadow_payload(storage: Any, svc: Any = None) -> dict[str, Any]:
    health = svc.health() if svc is not None else {"status": "DISABLED", "enabled": False}
    live_status = (svc.status if svc is not None else {}) or {}
    sessions = experiments(storage)
    current = sessions[0] if sessions else None
    config = live_status.get("config") or ((current or {}).get("config") or {})
    bots = live_status.get("bots")
    if not bots and current is not None:
        bots = storage.shadow_bots(current["session_id"])
    bots = bots or []
    fid = (current or {}).get("experiment_id")
    live_session = live_status.get("session_id") if live_status.get("status") in ("LIVE", "WARMING_UP", "DEGRADED") else None
    fx = experiment_summary(storage, fid, sessions, live_session) if fid else None
    sids = fx["session_ids"] if fx else []
    # Everything below is THIS experiment only: an incompatible earlier experiment is never merged.
    trades = _trades(storage, sids, None)
    fwd = forward_by_bot(trades)
    ds = storage.shadow_decisions(session_ids=sids)
    pair_ids = sorted({b["pair_id"] for b in bots if b.get("pair_id")})
    pairs = pairs_table(pair_ids, trades, ds)
    eligible = list(config.get("eligible_controls") or [])
    stats = decision_stats(ds)
    others = {}
    for x in sessions:
        if x.get("experiment_id") and x.get("experiment_id") != fid:
            o = others.setdefault(x["experiment_id"], {"experiment_id": x["experiment_id"], "sessions": 0,
                                                      "first_ts": x.get("created_ts"), "last_ts": x.get("created_ts")})
            o["sessions"] += 1
            o["first_ts"] = min(o["first_ts"] or 0, x.get("created_ts") or 0) or o["first_ts"]
            o["last_ts"] = max(o["last_ts"] or 0, x.get("created_ts") or 0)
    return {
        "ok": True, "status": health,
        "session": _pick(current, SESSION_FIELDS) if current else None, "sessions": len(sessions),
        "forward_experiment": ({k: v for k, v in fx.items() if k != "session_ids"}
                               | {"continuity": _pick(live_status.get("continuity") or config.get("continuity"),
                                                      CONTINUITY_FIELDS + ("resumed_from_sessions",))}) if fx else None,
        "other_experiments": list(others.values()),
        "config": _pick(config, CONFIG_FIELDS),
        "eligible_controls": eligible,
        "prices": live_status.get("prices") or {},
        "bots": [bot_public(b, fwd.get(b.get("key"))) for b in bots],
        "forward_total": {"trades": sum(v["trades"] for k, v in fwd.items() if not k.endswith("+JEV")),
                          "net": sum(v["net"] for k, v in fwd.items() if not k.endswith("+JEV"))},
        "pairs": pairs,
        "jev_live": {**stats, "calibration": calibration(ds),
                     "verdict": verdict(len(pair_ids), pairs, ds, n_eligible=len(eligible))},
        "jev_historical": HISTORICAL_V1,
        "decisions_recent": [_pick(d, DECISION_FIELDS) for d in ds[-50:]][::-1],
    }


def activity_payload(storage: Any, limit: int = 100, before_id: int | None = None) -> dict[str, Any]:
    rows = storage.shadow_events(limit=max(1, min(limit, 500)), before_id=before_id)
    return {"ok": True, "events": [{"id": r["id"], "ts": r["ts"], "kind": r["kind"],
                                    **_pick(r.get("data"), EVENT_FIELDS)} for r in rows]}
