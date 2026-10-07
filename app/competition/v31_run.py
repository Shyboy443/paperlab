"""V3.1 AGGRESSIVE EDGE orchestration (docs/V31_PROTOCOL.md).

DEVELOPMENT:  OBSERVE (RAW evidence) -> CONTROL (edge gate, causal) -> FIELD (activity only)
              -> JEV (Jev V3) -> TAKE -> RANDOM (matched action) -> CAPACITY (50 / 100 USDT) -> ANALYZE
TEST:         the pre-registered field, the frozen DEVELOPMENT evidence and policies, TEST months only:
              CONTROL -> JEV -> TAKE -> RANDOM -> CAPACITY -> ANALYZE (never an OBSERVE: no TEST outcome
              ever becomes evidence)

Every phase is resumable (a stored bot is never replayed again) and parallel (spawned worker processes,
each with its own arena; never a forked SQLite connection). Only the JEV phase makes network requests.
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

from app.competition import v31_analyzer as an
from app.competition.v31_arena import FAMILY, FAMILY_LABEL, V31Arena, select_field
from app.competition.v31_config import PARTICIPATION_PER_DAY, V31Config, V31Identity
from app.competition.v31_edge import calibration, load_evidence

log = logging.getLogger("paperlab.competition.v31.run")

_W: dict[str, Any] = {}


# ---- workers -----------------------------------------------------------------------------------------------

def _init(settings: Any, cfg: V31Config, rules: dict[str, Any], db: str, evidence: list[dict[str, Any]]) -> None:
    from app.core.storage import Storage
    _W["arena"] = V31Arena(settings, cfg, rules, evidence)
    _W["storage"] = Storage(db) if db else None


def _gate(job: Mapping[str, Any]) -> Any:
    from app.ai.jev.v3 import CachedDeciderV3, ClientDeciderV3, JevGateV3, ObserverGate, PolicyGateV3
    g = job["gate"]
    ident = V31Identity(**job["ident"])
    kind = g["kind"]
    if kind == "none":
        return None
    if kind == "observer":
        return ObserverGate()
    if kind == "take":
        return PolicyGateV3("TAKE", ident.key)
    if kind == "random":
        return PolicyGateV3("RANDOM", ident.key, seed=int(g["seed"]), distribution=g["distribution"],
                            strong_share=float(g.get("strong") or 0.0))
    from app.ai.jev.client import JevClient
    from app.ai.jev.models import JevConfig
    storage = _W["storage"]
    inner = None
    if not g.get("replay_only"):
        jc = dataclasses.replace(JevConfig.from_env(), max_retries=1)
        inner = ClientDeciderV3(JevClient(jc, os.environ.get("OPENROUTER_API_KEY")))
    decider = CachedDeciderV3(storage, inner, job["run_id"], ident.key, g["model"], replay_only=bool(g.get("replay_only")),
                              max_calls=g.get("max_calls"), max_cost_usd=g.get("max_cost_usd"))
    arena: V31Arena = _W["arena"]
    cls = arena.bound(ident)
    control = V31Identity(ident.strategy_id, ident.coin, ident.timeframe, "CONTROL")
    bot = {"key": ident.key, "strategy_id": ident.strategy_id, "strategy_name": getattr(cls, "name", ident.strategy_id),
           "params_version": "v3.1", "symbol": ident.symbol, "timeframe": ident.timeframe,
           "control_version": control.fingerprint(cls().params), "hypothesis": getattr(cls, "hypothesis", ""),
           "thesis": getattr(cls, "thesis", ""), "expected_hold": getattr(cls, "expected_hold", "")}
    return JevGateV3(decider, storage, job["run_id"], bot, ident.pair_id, g["model"])


def _job(job: Mapping[str, Any]) -> dict[str, Any]:
    t0 = time.time()
    try:
        rec = _W["arena"].run(V31Identity(**job["ident"]), _gate(job))
        return {"ok": True, "record": rec}
    except Exception as exc:
        log.exception("%s failed", job["ident"])
        return {"ok": False, "ident": job["ident"], "error": f"{type(exc).__name__}: {exc}"[:300],
                "elapsed_s": round(time.time() - t0, 2)}


def run_jobs(storage: Any, run_id: str, jobs: list[dict[str, Any]], settings: Any, cfg: V31Config,
             rules: dict[str, Any], db: str, workers: int, evidence: list[dict[str, Any]],
             on_done: Callable[[dict[str, Any], int, int], None] | None = None) -> list[dict[str, Any]]:
    done = storage.v31_bot_keys(run_id)
    todo = [j for j in jobs if V31Identity(**j["ident"]).key not in done]
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
                storage.v31_bot_save(run_id, r["record"], ts=int(time.time() * 1000))
            out.append(r)
            if on_done:
                on_done(r, i, len(todo))
    return out


# ---- phases ------------------------------------------------------------------------------------------------

def new_run(storage: Any, cfg: V31Config, label: str, extra: Mapping[str, Any] | None = None) -> str:
    run_id = "v31-" + uuid.uuid4().hex[:10]
    storage.v31_run_start({"run_id": run_id, "created_ts": int(time.time() * 1000), "status": "running",
                           "stage": "OBSERVE" if cfg.dataset_role == "DEVELOPMENT" else "CONTROL", "label": label,
                           "dataset_role": cfg.dataset_role, "config_fingerprint": cfg.fingerprint(),
                           "config": {**cfg.to_dict(), **dict(extra or {})}})
    return run_id


def _ident(sid: str, coin: str, tf: str, role: str, **k: Any) -> dict[str, Any]:
    jev = "JEV_POLICY_V3" if role in ("JEV", "TAKE", "RANDOM") else ""
    return dataclasses.asdict(V31Identity(sid, coin, tf, role, jev_policy=jev, **k))


def grid_jobs(cfg: V31Config, run_id: str, role: str, kind: str) -> list[dict[str, Any]]:
    from app.strategies.registry import load_v31
    return [{"ident": _ident(sid, coin, tf, role), "run_id": run_id, "gate": {"kind": kind}}
            for sid in sorted(load_v31()) for coin in cfg.coins for tf in cfg.timeframes]


def twin_jobs(field: Mapping[str, Any], run_id: str, kind: str, model: str = "", replay_only: bool = False,
              max_calls: int | None = None, max_cost_usd: float | None = None) -> list[dict[str, Any]]:
    role = {"jev": "JEV", "take": "TAKE", "control": "CONTROL"}[kind]
    return [{"ident": _ident(p["strategy_id"], p["coin"], p["timeframe"], role), "run_id": run_id,
             "gate": {"kind": "none" if kind == "control" else kind, "model": model, "replay_only": replay_only,
                      "max_calls": max_calls, "max_cost_usd": max_cost_usd}} for p in field["pairs"]]


def random_jobs(storage: Any, run_id: str, field: Mapping[str, Any], seeds: int) -> list[dict[str, Any]]:
    from app.ai.jev.v3 import action_distribution_v3
    jev = {b["key"]: b for b in storage.v31_bots(run_id, role="JEV", heavy=True)}
    jobs = []
    for p in field["pairs"]:
        jkey = V31Identity(p["strategy_id"], p["coin"], p["timeframe"], "JEV", jev_policy="JEV_POLICY_V3").key
        rec = jev.get(jkey)
        if rec is None:
            continue
        dist, strong = action_distribution_v3(rec.get("decisions") or [])
        for s in range(1, seeds + 1):
            jobs.append({"ident": _ident(p["strategy_id"], p["coin"], p["timeframe"], "RANDOM", seed=s), "run_id": run_id,
                         "gate": {"kind": "random", "seed": s, "distribution": dist, "strong": strong}})
    return jobs


def capacity_jobs(field: Mapping[str, Any], run_id: str, balances: Sequence[float]) -> list[dict[str, Any]]:
    return [{"ident": _ident(p["strategy_id"], p["coin"], p["timeframe"], "CAPACITY", balance=float(b)), "run_id": run_id,
             "gate": {"kind": "none"}} for p in field["pairs"] for b in balances]


def evidence_for(storage: Any, evidence_run: str, until_ms: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The edge model's evidence: the RAW observations of `evidence_run` (the DEVELOPMENT run), optionally
    only those that exited by `until_ms` -- TEST passes the DEVELOPMENT end, so no holdout outcome enters."""
    from app.competition.v31_edge import EdgeModel
    raws = storage.v31_bots(evidence_run, role="RAW", extra=True)
    ev = load_evidence(raws, until_ms)
    by: dict[str, int] = defaultdict(int)
    for o in ev:
        by[f"{o['sid']} {o['tf']}"] += 1
    stats = {"run_id": evidence_run, "observations": len(ev), "raw_bots": len(raws), "by_family_tf": dict(sorted(by.items())),
             "until_ms": until_ms, "fingerprint": EdgeModel(ev).fingerprint()}
    return ev, stats


# ---- analysis ----------------------------------------------------------------------------------------------

def _side(m: Mapping[str, Any] | None, a: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    if m is None:
        return None
    mm = m.get("metrics") or {}
    return {"key": m["key"], "net": mm.get("net_profit"), "gross": mm.get("gross_pnl"), "fees": mm.get("fees_paid"),
            "slippage": mm.get("slippage_cost"), "trades": mm.get("trades"), "exp_r": mm.get("expectancy_r"),
            "pf": mm.get("profit_factor"), "max_dd": mm.get("max_drawdown_pct"),
            "trades_per_day": _r((mm.get("trades") or 0) / max(1e-9, float(m["window"]["days"]))),
            "state": (a or {}).get("state"), "failure_mode": (a or {}).get("failure_mode")}


def _r(x: Any, nd: int = 4) -> Any:
    return an._r(x, nd)


def _count(items: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        out[str(x)] = out.get(str(x), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _lb(rec: Mapping[str, Any], a: Mapping[str, Any], mode: str) -> dict[str, Any]:
    e, act, j = a["edge"], a["activity"], a.get("jev") or {}
    fin = j.get("final") or {}
    n = sum(fin.values()) or 0
    mix = ({k: _r(v / n, 3) for k, v in fin.items()} if n else None)
    idn = rec["identity"]
    bal = float(rec.get("balance") or 20.0)
    net = (rec.get("metrics") or {}).get("net_profit") or 0.0
    return {"key": rec["key"], "role": rec["role"], "mode": mode, "strategy_id": idn["strategy_id"],
            "family": FAMILY.get(idn["strategy_id"], ""), "coin": idn["coin"], "tf": idn["timeframe"],
            "pair_id": rec["pair_id"], "equity": _r(bal + net), "net_pnl": e["net_pnl"],
            "net_return_pct": e["net_return_pct"], "trades": act["trades"], "trades_per_day": act["trades_per_day"],
            "exp_r": e["net_expectancy_r"], "gross_exp_r": e["gross_expectancy_r"], "pf": e["net_pf"],
            "max_dd": e["max_drawdown_pct"], "jev_actions": mix,
            "selection_alpha": ((a.get("baselines") or {}).get("selection") or {}).get("alpha_usdt"),
            "state": a["state"], "failure_mode": a["failure_mode"], "diagnoses": a["diagnoses"],
            "participation_ok": act["participation_ok"], "rt_cost_bps": a["cost"].get("round_trip_cost_bps")}


def _tapes_for(settings: Any, coin: str, months: Sequence[str]) -> Any:
    from app.backtest import archive
    from app.competition.v31_diagnostics import Tape
    try:
        return Tape(archive.load_klines(settings, f"{coin}USDT", list(months)))
    except Exception as exc:
        log.warning("no tape for %s: %s", coin, exc)
        return None


def analyze_run(storage: Any, run_id: str, cfg: V31Config, settings: Any,
                dev_summary: Mapping[str, Any] | None = None, ticks: Mapping[str, float] | None = None) -> dict[str, Any]:
    """Analyze every bot of the run (one coin's 1m tape in memory at a time) and build the summary."""
    from app.competition.v31_diagnostics import diagnose
    from app.competition.v31_maker import simulate_trades
    from app.competition.v31_maker import summarize as maker_summary
    run = storage.v31_run(run_id) or {}
    field = run.get("field") or {"pairs": []}
    recs = storage.v31_bots(run_id, heavy=True, extra=True)
    by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in recs:
        by_role[r["role"]].append(r)
    raw = by_role.get("RAW", [])
    controls = by_role.get("CONTROL", [])
    jevs = by_role.get("JEV", [])
    takes = {b["pair_id"]: b for b in by_role.get("TAKE", [])}
    randoms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for b in by_role.get("RANDOM", []):
        randoms[b["pair_id"]].append(b)
    ctrl_by_pair = {b["pair_id"]: b for b in controls}
    caps: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for b in by_role.get("CAPACITY", []):
        caps[b["pair_id"]].append(b)
    field_pids = {f"v31pair:{p['strategy_id']}-{p['coin']}-{p['timeframe']}" for p in field["pairs"]}

    analyses: dict[str, dict[str, Any]] = {}
    maker_rows: list[dict[str, Any]] = []
    path_records: dict[str, dict[str, Any]] = {}
    ticks = dict(ticks or {})
    for coin in cfg.coins:
        mine = [r for r in recs if r["identity"]["coin"] == coin and r["role"] in ("CONTROL", "JEV", "TAKE")]
        if not mine:
            continue
        tape = _tapes_for(settings, coin, cfg.months)
        for rec in mine:
            base = None
            if rec["role"] == "JEV":
                pid = rec["pair_id"]
                ctl = ctrl_by_pair.get(pid)
                base = {"control_net": (ctl or {}).get("metrics", {}).get("net_profit"),
                        "take_net": (takes.get(pid) or {}).get("metrics", {}).get("net_profit"), "skip_net": 0.0,
                        "selection": an.selection_alpha(rec, randoms.get(pid, []), cfg.gates.random_percentile)}
            analyses[rec["key"]] = an.analyze_bot(rec, cfg, tape, base)
        ctl_coin = [r for r in controls if r["identity"]["coin"] == coin]
        if tape is not None:
            for rec in ctl_coin:
                maker_rows += simulate_trades(rec.get("trades") or [], tape, cfg.maker, taker_fee=cfg.fees.taker_rate,
                                              maker_fee=cfg.fees.maker_rate, tick=float(ticks.get(coin) or 0.0),
                                              group_of=lambda t, tf=rec["identity"]["timeframe"]: tf)
            fam = diagnose({r["key"]: r for r in ctl_coin}, {f"{coin}USDT": tape})
            path_records[coin] = fam
        del tape
    for key, a in analyses.items():
        storage.v31_bot_analysis(run_id, key, a, a["state"], a["failure_mode"], None)

    pairs = []
    for rec in jevs:
        pid = rec["pair_id"]
        a = analyses[rec["key"]]
        ctl, take = ctrl_by_pair.get(pid), takes.get(pid)
        ca = analyses.get(ctl["key"]) if ctl else None
        sel = (a.get("baselines") or {}).get("selection") or {}
        idn = rec["identity"]
        pairs.append({"pair_id": pid, "jev_key": rec["key"], "control_key": ctl["key"] if ctl else None,
                      "strategy_id": idn["strategy_id"], "family": FAMILY.get(idn["strategy_id"], ""), "coin": idn["coin"],
                      "tf": idn["timeframe"], "jev": _side(rec, a), "control": _side(ctl, ca), "always_take": _side(take),
                      "always_skip": {"net": 0.0, "trades": 0}, "random": sel,
                      "delta_vs_control": (rec["metrics"].get("net_profit") or 0.0) - ((ctl or {}).get("metrics", {}).get("net_profit") or 0.0),
                      "jev_funnel": rec.get("funnel"), "control_funnel": (ctl or {}).get("funnel"),
                      "jev_actions": (a.get("jev") or {}).get("final"), "jev_auc": (a.get("jev") or {}).get("auc_support"),
                      "attack_jev": a.get("attack"), "attack_control": (ca or {}).get("attack"),
                      "state": a["state"], "failure_mode": a["failure_mode"]})
    pairs.sort(key=lambda p: (["3m", "5m", "15m", "30m"].index(p["tf"]), p["strategy_id"], p["coin"]))

    # leaderboards: the field (Jev bots + matched controls + always-take) and every control
    field_rows = []
    for rec in jevs + [c for c in controls if c["pair_id"] in field_pids] + list(takes.values()):
        mode = {"JEV": "JEV V3", "CONTROL": "CONTROL", "TAKE": "ALWAYS TAKE"}[rec["role"]]
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
    pooled_jev = an.jev_block({"role": "JEV", "decisions": jev_ds}) if jev_ds else {"decisions": 0}
    jev_trades = [t for r in jevs for t in (r.get("trades") or [])]
    ctl_field_trades = [t for c in controls if c["pair_id"] in field_pids for t in (c.get("trades") or [])]
    alpha = [p["random"].get("alpha_usdt") for p in pairs if (p.get("random") or {}).get("alpha_usdt") is not None]
    summary = {
        "run_id": run_id, "protocol": cfg.protocol, "dataset_role": cfg.dataset_role,
        "window": {"from": cfg.trade_from, "to": cfg.trade_to, "days": cfg.days},
        "venue": cfg.venue, "price_tape": cfg.price_tape, "evidence": run.get("evidence"),
        "counts": {"raw_observers": len(raw), "controls": len(controls), "jev_bots": len(jevs), "take_twins": len(takes),
                   "random_twins": sum(len(v) for v in randoms.values()), "capacity_bots": len(by_role.get("CAPACITY", [])),
                   "field_pairs": len(field["pairs"]), "field_by_tf": _count(p["timeframe"] for p in field["pairs"])},
        "field": field,
        "leaderboard_field": field_rows, "leaderboard_controls": ctrl_rows,
        "viability": an.viability(controls, raw, cfg.timeframes),
        "calibration": calibration(ev_rows),
        "calibration_by_tf": {tf: calibration([row for c in controls if c["identity"]["timeframe"] == tf
                                               for row in (c.get("edge_rows") or [])]) for tf in cfg.timeframes},
        "funnels": {"controls": {tf: an.funnel_totals([c for c in controls if c["identity"]["timeframe"] == tf]) for tf in cfg.timeframes},
                    "jev": {tf: an.funnel_totals([j for j in jevs if j["identity"]["timeframe"] == tf]) for tf in cfg.timeframes}},
        "holding": an.holding_table(controls),
        "exit_diagnostics": _merge_paths(path_records),
        "pairs": pairs,
        "baselines": {"pairs": len(pairs), "jev_total": _r(sum((p["jev"] or {}).get("net") or 0.0 for p in pairs)),
                      "control_total": _r(sum((p["control"] or {}).get("net") or 0.0 for p in pairs)),
                      "always_take_total": _r(sum((p["always_take"] or {}).get("net") or 0.0 for p in pairs)),
                      "always_skip_total": 0.0,
                      "random_median_total": _r(sum((p["random"] or {}).get("random_median") or 0.0 for p in pairs)),
                      "jev_beats_control": sum(1 for p in pairs if (p["delta_vs_control"] or 0) > 1e-9),
                      "jev_beats_control_meaningfully": sum(1 for p in pairs if (p["delta_vs_control"] or 0) >= cfg.gates.min_delta_vs_control),
                      "jev_beats_skip": sum(1 for p in pairs if ((p["jev"] or {}).get("net") or 0) > 0),
                      "jev_beats_take": sum(1 for p in pairs if ((p["jev"] or {}).get("net") or 0) > ((p["always_take"] or {}).get("net") or 0)),
                      "jev_beats_random_p90": sum(1 for p in pairs if (p["random"] or {}).get("random_p90") is not None
                                                  and ((p["jev"] or {}).get("net") or 0) > p["random"]["random_p90"])},
        "selection_alpha": {"pairs": len(alpha), "total_usdt": _r(sum(alpha)), "mean_usdt": _r(an._mean(alpha)),
                            "positive_pairs": sum(1 for x in alpha if x > 0)},
        "jev_pooled": pooled_jev,
        "attack": {"jev": an.attack_effectiveness(jev_trades, "JEV"),
                   "controls_field": an.attack_effectiveness(ctl_field_trades, "CONTROL"),
                   "controls_all": an.attack_effectiveness([t for c in controls for t in (c.get("trades") or [])], "CONTROL")},
        "capacity": capacity_table(field["pairs"], ctrl_by_pair, caps),
        "maker": maker_summary(maker_rows, cfg.maker) if maker_rows else {},
        "states": {"controls": _count(analyses[c["key"]]["state"] for c in controls),
                   "jev_bots": _count(analyses[j["key"]]["state"] for j in jevs)},
        "failure_modes": {"controls": _count(analyses[c["key"]]["failure_mode"] for c in controls),
                          "jev_bots": _count(analyses[j["key"]]["failure_mode"] for j in jevs), "labels": an.LABEL},
        "passed": {"controls": sorted(c["key"] for c in controls if analyses[c["key"]]["state"] == "ADVANCE"),
                   "jev_bots": sorted(j["key"] for j in jevs if analyses[j["key"]]["state"] == "ADVANCE")},
        "low_activity_edge": sorted(r["key"] for r in controls + jevs if analyses[r["key"]]["state"] == "LOW_ACTIVITY_EDGE"),
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
    storage.v31_run_update(run_id, summary_json=summary, stage="ANALYZED", status="complete",
                           finished_ts=int(time.time() * 1000))
    return summary


def _merge_paths(per_coin: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Family x timeframe exit/entry verdicts, pooled over coins by trade count."""
    out: dict[str, dict[str, Any]] = defaultdict(dict)
    for fam_block in per_coin.values():
        for sid, tfs in (fam_block.get("families") or {}).items():
            for tf, s in tfs.items():
                cur = out[sid].get(tf)
                if cur is None:
                    out[sid][tf] = {"n": s.get("n", 0), "verdicts": {s.get("verdict"): s.get("n", 0)},
                                    "median_hold_min": [s.get("median_hold_min")] if s.get("median_hold_min") is not None else [],
                                    "gross_bps": [(s.get("gross_bps_per_trade"), s.get("n", 0))],
                                    "cost_bps": [(s.get("cost_bps_per_trade"), s.get("n", 0))],
                                    "drift": [(s.get("drift_bps"), s.get("n", 0))],
                                    "gave_back": [(s.get("gave_back_share"), s.get("n", 0))]}
                else:
                    cur["n"] += s.get("n", 0)
                    cur["verdicts"][s.get("verdict")] = cur["verdicts"].get(s.get("verdict"), 0) + s.get("n", 0)
                    if s.get("median_hold_min") is not None:
                        cur["median_hold_min"].append(s.get("median_hold_min"))
                    cur["gross_bps"].append((s.get("gross_bps_per_trade"), s.get("n", 0)))
                    cur["cost_bps"].append((s.get("cost_bps_per_trade"), s.get("n", 0)))
                    cur["drift"].append((s.get("drift_bps"), s.get("n", 0)))
                    cur["gave_back"].append((s.get("gave_back_share"), s.get("n", 0)))

    def wmean(pairs):
        pairs = [(v, n) for v, n in pairs if isinstance(v, (int, float)) and n]
        tot = sum(n for _, n in pairs)
        return _r(sum(v * n for v, n in pairs) / tot, 2) if tot else None
    final: dict[str, Any] = {}
    for sid, tfs in out.items():
        final[sid] = {}
        for tf, c in tfs.items():
            drift_keys = set().union(*[set((d or {}).keys()) for d, _ in c["drift"]]) if c["drift"] else set()
            final[sid][tf] = {"n": c["n"], "verdict": max(c["verdicts"].items(), key=lambda kv: kv[1])[0] if c["verdicts"] else None,
                              "median_hold_min_median": _r(sorted(c["median_hold_min"])[len(c["median_hold_min"]) // 2], 1) if c["median_hold_min"] else None,
                              "gross_bps": wmean(c["gross_bps"]), "cost_bps": wmean(c["cost_bps"]),
                              "gave_back_share": wmean(c["gave_back"]),
                              "drift_bps": {k: wmean([((d or {}).get(k), n) for d, n in c["drift"]]) for k in sorted(drift_keys)}}
    return final


def capacity_table(pairs: Sequence[Mapping[str, Any]], ctrl_by_pair: Mapping[str, Mapping[str, Any]],
                   caps: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    """CAPACITY ANALYSIS ONLY: the same CONTROL at 20 / 50 / 100 USDT. Never qualifies the 20 USDT competition."""
    rows = []
    tot: dict[str, dict[str, float]] = defaultdict(lambda: {"net": 0.0, "start": 0.0, "trades": 0, "below_min": 0})
    for p in pairs:
        pid = f"v31pair:{p['strategy_id']}-{p['coin']}-{p['timeframe']}"
        books = ([ctrl_by_pair[pid]] if pid in ctrl_by_pair else []) + list(caps.get(pid, []))
        row = {"pair_id": pid, "strategy_id": p["strategy_id"], "coin": p["coin"], "tf": p["timeframe"], "books": {}}
        for b in books:
            bal = float(b.get("balance") or 20.0)
            m = b.get("metrics") or {}
            a = b.get("activity") or {}
            key = f"{bal:g}"
            row["books"][key] = {"net": _r(m.get("net_profit")), "return_pct": _r(m.get("net_return_pct")),
                                 "trades": m.get("trades"), "exp_r": _r(m.get("expectancy_r")),
                                 "max_dd": _r(m.get("max_drawdown_pct")), "below_min": a.get("below_exchange_minimum", 0)}
            t = tot[key]
            t["net"] += m.get("net_profit") or 0.0
            t["start"] += bal
            t["trades"] += m.get("trades") or 0
            t["below_min"] += a.get("below_exchange_minimum", 0) or 0
        rows.append(row)
    return {"rows": rows, "totals": {k: {"net": _r(v["net"]), "return_pct": _r(v["net"] / v["start"]) if v["start"] else None,
                                         "trades": v["trades"], "below_min": v["below_min"]} for k, v in sorted(tot.items(), key=lambda kv: float(kv[0]))},
            "note": "capacity diagnostic only: a 50 / 100 USDT result never qualifies the 20 USDT competition"}


def answers(s: Mapping[str, Any], cfg: V31Config) -> dict[str, str]:
    b = s["baselines"]
    via = s.get("viability") or {}
    tf_line = "; ".join(f"{tf}: net {v.get('net_expectancy_r')}R over {v.get('trades')} trades, "
                        f"{v.get('profitable_bots')}/{v.get('bots')} bots profitable" for tf, v in via.items())
    passed = s.get("passed_all") or []
    if cfg.dataset_role == "TEST":
        adv = s["advanced_set"]
        q1 = ("YES: " + ", ".join(adv)) if isinstance(adv, list) else "NO: no bot passed every V3.1 gate on BOTH DEVELOPMENT and TEST"
    else:
        q1 = (f"{len(passed)} bot(s) passed every DEVELOPMENT gate -> TEST decides: " + ", ".join(passed)) if passed else \
            "NO: no V3.1 bot passed every DEVELOPMENT gate"
    if not s["pairs"]:
        q2 = "NOT TESTED: no Jev pairs ran"
    else:
        sa = s.get("selection_alpha") or {}
        q2 = (f"Jev beat its control in {b['jev_beats_control']} of {b['pairs']} pairs (meaningfully in "
              f"{b['jev_beats_control_meaningfully']}), the matched random action's 90th percentile in "
              f"{b['jev_beats_random_p90']}; selection alpha total {sa.get('total_usdt')} USDT "
              f"({sa.get('positive_pairs')}/{sa.get('pairs')} pairs positive)")
    return {"aggressive_edge": q1, "jev_selection": q2, "small_timeframes": tf_line,
            "advanced_set": ", ".join(s["advanced_set"]) if isinstance(s["advanced_set"], list) else str(s["advanced_set"])}
