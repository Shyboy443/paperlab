"""V4 INTRADAY SPECIALIST orchestration (docs/V4_PROTOCOL.md).

Both windows run the SAME phases -- the expected-edge model is causal inside its own window, so the holdout
needs no DEVELOPMENT evidence and no DEVELOPMENT outcome ever reaches it:

    OBSERVE (RAW evidence) -> CONTROL (edge gate, causal) -> FIELD (activity only) -> JEV (Jev V4)
    -> TAKE -> RANDOM (matched action) -> CAPACITY (50 / 100 USDT) -> ANALYZE

Every phase is resumable (a stored bot is never replayed again) and parallel (spawned worker processes, each
with its own arena; never a forked SQLite connection). Only the JEV phase makes network requests.
"""
from __future__ import annotations

import dataclasses
import logging
import multiprocessing
import os
import time
import uuid
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Callable, Mapping, Sequence

from app.competition import v4_analyzer as an
from app.competition.v4_arena import V4Arena, family_of
from app.competition.v4_config import V4Config, V4Identity
from app.competition.v31_edge import calibration, load_evidence

log = logging.getLogger("paperlab.competition.v4.run")

_W: dict[str, Any] = {}
TF_ORDER = ("3m", "5m", "15m", "30m")


# ---- workers -----------------------------------------------------------------------------------------------

def _init(settings: Any, cfg: V4Config, rules: dict[str, Any], db: str, evidence: list[dict[str, Any]]) -> None:
    from app.core.storage import Storage
    _W["arena"] = V4Arena(settings, cfg, rules, evidence)
    _W["storage"] = Storage(db) if db else None


def _gate(job: Mapping[str, Any]) -> Any:
    from app.ai.jev.v4 import CachedDeciderV4, ClientDeciderV4, JevGateV4, ObserverGate, PolicyGateV4
    g = job["gate"]
    ident = V4Identity(**job["ident"])
    kind = g["kind"]
    if kind == "none":
        return None
    if kind == "observer":
        return ObserverGate()
    if kind == "take":
        return PolicyGateV4("TAKE", ident.key)
    if kind == "random":
        return PolicyGateV4("RANDOM", ident.key, seed=int(g["seed"]), distribution=g["distribution"])
    from app.ai.jev.client import JevClient
    from app.ai.jev.models import JevConfig
    storage = _W["storage"]
    inner = None
    if not g.get("replay_only"):
        jc = dataclasses.replace(JevConfig.from_env(), max_retries=1)
        inner = ClientDeciderV4(JevClient(jc, os.environ.get("OPENROUTER_API_KEY")))
    decider = CachedDeciderV4(storage, inner, job["run_id"], ident.key, g["model"], replay_only=bool(g.get("replay_only")),
                              max_calls=g.get("max_calls"), max_cost_usd=g.get("max_cost_usd"))
    arena: V4Arena = _W["arena"]
    cls = arena.bound(ident)
    control = V4Identity(ident.strategy_id, ident.coin, ident.timeframe, "CONTROL")
    bot = {"key": ident.key, "strategy_id": ident.strategy_id, "strategy_name": getattr(cls, "name", ident.strategy_id),
           "params_version": "v4", "symbol": ident.symbol, "timeframe": ident.timeframe,
           "control_version": control.fingerprint(cls().params), "hypothesis": getattr(cls, "hypothesis", ""),
           "thesis": getattr(cls, "thesis", ""), "expected_hold": getattr(cls, "expected_hold", "")}
    return JevGateV4(decider, storage, job["run_id"], bot, ident.pair_id, g["model"])


def _job(job: Mapping[str, Any]) -> dict[str, Any]:
    t0 = time.time()
    try:
        rec = _W["arena"].run(V4Identity(**job["ident"]), _gate(job))
        return {"ok": True, "record": rec}
    except Exception as exc:
        log.exception("%s failed", job["ident"])
        return {"ok": False, "ident": job["ident"], "error": f"{type(exc).__name__}: {exc}"[:300],
                "elapsed_s": round(time.time() - t0, 2)}


def run_jobs(storage: Any, run_id: str, jobs: list[dict[str, Any]], settings: Any, cfg: V4Config,
             rules: dict[str, Any], db: str, workers: int, evidence: list[dict[str, Any]],
             on_done: Callable[[dict[str, Any], int, int], None] | None = None) -> list[dict[str, Any]]:
    done = storage.v4_bot_keys(run_id)
    todo = [j for j in jobs if V4Identity(**j["ident"]).key not in done]
    out: list[dict[str, Any]] = []
    if not todo:
        return out
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=max(1, workers), mp_context=ctx, initializer=_init,
                             initargs=(settings, cfg, rules, db, evidence)) as pool:
        futures = [pool.submit(_job, j) for j in todo]
        for i, f in enumerate(as_completed(futures), 1):
            r = f.result()
            if r["ok"]:
                storage.v4_bot_save(run_id, r["record"], ts=int(time.time() * 1000))
            out.append(r)
            if on_done:
                on_done(r, i, len(todo))
    return out


# ---- phases ------------------------------------------------------------------------------------------------

def new_run(storage: Any, cfg: V4Config, label: str, extra: Mapping[str, Any] | None = None) -> str:
    run_id = "v4-" + uuid.uuid4().hex[:10]
    storage.v4_run_start({"run_id": run_id, "created_ts": int(time.time() * 1000), "status": "running",
                          "stage": "OBSERVE", "label": label, "dataset_role": cfg.dataset_role,
                          "config_fingerprint": cfg.fingerprint(), "config": {**cfg.to_dict(), **dict(extra or {})}})
    return run_id


def _ident(sid: str, coin: str, tf: str, role: str, **k: Any) -> dict[str, Any]:
    jev = "JEV_POLICY_V4" if role in ("JEV", "TAKE", "RANDOM") else ""
    return dataclasses.asdict(V4Identity(sid, coin, tf, role, jev_policy=jev, **k))


def grid_jobs(cfg: V4Config, run_id: str, role: str, kind: str) -> list[dict[str, Any]]:
    from app.strategies.registry import load_v4
    return [{"ident": _ident(sid, coin, tf, role), "run_id": run_id, "gate": {"kind": kind}}
            for sid in sorted(load_v4()) for tf in cfg.timeframes for coin in cfg.coins_for(tf)]


def twin_jobs(field: Mapping[str, Any], run_id: str, kind: str, model: str = "", replay_only: bool = False,
              max_calls: int | None = None, max_cost_usd: float | None = None) -> list[dict[str, Any]]:
    role = {"jev": "JEV", "take": "TAKE"}[kind]
    return [{"ident": _ident(p["strategy_id"], p["coin"], p["timeframe"], role), "run_id": run_id,
             "gate": {"kind": kind, "model": model, "replay_only": replay_only, "max_calls": max_calls,
                      "max_cost_usd": max_cost_usd}} for p in field["pairs"]]


def random_jobs(storage: Any, run_id: str, field: Mapping[str, Any], seeds: int) -> list[dict[str, Any]]:
    from app.ai.jev.v4 import action_distribution_v4
    jev = {b["key"]: b for b in storage.v4_bots(run_id, role="JEV", heavy=True)}
    jobs = []
    for p in field["pairs"]:
        rec = jev.get(V4Identity(p["strategy_id"], p["coin"], p["timeframe"], "JEV", jev_policy="JEV_POLICY_V4").key)
        if rec is None:
            continue
        dist = action_distribution_v4(rec.get("decisions") or [])
        for s in range(1, seeds + 1):
            jobs.append({"ident": _ident(p["strategy_id"], p["coin"], p["timeframe"], "RANDOM", seed=s), "run_id": run_id,
                         "gate": {"kind": "random", "seed": s, "distribution": dist}})
    return jobs


def capacity_jobs(field: Mapping[str, Any], run_id: str, balances: Sequence[float]) -> list[dict[str, Any]]:
    return [{"ident": _ident(p["strategy_id"], p["coin"], p["timeframe"], "CAPACITY", balance=float(b)), "run_id": run_id,
             "gate": {"kind": "none"}} for p in field["pairs"] for b in balances]


def evidence_for(storage: Any, run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The edge model's evidence: THIS run's RAW observations (used causally by every CONTROL / twin)."""
    from app.competition.v4_edge import EdgeModelV4
    raws = storage.v4_bots(run_id, role="RAW", extra=True)
    ev = load_evidence(raws)
    by: dict[str, int] = defaultdict(int)
    for o in ev:
        by[f"{o['sid']} {o['tf']}"] += 1
    return ev, {"run_id": run_id, "observations": len(ev), "raw_bots": len(raws), "by_family_tf": dict(sorted(by.items())),
                "fingerprint": EdgeModelV4(ev).fingerprint()}


# ---- analysis ----------------------------------------------------------------------------------------------

def _r(x: Any, nd: int = 4) -> Any:
    return an._r(x, nd)


def _count(items: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        out[str(x)] = out.get(str(x), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _side(m: Mapping[str, Any] | None, a: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    if m is None:
        return None
    mm = m.get("metrics") or {}
    return {"key": m["key"], "net": mm.get("net_profit"), "gross": mm.get("gross_pnl"), "fees": mm.get("fees_paid"),
            "slippage": mm.get("slippage_cost"), "trades": mm.get("trades"), "exp_r": mm.get("expectancy_r"),
            "pf": mm.get("profit_factor"), "max_dd": mm.get("max_drawdown_pct"),
            "trades_per_day": _r((mm.get("trades") or 0) / max(1e-9, float(m["window"]["days"]))),
            "state": (a or {}).get("state"), "failure_mode": (a or {}).get("failure_mode")}


def _lb(rec: Mapping[str, Any], a: Mapping[str, Any], mode: str) -> dict[str, Any]:
    e, act, j = a["edge"], a["activity"], a.get("jev") or {}
    fin = j.get("final") or {}
    n = sum(fin.values()) or 0
    bal = float(rec.get("balance") or 20.0)
    net = (rec.get("metrics") or {}).get("net_profit") or 0.0
    idn = rec["identity"]
    return {"key": rec["key"], "role": rec["role"], "mode": mode, "strategy_id": idn["strategy_id"],
            "family": rec.get("family") or family_of(idn["strategy_id"]), "coin": idn["coin"], "tf": idn["timeframe"],
            "pair_id": rec["pair_id"], "equity": _r(bal + net), "net_pnl": e["net_pnl"],
            "net_return_pct": e["net_return_pct"], "trades": act["trades"], "trades_per_day": act["trades_per_day"],
            "exp_r": e["net_expectancy_r"], "gross_exp_r": e["gross_expectancy_r"], "pf": e["net_pf"],
            "max_dd": e["max_drawdown_pct"], "p_mean_le_0": a.get("p_mean_le_0"),
            "jev_actions": ({k: _r(v / n, 3) for k, v in fin.items()} if n else None),
            "selection_alpha": ((a.get("baselines") or {}).get("selection") or {}).get("alpha_usdt"),
            "state": a["state"], "failure_mode": a["failure_mode"], "diagnoses": a["diagnoses"],
            "participation_ok": act["participation_ok"], "rt_cost_bps": a["cost"].get("round_trip_cost_bps"),
            "exit_flags": (a.get("exits") or {}).get("flags")}


def _tape(settings: Any, coin: str, months: Sequence[str]) -> Any:
    from app.backtest import archive
    from app.competition.exit_analyzer import Tape
    try:
        return Tape(archive.load_klines(settings, f"{coin}USDT", list(months)))
    except Exception as exc:
        log.warning("no tape for %s: %s", coin, exc)
        return None


def analyze_run(storage: Any, run_id: str, cfg: V4Config, settings: Any,
                dev_summary: Mapping[str, Any] | None = None) -> dict[str, Any]:
    run = storage.v4_run(run_id) or {}
    field = run.get("field") or {"pairs": []}
    recs = storage.v4_bots(run_id, heavy=True, extra=True)
    by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in recs:
        by_role[r["role"]].append(r)
    raw, controls, jevs = by_role.get("RAW", []), by_role.get("CONTROL", []), by_role.get("JEV", [])
    takes = {b["pair_id"]: b for b in by_role.get("TAKE", [])}
    randoms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for b in by_role.get("RANDOM", []):
        randoms[b["pair_id"]].append(b)
    ctrl_by_pair = {b["pair_id"]: b for b in controls}
    caps: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for b in by_role.get("CAPACITY", []):
        caps[b["pair_id"]].append(b)
    field_pids = {f"v4pair:{p['strategy_id']}-{p['coin']}-{p['timeframe']}" for p in field["pairs"]}

    analyses: dict[str, dict[str, Any]] = {}
    for coin in cfg.coins:
        mine = [r for r in recs if r["identity"]["coin"] == coin and r["role"] in ("CONTROL", "JEV", "TAKE", "CAPACITY")]
        if not mine:
            continue
        tape = _tape(settings, coin, cfg.months)
        for rec in mine:
            base = None
            if rec["role"] == "JEV":
                pid = rec["pair_id"]
                ctl = ctrl_by_pair.get(pid)
                base = {"control_net": (ctl or {}).get("metrics", {}).get("net_profit"),
                        "take_net": (takes.get(pid) or {}).get("metrics", {}).get("net_profit"), "skip_net": 0.0,
                        "selection": an.selection_alpha(rec, randoms.get(pid, []), cfg.gates.random_percentile)}
            analyses[rec["key"]] = an.analyze_bot(rec, cfg, tape, base)
        del tape
    for key, a in analyses.items():
        storage.v4_bot_analysis(run_id, key, a, a["state"], a["failure_mode"], None)

    pairs = []
    for rec in jevs:
        pid = rec["pair_id"]
        a = analyses[rec["key"]]
        ctl, take = ctrl_by_pair.get(pid), takes.get(pid)
        ca = analyses.get(ctl["key"]) if ctl else None
        idn = rec["identity"]
        pairs.append({"pair_id": pid, "jev_key": rec["key"], "control_key": ctl["key"] if ctl else None,
                      "strategy_id": idn["strategy_id"], "family": rec.get("family"), "coin": idn["coin"],
                      "tf": idn["timeframe"], "jev": _side(rec, a), "control": _side(ctl, ca), "always_take": _side(take),
                      "always_skip": {"net": 0.0, "trades": 0}, "random": (a.get("baselines") or {}).get("selection") or {},
                      "delta_vs_control": (rec["metrics"].get("net_profit") or 0.0) - ((ctl or {}).get("metrics", {}).get("net_profit") or 0.0),
                      "jev_actions": (a.get("jev") or {}).get("final"), "jev_auc": (a.get("jev") or {}).get("auc_support"),
                      "attack_jev": a.get("attack"), "state": a["state"], "failure_mode": a["failure_mode"]})
    pairs.sort(key=lambda p: (TF_ORDER.index(p["tf"]), p["strategy_id"], p["coin"]))

    field_rows = []
    for rec in jevs + [c for c in controls if c["pair_id"] in field_pids] + list(takes.values()):
        mode = {"JEV": "JEV V4", "CONTROL": "CONTROL", "TAKE": "ALWAYS TAKE"}[rec["role"]]
        field_rows.append(_lb(rec, analyses[rec["key"]], mode))
    ctrl_rows = [_lb(rec, analyses[rec["key"]], "CONTROL") for rec in controls]
    for rows in (field_rows, ctrl_rows):
        sc = an.scores([{**r, "edge": analyses[r["key"]]["edge"], "activity": analyses[r["key"]]["activity"],
                         "cost": analyses[r["key"]]["cost"], "liquidations": 0} for r in rows], cfg)
        for r, s in zip(rows, sc):
            r["score"] = s
        rows.sort(key=lambda r: -(r["score"] or 0))
        for i, r in enumerate(rows, 1):
            r["rank"] = i

    ev_rows = [row for c in controls for row in (c.get("edge_rows") or [])]
    jev_ds = [d for r in jevs for d in (r.get("decisions") or [])]
    alpha = [p["random"].get("alpha_usdt") for p in pairs if (p.get("random") or {}).get("alpha_usdt") is not None]
    fam_tf = lambda r: f"{r['identity']['strategy_id']} {r['identity']['timeframe']}"  # noqa: E731
    capacity = []
    for p in field["pairs"]:
        pid = f"v4pair:{p['strategy_id']}-{p['coin']}-{p['timeframe']}"
        c20 = ctrl_by_pair.get(pid)
        books = {f"{float(b.get('balance') or 0):g}": _side(b, analyses.get(b["key"])) for b in caps.get(pid, [])}
        capacity.append({"pair_id": pid, "20": _side(c20, analyses.get(c20["key"]) if c20 else None), **books,
                         "verdict": an.capacity_verdict(c20, analyses.get(c20["key"]) if c20 else None, caps.get(pid, []))})
    summary = {
        "run_id": run_id, "protocol": cfg.protocol, "dataset_role": cfg.dataset_role,
        "window": {"from": cfg.trade_from, "to": cfg.trade_to, "days": cfg.days}, "venue": cfg.venue,
        "price_tape": cfg.price_tape, "universe": dict(cfg.coins_per_tf), "evidence": run.get("evidence"),
        "counts": {"raw_observers": len(raw), "controls": len(controls), "jev_bots": len(jevs), "take_twins": len(takes),
                   "random_twins": sum(len(v) for v in randoms.values()), "capacity_bots": len(by_role.get("CAPACITY", [])),
                   "field_pairs": len(field["pairs"]), "controls_by_tf": _count(c["identity"]["timeframe"] for c in controls)},
        "field": field, "leaderboard_field": field_rows, "leaderboard_controls": ctrl_rows,
        "viability": an.a31.viability(controls, raw, cfg.timeframes),
        "raw_edge": {"by_family_tf": an.raw_edge_table(raw, fam_tf),
                     "by_family": an.raw_edge_table(raw, lambda r: r["identity"]["strategy_id"]),
                     "by_tf": an.raw_edge_table(raw, lambda r: r["identity"]["timeframe"]),
                     "note": "every legal setup at TAKE size, sequenced like an ungated bot: the edge BEFORE the gate"},
        "edge_quality": {"by_family_tf": an.edge_quality_table(controls, fam_tf),
                         "by_tf": an.edge_quality_table(controls, lambda r: r["identity"]["timeframe"]),
                         "by_family": an.edge_quality_table(controls, lambda r: r["identity"]["strategy_id"]),
                         "by_coin": an.edge_quality_table(controls, lambda r: r["identity"]["coin"])},
        "exits": {"by_tf": an.exit_table(analyses, controls, lambda r: r["identity"]["timeframe"]),
                  "by_family": an.exit_table(analyses, controls, lambda r: r["identity"]["strategy_id"])},
        "calibration": calibration(ev_rows),
        "funnels": {"controls": {tf: an.funnel_totals([c for c in controls if c["identity"]["timeframe"] == tf]) for tf in cfg.timeframes},
                    "jev": {tf: an.funnel_totals([j for j in jevs if j["identity"]["timeframe"] == tf]) for tf in cfg.timeframes}},
        "pairs": pairs,
        "baselines": {"pairs": len(pairs), "jev_total": _r(sum((p["jev"] or {}).get("net") or 0.0 for p in pairs)),
                      "control_total": _r(sum((p["control"] or {}).get("net") or 0.0 for p in pairs)),
                      "always_take_total": _r(sum((p["always_take"] or {}).get("net") or 0.0 for p in pairs)),
                      "always_skip_total": 0.0,
                      "random_median_total": _r(sum((p["random"] or {}).get("random_median") or 0.0 for p in pairs)),
                      "jev_beats_control": sum(1 for p in pairs if (p["delta_vs_control"] or 0) > 1e-9),
                      "jev_beats_skip": sum(1 for p in pairs if ((p["jev"] or {}).get("net") or 0) > 0),
                      "jev_beats_take": sum(1 for p in pairs if ((p["jev"] or {}).get("net") or 0) > ((p["always_take"] or {}).get("net") or 0)),
                      "jev_beats_random_p90": sum(1 for p in pairs if (p["random"] or {}).get("random_p90") is not None
                                                  and ((p["jev"] or {}).get("net") or 0) > p["random"]["random_p90"])},
        "selection_alpha": {"pairs": len(alpha), "total_usdt": _r(sum(alpha)), "mean_usdt": _r(an._mean(alpha)),
                            "positive_pairs": sum(1 for x in alpha if x > 0)},
        "jev_pooled": an.jev_block({"role": "JEV", "decisions": jev_ds}) if jev_ds else {"decisions": 0},
        "attack": {"jev": an.attack_test([t for r in jevs for t in (r.get("trades") or [])], "JEV"),
                   "controls": an.attack_test([t for c in controls for t in (c.get("trades") or [])], "CONTROL")},
        "capacity": {"rows": capacity, "verdicts": _count(r["verdict"] for r in capacity),
                     "note": "capacity diagnostic only: a 50 / 100 USDT result never qualifies the 20 USDT competition"},
        "states": {"controls": _count(analyses[c["key"]]["state"] for c in controls),
                   "jev_bots": _count(analyses[j["key"]]["state"] for j in jevs)},
        "failure_modes": {"controls": _count(analyses[c["key"]]["failure_mode"] for c in controls),
                          "jev_bots": _count(analyses[j["key"]]["failure_mode"] for j in jevs), "labels": an.LABEL},
        "passed": {"controls": sorted(c["key"] for c in controls if analyses[c["key"]]["state"] == "ADVANCE"),
                   "jev_bots": sorted(j["key"] for j in jevs if analyses[j["key"]]["state"] == "ADVANCE")},
        "analyzed_ts": int(time.time() * 1000),
    }
    passed = set(summary["passed"]["controls"]) | set(summary["passed"]["jev_bots"])
    if cfg.dataset_role == "TEST":
        dev_passed = set((dev_summary or {}).get("passed_all") or [])
        summary["dev_passed"] = sorted(dev_passed)
        summary["advanced_set"] = sorted(passed & dev_passed) or "NONE"
    else:
        summary["advanced_set"] = "PENDING TEST" if passed else "NONE"
    summary["passed_all"] = sorted(passed)
    summary["answers"] = answers(summary, cfg)
    storage.v4_run_update(run_id, summary_json=summary, stage="ANALYZED", status="complete",
                          finished_ts=int(time.time() * 1000))
    return summary


def answers(s: Mapping[str, Any], cfg: V4Config) -> dict[str, str]:
    b = s["baselines"]
    eq = (s.get("edge_quality") or {}).get("by_tf") or []
    tf_line = "; ".join(f"{r['group']}: gross {r['gross']} -> net {r['net']} USDT over {r['trades']} trades "
                        f"({r['diagnosis']})" for r in sorted(eq, key=lambda r: TF_ORDER.index(r["group"])))
    passed = s.get("passed_all") or []
    if cfg.dataset_role == "TEST":
        adv = s["advanced_set"]
        q1 = ("YES: " + ", ".join(adv)) if isinstance(adv, list) else "NO: no bot passed every V4 gate on BOTH DEVELOPMENT and TEST"
    else:
        q1 = (f"{len(passed)} bot(s) passed every DEVELOPMENT gate -> TEST decides: " + ", ".join(passed)) if passed else \
            "NO: no V4 bot passed every DEVELOPMENT gate"
    if not s["pairs"]:
        q2 = "NOT TESTED: no Jev pairs ran"
    else:
        sa = s.get("selection_alpha") or {}
        q2 = (f"Jev beat its control in {b['jev_beats_control']} of {b['pairs']} pairs, the matched random action's "
              f"90th percentile in {b['jev_beats_random_p90']}; selection alpha total {sa.get('total_usdt')} USDT "
              f"({sa.get('positive_pairs')}/{sa.get('pairs')} pairs positive)")
    return {"intraday_edge": q1, "jev_selection": q2, "timeframes": tf_line,
            "advanced_set": ", ".join(s["advanced_set"]) if isinstance(s["advanced_set"], list) else str(s["advanced_set"])}
