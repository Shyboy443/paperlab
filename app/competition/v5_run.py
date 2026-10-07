"""V5 HOURLY / DAILY FUTURES ARENA orchestration (docs/V5_PROTOCOL.md).

    STAGE 1   RAW DISCOVERY     every family x coin x class: CONTROL at 20 USDT (the official book) and a CAPACITY twin
                                at 100 USDT (Amendment 1), baseline exit (structural stop + 24 h / 72 h time stop, no
                                target), no Jev, no gate
    ANALYZE 1                   family x class RAW-EDGE gate on the 100 USDT twins; MFE / MAE at 1-72 h; on DEVELOPMENT
                                the exit grid for the raw-edge survivors and the selected exit (Amendment 2)
    STAGE 2   AFTER-COST        raw-edge survivors re-run with the selected exit at 20 / 50 / 100 USDT
    ANALYZE 2                   the ECONOMIC gate (20 USDT books, net R incl. funding) -> the Jev pairs
    STAGE 3   JEV               economic survivors only: +JEV5, ALWAYS-TAKE, RANDOM x20 (matched action) at 20 USDT
    ANALYZE                     per-bot gates, leaderboards, viability, costs, pairs, selection alpha, ATTACK, capacity

The TEST (pseudo-holdout) run repeats Stage 1 for every family, then Stages 2-3 for the DEVELOPMENT survivors with their
frozen DEVELOPMENT exits -- nothing is selected on TEST. Every phase is resumable (a stored bot is never replayed) and
parallel (spawned worker processes, one arena each; never a forked SQLite connection). Only STAGE 3 makes requests.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import multiprocessing
import os
import time
import uuid
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Callable, Mapping, Sequence

from app.competition import v5_analyzer as an
from app.competition.v5_arena import V5Arena
from app.competition.v5_config import EXIT_BASELINE, V5Config, V5Identity

log = logging.getLogger("paperlab.competition.v5.run")

_W: dict[str, Any] = {}


# ---- workers -----------------------------------------------------------------------------------------------

def _init(settings: Any, cfg: V5Config, rules: dict[str, Any], db: str, data_dir: str) -> None:
    from app.core.storage import Storage
    _W["arena"] = V5Arena(settings, cfg, rules, data_dir)
    _W["storage"] = Storage(db) if db else None


def control_version(ident: V5Identity, params: Mapping[str, Any]) -> str:
    base = dataclasses.replace(ident, role="CONTROL", seed=0, balance=20.0, jev_policy="")
    blob = json.dumps({"key": base.key, "params": dict(params)}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _gate(job: Mapping[str, Any]) -> Any:
    from app.ai.jev.v5 import CachedDeciderV5, ClientDeciderV5, JevGateV5, PolicyGateV5
    g = job["gate"]
    ident = V5Identity(**job["ident"])
    kind = g["kind"]
    if kind == "none":
        return None
    if kind == "take":
        return PolicyGateV5("TAKE", ident.key)
    if kind == "random":
        return PolicyGateV5("RANDOM", ident.key, seed=int(g["seed"]), distribution=g["distribution"])
    from app.ai.jev.client import JevClient
    from app.ai.jev.models import JevConfig
    storage = _W["storage"]
    inner = None
    if not g.get("replay_only"):
        jc = dataclasses.replace(JevConfig.from_env(), max_retries=1)
        inner = ClientDeciderV5(JevClient(jc, os.environ.get("OPENROUTER_API_KEY")))
    decider = CachedDeciderV5(storage, inner, job["run_id"], ident.key, g["model"], replay_only=bool(g.get("replay_only")),
                              max_calls=g.get("max_calls"), max_cost_usd=g.get("max_cost_usd"))
    arena: V5Arena = _W["arena"]
    cls = arena.bound(ident)
    strat = cls()
    params = {f.name: getattr(strat.params, f.name) for f in dataclasses.fields(strat.params)}
    fam = g.get("family") or {}
    bot = {"key": ident.key, "strategy_id": ident.strategy_id, "strategy_name": getattr(cls, "name", ident.strategy_id),
           "params_version": "v5", "symbol": ident.symbol, "timeframe": cls.signal_tf, "signal_tf": cls.signal_tf,
           "horizon": ident.horizon, "control_version": control_version(ident, params),
           "thesis": getattr(cls, "thesis", ""), "fails_when": getattr(cls, "fails_when", ""),
           "expected_hold": getattr(cls, "expected_hold", ""), "target_r": cls.target_r,
           "family_raw_edge_r": fam.get("raw_edge_r"), "family_raw_edge_trades": fam.get("raw_edge_trades"),
           "family_net_edge_r": fam.get("net_edge_r")}
    return JevGateV5(decider, storage, job["run_id"], bot, ident.pair_id, g["model"])


def _job(job: Mapping[str, Any]) -> dict[str, Any]:
    t0 = time.time()
    try:
        rec = _W["arena"].run(V5Identity(**job["ident"]), _gate(job))
        return {"ok": True, "record": rec}
    except Exception as exc:
        log.exception("%s failed", job["ident"])
        return {"ok": False, "ident": job["ident"], "error": f"{type(exc).__name__}: {exc}"[:300],
                "elapsed_s": round(time.time() - t0, 2)}


def run_jobs(storage: Any, run_id: str, jobs: list[dict[str, Any]], settings: Any, cfg: V5Config,
             rules: dict[str, Any], db: str, data_dir: str, workers: int,
             on_done: Callable[[dict[str, Any], int, int], None] | None = None) -> list[dict[str, Any]]:
    done = storage.v5_bot_keys(run_id)
    todo, seen = [], set(done)
    for j in jobs:
        k = V5Identity(**j["ident"]).key
        if k not in seen:
            seen.add(k)
            todo.append(j)
    out: list[dict[str, Any]] = []
    if not todo:
        return out
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=max(1, workers), mp_context=ctx, initializer=_init,
                             initargs=(settings, cfg, rules, db, data_dir)) as pool:
        futures = [pool.submit(_job, j) for j in todo]
        for i, f in enumerate(as_completed(futures), 1):
            r = f.result()
            if r["ok"]:
                storage.v5_bot_save(run_id, r["record"], ts=int(time.time() * 1000))
            out.append(r)
            if on_done:
                on_done(r, i, len(todo))
    return out


# ---- jobs --------------------------------------------------------------------------------------------------

def new_run(storage: Any, cfg: V5Config, label: str, extra: Mapping[str, Any] | None = None) -> str:
    run_id = "v5-" + uuid.uuid4().hex[:10]
    storage.v5_run_start({"run_id": run_id, "created_ts": int(time.time() * 1000), "status": "running",
                          "stage": "STAGE1", "label": label, "dataset_role": cfg.dataset_role,
                          "config_fingerprint": cfg.fingerprint(), "config": {**cfg.to_dict(), **dict(extra or {})}})
    return run_id


def _ident(sid: str, coin: str, horizon: str, role: str, **k: Any) -> dict[str, Any]:
    jev = "JEV_POLICY_V5" if role in ("JEV", "TAKE", "RANDOM") else ""
    return dataclasses.asdict(V5Identity(sid, coin, horizon, role, jev_policy=jev, **k))


def _exit_kw(exit_plan: Mapping[str, Any] | None) -> dict[str, float]:
    """Identity fields for an exit: the baseline stays (0, 0) so a stage 2 book with an unchanged exit IS the stage 1
    book (same key, never replayed)."""
    if not exit_plan or not exit_plan.get("changed"):
        return {"time_stop_h": 0.0, "target_r": 0.0}
    return {"time_stop_h": float(exit_plan["time_stop_h"]), "target_r": float(exit_plan.get("target_r") or 0.0)}


def stage1_jobs(cfg: V5Config, run_id: str) -> list[dict[str, Any]]:
    from app.strategies.registry import load_v5
    jobs = []
    for sid in sorted(load_v5()):
        for h in cfg.horizons:
            for coin in cfg.coins:
                jobs.append({"ident": _ident(sid, coin, h, "CONTROL"), "run_id": run_id, "gate": {"kind": "none"}})
                jobs.append({"ident": _ident(sid, coin, h, "CAPACITY", balance=float(cfg.raw_edge_balance)),
                             "run_id": run_id, "gate": {"kind": "none"}})
    return jobs


def stage2_jobs(cfg: V5Config, run_id: str, field: Mapping[str, Any]) -> list[dict[str, Any]]:
    jobs = []
    for s in field.get("survivors") or []:
        kw = _exit_kw(s.get("exit"))
        for coin in cfg.coins:
            jobs.append({"ident": _ident(s["strategy_id"], coin, s["horizon"], "CONTROL", **kw), "run_id": run_id,
                         "gate": {"kind": "none"}})
            for b in cfg.capacity_balances:
                jobs.append({"ident": _ident(s["strategy_id"], coin, s["horizon"], "CAPACITY", balance=float(b), **kw),
                             "run_id": run_id, "gate": {"kind": "none"}})
    return jobs


def twin_jobs(field: Mapping[str, Any], run_id: str, kind: str, model: str = "", replay_only: bool = False,
              max_calls: int | None = None, max_cost_usd: float | None = None) -> list[dict[str, Any]]:
    role = {"jev": "JEV", "take": "TAKE"}[kind]
    return [{"ident": _ident(p["strategy_id"], p["coin"], p["horizon"], role, **_exit_kw(p.get("exit"))), "run_id": run_id,
             "gate": {"kind": kind, "model": model, "replay_only": replay_only, "max_calls": max_calls,
                      "max_cost_usd": max_cost_usd, "family": p.get("family_edge")}} for p in field.get("pairs") or []]


def random_jobs(storage: Any, run_id: str, field: Mapping[str, Any], seeds: int) -> list[dict[str, Any]]:
    from app.ai.jev.v5 import action_distribution_v5
    jev = {b["key"]: b for b in storage.v5_bots(run_id, role="JEV", heavy=True)}
    jobs = []
    for p in field.get("pairs") or []:
        kw = _exit_kw(p.get("exit"))
        rec = jev.get(V5Identity(p["strategy_id"], p["coin"], p["horizon"], "JEV", jev_policy="JEV_POLICY_V5", **kw).key)
        if rec is None:
            continue
        dist = action_distribution_v5(rec.get("decisions") or [])
        for s in range(1, seeds + 1):
            jobs.append({"ident": _ident(p["strategy_id"], p["coin"], p["horizon"], "RANDOM", seed=s, **kw),
                         "run_id": run_id, "gate": {"kind": "random", "seed": s, "distribution": dist}})
    return jobs


# ---- analysis helpers ------------------------------------------------------------------------------------------

def _r(x: Any, nd: int = 4) -> Any:
    return an._r(x, nd)


def _count(items: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        out[str(x)] = out.get(str(x), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def is_baseline(rec: Mapping[str, Any]) -> bool:
    idn = rec["identity"]
    return not float(idn.get("time_stop_h") or 0) and not float(idn.get("target_r") or 0)


def fam_key(rec: Mapping[str, Any]) -> str:
    return f"{rec['identity']['strategy_id']}|{rec['horizon']}"


def _tape(settings: Any, coin: str, months: Sequence[str]) -> Any:
    from app.backtest import archive
    from app.competition.v31_diagnostics import Tape
    try:
        return Tape(archive.load_klines(settings, f"{coin}USDT", list(months)))
    except Exception as exc:
        log.warning("no tape for %s: %s", coin, exc)
        return None


def _funding(settings: Any, coin: str, months: Sequence[str]) -> list[tuple[int, float]]:
    from app.backtest import archive
    try:
        return archive.load_funding(settings, f"{coin}USDT", list(months))
    except Exception:
        return []


def _allowed_plans(cfg: V5Config, horizon: str) -> tuple[list[int], list[float | None]]:
    return list(dict(cfg.exit_time_stops_by_class)[horizon]), list(cfg.exit_targets_r)


# ---- ANALYZE 1: the raw-edge gate, the excursions and (DEVELOPMENT) the exit ------------------------------------

def analyze_stage1(storage: Any, run_id: str, cfg: V5Config, settings: Any,
                   frozen: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """`frozen` (TEST only): the DEVELOPMENT survivors and their exits from the pre-registration; nothing is selected."""
    recs = [r for r in storage.v5_bots(run_id, heavy=True) if is_baseline(r) and r["role"] in ("CONTROL", "CAPACITY")]
    c20: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cap: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in recs:
        if r["role"] == "CONTROL":
            c20[fam_key(r)].append(r)
        elif abs(float(r.get("balance") or 0) - cfg.raw_edge_balance) < 1e-9:
            cap[fam_key(r)].append(r)
    keys = sorted(set(c20) | set(cap))
    families: dict[str, dict[str, Any]] = {}
    for k in keys:
        sid, horizon = k.split("|")
        raw = an.edge_test(cap.get(k, []), cfg, "gross")
        econ20 = an.edge_test(c20.get(k, []), cfg, "net")
        econ100 = an.edge_test(cap.get(k, []), cfg, "net")
        fam_name = next((r.get("family") for r in cap.get(k, []) + c20.get(k, []) if r.get("family")), "")
        families[k] = {"strategy_id": sid, "family": fam_name, "horizon": horizon, "raw_edge": raw,
                       "economic_20_baseline": econ20, "economic_100_baseline": econ100,
                       "verdict_baseline": an.family_verdict(raw, econ20),
                       "costs_20": an.pool_costs(c20.get(k, []), cfg), "costs_100": an.pool_costs(cap.get(k, []), cfg),
                       "by_regime": an.split(cap.get(k, []), "regime"), "by_vol_band": an.split(cap.get(k, []), "vol_band"),
                       "by_side": an.split(cap.get(k, []), "side"), "by_setup": an.split(cap.get(k, []), "setup"),
                       "bots_20": len(c20.get(k, [])), "bots_100": len(cap.get(k, []))}
    # the per-coin tape pass: MFE / MAE for every family x class; the exit grid for the raw survivors (DEVELOPMENT)
    select = frozen is None and cfg.dataset_role == "DEVELOPMENT"
    survivors = [k for k in keys if families[k]["raw_edge"]["passed"]] if select else []
    exc: dict[str, dict[int, dict[str, list[float]]]] = {}
    grid: dict[str, dict[str, list[float]]] = {k: defaultdict(list) for k in survivors}
    for coin in cfg.coins:
        mine = [r for r in recs if r["identity"]["coin"] == coin and r["role"] == "CAPACITY"
                and abs(float(r.get("balance") or 0) - cfg.raw_edge_balance) < 1e-9]
        if not mine:
            continue
        tape = _tape(settings, coin, cfg.months)
        fund = _funding(settings, coin, cfg.months) if survivors else []
        for r in mine:
            k = fam_key(r)
            exc[k] = an.excursion_acc(r.get("trades") or [], tape, cfg.mfe_horizons_h, exc.get(k))
            if k in grid:
                hs, ts = _allowed_plans(cfg, r["horizon"])
                for plan, vals in an.exit_grid(r.get("trades") or [], tape, fund, hs, ts).items():
                    grid[k][plan].extend(vals)
        del tape
    for k in keys:
        families[k]["horizons"] = an.excursion_summary(exc.get(k) or {})
        if k in grid:
            table = an.grid_table(grid[k])
            families[k]["exit_grid"] = table
            families[k]["exit"] = an.select_exit(table, EXIT_BASELINE[families[k]["horizon"]], cfg.exit_min_improvement_r)
    if frozen is not None:
        surv = list(frozen.get("survivors") or [])
    else:
        surv = [{"strategy_id": families[k]["strategy_id"], "horizon": families[k]["horizon"],
                 "family": families[k]["family"], "exit": families[k].get("exit"),
                 "raw_edge_r": families[k]["raw_edge"]["mean_r"], "raw_edge_trades": families[k]["raw_edge"]["trades"]}
                for k in survivors]
    field = {"stage": "ANALYZE1", "families": list(families.values()), "survivors": surv,
             "raw_edge_passed": [k for k in keys if families[k]["raw_edge"]["passed"]],
             "exit_selection": "DEVELOPMENT grid (Amendment 2)" if select else "frozen DEVELOPMENT exits (pre-registered)"}
    storage.v5_run_update(run_id, field_json=field, stage="ANALYZE1")
    return field


# ---- ANALYZE 2: the economic gate on the 20 USDT books with the selected exit -> the Jev pairs ---------------------

def _books_for(recs: Sequence[Mapping[str, Any]], s: Mapping[str, Any], role: str, balance: float | None = None) -> list[dict[str, Any]]:
    kw = _exit_kw(s.get("exit"))
    out = []
    for r in recs:
        idn = r["identity"]
        if (idn["strategy_id"], r["horizon"], r["role"]) != (s["strategy_id"], s["horizon"], role):
            continue
        if float(idn.get("time_stop_h") or 0) != kw["time_stop_h"] or float(idn.get("target_r") or 0) != kw["target_r"]:
            continue
        if balance is not None and abs(float(r.get("balance") or 0) - balance) > 1e-9:
            continue
        out.append(r)
    return out


def analyze_stage2(storage: Any, run_id: str, cfg: V5Config,
                   dev_edges: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """The economic verdict per survivor and the Jev pairs. DEVELOPMENT: the pairs are the coins of every family x
    class that PASSED here. TEST (`dev_edges` = the DEVELOPMENT economic survivors and their DEVELOPMENT edges, from the
    pre-registration): the pairs are the TEST coins of exactly those families, whatever TEST says -- never selected on
    a TEST outcome. Either way a pair needs >= the minimum sample in its 20 USDT control (activity only): a +JEV bot
    trades a subset of its control's setups, so a control below it cannot yield a qualifying Jev bot."""
    field = dict((storage.v5_run(run_id) or {}).get("field") or {})
    recs = storage.v5_bots(run_id, heavy=True)
    fams = {f"{f['strategy_id']}|{f['horizon']}": f for f in field.get("families") or []}
    econ, pairs = [], []
    for s in field.get("survivors") or []:
        k = f"{s['strategy_id']}|{s['horizon']}"
        books20 = _books_for(recs, s, "CONTROL")
        books100 = _books_for(recs, s, "CAPACITY", cfg.raw_edge_balance)
        e20 = an.edge_test(books20, cfg, "net")
        raw = (fams.get(k) or {}).get("raw_edge") or {"passed": False}
        verdict = an.family_verdict(raw, e20)
        econ.append({"strategy_id": s["strategy_id"], "horizon": s["horizon"], "family": s.get("family"),
                     "exit": s.get("exit"), "economic_20": e20, "economic_100": an.edge_test(books100, cfg, "net"),
                     "costs_20": an.pool_costs(books20, cfg), "costs_100": an.pool_costs(books100, cfg), "verdict": verdict})
        if k in fams:
            fams[k]["verdict"] = verdict
            fams[k]["economic_20"] = e20
        if dev_edges is None:
            take = verdict == "PASSED"
            fam_edge = {"raw_edge_r": raw.get("mean_r"), "raw_edge_trades": raw.get("trades"), "net_edge_r": e20.get("mean_r")}
        else:
            take = k in dev_edges
            fam_edge = dict(dev_edges.get(k) or {})
        if not take:
            continue
        for b in books20:
            if len(b.get("trades") or []) >= cfg.gates.min_trades:
                pairs.append({"strategy_id": s["strategy_id"], "coin": b["identity"]["coin"], "horizon": s["horizon"],
                              "exit": s.get("exit"), "family_edge": fam_edge, "control_key": b["key"]})
    for f in fams.values():
        if "verdict" not in f:
            f["verdict"] = "NO_RAW_EDGE" if not f["raw_edge"]["passed"] else "NOT_EVALUATED"
    field.update({"stage": "ANALYZE2", "families": list(fams.values()), "economic": econ, "pairs": pairs,
                  "economic_passed": [f"{e['strategy_id']}|{e['horizon']}" for e in econ if e["verdict"] == "PASSED"]})
    storage.v5_run_update(run_id, field_json=field, stage="ANALYZE2")
    return field


# ---- the final analysis -------------------------------------------------------------------------------------------

def official_controls(recs: Sequence[Mapping[str, Any]], field: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One 20 USDT CONTROL per family x coin x class: the stage 2 book (selected exit) for a raw-edge survivor, the
    stage 1 book otherwise."""
    exits = {f"{s['strategy_id']}|{s['horizon']}": _exit_kw(s.get("exit")) for s in field.get("survivors") or []}
    out = []
    for r in recs:
        if r["role"] != "CONTROL":
            continue
        kw = exits.get(fam_key(r), {"time_stop_h": 0.0, "target_r": 0.0})
        idn = r["identity"]
        if float(idn.get("time_stop_h") or 0) == kw["time_stop_h"] and float(idn.get("target_r") or 0) == kw["target_r"]:
            out.append(r)
    return out


def analyze_run(storage: Any, run_id: str, cfg: V5Config, settings: Any,
                dev_summary: Mapping[str, Any] | None = None) -> dict[str, Any]:
    run = storage.v5_run(run_id) or {}
    field = run.get("field") or {}
    recs = storage.v5_bots(run_id, heavy=True)
    fams = {f"{f['strategy_id']}|{f['horizon']}": f for f in field.get("families") or []}
    by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in recs:
        by_role[r["role"]].append(r)
    controls = official_controls(recs, field)
    ctrl_by_pair = {b["pair_id"]: b for b in controls}
    jevs, takes = by_role.get("JEV", []), {b["pair_id"]: b for b in by_role.get("TAKE", [])}
    randoms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for b in by_role.get("RANDOM", []):
        randoms[b["pair_id"]].append(b)
    caps: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for b in by_role.get("CAPACITY", []):
        caps[b["pair_id"]].append(b)

    analyses: dict[str, dict[str, Any]] = {}
    for coin in cfg.coins:
        mine = [r for r in controls + jevs + list(takes.values()) if r["identity"]["coin"] == coin]
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
            analyses[rec["key"]] = an.analyze_bot(rec, cfg, tape, fams.get(fam_key(rec)), base)
        del tape
    for r in by_role.get("CAPACITY", []) + by_role.get("RANDOM", []):
        if r["key"] not in analyses:
            analyses[r["key"]] = an.analyze_bot(r, cfg, None, fams.get(fam_key(r)))
    for key, a in analyses.items():
        storage.v5_bot_analysis(run_id, key, a, a["state"], a["failure_mode"], (a["money"] or {}).get("net_per_day"))

    ctrl_rows = an.rank([an.leaderboard_row(r, analyses[r["key"]]) for r in controls])
    pairs = []
    for rec in jevs:
        pid = rec["pair_id"]
        a = analyses[rec["key"]]
        ctl, take = ctrl_by_pair.get(pid), takes.get(pid)
        idn = rec["identity"]

        def side(m: Mapping[str, Any] | None) -> dict[str, Any] | None:
            return an.leaderboard_row(m, analyses[m["key"]]) if (m is not None and m["key"] in analyses) else None
        jnet = (rec.get("metrics") or {}).get("net_profit") or 0.0
        cnet = ((ctl or {}).get("metrics") or {}).get("net_profit") or 0.0
        pairs.append({"pair_id": pid, "strategy_id": idn["strategy_id"], "coin": idn["coin"], "horizon": rec["horizon"],
                      "jev": side(rec), "control": side(ctl), "always_take": side(take), "always_skip": {"net": 0.0, "trades": 0},
                      "random": (a.get("baselines") or {}).get("selection") or {}, "delta_vs_control": _r(jnet - cnet),
                      "jev_actions": (a.get("jev") or {}).get("final"), "jev_auc": (a.get("jev") or {}).get("auc_support"),
                      "attack": a.get("attack"), "state": a["state"], "failure_mode": a["failure_mode"]})
    field_rows = an.rank([an.leaderboard_row(r, analyses[r["key"]]) for r in jevs + list(takes.values())]
                         + [an.leaderboard_row(ctrl_by_pair[p["pair_id"]], analyses[ctrl_by_pair[p["pair_id"]]["key"]])
                            for p in pairs if p["pair_id"] in ctrl_by_pair])
    capacity = []
    for c in controls:
        big = [b for b in caps.get(c["pair_id"], [])]
        capacity.append({"pair_id": c["pair_id"], "20": an.leaderboard_row(c, analyses[c["key"]]),
                         **{f"{float(b.get('balance') or 0):g}": an.leaderboard_row(b, analyses[b["key"]]) for b in big},
                         "verdict": an.capacity_verdict(c, analyses[c["key"]], big)})
    alpha = [p["random"].get("alpha_usdt") for p in pairs if (p.get("random") or {}).get("alpha_usdt") is not None]
    jev_ds = [d for r in jevs for d in (r.get("decisions") or [])]
    jev_trades = [t for r in jevs for t in (r.get("trades") or [])]
    passed_c = sorted(c["key"] for c in controls if analyses[c["key"]]["state"] == "ADVANCE")
    passed_j = sorted(j["key"] for j in jevs if analyses[j["key"]]["state"] == "ADVANCE")
    summary = {
        "run_id": run_id, "protocol": cfg.protocol, "dataset_role": cfg.dataset_role,
        "window": {"from": cfg.trade_from, "to": cfg.trade_to, "days": cfg.days}, "venue": cfg.venue,
        "price_tape": cfg.price_tape, "data": "BYBIT-NATIVE (klines, funding, open interest, premium / basis, account "
                                             "ratio); no Binance surrogate", "coins": list(cfg.coins),
        "counts": {"controls": len(controls), "stage1_books": sum(1 for r in recs if is_baseline(r)),
                   "capacity_books": len(by_role.get("CAPACITY", [])), "jev_bots": len(jevs), "take_twins": len(takes),
                   "random_twins": sum(len(v) for v in randoms.values()), "pairs": len(pairs), "all_books": len(recs)},
        "families": list(fams.values()), "survivors": field.get("survivors") or [], "economic": field.get("economic") or [],
        "viability": an.viability(controls, analyses),
        "costs": {"controls_20": an.totals(controls), "all_20": an.totals([r for r in recs if r["role"] != "CAPACITY"]),
                  "capacity": an.totals(by_role.get("CAPACITY", [])),
                  "note": "every V5 order is a MARKET (taker) order: maker fees are 0 by construction (Amendment 2b)"},
        "leaderboard_controls": ctrl_rows, "leaderboard_field": field_rows, "pairs": pairs,
        "baselines": {"pairs": len(pairs), "jev_total": _r(sum((p["jev"] or {}).get("net") or 0.0 for p in pairs)),
                      "control_total": _r(sum((p["control"] or {}).get("net") or 0.0 for p in pairs)),
                      "always_take_total": _r(sum((p["always_take"] or {}).get("net") or 0.0 for p in pairs)),
                      "always_skip_total": 0.0,
                      "random_median_total": _r(sum((p["random"] or {}).get("random_median") or 0.0 for p in pairs)),
                      "jev_beats_control": sum(1 for p in pairs if (p["delta_vs_control"] or 0) > 1e-9),
                      "jev_beats_random_p90": sum(1 for p in pairs if (p["random"] or {}).get("random_p90") is not None
                                                  and ((p["jev"] or {}).get("net") or 0) > p["random"]["random_p90"])},
        "selection_alpha": {"pairs": len(alpha), "total_usdt": _r(sum(alpha)), "mean_usdt": _r(an._mean(alpha)),
                            "positive_pairs": sum(1 for x in alpha if x > 0)},
        "jev_pooled": an.jev_block({"role": "JEV", "decisions": jev_ds}) if jev_ds else {"decisions": 0},
        "attack": an.attack_test(jev_trades, "JEV") if jev_trades else {"attack_trades": 0},
        "capacity": {"rows": capacity, "verdicts": _count(r["verdict"] for r in capacity),
                     "note": "capacity diagnostic only: a 50 / 100 USDT result never qualifies the 20 USDT competition"},
        "labels": _count(analyses[c["key"]]["label"] for c in controls),
        "states": {"controls": _count(analyses[c["key"]]["state"] for c in controls),
                   "jev_bots": _count(analyses[j["key"]]["state"] for j in jevs)},
        "failure_modes": {"controls": _count(analyses[c["key"]]["failure_mode"] for c in controls),
                          "jev_bots": _count(analyses[j["key"]]["failure_mode"] for j in jevs)},
        "profitable_controls": sum(1 for c in controls if ((c.get("metrics") or {}).get("net_profit") or 0) > 0),
        "passed": {"controls": passed_c, "jev_bots": passed_j},
        "analyzed_ts": int(time.time() * 1000),
    }
    passed = set(passed_c) | set(passed_j)
    if cfg.dataset_role == "TEST":
        dev_passed = set((dev_summary or {}).get("passed_all") or [])
        summary["dev_passed"] = sorted(dev_passed)
        summary["advanced_set"] = sorted(passed & dev_passed) or "NONE"
    else:
        summary["advanced_set"] = "PENDING PSEUDO-HOLDOUT" if passed else "NONE"
    summary["passed_all"] = sorted(passed)
    summary["answers"] = answers(summary, cfg)
    storage.v5_run_update(run_id, summary_json=summary, stage="ANALYZED", status="complete",
                          finished_ts=int(time.time() * 1000))
    return summary


def answers(s: Mapping[str, Any], cfg: V5Config) -> dict[str, str]:
    fams = s.get("families") or []
    raw = [f"{f['strategy_id']} {f['horizon']}" for f in fams if (f.get("raw_edge") or {}).get("passed")]
    econ = [f"{e['strategy_id']} {e['horizon']}" for e in s.get("economic") or [] if e.get("verdict") == "PASSED"]
    v = {r["horizon"]: r for r in s.get("viability") or []}
    via = "; ".join(f"{h}: {r['bots']} bots, {r['trades']} trades, gross {r['gross_exp_r']} R -> net {r['net_exp_r']} R "
                    f"({r['net']} USDT), {r['qualified']} qualified" for h, r in v.items())
    passed = s.get("passed_all") or []
    if cfg.dataset_role == "TEST":
        adv = s["advanced_set"]
        verdict = ("YES: " + ", ".join(adv)) if isinstance(adv, list) else \
            "NO: no bot passed every V5 gate on both DEVELOPMENT and the pseudo-holdout"
    else:
        verdict = (f"{len(passed)} bot(s) passed every DEVELOPMENT gate -> the pseudo-holdout decides: "
                   + ", ".join(passed)) if passed else "NO: no V5 bot passed every DEVELOPMENT gate"
    sa = s.get("selection_alpha") or {}
    b = s.get("baselines") or {}
    jev = ("NOT RUN: no family survived Stages 1-2, so no Jev call was spent (protocol §7 Stage 3)" if not s.get("pairs")
           else f"Jev beat its control in {b.get('jev_beats_control')} of {b.get('pairs')} pairs and the matched random "
                f"90th percentile in {b.get('jev_beats_random_p90')}; selection alpha {sa.get('total_usdt')} USDT")
    return {"raw_edge": ("families with a raw edge: " + ", ".join(raw)) if raw else "NO family x class has a raw edge",
            "economics": ("after costs: " + ", ".join(econ)) if econ else "NO family x class survives costs",
            "viability": via, "verdict": verdict, "jev": jev,
            "advanced_set": ", ".join(s["advanced_set"]) if isinstance(s["advanced_set"], list) else str(s["advanced_set"])}
