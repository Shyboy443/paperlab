"""Compare BASE, archived hourly-proxy HTF, and revised closed-candle HTF.

Correctness revision on 2026-10-05: no EMA/ATR parameter search. An initial
range-permitting reversion prototype failed the discovery-half VWAP comparison
and increased total losses, so reversion retains a directional-context requirement
(archived in snapshots/2026-10-05-htf-audit). This is an exploratory revision.
Same 25-day warm-up,
engine, sizing, fees, recorded funding, 60.001s latency, daily book resets in
all arms. Include reset/end closes and their costs. Compare net R in both
chronological halves and net USDT, reporting counts separately. The second
half is a stability check on previously studied data, not an unseen holdout.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import sys
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from scripts.v14_htf_study import DB, DAY, V8_COINS, summarize, tape, window

ARCHIVE = PROJECT / "snapshots/2026-10-05-htf-audit"
OUT = PROJECT / "docs/V14_HTF_REVISION_STUDY.json"
WARMUP_DAYS = 25


def revision():
    return hashlib.sha256(b"".join((PROJECT / p).read_bytes() for p in
                          ("app/strategies/v14/htf.py", "app/live/v14_engine.py"))).hexdigest()


def old_module():
    path = ARCHIVE / "app/strategies/v14/htf.py"
    spec = importlib.util.spec_from_file_location("v14_legacy_htf", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(args):
    import duckdb
    from app.backtest.funding import FundingSchedule
    from app.competition import v14_config as cfg
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6
    from app.core.types import MarketRules
    from app.live.scan_engine import BE_COVER_BPS, ScanReplayEngine
    from app.live.v8_engine import LevelMakerEngineV8
    from app.live.v13_engine import LimitEntryEngineV13
    from app.live.v14_engine import HTFScalpEngine, HTFScanEngine, HTFLimitEngine
    from app.strategies.v14 import htf

    sid, mode, coin = args
    universe = [coin] if sid == "V14.1" else list(cfg.UNIVERSE)
    start, end = window(universe)
    since = start + WARMUP_DAYS * DAY
    rules = {s: MarketRules(**r) for s, r in cfg.load_freeze()["rules"].items() if s in universe}
    con = duckdb.connect(str(DB), read_only=True)
    funding = {s: [(int(t), float(r)) for t, r in con.execute(
        "SELECT ts, rate FROM funding WHERE symbol = ? AND ts >= ? AND ts <= ? ORDER BY ts", [s, start, end]).fetchall()]
        for s in universe}
    con.close()
    settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
    kw = dict(rules=rules, seed=7, fees=FEES_V6, fee_source="schedule", funding=FundingSchedule(funding),
              sizing=SizingV6(rules, jev=False), leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct,
              execution=cfg.EXECUTION_V14, maker_tp=True)
    kind = htf.SOURCES[sid]["kind"]
    if kind == "single":
        eng = (HTFScalpEngine if mode == "NEW" else LevelMakerEngineV8)(settings, universe, **kw)
    elif kind == "scanner":
        eng = (HTFScanEngine if mode == "NEW" else ScanReplayEngine)(settings, universe, be_cover_bps=BE_COVER_BPS, **kw)
    else:
        eng = (HTFLimitEngine if mode == "NEW" else LimitEntryEngineV13)(settings, universe, **kw)
    eng.portfolio.closed_trades = deque(maxlen=None)
    module = old_module() if mode == "OLD" else htf
    cls = module.build(sid, universe, htf=mode != "BASE")
    instances = []
    class Observed(cls):
        def __init__(self):
            super().__init__()
            instances.append(self)
    anchor = None if kind == "single" else cfg.ANCHOR
    result = eng.run(Observed, tape(universe, anchor, start, end), since_ms=since, leverage=20,
                     signal_tf=htf.SIGNAL_TF[sid], only_symbol=coin if kind == "single" else None,
                     reset_at=list(range(since + DAY, end, DAY)))
    trades = [t for t in result.trades if t.entry_ts >= since]
    paid, risk = {}, {}
    for f in result.fills:
        if f.kind == "funding":
            paid[f.position_id] = paid.get(f.position_id, 0.0) + f.realized_pnl
        elif f.kind == "entry":
            risk[f.position_id] = f.qty * abs(f.price - f.meta["stop"])
    return {"sid": sid, "mode": mode, "coin": coin, "since": since, "end": end, "revision": revision(),
            "rejects": result.rejects, "htf_skips": getattr(instances[0], "htf_skips", 0),
            "htf_rejects": getattr(instances[0], "htf_rejects", {}),
            "funding_coverage": {s: {"events": len(v), "first": v[0][0] if v else None,
                                        "last": v[-1][0] if v else None} for s, v in funding.items()},
            "trades": [{"ts": t.entry_ts, "r": (t.net + paid.get(t.position_id, 0.0)) / risk[t.position_id],
                        "net": t.net + paid.get(t.position_id, 0.0), "exit": t.exit_kind,
                        "fees": t.fees, "funding": paid.get(t.position_id, 0.0)} for t in trades]}


def report(rows):
    results, verdicts = {}, {}
    for sid in sorted({r["sid"] for r in rows}):
        part = [r for r in rows if r["sid"] == sid]
        since, end = min(r["since"] for r in part), max(r["end"] for r in part)
        mid = since + (end - since) // 2
        results[sid] = {}
        for mode in ("BASE", "OLD", "NEW"):
            trades = [t for r in part if r["mode"] == mode for t in r["trades"]]
            stats = summarize(trades, mid, (end - since) / DAY)
            gains = sum(max(0, t["net"]) for t in trades)
            losses = -sum(min(0, t["net"]) for t in trades)
            stats.update(net_profit_factor=round(gains / losses, 4) if losses else None,
                         fees_usdt=sum(t["fees"] for t in trades), funding_usdt=sum(t["funding"] for t in trades))
            results[sid][mode] = stats
        new = results[sid]["NEW"]
        old = results[sid]["OLD"]
        helps = all(new.get(k) is not None and old.get(k) is not None and new[k] > old[k]
                    for k in ("net_r_half1", "net_r_half2"))
        verdicts[sid] = "IMPROVES BOTH HALVES VS OLD" if helps else "NO CONSISTENT IMPROVEMENT VS OLD"
    paths = ("app/strategies/v14/htf.py", "app/competition/v14_config.py", "scripts/v14_htf_revision_study.py",
             "app/backtest/replay.py", "app/live/scan_engine.py", "app/live/v8_engine.py", "app/live/v13_engine.py",
             "app/live/v14_engine.py", "scripts/v14_htf_study.py")
    with DB.open("rb") as source:
        dataset_hash = hashlib.file_digest(source, "sha256").hexdigest()
    return {"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "method": __doc__,
            "source_sha256": {p: hashlib.sha256((PROJECT / p).read_bytes()).hexdigest() for p in paths},
            "old_htf_sha256": hashlib.sha256((ARCHIVE / paths[0]).read_bytes()).hexdigest(),
            "dataset": {"path": str(DB.relative_to(PROJECT)), "sha256": dataset_hash},
            "results": results, "verdicts": verdicts, "runs": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--sid", choices=("V14.1", "V14.2", "V14.3", "V14.4"))
    parser.add_argument("--resume", action="store_true", help="Reuse completed BASE/OLD runs and identical NEW revision")
    args = parser.parse_args()
    jobs = [(sid, mode, coin) for sid in ("V14.1", "V14.2", "V14.3", "V14.4") if args.sid in (None, sid)
            for coin in (V8_COINS if sid == "V14.1" else (None,)) for mode in ("BASE", "OLD", "NEW")]
    checkpoint = OUT.with_suffix(".partial.json")
    prior = json.loads(checkpoint.read_text()) if args.resume and checkpoint.exists() else []
    rows = [r for r in prior if (r["sid"], r["mode"], r["coin"]) in jobs
            and (r["mode"] != "NEW" or r.get("revision") == revision())]
    done = {(r["sid"], r["mode"], r["coin"]) for r in rows}
    todo = [job for job in jobs if job not in done]
    started = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(run, job): job for job in todo}
        for future in as_completed(pending):
            rows.append(future.result())
            print(f"{len(rows)}/{len(jobs)} {pending[future]} {len(rows[-1]['trades'])} trades; {time.time()-started:.0f}s", flush=True)
            OUT.with_suffix(".partial.json").write_text(json.dumps(rows))
    out = report(rows)
    target = OUT if not args.sid else OUT.with_name(f"V14_HTF_REVISION_STUDY_{args.sid}.json")
    target.write_text(json.dumps(out, indent=1))
    print(json.dumps({"results": out["results"], "verdicts": out["verdicts"]}, indent=2), flush=True)
    print(f"Wrote {target}; {time.time()-started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
