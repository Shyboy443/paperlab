"""PUBLIC INSPECTION SNAPSHOT: /public/inspection.json and /public/inspection/bot/{id}.json.

One compact, sanitized JSON document that answers -- without JavaScript, cookies, redirects or custom
headers -- what an outside reviewer (or ChatGPT) needs: the best bots, the leaderboard, every bot's
money decomposition and status, qualification counts, Jev results and the forward shadow.

Everything comes from the READ-ONLY results index (app/core/results_index.py) and is rebuilt from
allow-listed fields. The live engine is consulted for exactly two things -- its paper-book scoreboard
numbers and whether it is paper or live (dry run) -- never for the exchange wallet, router, positions,
settings or credentials. Nothing here can place, change or cancel anything.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from app.core import results_index as ri

TOP_FIELDS = ("rank", "bot_id", "program", "stage", "window_from", "window_to", "strategy", "family", "coin",
              "timeframe", "mode", "equity", "return_pct", "trades", "trades_per_day", "gross_pnl", "fees",
              "slippage", "funding", "net_pnl", "expectancy_r", "profit_factor", "max_drawdown", "cost_to_edge",
              "net_without_top3", "status", "failure_reason", "qualified")
LEADER_FIELDS = ("rank", "bot_id", "program", "stage", "strategy", "coin", "timeframe", "mode", "trades",
                 "net_pnl", "return_pct", "expectancy_r", "profit_factor", "max_drawdown", "status", "failure_reason")
JEV_FIELDS = ("jev_policy", "decisions", "skip_rate", "take_rate", "attack_rate", "auc", "auc_low", "auc_high",
              "selection_alpha", "avg_latency_ms", "control_net", "delta_vs_control")
LIVE_FIELDS = ("bot_id", "strategy", "family", "coin", "equity", "trades", "gross_pnl", "fees", "funding",
               "net_pnl", "profit_factor", "max_drawdown", "win_rate", "status")
PIPELINE = ("DISCOVERY / DEVELOPMENT", "HOLDOUT TEST", "MULTI-YEAR", "FORWARD SHADOW", "QUALIFIED",
            "LIVE_CANDIDATE (operator only)")

_CACHE: dict[str, Any] = {"ts": 0.0, "value": None}
_LOCK = threading.Lock()
TTL_S = 30.0


def _pick(d: Mapping[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any]:
    d = d or {}
    return {k: d.get(k) for k in fields}


def _bot(r: Mapping[str, Any], fields: tuple[str, ...] = TOP_FIELDS) -> dict[str, Any]:
    out = _pick(r, fields)
    if r.get("jev"):
        out["jev"] = _pick(r["jev"], JEV_FIELDS)
    if r.get("in_forward") and r.get("forward"):
        f = r["forward"]
        out["forward"] = {"trades": f.get("trades"), "net": f.get("net"), "open_positions": f.get("open_positions")}
    return out


def _paper_status(state: Any, storage: Any = None) -> dict[str, Any]:
    """PAPER or LIVE, the kill switch and the engine state -- the safety facts a reader must see."""
    e = getattr(state, "engine", None)
    storage = storage if storage is not None else getattr(getattr(state, "competition", None), "storage", None)
    real_orders = None
    if storage is not None:
        try:
            real_orders = int(storage.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
        except Exception:
            real_orders = None
    if e is None:
        return {"execution": "UNKNOWN (engine not started)", "real_orders_placed": real_orders}
    dry = bool(getattr(getattr(e, "router", None), "dry_run", True))
    killed = bool(getattr(getattr(getattr(e, "risk", None), "state", None), "killed", False))
    return {"execution": "PAPER (simulated fills, no real orders)" if dry else "LIVE (real orders)",
            "real_orders_placed": real_orders, "kill_switch": "ENGAGED" if killed else "off",
            "engine_state": str(getattr(e, "state", "unknown")),
            "paused_reason": getattr(e, "paused_reason", None) or None}


def _research_programs(storage: Any) -> list[dict[str, Any]]:
    """Every research run, newest first, as one line each (status + headline, no ids users don't need
    beyond the run id itself)."""
    out: list[dict[str, Any]] = []
    try:
        for r in storage.v5_runs(20):
            s = r.get("summary") or {}
            out.append({"program": "V5", "stage": r.get("dataset_role"), "run_id": r["run_id"], "status": r.get("status"),
                        "window": f"{(r.get('config') or {}).get('trade_from')}..{(r.get('config') or {}).get('trade_to')}",
                        "advanced_set": s.get("advanced_set"), "headline": (s.get("answers") or {}).get("raw_edge"),
                        "stage_now": r.get("stage")})
        for r in storage.v4_runs(20):
            s = r.get("summary") or {}
            out.append({"program": "V4", "stage": r.get("dataset_role"), "run_id": r["run_id"], "status": r.get("status"),
                        "window": f"{(r.get('config') or {}).get('trade_from')}..{(r.get('config') or {}).get('trade_to')}",
                        "advanced_set": s.get("advanced_set"), "headline": (s.get("answers") or {}).get("intraday_edge"),
                        "stage_now": r.get("stage")})
        for r in storage.v31_runs(20):
            s = r.get("summary") or {}
            out.append({"program": "V3.1", "stage": r.get("dataset_role"), "run_id": r["run_id"], "status": r.get("status"),
                        "window": f"{(r.get('config') or {}).get('trade_from')}..{(r.get('config') or {}).get('trade_to')}",
                        "advanced_set": s.get("advanced_set"), "headline": (s.get("answers") or {}).get("aggressive_edge")})
        for r in storage.v3_runs(10):
            s = r.get("summary") or {}
            out.append({"program": "V3", "stage": "DEVELOPMENT", "run_id": r["run_id"], "status": r.get("status"),
                        "window": f"{(r.get('config') or {}).get('trade_from')}..{(r.get('config') or {}).get('trade_to')}",
                        "advanced_set": s.get("advanced_set"), "headline": (s.get("answers") or {}).get("aggressive_edge")})
        for r in storage.candidate_runs(10):
            if r.get("status") != "complete":
                continue
            s = r.get("summary") or {}
            out.append({"program": "V2", "stage": "MULTI_YEAR", "run_id": r["run_id"], "status": r.get("status"),
                        "window": f"{r.get('first_month')}..{r.get('last_month')}",
                        "advanced_set": "NONE", "headline": s.get("verdict") or "0 of 3 candidates passed"})
        for r in storage.arena_runs(25):
            out.append({"program": "V2" if str(r.get("label", "")).lower().startswith("v2") else "V1",
                        "stage": r.get("label"), "run_id": r["run_id"], "status": r.get("status"),
                        "window": f"{r.get('first_month')}..{r.get('last_month')}",
                        "advanced_set": r.get("advanced"), "headline": f"{r.get('active_bots')} active bots, "
                                                                       f"{r.get('advanced')} advanced"})
        for r in storage.jev_runs(10):
            s = r.get("summary") or {}
            out.append({"program": "V1", "stage": "JEV EXPERIMENT", "run_id": r["run_id"], "status": r.get("status"),
                        "window": f"{r.get('first_month')}..{r.get('last_month')}", "advanced_set": None,
                        "headline": f"Jev {r.get('policy_version')}: verdict {s.get('verdict')}"})
        for r in storage.validation_runs(10):
            out.append({"program": "V1", "stage": "MULTI_YEAR", "run_id": r["run_id"], "status": r.get("status"),
                        "window": f"{r.get('first_month')}..{r.get('last_month')}", "advanced_set": None,
                        "headline": f"{r.get('total_competitors')} bots, {r.get('windows')} walk-forward windows"})
        for r in storage.competition_runs(10):
            out.append({"program": "V1", "stage": "SEASON", "run_id": r["run_id"], "status": r.get("status"),
                        "window": r.get("label"), "advanced_set": None, "headline": r.get("label")})
    except Exception:
        pass
    return out


def reader(state: Any) -> Any:
    """A READ-ONLY connection to the same database for the inspection thread (the app's own connection
    belongs to the event loop). Falls back to the shared storage for an in-memory database."""
    st = state.competition.storage
    path = str(getattr(st, "path", "") or "")
    if not path or path == ":memory:":
        return st
    try:
        ro = object.__new__(type(st))
        ro.path = path
        ro.on_write = None
        ro.conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False,
                                  timeout=10)
        ro.conn.row_factory = sqlite3.Row
        return ro
    except Exception:
        return st


def live_paper(state: Any) -> list[dict[str, Any]]:
    """The paper bake-off scoreboard -- call on the event loop, where the engine lives."""
    return ri.live_paper_rows(getattr(state, "engine", None))


def build(state: Any, live: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The whole snapshot (cached for TTL_S seconds; the research part is cached inside the index)."""
    now = time.time()
    with _LOCK:
        if _CACHE["value"] is not None and now - _CACHE["ts"] < TTL_S:
            return _CACHE["value"]
    storage = reader(state)
    from app.core.shadow_view import shadow_payload
    try:
        shadow = shadow_payload(storage, getattr(state, "shadow", None))
    except Exception:
        shadow = None
    idx = ri.build(storage, shadow, None, live if live is not None else [])
    cur = idx["current"]
    counts = ri.counts(cur, idx["live_paper"], idx["forward"])
    top = ri.ranked(cur, n=10)
    top_jev = ri.ranked(cur, jev=True, n=10)
    leader = ri.ranked(cur, n=50)
    failures = ri.failure_counts(cur)
    research = [r for r in cur if r.get("stage") not in ("FORWARD", "LIVE_PAPER")]
    control = [r for r in research if r.get("mode") == "CONTROL"]
    jev = getattr(state, "jev", None)
    jev_h = {}
    if jev is not None:
        try:
            h = jev.health()
            jev_h = {"status": h.get("status"), "model": h.get("model_requested"), "calls_since_boot": h.get("calls_since_boot"),
                     "errors_since_boot": h.get("errors_since_boot"), "latency_p50_ms": h.get("latency_p50_ms")}
        except Exception:
            jev_h = {"status": "ERROR"}
    rt = getattr(state, "realtime", None)
    ws = {"status": "LIVE" if rt is not None and getattr(rt, "tasks", None) else "OFF",
          "clients": getattr(getattr(rt, "bus", None), "clients", None)}
    programs = _research_programs(storage)
    best = top[0] if top else None
    from app.core.v6_view import inspection_block as v6_block
    v6svc = getattr(state, "v6", None)
    try:
        v6 = v6_block(storage, v6svc)
    except Exception as exc:
        v6 = {"error": type(exc).__name__}
    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "about": ("PaperLab. The CURRENT experiment is the V6 FORWARD ARENA (`v6_forward`): frozen V6 bots on LIVE "
                  "Bybit market data with simulated fills and real trading costs -- the only clean evaluation data. "
                  "Everything else is research history (historical replays, V1-V5, all frozen). Every result is PAPER. "
                  "Ranked research bots need >= 30 closed trades and are ordered by net expectancy per trade (R)."),
        "v6_forward": v6,
        "system": {"competition_status": _competition_status(programs), **_paper_status(state, storage),
                   "v6_forward_status": (v6svc.health() if v6svc is not None else {"status": "DISABLED"}).get("status"),
                   "ws_status": ws["status"], "jev_status": jev_h.get("status") or "NOT_STARTED",
                   "forward_shadow": (idx["forward"] or {}).get("status"),
                   "venue": ("V5: BYBIT-NATIVE (Bybit 1m tape, funding, open interest, basis; Bybit fees & filters) · "
                             "V3-V4: Bybit costs & filters on a Binance 1m tape · V1, V2: BINANCE_USDM"),
                   "research_jobs_running": [p["run_id"] for p in programs if p.get("status") == "running"]},
        "summary": {"active_bots": counts["running_now"], "evaluated_bots": counts["evaluated_bots"],
                    "bots_with_trades": counts["bots_with_trades"], "gross_profitable": counts["gross_profitable"],
                    "profitable_after_costs": counts["net_profitable"],
                    "profitable_after_costs_30_trades": counts["net_profitable_30_trades"],
                    "positive_expectancy": counts["positive_expectancy"], "pf_above_1": counts["pf_above_1"],
                    "passed_discovery": counts["passed_discovery"], "passed_holdout": counts["passed_holdout"],
                    "passed_multi_year": counts["passed_multi_year"], "in_forward_shadow": counts["in_forward_shadow"],
                    "live_paper_books": counts["live_paper_books"], "live_paper_running": counts["live_paper_running"],
                    "forward_shadow_running": counts["forward_shadow_running"], "qualified": counts["qualified"],
                    "advanced_set": "NONE" if counts["qualified"] == 0 else "SEE qualification",
                    "best_bot": _bot(best, ("bot_id", "program", "stage", "strategy", "coin", "timeframe", "trades",
                                            "net_pnl", "return_pct", "expectancy_r", "profit_factor", "max_drawdown",
                                            "status", "failure_reason")) if best else None,
                    "jev_helping": _jev_verdict(idx["jev_experiments"])},
        "v5": _v5_status(storage),
        "top_bots": [_bot(r) for r in top],
        "top_jev_bots": [_bot(r) for r in top_jev],
        "leaderboard": [_bot(r, LEADER_FIELDS) for r in leader],
        "experiments": programs,
        "forward_shadow": idx["forward"],
        "live_paper_bakeoff": {"note": ("V1 bake-off books running on live data with simulated fills; a book that "
                                        "blows its floor respawns (lives)." if counts["live_paper_running"] else
                                        "STOPPED for V5 (docs/V5_RUNTIME_AUDIT.md): the V1 bake-off books are disarmed "
                                        "and flat; their records are kept as research history."),
                               "running": counts["live_paper_running"],
                               "books": [{**_pick(r, LIVE_FIELDS), "lives": r.get("lives"),
                                          "realized_all_lives": r.get("realized_all_lives")}
                                         for r in sorted(idx["live_paper"], key=lambda r: -(r.get("net_pnl") or 0))]},
        "jev": {"experiments": idx["jev_experiments"], "live": (idx["forward"] or {}).get("jev"), "health": jev_h},
        "qualification": {"pipeline": list(PIPELINE),
                          "counts": {k: counts[k] for k in ("passed_discovery", "passed_holdout", "passed_multi_year",
                                                            "in_forward_shadow", "qualified")},
                          "rule": ("A bot advances only by passing every pre-registered gate of a stage on data it "
                                   "was not designed on; forward shadow and live capital need QUALIFIED + an operator.")},
        "root_causes": {"counts": failures, "labels": ri.ROOT_CAUSE_LABEL,
                        "note": "one primary root cause per bot's current result (what broke first)"},
        "decomposition": {"note": "CONTROL books only (a Jev book re-trades its control's signals)",
                          "by_program": ri.decomposition(control, lambda r: f"{r['program']} {r['stage']}"),
                          "by_family_timeframe": _trim(ri.decomposition(
                              [r for r in control if r.get("program") in ("V3", "V3.1", "V4", "V5")],
                              lambda r: f"{r['program']} {r['stage']} {r['strategy']} {r['timeframe']}"))},
        "links": {"static_report": "/public/competition/report", "static_report_txt": "/public/competition/report.txt",
                  "bot_detail": "/public/inspection/bot/{bot_id}.json", "ui": "/public/competition",
                  "v6_live": "/api/public/competition/v6", "v6_activity": "/api/public/competition/v6/activity",
                  "v6_bot": "/api/public/competition/v6/bot/{bot_key}"},
    }
    with _LOCK:
        _CACHE.update({"ts": now, "value": out})
    return out


def _competition_status(programs: list[dict[str, Any]]) -> str:
    running = [p for p in programs if p.get("status") == "running"]
    if running:
        return "RUNNING: " + ", ".join(f"{p['program']} {p.get('stage')} ({p['run_id']})" for p in running)
    if not programs:
        return "IDLE: no research run yet"
    p = programs[0]
    return f"IDLE · latest {p['program']} {p.get('stage')} {p.get('status')} · advanced set {p.get('advanced_set') or 'NONE'}"


def _trim(rows: list[dict[str, Any]], n: int = 60) -> list[dict[str, Any]]:
    return rows[:n]


def _jev_verdict(exps: list[dict[str, Any]]) -> str:
    """Is Jev helping? Only if it beat the matched random action (selection alpha > 0) with a
    discrimination interval above 0.5 -- beating losing controls by trading less does not count."""
    for e in exps:
        if (e.get("selection_alpha") or 0) > 0 and (e.get("auc_low") or 0) > 0.5:
            return f"YES in {e['experiment']}"
    return ("NO: no Jev experiment beat its matched random action with an AUC interval above 0.5 "
            "(lower exposure on losing strategies is not selection)")


def bot_detail(state: Any, bot_id: str, live: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """One bot, deeper: every index field plus a small sanitized slice of its evidence."""
    snap_storage = reader(state)
    from app.core.shadow_view import shadow_payload
    try:
        shadow = shadow_payload(snap_storage, getattr(state, "shadow", None))
    except Exception:
        shadow = None
    idx = ri.build(snap_storage, shadow, None, live if live is not None else [])
    rows = [r for r in idx["rows"] + idx["current"] + idx["live_paper"] if r.get("bot_id") == bot_id]
    if not rows:
        return None
    r = rows[0]
    out = {k: v for k, v in r.items() if not k.startswith("_") and k in ri.ROW_FIELDS}
    out["evaluations"] = [_pick(x, ("bot_id", "program", "stage", "run_id", "window_from", "window_to", "trades",
                                    "net_pnl", "expectancy_r", "profit_factor", "status", "failure_reason"))
                          for x in idx["rows"] if x.get("_identity") and x.get("_identity") == r.get("_identity")]
    out["analysis"] = _analysis(snap_storage, r)
    return out


def _analysis(storage: Any, r: Mapping[str, Any]) -> dict[str, Any] | None:
    """V3 / V3.1 / V4 bots carry the Bot Analyzer's verdict: gates, diagnoses, entry / exit / holding, edge
    quality. Numbers and labels only."""
    prog, rid = r.get("program"), r.get("run_id")
    key = str((r.get("_identity") or (None, ""))[1] or r.get("bot_id", "").split(":", 1)[-1])
    try:
        if prog == "V3.1":
            rows = storage.v31_bots(rid, keys=[key])
        elif prog == "V3":
            rows = storage.v3_bots(rid, keys=[key])
        elif prog == "V4":
            rows = storage.v4_bots(rid, keys=[key])
        elif prog == "V5":
            rows = storage.v5_bots(rid, keys=[key])
        else:
            return None
    except Exception:
        return None
    if not rows:
        return None
    a = rows[0].get("analysis") or {}
    gates = [{"name": g.get("name"), "ok": g.get("ok"), "actual": g.get("actual"), "threshold": g.get("threshold")}
             for g in a.get("gates") or []]
    keep = {}
    for block in ("activity", "edge", "cost", "concentration", "entry", "exit", "exits", "edge_quality", "holding",
                  "attack", "funnel", "money", "horizons", "exit_mix"):
        v = a.get(block)
        if isinstance(v, dict):
            keep[block] = json.loads(json.dumps(v, default=str))
    return {"state": a.get("state"), "failure_mode": a.get("failure_mode"), "diagnoses": a.get("diagnoses"),
            "p_mean_le_0": a.get("p_mean_le_0"), "gates": gates, "label": a.get("label"), "flags": a.get("flags"),
            "family_verdict": a.get("family_verdict"), "ci90_net_r": a.get("ci90_net_r"), **keep}


V5_ROW = ("rank", "key", "strategy_id", "coin", "horizon", "trades", "trades_per_day", "avg_hold_h", "gross", "fees",
          "slippage", "funding", "net", "net_per_trade", "net_per_day", "exp_r", "gross_exp_r", "pf", "max_dd", "label",
          "state", "failure_mode")


def _v5_status(storage: Any) -> dict[str, Any] | None:
    """The CURRENT arena (V5 HOURLY / DAILY FUTURES ARENA): status, raw-edge and economic verdicts per family x class,
    HOURLY / DAILY viability, the gross -> fees -> slippage -> funding -> net totals, the top controls and Jev bots
    after costs, Jev selection alpha and the qualification state. Numbers and labels only."""
    try:
        runs = storage.v5_runs(20)
    except Exception:
        return None
    out: dict[str, Any] = {"program": "V5 HOURLY / DAILY FUTURES ARENA", "protocol": "docs/V5_PROTOCOL.md",
                           "data": "BYBIT-NATIVE: 1m tape, funding settlements, 1h open interest, premium index (basis), "
                                   "account long/short ratio; no Binance surrogate",
                           "runs": {}}
    for role, name in (("DEVELOPMENT", "development"), ("TEST", "pseudo_holdout")):
        r = next((x for x in runs if x.get("dataset_role") == role), None)
        if r is None:
            out["runs"][name] = None
            continue
        s = r.get("summary") or {}
        block: dict[str, Any] = {"run_id": r["run_id"], "status": r.get("status"), "stage": r.get("stage"),
                                 "window": f"{(r.get('config') or {}).get('trade_from')}..{(r.get('config') or {}).get('trade_to')}"}
        if s:
            fams = [{"strategy_id": f.get("strategy_id"), "family": f.get("family"), "horizon": f.get("horizon"),
                     "raw_edge_passed": (f.get("raw_edge") or {}).get("passed"),
                     "gross_r_100usdt": (f.get("raw_edge") or {}).get("mean_r"),
                     "p_mean_le_0": (f.get("raw_edge") or {}).get("p_mean_le_0"),
                     "trades_100usdt": (f.get("raw_edge") or {}).get("trades"),
                     "subperiod_gross_r": (f.get("raw_edge") or {}).get("subperiod_mean_r"),
                     "net_r_20usdt": (f.get("economic_20") or f.get("economic_20_baseline") or {}).get("mean_r"),
                     "trades_20usdt": (f.get("economic_20") or f.get("economic_20_baseline") or {}).get("trades"),
                     "verdict": f.get("verdict")} for f in s.get("families") or []]
            pairs = sorted(s.get("pairs") or [], key=lambda p: -(((p.get("jev") or {}).get("net")) or -9e9))
            block.update({"answers": s.get("answers"), "advanced_set": s.get("advanced_set"), "families": fams,
                          "viability": s.get("viability"), "costs": (s.get("costs") or {}).get("controls_20"),
                          "costs_all_20usdt_books": (s.get("costs") or {}).get("all_20"),
                          "profitable_controls": s.get("profitable_controls"), "labels": s.get("labels"),
                          "failure_modes": s.get("failure_modes"),
                          "top_controls": [{k: x.get(k) for k in V5_ROW} for x in (s.get("leaderboard_controls") or [])[:10]],
                          "top_jev_bots": [{"pair_id": p.get("pair_id"), "jev": {k: (p.get("jev") or {}).get(k) for k in V5_ROW},
                                            "control_net": (p.get("control") or {}).get("net"),
                                            "selection_alpha": (p.get("random") or {}).get("alpha_usdt")} for p in pairs[:10]],
                          "jev_selection_alpha": s.get("selection_alpha"), "attack": s.get("attack"),
                          "qualified": 0, "passed_all": s.get("passed_all")})
        out["runs"][name] = block
    dev = out["runs"].get("development") or {}
    test = out["runs"].get("pseudo_holdout") or {}
    adv = (test.get("advanced_set") if test.get("answers") else None) or dev.get("advanced_set") or "PENDING"
    out["advanced_set"] = adv
    out["qualification"] = ("QUALIFIED needs every DEVELOPMENT and PSEUDO-HOLDOUT gate, then multi-year, Monte Carlo, "
                            "stress and FORWARD VALIDATION; a 50 / 100 USDT capacity result never qualifies a 20 USDT bot")
    return out
