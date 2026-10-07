"""V3 AGGRESSIVE DISCOVERY orchestration: SCAN -> FIELD -> JEV + TAKE -> RANDOM -> ANALYZE.

Every phase is resumable (a bot row that exists is not replayed again) and parallel (spawned worker
processes, each with its own arena; never a forked SQLite connection). Only the JEV phase makes
network requests -- to OpenRouter, server-side, with the key from the process environment -- and every
answer lands in the run's jev_decisions ledger before the replay moves on.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import multiprocessing
import os
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Callable

from app.competition.v3_arena import FAMILY, FAMILY_LABEL, V3Arena, action_distribution, select_field
from app.competition.v3_config import V3Config, V3Identity, min_trades
from app.competition import v3_analyzer as an

log = logging.getLogger("paperlab.competition.v3.run")

_W: dict[str, Any] = {}


# ---- workers -----------------------------------------------------------------------------------------------

def _init(settings: Any, cfg: V3Config, rules: dict[str, Any], db: str) -> None:
    from app.core.storage import Storage
    _W["arena"] = V3Arena(settings, cfg, rules)
    _W["db"] = db
    _W["storage"] = Storage(db) if db else None


def _gate(job: dict[str, Any]) -> Any:
    from app.ai.jev.v2 import CachedDeciderV2, ClientDeciderV2, JevGateV2, PolicyGate
    g = job["gate"]
    ident = V3Identity(**job["ident"])
    if g["kind"] == "none":
        return None
    if g["kind"] == "take":
        return PolicyGate("TAKE", ident.key)
    if g["kind"] == "random":
        return PolicyGate("RANDOM", ident.key, seed=int(g["seed"]), distribution=g["distribution"],
                          attack_strong_share=float(g.get("strong") or 0.0))
    from app.ai.jev.client import JevClient
    from app.ai.jev.models import JevConfig
    storage = _W["storage"]
    inner = None
    if not g.get("replay_only"):
        jc = dataclasses.replace(JevConfig.from_env(), max_retries=1)
        inner = ClientDeciderV2(JevClient(jc, os.environ.get("OPENROUTER_API_KEY")))
    decider = CachedDeciderV2(storage, inner, job["run_id"], ident.key, g["model"],
                              replay_only=bool(g.get("replay_only")), max_calls=g.get("max_calls"),
                              max_cost_usd=g.get("max_cost_usd"))
    arena: V3Arena = _W["arena"]
    cls = arena.bound(ident)
    control = V3Identity(ident.strategy_id, ident.coin, ident.timeframe, "CONTROL")
    bot = {"key": ident.key, "strategy_id": ident.strategy_id, "strategy_name": getattr(cls, "name", ident.strategy_id),
           "params_version": "v3", "symbol": ident.symbol, "timeframe": ident.timeframe,
           "control_version": control.fingerprint(cls().params)}
    return JevGateV2(decider, storage, job["run_id"], bot, ident.pair_id, g["model"])


def _job(job: dict[str, Any]) -> dict[str, Any]:
    t0 = time.time()
    try:
        rec = _W["arena"].run(V3Identity(**job["ident"]), _gate(job))
        return {"ok": True, "record": rec}
    except Exception as exc:
        log.exception("%s failed", job["ident"])
        return {"ok": False, "ident": job["ident"], "error": f"{type(exc).__name__}: {exc}"[:300],
                "elapsed_s": round(time.time() - t0, 2)}


def run_jobs(storage: Any, run_id: str, jobs: list[dict[str, Any]], settings: Any, cfg: V3Config,
             rules: dict[str, Any], db: str, workers: int,
             on_done: Callable[[dict[str, Any], int, int], None] | None = None) -> list[dict[str, Any]]:
    done = storage.v3_bot_keys(run_id)
    todo = [j for j in jobs if V3Identity(**j["ident"]).key not in done]
    out: list[dict[str, Any]] = []
    if not todo:
        return out
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=max(1, workers), mp_context=ctx, initializer=_init,
                             initargs=(settings, cfg, rules, db)) as pool:
        futures = [pool.submit(_job, j) for j in todo]
        for i, f in enumerate(as_completed(futures), 1):
            r = f.result()
            if r["ok"]:
                storage.v3_bot_save(run_id, r["record"], ts=int(time.time() * 1000))
            out.append(r)
            if on_done:
                on_done(r, i, len(todo))
    return out


# ---- phases ------------------------------------------------------------------------------------------------

def new_run(storage: Any, cfg: V3Config, label: str, extra: dict[str, Any] | None = None) -> str:
    run_id = "v3-" + uuid.uuid4().hex[:10]
    storage.v3_run_start({"run_id": run_id, "created_ts": int(time.time() * 1000), "status": "running",
                          "stage": "SCAN", "label": label, "dataset_role": cfg.dataset_role,
                          "config_fingerprint": cfg.fingerprint(), "config": {**cfg.to_dict(), **(extra or {})}})
    return run_id


def scan_jobs(cfg: V3Config, run_id: str) -> list[dict[str, Any]]:
    from app.strategies.registry import load_v3
    jobs = []
    for sid in sorted(load_v3()):
        for coin in cfg.coins:
            for tf in cfg.scan_timeframes:
                jobs.append({"ident": dataclasses.asdict(V3Identity(sid, coin, tf, "CONTROL")), "run_id": run_id,
                             "gate": {"kind": "none"}})
    return jobs


def twin_jobs(field: dict[str, Any], run_id: str, kind: str, model: str = "", replay_only: bool = False,
              max_calls: int | None = None, max_cost_usd: float | None = None) -> list[dict[str, Any]]:
    role = {"jev": "JEV", "take": "TAKE"}[kind]
    jobs = []
    for p in field["pairs"]:
        ident = V3Identity(p["strategy_id"], p["coin"], p["timeframe"], role, jev_policy="JEV_POLICY_V2")
        jobs.append({"ident": dataclasses.asdict(ident), "run_id": run_id,
                     "gate": {"kind": kind, "model": model, "replay_only": replay_only, "max_calls": max_calls,
                              "max_cost_usd": max_cost_usd}})
    return jobs


def random_jobs(storage: Any, run_id: str, field: dict[str, Any], seeds: int) -> list[dict[str, Any]]:
    jobs = []
    jev = {b["key"]: b for b in storage.v3_bots(run_id, role="JEV", heavy=True)}
    for p in field["pairs"]:
        jkey = V3Identity(p["strategy_id"], p["coin"], p["timeframe"], "JEV", jev_policy="JEV_POLICY_V2").key
        rec = jev.get(jkey)
        if rec is None:
            continue
        dist, strong = action_distribution(rec.get("decisions") or [])
        for s in range(1, seeds + 1):
            ident = V3Identity(p["strategy_id"], p["coin"], p["timeframe"], "RANDOM", seed=s, jev_policy="JEV_POLICY_V2")
            jobs.append({"ident": dataclasses.asdict(ident), "run_id": run_id,
                         "gate": {"kind": "random", "seed": s, "distribution": dist, "strong": strong}})
    return jobs


# ---- analysis ----------------------------------------------------------------------------------------------

def regime_tables(cfg: V3Config, fetch: Callable[[str, int, int], list] | None = None) -> dict[str, dict[int, dict[str, str]]]:
    """Daily regime labels per coin (same definitions as the V2 multi-year protocol), from Binance
    daily closes up to the END of the window only -- nothing after it is ever requested."""
    from app.competition.candidate_validation import Protocol, fetch_daily, regime_table
    from app.competition.v3_arena import day_ms
    fetch = fetch or fetch_daily
    out = {}
    start = day_ms("2025-01-01")
    end = day_ms(cfg.trade_to) + 86_400_000 - 1
    for coin in cfg.coins:
        try:
            daily = fetch(f"{coin}USDT", start, end)
            out[coin] = regime_table([d for d in daily if d[0] <= end], Protocol())
        except Exception as exc:
            log.warning("no daily regime table for %s: %s", coin, exc)
            out[coin] = {}
    return out


def _row(rec: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    idn = rec["identity"]
    return {"key": rec["key"], "role": rec["role"], "pair_id": rec["pair_id"], "strategy_id": idn["strategy_id"],
            "family": FAMILY.get(idn["strategy_id"], ""), "coin": idn["coin"], "tf": idn["timeframe"],
            "experimental": bool(rec.get("experimental")), "edge": analysis["edge"], "cost": analysis["cost"],
            "activity": analysis["activity"], "jev": analysis.get("jev"),
            "liquidations": rec["metrics"].get("liquidation_count") or 0,
            "regime_consistency": an.regime_consistency(analysis["regime"]),
            "state": analysis["state"], "failure_mode": analysis["failure_mode"], "summary": analysis["summary"]}


def analyze_run(storage: Any, run_id: str, cfg: V3Config, tables: dict[str, dict[int, dict[str, str]]]) -> dict[str, Any]:
    run = storage.v3_run(run_id) or {}
    field = run.get("field") or {"pairs": []}
    controls = storage.v3_bots(run_id, role="CONTROL", heavy=True)
    jevs = storage.v3_bots(run_id, role="JEV", heavy=True)
    takes = {b["pair_id"]: b for b in storage.v3_bots(run_id, role="TAKE")}
    randoms: dict[str, list[float]] = {}
    for b in storage.v3_bots(run_id, role="RANDOM"):
        randoms.setdefault(b["pair_id"], []).append(b["metrics"].get("net_profit") or 0.0)
    ctrl_by_pair = {b["pair_id"]: b for b in controls}
    rows: dict[str, dict[str, Any]] = {}
    analyses: dict[str, dict[str, Any]] = {}
    for rec in controls:
        a = an.analyze_bot(rec, cfg, tables.get(rec["identity"]["coin"]))
        analyses[rec["key"]] = a
        rows[rec["key"]] = _row(rec, a)
    pairs = []
    for rec in jevs:
        pid = rec["pair_id"]
        ctl = ctrl_by_pair.get(pid)
        take = takes.get(pid)
        net = rec["metrics"].get("net_profit") or 0.0
        base = {"control_net": (ctl or {}).get("metrics", {}).get("net_profit"),
                "take_net": (take or {}).get("metrics", {}).get("net_profit"),
                "skip_net": 0.0, "random": an.random_baseline(net, randoms.get(pid, []), cfg.gates.random_percentile)}
        a = an.analyze_bot(rec, cfg, tables.get(rec["identity"]["coin"]), base)
        analyses[rec["key"]] = a
        row = _row(rec, a)
        row["jev_contribution"] = net - (base["control_net"] or 0.0)
        rows[rec["key"]] = row
        ca = analyses.get(ctl["key"]) if ctl else None
        pairs.append(pair_block(rec, a, ctl, ca, take, base))
    # ranking: field leaderboard (Jev bots + their controls) and the whole scan (every control)
    field_ctrl_keys = {p["control_key"] for p in pairs}
    field_rows = [rows[k] for k in rows if rows[k]["role"] == "JEV" or k in field_ctrl_keys]
    scan_rows = [r for r in rows.values() if r["role"] == "CONTROL" and not r["experimental"]]
    for r, sc in zip(scan_rows, an.scores(scan_rows, cfg)):
        r["score"] = sc                       # rank among every scanned control
    for r, sc in zip(field_rows, an.scores(field_rows, cfg)):
        r["field_score"] = sc                 # rank inside the Jev field (Jev bots + matched controls)
    for key, r in rows.items():
        storage.v3_bot_analysis(run_id, key, analyses[key], r["state"], r["failure_mode"],
                                r.get("field_score") if r["role"] == "JEV" else r.get("score"))
    field_rows.sort(key=lambda r: -(r.get("field_score") or 0))
    scan_rows.sort(key=lambda r: -(r.get("score") or 0))
    tfs = [tf for tf in ("3m", "5m", "15m", "30m")]
    exp_rows = [r for r in rows.values() if r["role"] == "CONTROL" and r["experimental"]]
    all_ctrl = scan_rows + exp_rows
    summary = {
        "run_id": run_id, "protocol": cfg.protocol, "dataset_role": cfg.dataset_role,
        "window": {"from": cfg.trade_from, "to": cfg.trade_to, "days": cfg.days},
        "venue": cfg.venue, "price_tape": cfg.price_tape,
        "counts": {"controls_scanned": len(scan_rows), "experimental_1m": len(exp_rows), "jev_bots": len(jevs),
                   "matched_controls": len(field_ctrl_keys), "take_twins": len(takes),
                   "random_twins": sum(len(v) for v in randoms.values()),
                   "field_by_tf": _count(r["tf"] for r in field_rows if r["role"] == "JEV"),
                   "active_jev_bots": sum(1 for r in field_rows if r["role"] == "JEV" and r["activity"]["trades"] > 0)},
        "field": field,
        "leaderboard_field": [_lb(r) for r in field_rows],
        "leaderboard_scan": [_lb(r) for r in scan_rows],
        "experimental_1m": [_lb(r) for r in sorted(exp_rows, key=lambda r: -(r["edge"]["net_return_pct"] or -9))],
        "failure_modes": {"jev_bots": _count(r["failure_mode"] for r in field_rows if r["role"] == "JEV"),
                          "field_controls": _count(r["failure_mode"] for r in field_rows if r["role"] == "CONTROL"),
                          "all_controls": _count(r["failure_mode"] for r in scan_rows),
                          "labels": an.LABEL},
        "states": {"jev_bots": _count(r["state"] for r in field_rows if r["role"] == "JEV"),
                   "all_controls": _count(r["state"] for r in scan_rows)},
        "matrix_strategy_tf": an.matrix([{**r, "strategy": f"{r['strategy_id']} {FAMILY_LABEL.get(r['strategy_id'], '')}"} for r in all_ctrl],
                                        "strategy", ["1m"] + tfs),
        "matrix_coin_tf": an.matrix(all_ctrl, "coin", ["1m"] + tfs),
        "pairs": pairs,
        "jev_pooled": jev_pooled(jevs, analyses, storage, run_id),
        "baselines": baselines_summary(pairs),
        "advanced": {"strategies": [r["key"] for r in scan_rows if r["state"] == "ADVANCE"],
                     "jev_bots": [r["key"] for r in field_rows if r["role"] == "JEV" and r["state"] == "ADVANCE"]},
        "best_aggressive": [_lb(r) for r in field_rows + scan_rows
                            if r["activity"]["trades"] >= min_trades(r["tf"], cfg.days)
                            and (r["edge"]["net_pnl"] or 0) > 0][:10],
        "timeframe_verdict": timeframe_verdict(scan_rows, cfg),
        "analyzed_ts": int(time.time() * 1000),
    }
    summary["advanced_set"] = sorted(set(summary["advanced"]["strategies"]) | set(summary["advanced"]["jev_bots"])) or "NONE"
    summary["answers"] = answers(summary)
    storage.v3_run_update(run_id, summary_json=summary, stage="ANALYZED", status="complete",
                          finished_ts=int(time.time() * 1000))
    return summary


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        out[str(x)] = out.get(str(x), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _lb(r: dict[str, Any]) -> dict[str, Any]:
    e, c, a, j = r["edge"], r["cost"], r["activity"], r.get("jev") or {}
    return {"key": r["key"], "role": r["role"], "strategy_id": r["strategy_id"], "family": r["family"],
            "coin": r["coin"], "tf": r["tf"], "pair_id": r["pair_id"], "state": r["state"],
            "failure_mode": r["failure_mode"], "score": r.get("score"), "field_score": r.get("field_score"),
            "net_pnl": e["net_pnl"], "net_return_pct": e["net_return_pct"], "gross_pnl": e["gross_pnl"],
            "trades": a["trades"], "trades_per_day": a["trades_per_day"], "exp_r": e["net_expectancy_r"],
            "gross_exp_r": e["gross_expectancy_r"], "pf": e["net_pf"], "max_dd": e["max_drawdown_pct"],
            "rt_cost_bps": c["round_trip_cost_bps"], "edge_to_cost": c["realized_edge_to_cost"],
            "jev_take": j.get("acceptance_rate"), "jev_auc": (j.get("auc") or {}).get("auc"),
            "jev_contribution": r.get("jev_contribution")}


def pair_block(rec: dict[str, Any], a: dict[str, Any], ctl: dict[str, Any] | None, ca: dict[str, Any] | None,
               take: dict[str, Any] | None, base: dict[str, Any]) -> dict[str, Any]:
    def side(m: dict[str, Any] | None, x: dict[str, Any] | None) -> dict[str, Any] | None:
        if m is None:
            return None
        mm = m.get("metrics") or {}
        return {"key": m["key"], "net": mm.get("net_profit"), "gross": mm.get("gross_pnl"), "fees": mm.get("fees_paid"),
                "slippage": mm.get("slippage_cost"), "trades": mm.get("trades"), "exp_r": mm.get("expectancy_r"),
                "pf": mm.get("profit_factor"), "max_dd": mm.get("max_drawdown_pct"),
                "state": (x or {}).get("state"), "failure_mode": (x or {}).get("failure_mode")}
    j = a.get("jev") or {}
    idn = rec["identity"]
    return {"pair_id": rec["pair_id"], "jev_key": rec["key"], "control_key": ctl["key"] if ctl else None,
            "strategy_id": idn["strategy_id"], "family": FAMILY.get(idn["strategy_id"], ""), "coin": idn["coin"],
            "tf": idn["timeframe"], "jev": side(rec, a), "control": side(ctl, ca),
            "always_take": side(take, None), "always_skip": {"net": 0.0, "trades": 0},
            "random": base.get("random"), "delta_vs_control": (rec["metrics"].get("net_profit") or 0.0) - (base.get("control_net") or 0.0),
            "jev_accepted": j.get("accepted"), "jev_skipped": j.get("skipped"),
            "jev_refused": j.get("refused_after_resize"), "jev_final": j.get("final"),
            "jev_auc": j.get("auc"), "permutation": j.get("permutation"), "state": a["state"],
            "failure_mode": a["failure_mode"]}


def jev_pooled(jevs: list[dict[str, Any]], analyses: dict[str, dict[str, Any]],
               storage: Any = None, run_id: str = "") -> dict[str, Any]:
    ds = [d for r in jevs for d in (r.get("decisions") or [])]
    if not ds:
        return {"decisions": 0}
    pooled = an.jev_block({"role": "JEV", "decisions": ds})
    pooled.pop("permutation", None)
    if storage is not None and run_id:
        pooled["latency_model"] = latency_model(storage, run_id)
    return pooled


def latency_model(storage: Any, run_id: str) -> dict[str, Any]:
    """MODELLED price move while Jev is thinking. A historical fill cannot show it (orders fill at the
    next 1m bar's open either way), so each API decision's own 1m realized volatility (from the state
    Jev was shown) is scaled to its measured request latency: 1-sigma move = vol_1m x sqrt(latency / 60s).
    Reported as percentiles in bps; the real number is measured on the live forward shadow."""
    import math as _m
    moves, lat = [], []
    for d in storage.jev_decisions(run_id):
        if d.get("source") != "api" or d.get("error_code") or not d.get("request_latency_ms"):
            continue
        try:
            st = json.loads(d.get("state_json") or "{}")
            rv = ((st.get("fast_market") or {}).get("realized_vol_1m_60"))
        except ValueError:
            rv = None
        if not isinstance(rv, (int, float)):
            continue
        ms = float(d["request_latency_ms"])
        lat.append(ms)
        moves.append(rv * 1e4 * _m.sqrt(ms / 60_000.0))
    if not moves:
        return {"n": 0}
    return {"n": len(moves), "move_bps_p50": an._r(an._pct(moves, 0.50), 3), "move_bps_p95": an._r(an._pct(moves, 0.95), 3),
            "move_bps_p99": an._r(an._pct(moves, 0.99), 3), "latency_ms_p50": an._r(an._pct(lat, 0.50), 0),
            "latency_ms_p95": an._r(an._pct(lat, 0.95), 0), "latency_ms_p99": an._r(an._pct(lat, 0.99), 0),
            "method": "modelled: 1m realized volatility x sqrt(request latency / 60 s); live slippage is measured on the forward shadow"}


def baselines_summary(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    def tot(k: str) -> float:
        return sum(((p.get(k) or {}).get("net") or 0.0) for p in pairs)
    return {"pairs": len(pairs), "jev_total": tot("jev"), "control_total": tot("control"),
            "always_take_total": tot("always_take"), "always_skip_total": 0.0,
            "random_median_total": sum(((p.get("random") or {}).get("p50") or 0.0) for p in pairs),
            "jev_beats_control": sum(1 for p in pairs if (p["delta_vs_control"] or 0) > 1e-9),
            "jev_worse_than_control": sum(1 for p in pairs if (p["delta_vs_control"] or 0) < -1e-9),
            "jev_beats_skip": sum(1 for p in pairs if ((p.get("jev") or {}).get("net") or 0) > 0),
            "jev_beats_take": sum(1 for p in pairs if ((p.get("jev") or {}).get("net") or 0) > ((p.get("always_take") or {}).get("net") or 0)),
            "jev_beats_random_p90": sum(1 for p in pairs if (p.get("random") or {}).get("p90") is not None
                                        and ((p.get("jev") or {}).get("net") or 0) > p["random"]["p90"])}


def timeframe_verdict(rows: list[dict[str, Any]], cfg: V3Config) -> dict[str, Any]:
    """Which timeframe works, per family: the timeframe with the best mean net return, and how many of
    its bots made money after costs with enough trades."""
    out: dict[str, Any] = {}
    fams = sorted({r["strategy_id"] for r in rows})
    for sid in fams:
        per = {}
        for tf in ("3m", "5m", "15m", "30m"):
            part = [r for r in rows if r["strategy_id"] == sid and r["tf"] == tf]
            if not part:
                continue
            per[tf] = {"mean_net_return": an._r(an._mean([r["edge"]["net_return_pct"] or 0.0 for r in part]), 4),
                       "profitable_active": sum(1 for r in part if (r["edge"]["net_pnl"] or 0) > 0
                                                and r["activity"]["trades"] >= min_trades(tf, cfg.days)),
                       "gross_positive": sum(1 for r in part if (r["edge"]["gross_pnl"] or 0) > 0), "bots": len(part)}
        best = max(per.items(), key=lambda kv: kv[1]["mean_net_return"] or -9)[0] if per else None
        out[sid] = {"by_tf": per, "best_tf": best}
    return out


def answers(s: dict[str, Any]) -> dict[str, str]:
    adv = s["advanced"]
    strat = adv["strategies"]
    jev = adv["jev_bots"]
    b = s["baselines"]
    q1 = ("YES: " + ", ".join(strat) if strat else
          "NO: no aggressive small-timeframe bot passed every discovery gate after costs")
    if not s["pairs"]:
        q2 = "NOT TESTED: no Jev pairs ran"
    elif jev:
        q2 = "YES for " + ", ".join(jev) + " (beat control, always-skip and the random filter, with discrimination)"
    else:
        q2 = (f"NO: Jev beat its control in {b['jev_beats_control']} of {b['pairs']} pairs, the always-skip null in "
              f"{b['jev_beats_skip']}, the random-filter 90th percentile in {b['jev_beats_random_p90']}; no Jev bot "
              f"passed every gate")
    return {"aggressive_edge": q1, "jev_makes_it_better": q2,
            "advanced_set": ", ".join(s["advanced_set"]) if isinstance(s["advanced_set"], list) else "NONE"}
