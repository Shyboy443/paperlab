"""Run the multi-year validation tournament: walk-forward -> Monte Carlo -> stress -> qualification.

    python scripts/run_validation.py --from 2021-01 --to 2026-08 --workers 8
    python scripts/run_validation.py --resume <run_id>
    python scripts/run_validation.py --report <run_id>

Every strategy is entered once per leverage ceiling (5x / 10x / 20x) as a SEPARATE competitor, and
final qualification is computed only from the master out-of-sample ledger.

Checkpointing: each finished competitor is written immediately, so a restart resumes rather than
repeating hours of replay. A resume refuses to continue if the config fingerprint changed, because
blending two configurations into one run would make the result meaningless.

Parallelism is safe here because competitors are independent: each worker builds its own seeded
engine and its own isolated wallet, and every one is driven from an identically ordered tape. The
result does not depend on the worker count.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest import archive, load_rules  # noqa: E402
from app.backtest import brackets as bracket_mod  # noqa: E402
from app.competition import walkforward as wf  # noqa: E402
from app.competition.config import (LEVERAGE_IDENTITIES, QualificationConfig, RiskConfig,  # noqa: E402
                                    Season, competitor_key)
from app.competition.validation import Validator, ValidationConfig  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.strategies.registry import load_all  # noqa: E402
from scripts.backtest import settings_for  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

# Rebuilt per worker process rather than pickled across: a Validator holds the funding schedule and
# an engine factory, and shipping those to every task would cost more than rebuilding them once.
_CTX: dict[str, object] = {}


def _context(cfg_blob: str, months: list[str], balance: float, symbols: list[str]):
    key = "ctx"
    if key not in _CTX:
        settings = settings_for(balance)
        cfg = _decode_cfg(cfg_blob)
        lo, hi = _bounds(settings, symbols, months)
        season = Season("validation", "paperlab-bake-off", lo, hi, symbols=tuple(symbols),
                        risk=RiskConfig(starting_balance=balance,
                                        max_leverage=max(cfg.leverages)))
        _CTX[key] = Validator(settings, season, cfg, months,
                              rules=load_rules(settings, symbols),
                              brackets=bracket_mod.load(symbols, settings.data_dir))
        _CTX["windows"] = wf.schedule(lo, hi, cfg.walk_forward)
        _CTX["classes"] = load_all(strict=True)
    return _CTX[key], _CTX["windows"], _CTX["classes"]


def _encode_cfg(cfg: ValidationConfig) -> str:
    return json.dumps(dataclasses.asdict(cfg), default=str)


def _decode_cfg(blob: str) -> ValidationConfig:
    d = json.loads(blob)
    return ValidationConfig(
        walk_forward=wf.WalkForwardConfig(**d["walk_forward"]),
        monte_carlo=__import__("app.competition.montecarlo", fromlist=["x"]).MonteCarloConfig(
            **{k: (tuple(v) if isinstance(v, list) else v) for k, v in d["monte_carlo"].items()}),
        qualification=QualificationConfig(**{k: (frozenset(v) if k == "soft" else v)
                                             for k, v in d["qualification"].items()}),
        leverages=tuple(d["leverages"]), run_stress=d["run_stress"],
        run_monte_carlo=d["run_monte_carlo"],
        skip_later_stages_when_hopeless=d["skip_later_stages_when_hopeless"])


def _bounds(settings, symbols: list[str], months: list[str]) -> tuple[int, int]:
    """First and last bar timestamp actually present in the cache."""
    first = archive.load_klines(settings, symbols[0], months[:1])
    last = archive.load_klines(settings, symbols[0], months[-1:])
    if not first or not last:
        raise SystemExit("no cached klines for that window; run scripts/fetch_archive.py first")
    return first[0].open_time, last[-1].close_time


def task(args: tuple) -> dict:
    """One competitor, in a worker process."""
    sid, leverage, cfg_blob, months, balance, symbols = args
    validator, windows, classes = _context(cfg_blob, months, balance, symbols)
    cv = validator.validate(sid, classes[sid], leverage, windows)
    return cv.to_dict()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="first", default="2021-01")
    ap.add_argument("--to", dest="last", default="2026-08")
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--strategies", default="", help="comma separated; blank = all")
    ap.add_argument("--leverages", default=",".join(str(x) for x in LEVERAGE_IDENTITIES))
    ap.add_argument("--balance", type=float, default=20.0)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--train-days", type=int, default=180)
    ap.add_argument("--val-days", type=int, default=30)
    ap.add_argument("--test-days", type=int, default=30)
    ap.add_argument("--step-days", type=int, default=30)
    ap.add_argument("--db", default="")
    ap.add_argument("--resume", default="", help="run_id to continue")
    ap.add_argument("--report", default="", help="run_id to print")
    args = ap.parse_args(argv)

    settings = settings_for(args.balance)
    db = args.db or str(settings.db_path)
    storage = Storage(db)

    if args.report:
        return report(storage, args.report)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    months = archive.months_between(args.first, args.last)
    cfg = ValidationConfig(
        walk_forward=wf.WalkForwardConfig(train_days=args.train_days, validation_days=args.val_days,
                                          test_days=args.test_days, step_days=args.step_days),
        leverages=tuple(int(x) for x in args.leverages.split(",") if x.strip()))
    cfg_blob = _encode_cfg(cfg)
    meta = archive.load_meta(settings) or {}

    classes = load_all(strict=True)
    wanted = [s.strip().upper() for s in args.strategies.split(",") if s.strip()] or sorted(classes)
    jobs = [(sid, lev) for sid in wanted for lev in cfg.leverages]

    lo, hi = _bounds(settings, symbols, months)
    windows = wf.schedule(lo, hi, cfg.walk_forward)
    if not windows:
        print("history too short for even one walk-forward window")
        return 1

    run_id = args.resume or uuid.uuid4().hex[:12]
    done: set[str] = set()
    if args.resume:
        row = storage.validation_run(run_id)
        if row is None:
            print(f"no such run {run_id}")
            return 1
        if row["config_fingerprint"] != cfg.fingerprint():
            print("REFUSING TO RESUME: config fingerprint changed "
                  f"({row['config_fingerprint']} -> {cfg.fingerprint()}). Start a new run instead.")
            return 2
        done = storage.validation_done_keys(run_id)
        print(f"resuming {run_id}: {len(done)} competitors already complete")
    else:
        storage.start_validation_run({
            "run_id": run_id, "created_ts": int(time.time() * 1000), "status": "running",
            "config_fingerprint": cfg.fingerprint(),
            "dataset_fingerprint": meta.get("fingerprint", ""), "symbols": symbols,
            "first_month": args.first, "last_month": args.last, "windows": len(windows),
            "total_competitors": len(jobs), "starting_balance": args.balance,
            "leverages": list(cfg.leverages), "config": json.loads(cfg_blob),
            "stage": "RUNNING_WALK_FORWARD"})

    todo = [(sid, lev) for sid, lev in jobs if competitor_key(sid, lev) not in done]
    print(f"run {run_id}: {len(todo)} competitors to evaluate "
          f"({len(jobs)} total, {len(done)} done), {len(windows)} windows, "
          f"{len(months)} months, {args.workers} workers", flush=True)
    print(f"dataset fingerprint {meta.get('fingerprint','?')}  config fingerprint {cfg.fingerprint()}",
          flush=True)

    t0 = time.time()
    payload = [(sid, lev, cfg_blob, months, args.balance, symbols) for sid, lev in todo]
    completed = len(done)
    try:
        if args.workers <= 1:
            for p in payload:
                _finish(storage, run_id, task(p), completed := completed + 1, len(jobs), t0)
        else:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(task, p): p for p in payload}
                for fut in as_completed(futures):
                    completed += 1
                    _finish(storage, run_id, fut.result(), completed, len(jobs), t0)
    except KeyboardInterrupt:
        storage.update_validation_run(run_id, status="cancelled", stage="CANCELLED")
        print("\ncancelled; resume with --resume " + run_id)
        return 130

    storage.update_validation_run(run_id, status="done", stage="COMPLETE",
                                  finished_ts=int(time.time() * 1000))
    print(f"\ncomplete in {(time.time()-t0)/60:.1f} min")
    return report(storage, run_id)


def _finish(storage: Storage, run_id: str, cv: dict, n: int, total: int, t0: float) -> None:
    storage.save_validation_competitor(run_id, cv)
    el = time.time() - t0
    rate = n / el if el else 0
    m = cv.get("oos_metrics") or {}
    print(f"  [{n:>3}/{total}] {cv['key']:<12} {cv.get('state',''):<20} "
          f"trades={m.get('trades',0):>5} net={m.get('net_profit',0):>+8.2f} "
          f"{cv.get('elapsed_s',0):>5.0f}s  (eta {max(0,(total-n)/rate)/60:.0f}m)", flush=True)
    storage.update_validation_run(run_id, progress_json={"completed": n, "total": total,
                                                         "elapsed_s": el, "last": cv["key"]})


def report(storage: Storage, run_id: str) -> int:
    row = storage.validation_run(run_id)
    if row is None:
        print(f"no such run {run_id}")
        return 1
    comps = storage.validation_competitors(run_id)
    print("=" * 128)
    print(f"VALIDATION {run_id}   {row['first_month']} -> {row['last_month']}   "
          f"{row['windows']} windows   {len(comps)}/{row['total_competitors']} competitors   "
          f"{row['starting_balance']:.0f} USDT")
    print(f"dataset {row['dataset_fingerprint']}   config {row['config_fingerprint']}   "
          f"status {row['status']}")
    print("=" * 128)
    print(f"{'competitor':<12}{'OOS net':>9}{'ret%':>8}{'trades':>7}{'expR':>7}{'PF':>6}"
          f"{'maxDD':>7}{'win+':>6}{'act':>6}{'ruin':>7}{'stress':>8}  state")
    for c in comps:
        r = storage.validation_competitor(run_id, c["key"]) or {}
        m = r.get("oos_metrics") or {}
        w = r.get("walk_forward") or {}
        mcr = r.get("monte_carlo") or {}
        sr = r.get("stress") or {}
        pf = m.get("profit_factor")
        print(f"{c['key']:<12}{m.get('net_profit',0):>+9.2f}{(m.get('net_return_pct') or 0)*100:>7.1f}%"
              f"{m.get('trades',0):>7}{m.get('expectancy_r') or 0:>+7.2f}"
              f"{(999 if pf in (None,float('inf')) else pf):>6.2f}"
              f"{(m.get('max_drawdown_pct') or 0)*100:>6.1f}%"
              f"{(w.get('profitable_ratio') or 0)*100:>5.0f}%{(w.get('active_ratio') or 0)*100:>5.0f}%"
              f"{(mcr.get('ruin_probability') if mcr.get('ran') else float('nan')):>7.2f}"
              f"{('PASS' if sr.get('survives') else ('FAIL' if sr.get('ran') else 'n/a')):>8}"
              f"  {c['state']}")
    states: dict[str, int] = {}
    for c in comps:
        states[c["state"]] = states.get(c["state"], 0) + 1
    qualified = [c["key"] for c in comps if c["state"] in ("QUALIFIED", "SHADOW_LIVE", "LIVE_CANDIDATE")]
    print("\nstates: " + ", ".join(f"{k}={v}" for k, v in sorted(states.items())))
    print(f"QUALIFIED = {qualified if qualified else '[]'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
