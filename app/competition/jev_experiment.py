"""CONTROL vs +JEV: does Jev add an after-cost edge over the SAME strategy without it?

Every pair is two bots that differ in exactly one thing:

    S02-ADAUSDT-5m@20x-v1                 CONTROL   the arena bot as it is
    S02-ADAUSDT-5m@20x-v1+JEV1.13-P1      +JEV      the same bot with Jev between signal and RiskManager

Same strategy, coin, timeframe, 20 USDT wallet, 1m tape, fees, slippage, funding, risk profile,
leverage ceiling and execution model. Both are replayed in this experiment, by this code, so nothing
differs by accident. Pairs are taken from a finished arena with the arena's own deterministic
rotation (`bots.select`) -- never by which bots did well.

The headline number is the JEV EDGE DELTA: +JEV net result minus CONTROL net result. A +JEV bot that
makes money but less than its control made the system worse, and is reported that way.
"""
from __future__ import annotations

import dataclasses
import json
import math
import multiprocessing
import os
import statistics
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from app.ai.jev.models import DEFAULT_MODEL, PRICE_PER_M_INPUT_USD, PROMPT_VERSION, QUESTIONS_FINGERPRINT
from app.ai.jev.policy import POLICY_V1, JevPolicyConfig
from app.ai.jev.state import STATE_VERSION
from app.competition.arena import Arena, ArenaConfig, _downsample, _trade_row, entry_stats
from app.competition.bots import BotSpec, select
from app.core.types import MarketRules
from app.execution.config import ExecutionConfig, FeeSchedule

PROB_BUCKETS = ((0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0001))


def model_tag(model: str) -> str:
    """typesafe/jev-1.13 -> 1.13"""
    tail = model.rsplit("/", 1)[-1]
    return tail.split("jev-", 1)[-1] if "jev-" in tail else tail


def policy_tag(version: str) -> str:
    """JEV_POLICY_V1 -> P1"""
    return "P" + version.rsplit("V", 1)[-1] if "_V" in version else version


def jev_key(control_key: str, model: str, policy_version: str) -> str:
    return f"{control_key}+JEV{model_tag(model)}-{policy_tag(policy_version)}"


def pair_id(control_key: str) -> str:
    return f"pair:{control_key}"


@dataclass(frozen=True)
class JevExperimentConfig:
    arena_run_id: str
    pairs: int = 30
    model: str = DEFAULT_MODEL
    policy: JevPolicyConfig = POLICY_V1
    max_calls: int = 20_000
    max_cost_usd: float = 2.0
    workers: int = 4
    replay_of: str = ""                  # a finished run to reproduce from its frozen ledger

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["policy"] = self.policy.to_dict()
        d.update({"prompt_version": PROMPT_VERSION, "questions_fingerprint": QUESTIONS_FINGERPRINT,
                  "state_version": STATE_VERSION, "policy_fingerprint": self.policy.fingerprint()})
        return d


def arena_config_from(stored: dict[str, Any]) -> ArenaConfig:
    """Rebuild the arena's exact configuration from what the arena run stored."""
    names = {f.name for f in dataclasses.fields(ArenaConfig)}
    special = ("fees", "execution", "min_trades", "only_keys", "strategy_fingerprints")
    kw = {k: v for k, v in stored.items() if k in names and k not in special}
    return ArenaConfig(**kw, fees=FeeSchedule(**(stored.get("fees") or {})),
                       execution=ExecutionConfig(**(stored.get("execution") or {})),
                       min_trades=tuple((stored.get("min_trades") or {}).items()) or ArenaConfig().min_trades,
                       only_keys=tuple(stored.get("only_keys") or ()),
                       strategy_fingerprints=tuple(sorted((stored.get("strategy_fingerprints") or {}).items())))


def plan_pairs(storage: Any, arena_run_id: str, n: int) -> tuple[dict[str, Any], list[BotSpec]]:
    run = storage.arena_run(arena_run_id, heavy=True)
    if run is None:
        raise ValueError(f"no arena run {arena_run_id}")
    rows = storage.arena_bots(arena_run_id)
    specs = [BotSpec(r["strategy_id"], r["symbol"], r["timeframe"], int(r["max_leverage"]),
                     r.get("profile") or "AGGRESSIVE", r.get("params_version") or "v1",
                     r.get("name") or r["strategy_id"])
             for r in rows if r.get("metrics") is not None]
    specs.sort(key=lambda s: s.key)
    return run, select(specs, n)


# ---- one competitor (runs in a worker process) -----------------------------------------------------

def _settings(balance: float) -> Any:
    from scripts.backtest import settings_for
    over = {"DATA_DIR": os.environ["DATA_DIR"]} if os.environ.get("DATA_DIR") else {}
    return settings_for(balance, **over)


def run_member(job: dict[str, Any]) -> dict[str, Any]:
    """Replay one CONTROL or one +JEV bot and persist its result. Top-level so it pickles."""
    from app.ai.jev.client import JevClient
    from app.ai.jev.gate import CachedDecider, ClientDecider, JevGate
    from app.ai.jev.models import JevConfig, JevOutcome
    from app.backtest import brackets as bracket_mod
    from app.core.storage import Storage
    from app.strategies.registry import load_all

    t0 = time.time()
    cfg = arena_config_from(job["arena_config"])
    settings = _settings(cfg.starting_balance)
    rules = {s: MarketRules(**r) for s, r in job["rules"].items()}
    spec = BotSpec(**job["spec"])
    arena = Arena(settings, cfg, job["months"], rules,
                  brackets=bracket_mod.load([spec.symbol], settings.data_dir))
    cls = load_all(strict=True)[spec.strategy_id]
    storage = Storage(job["db"])
    policy = JevPolicyConfig(**{**job["policy"], "multipliers": tuple(job["policy"]["multipliers"].items())})
    gate = None
    if job["role"] == "JEV":
        inner = None
        if job.get("dry_run"):
            class _Dry:
                def decide(self, state):
                    return JevOutcome(False, error_code="DRY_RUN", error_message="counting only")
            inner = _Dry()
        else:
            client = JevClient(JevConfig.from_env(), os.environ.get("OPENROUTER_API_KEY"))
            inner = ClientDecider(client)
        decider = CachedDecider(storage, inner, job["run_id"], job["key"], job["model"],
                                replay_only=bool(job.get("replay_only")),
                                max_calls=job.get("max_calls"), max_cost_usd=job.get("max_cost_usd"))
        gate = JevGate(decider, storage, job["run_id"],
                       {"key": job["key"], "strategy_id": spec.strategy_id,
                        "strategy_name": getattr(cls, "name", spec.strategy_id),
                        "params_version": spec.params_version, "symbol": spec.symbol,
                        "timeframe": spec.timeframe, "control_version": spec.version()},
                       job["pair_id"], job["model"], policy)
    res, m = arena.run_bot(spec, cls, gate=gate)
    state, gates, reasons = arena.judge(spec, m)
    bot = {"key": job["key"], "pair_id": job["pair_id"], "role": job["role"],
           "control_key": spec.key, **spec.to_dict(), "key_control": spec.key,
           "version": spec.version(), "state": state, "gates": gates, "reasons": reasons,
           **entry_stats(res), "metrics": m.to_dict(),
           "equity": _downsample(res.equity, 400), "trades_ledger": [_trade_row(t) for t in res.trades],
           "elapsed_s": round(time.time() - t0, 2)}
    bot["key"] = job["key"]
    if gate is not None:
        link_outcomes(storage, job["run_id"], res)
        bot["jev"] = {"model": job["model"], "prompt_version": PROMPT_VERSION,
                      "policy_version": policy.version, "decisions": gate.decisions}
    if not job.get("dry_run"):
        storage.save_jev_bot(job["run_id"], bot)
    out = {"key": job["key"], "role": job["role"], "pair_id": job["pair_id"], "state": state,
           "trades": m.trades, "net": m.net_profit, "signals": res.signals,
           "decisions": gate.decisions if gate else 0, "elapsed_s": bot["elapsed_s"]}
    storage.close()
    return out


def link_outcomes(storage: Any, run_id: str, res: Any) -> None:
    """Attach what really happened (or would have) to every decision of this bot."""
    by_decision: dict[str, dict[str, Any]] = {}
    for ev in res.gate_events:
        if ev.get("decision_id"):
            by_decision.setdefault(ev["decision_id"], {})["result"] = ev["result"]
    trade_by_pos = {t.position_id: t for t in res.trades}
    for pos_id, did in res.entry_links.items():
        t = trade_by_pos.get(pos_id)
        if t is not None:
            by_decision.setdefault(did, {}).update(outcome_kind="TAKEN", outcome_net=t.net,
                                                   outcome_r=t.r_multiple, outcome_exit=t.exit_kind)
    shadow_by_pos = {t.position_id: t for t in res.shadow_trades}
    for pos_id, did in res.shadow_links.items():
        t = shadow_by_pos.get(pos_id)
        if t is not None:
            by_decision.setdefault(did, {}).update(outcome_kind="SHADOW", outcome_net=t.net,
                                                   outcome_r=t.r_multiple, outcome_exit=t.exit_kind)
    for did, fields in by_decision.items():
        storage.update_jev_outcome(did, **fields)


# ---- the experiment ---------------------------------------------------------------------------------

def run_experiment(storage: Any, db_path: str, cfg: JevExperimentConfig, label: str = "",
                   dry_run: bool = False,
                   on_done: Callable[[dict[str, Any], int, int], None] | None = None) -> dict[str, Any]:
    arena_run, picked = plan_pairs(storage, cfg.arena_run_id, cfg.pairs)
    acfg = dict(arena_run.get("config") or {})
    months = list(acfg.get("months") or [])
    rules = dict(acfg.get("rules") or {})
    run_id = cfg.replay_of or ("dry-" if dry_run else "") + uuid.uuid4().hex[:12]
    replay_only = bool(cfg.replay_of)
    policy = cfg.policy.to_dict()
    jobs: list[dict[str, Any]] = []
    for spec in picked:
        base = {"spec": dataclasses.asdict(spec), "months": months, "rules": rules,
                "arena_config": acfg, "db": ":memory:" if dry_run else db_path, "run_id": run_id,
                "model": cfg.model,
                "policy": policy, "pair_id": pair_id(spec.key), "dry_run": dry_run,
                "replay_only": replay_only, "max_calls": cfg.max_calls,
                "max_cost_usd": cfg.max_cost_usd}
        jobs.append({**base, "role": "JEV", "key": jev_key(spec.key, cfg.model, cfg.policy.version)})
        jobs.append({**base, "role": "CONTROL", "key": spec.key})
    if not dry_run and not replay_only:
        storage.start_jev_run({
            "run_id": run_id, "created_ts": int(time.time() * 1000), "status": "running",
            "label": label or f"CONTROL vs +JEV - {months[0]}..{months[-1]}",
            "arena_run_id": cfg.arena_run_id,
            "config_fingerprint": _fp({**cfg.to_dict(), "arena": arena_run.get("config_fingerprint")}),
            "model": cfg.model, "prompt_version": PROMPT_VERSION, "policy_version": cfg.policy.version,
            "first_month": months[0] if months else None, "last_month": months[-1] if months else None,
            "pairs": len(picked), "config": {**cfg.to_dict(), "months": months,
                                             "arena_config_fingerprint": arena_run.get("config_fingerprint"),
                                             "pairs": [s.key for s in picked]},
            "summary": {}})
    results: list[dict[str, Any]] = []
    # spawn, not fork: no worker may inherit this process's open SQLite connection.
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=max(1, cfg.workers), mp_context=ctx) as pool:
        futures = [pool.submit(run_member, j) for j in jobs]
        for i, f in enumerate(as_completed(futures), 1):
            r = f.result()
            results.append(r)
            if on_done:
                on_done(r, i, len(jobs))
    if dry_run:
        return {"run_id": run_id, "dry_run": True, "pairs": len(picked), "results": results,
                "months": months}
    summary = analytics(storage, run_id)
    storage.update_jev_run(run_id, status="complete", finished_ts=int(time.time() * 1000),
                           summary_json=summary)
    return summary


def _fp(obj: Any) -> str:
    from app.ai.jev.models import fingerprint
    return fingerprint(obj)


# ---- analytics ----------------------------------------------------------------------------------------

def _pct(vals: Sequence[float], q: float) -> float | None:
    v = sorted(vals)
    return v[min(len(v) - 1, int(len(v) * q))] if v else None


def sign_test_p(improved: int, worsened: int) -> float | None:
    """Two-sided exact binomial sign test: could this split be a coin toss?"""
    n = improved + worsened
    if n == 0:
        return None
    k = max(improved, worsened)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def jev_stats(decisions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(decisions)
    act = lambda a: [d for d in decisions if d.get("final_action") == a]   # noqa: E731
    take, reduce_, skip = act("TAKE"), act("REDUCE"), act("SKIP")
    errors = [d for d in decisions if d.get("error_code")]
    api = [d for d in decisions if d.get("source") == "api"]
    lat = [int(d["request_latency_ms"]) for d in api if d.get("request_latency_ms") and not d.get("error_code")]
    shadow_skips = [d for d in skip if d.get("outcome_kind") == "SHADOW" and d.get("outcome_net") is not None]
    attack = [d for d in take if d.get("final_level") == "ATTACK"]
    attribution = (sum(-(d["outcome_net"]) for d in shadow_skips)
                   + sum(-(d.get("outcome_net") or 0.0) for d in reduce_ if d.get("outcome_kind") == "TAKEN")
                   + sum((d.get("outcome_net") or 0.0) / 3.0 for d in attack if d.get("outcome_kind") == "TAKEN"))
    return {
        "reviewed": n, "accepted": len(take), "reduced": len(reduce_), "skipped": len(skip),
        "attack": len(attack), "errors": len(errors),
        "error_codes": _count(d.get("error_code") for d in errors),
        "acceptance_rate": (len(take) + len(reduce_)) / n if n else None,
        "traded": sum(1 for d in decisions if d.get("result") == "TRADED"),
        "risk_rejected_after_resize": sum(1 for d in decisions if str(d.get("result") or "").startswith("RISK_REJECTED")),
        "skipped_losing": sum(1 for d in shadow_skips if d["outcome_net"] < 0),
        "skipped_winning": sum(1 for d in shadow_skips if d["outcome_net"] > 0),
        "skipped_shadow_net": sum(d["outcome_net"] for d in shadow_skips),
        "decision_attribution_estimate": attribution,
        "api_calls": len(api), "cache_hits": sum(1 for d in decisions if d.get("source") in ("cache", "ledger")),
        "cache_hit_rate": (sum(1 for d in decisions if d.get("source") in ("cache", "ledger")) / n) if n else None,
        "latency_avg_ms": (sum(lat) / len(lat)) if lat else None,
        "latency_p95_ms": _pct(lat, 0.95),
        "input_tokens": sum(int(d.get("input_tokens") or 0) for d in decisions),
        "cost_usd": sum(float(d.get("cost_usd") or 0.0) for d in decisions),
        "models_resolved": sorted({d["model_resolved"] for d in decisions if d.get("model_resolved")}),
    }


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        out[str(x)] = out.get(str(x), 0) + 1
    return out


def auc_with_ci(labelled: Sequence[tuple[float, int]]) -> dict[str, Any]:
    """Can the take probability tell winners from losers? Mann-Whitney AUC with a Hanley-McNeil 95%
    interval. 0.5 is a coin flip; an interval that includes 0.5 is no evidence of discrimination."""
    pos = sum(1 for _, y in labelled if y)
    neg = len(labelled) - pos
    if not pos or not neg:
        return {"auc": None, "low": None, "high": None, "wins": pos, "losses": neg}
    items = sorted(labelled)
    rank_sum, i = 0.0, 0
    while i < len(items):
        k = i
        while k < len(items) and items[k][0] == items[i][0]:
            k += 1
        avg = (i + 1 + k) / 2.0
        rank_sum += avg * sum(1 for t in range(i, k) if items[t][1])
        i = k
    a = (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)
    q1, q2 = a / (2 - a), 2 * a * a / (1 + a)
    se = math.sqrt(max(0.0, (a * (1 - a) + (pos - 1) * (q1 - a * a) + (neg - 1) * (q2 - a * a)) / (pos * neg)))
    return {"auc": a, "low": a - 1.96 * se, "high": a + 1.96 * se, "wins": pos, "losses": neg}


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        out, i = [0.0] * len(v), 0
        while i < len(order):
            k = i
            while k < len(order) and v[order[k]] == v[order[i]]:
                k += 1
            for t in range(i, k):
                out[order[t]] = (i + k - 1) / 2.0
            i = k
        return out
    if len(xs) < 3:
        return None
    rx, ry = ranks(list(xs)), ranks(list(ys))
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return cov / den if den else None


def quintiles(decisions: Sequence[dict[str, Any]], q: int = 5) -> list[dict[str, Any]]:
    """Equal-count probability bands. Fixed buckets say nothing when every answer sits below 0.5."""
    lab = sorted((d["take_probability"], d["outcome_r"]) for d in decisions
                 if d.get("take_probability") is not None and d.get("outcome_r") is not None)
    out = []
    for i in range(q):
        part = lab[i * len(lab) // q:(i + 1) * len(lab) // q]
        if not part:
            continue
        rs = [r for _, r in part]
        out.append({"band": i + 1, "p_low": part[0][0], "p_high": part[-1][0], "candidates": len(part),
                    "win_rate": sum(1 for r in rs if r > 0) / len(rs), "mean_r": sum(rs) / len(rs)})
    return out


def verdict(summary: dict[str, Any]) -> tuple[str, str]:
    """The experiment's question, answered against BOTH baselines.

    Beating a losing control proves nothing on its own: a filter that refuses every trade beats it
    too. A Jev edge needs +JEV to beat its controls AND the never-trade null, AND Jev's probability
    to separate winners from losers better than chance.
    """
    imp, wor, p = summary.get("improved") or 0, summary.get("worsened") or 0, summary.get("sign_test_p")
    jev_total = summary.get("total_jev_net") or 0.0
    auc = summary.get("auc") or {}
    beats_control = bool(imp > wor and p is not None and p < 0.05 and (summary.get("mean_edge_delta_usdt") or 0) > 0)
    beats_null = jev_total > 0 and (summary.get("pairs_jev_profitable") or 0) > (summary.get("pairs_jev_losing") or 0)
    discriminates = auc.get("low") is not None and auc["low"] > 0.5
    detail = (f"vs controls: {imp} improved / {wor} worsened (sign test p={p:.2g}); vs never trading: +JEV total "
              f"{jev_total:+.2f} USDT from {summary.get('accepted', 0) + summary.get('reduced', 0)} allowed trades; "
              f"AUC {auc.get('auc') if auc.get('auc') is None else round(auc['auc'], 3)} "
              f"[{'' if auc.get('low') is None else round(auc['low'], 3)}, "
              f"{'' if auc.get('high') is None else round(auc['high'], 3)}]") if p is not None else "no pairs differed"
    if beats_control and beats_null and discriminates:
        return "YES", "Jev beat its controls and never-trading, and its probability discriminates. " + detail
    if beats_control and not beats_null:
        return "NO", ("The improvement over the controls comes from NOT TRADING: an always-skip filter scores "
                      f"{summary.get('null_mean_edge_delta_usdt', 0):+.2f} USDT per pair against Jev's "
                      f"{summary.get('mean_edge_delta_usdt', 0):+.2f}, and the trades Jev allowed lost money. " + detail)
    if wor > imp and p is not None and p < 0.05:
        return "NO", "Jev made significantly more pairs worse. " + detail
    return "NOT DEMONSTRATED", detail


def calibration(decisions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Take-probability bucket -> how the candidate actually did (real trade or shadow)."""
    rows = []
    for lo, hi in PROB_BUCKETS:
        ds = [d for d in decisions if d.get("take_probability") is not None
              and lo <= d["take_probability"] < hi and d.get("outcome_r") is not None]
        rs = [float(d["outcome_r"]) for d in ds]
        rows.append({"bucket": f"{lo:.2f}-{min(hi, 1.0):.2f}", "candidates": len(ds),
                     "mean_r": (sum(rs) / len(rs)) if rs else None,
                     "win_rate": (sum(1 for r in rs if r > 0) / len(rs)) if rs else None,
                     "sum_r": sum(rs) if rs else 0.0,
                     "taken": sum(1 for d in ds if d.get("outcome_kind") == "TAKEN"),
                     "shadow": sum(1 for d in ds if d.get("outcome_kind") == "SHADOW")})
    return rows


def analytics(storage: Any, run_id: str) -> dict[str, Any]:
    bots = storage.jev_bots(run_id)
    decisions = storage.jev_decisions(run_id)
    by_pair: dict[str, dict[str, Any]] = {}
    for b in bots:
        by_pair.setdefault(b["pair_id"], {})[b["role"]] = b
    pairs = []
    for pid, p in sorted(by_pair.items()):
        c, j = p.get("CONTROL"), p.get("JEV")
        if not c or not j:
            continue
        cm, jm = c.get("metrics") or {}, j.get("metrics") or {}
        ds = [d for d in decisions if d["bot_key"] == j["key"]]
        st = jev_stats(ds)
        delta = (jm.get("net_profit") or 0.0) - (cm.get("net_profit") or 0.0)
        pairs.append({
            "pair_id": pid, "control_key": c["key"], "jev_key": j["key"],
            "strategy_id": c["strategy_id"], "name": c.get("name"), "symbol": c["symbol"],
            "coin": c.get("coin"), "timeframe": c["timeframe"],
            "control": _side(cm, c), "jev": _side(jm, j), "jev_stats": st,
            "edge_delta_usdt": delta,
            "edge_delta_return_pp": ((jm.get("net_return_pct") or 0.0) - (cm.get("net_return_pct") or 0.0)) * 100,
            "economic_net_after_ai": (jm.get("net_profit") or 0.0) - st["cost_usd"],
            "verdict": "IMPROVED" if delta > 1e-9 else ("WORSENED" if delta < -1e-9 else "UNCHANGED")})
    deltas = [p["edge_delta_usdt"] for p in pairs]
    dpp = [p["edge_delta_return_pp"] for p in pairs]
    improved = [p for p in pairs if p["verdict"] == "IMPROVED"]
    worsened = [p for p in pairs if p["verdict"] == "WORSENED"]
    allst = jev_stats(decisions)
    labelled = [(float(d["take_probability"]), 1 if float(d["outcome_r"]) > 0 else 0)
                for d in decisions if d.get("take_probability") is not None and d.get("outcome_r") is not None]
    rho = spearman([d["take_probability"] for d in decisions if d.get("take_probability") is not None
                    and d.get("outcome_r") is not None],
                   [d["outcome_r"] for d in decisions if d.get("take_probability") is not None
                    and d.get("outcome_r") is not None])
    allowed = [d for d in decisions if d.get("final_action") in ("TAKE", "REDUCE") and d.get("outcome_r") is not None]
    skipped_o = [d for d in decisions if d.get("final_action") == "SKIP" and d.get("outcome_r") is not None]
    ps = sorted(d["take_probability"] for d in decisions if d.get("take_probability") is not None)
    risk_states = _count(d.get("risk_state") for d in decisions if d.get("risk_state"))
    regimes = _count(d.get("regime") for d in decisions if d.get("regime"))
    best = max(pairs, key=lambda p: p["edge_delta_usdt"]) if pairs else None
    worst = min(pairs, key=lambda p: p["edge_delta_usdt"]) if pairs else None
    gross = sum(max(0.0, (p["jev"]["gross_pnl"] or 0.0)) for p in pairs)
    out_base = {
        "run_id": run_id, "pairs": len(pairs), "jev_bots": len(pairs), "control_bots": len(pairs),
        "candidate_signals": allst["reviewed"], "jev_calls": allst["api_calls"],
        "cache_hits": allst["cache_hits"], "api_errors": sum(1 for d in decisions
                                                           if d.get("source") == "api" and d.get("error_code")),
        "error_rate": (sum(1 for d in decisions if d.get("source") == "api" and d.get("error_code"))
                       / allst["api_calls"]) if allst["api_calls"] else None,
        "error_codes": allst["error_codes"], "accepted": allst["accepted"], "reduced": allst["reduced"],
        "skipped": allst["skipped"], "attack": allst["attack"], "acceptance_rate": allst["acceptance_rate"],
        "skipped_losing": allst["skipped_losing"], "skipped_winning": allst["skipped_winning"],
        "total_input_tokens": allst["input_tokens"], "total_cost_usd": allst["cost_usd"],
        "cost_per_decision_usd": (allst["cost_usd"] / allst["api_calls"]) if allst["api_calls"] else None,
        "ai_cost_vs_gross_profit": (allst["cost_usd"] / gross) if gross > 0 else None,
        "latency_avg_ms": allst["latency_avg_ms"], "latency_p95_ms": allst["latency_p95_ms"],
        "models_resolved": allst["models_resolved"],
        "improved": len(improved), "worsened": len(worsened),
        "unchanged": len(pairs) - len(improved) - len(worsened),
        "improved_keys": [p["jev_key"] for p in improved], "worsened_keys": [p["jev_key"] for p in worsened],
        "sign_test_p": sign_test_p(len(improved), len(worsened)),
        "mean_edge_delta_usdt": statistics.fmean(deltas) if deltas else None,
        "median_edge_delta_usdt": statistics.median(deltas) if deltas else None,
        "mean_edge_delta_return_pp": statistics.fmean(dpp) if dpp else None,
        "median_edge_delta_return_pp": statistics.median(dpp) if dpp else None,
        "total_control_net": sum(p["control"]["net_profit"] or 0.0 for p in pairs),
        "total_jev_net": sum(p["jev"]["net_profit"] or 0.0 for p in pairs),
        "best_pair": best["pair_id"] if best else None,
        "best_edge_delta_usdt": best["edge_delta_usdt"] if best else None,
        "worst_pair": worst["pair_id"] if worst else None,
        "worst_edge_delta_usdt": worst["edge_delta_usdt"] if worst else None,
        "calibration": calibration(decisions),
        "price_per_m_input_usd": PRICE_PER_M_INPUT_USD,
        # the never-trade null: a filter that refuses every candidate ends each pair at 0 net
        "null_mean_edge_delta_usdt": statistics.fmean([-(p["control"]["net_profit"] or 0.0) for p in pairs]) if pairs else None,
        "pairs_jev_profitable": sum(1 for p in pairs if (p["jev"]["net_profit"] or 0.0) > 1e-9),
        "pairs_jev_losing": sum(1 for p in pairs if (p["jev"]["net_profit"] or 0.0) < -1e-9),
        "auc": auc_with_ci(labelled),
        "spearman_take_p_vs_r": rho,
        "quintiles": quintiles(decisions),
        "take_probability_quartiles": ([ps[0], ps[len(ps) // 4], ps[len(ps) // 2], ps[3 * len(ps) // 4], ps[-1]]
                                       if ps else None),
        "risk_state_counts": risk_states, "regime_counts": regimes,
        "allowed_outcomes": {"n": len(allowed), "net": sum(float(d.get("outcome_net") or 0.0) for d in allowed),
                             "win_rate": (sum(1 for d in allowed if d["outcome_r"] > 0) / len(allowed)) if allowed else None,
                             "mean_r": statistics.fmean([d["outcome_r"] for d in allowed]) if allowed else None},
        "skipped_outcomes": {"n": len(skipped_o),
                             "win_rate": (sum(1 for d in skipped_o if d["outcome_r"] > 0) / len(skipped_o)) if skipped_o else None,
                             "mean_r": statistics.fmean([d["outcome_r"] for d in skipped_o]) if skipped_o else None},
        "pairs_detail": pairs,
    }
    out = dict(out_base)
    out["verdict"], out["verdict_detail"] = verdict(out)
    return out


def _side(m: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {"key": b["key"], "state": b.get("state"), "trades": m.get("trades"),
            "net_profit": m.get("net_profit"), "net_return_pct": m.get("net_return_pct"),
            "gross_pnl": m.get("gross_pnl"), "fees_paid": m.get("fees_paid"),
            "slippage_cost": m.get("slippage_cost"), "funding_paid": m.get("funding_paid"),
            "expectancy_r": m.get("expectancy_r"), "profit_factor": m.get("profit_factor"),
            "max_drawdown_pct": m.get("max_drawdown_pct"), "liquidation_count": m.get("liquidation_count"),
            "win_rate": m.get("win_rate"), "avg_effective_leverage": m.get("avg_effective_leverage")}
