"""Paired follow-up: archived completed-candle V14 vs bug-fixed V14 (2026-10-05).

No parameter search. Replay all nine revised bots on the same 72-day measured
window, with the same fees, recorded funding, sizing, daily resets and execution
as the preceding comparison. Reuse BEFORE rows only after verifying the dataset,
common frozen dependencies, execution profile and instrument rules. The historical
sample has already been studied and is not an independent holdout.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from app.competition import v14_config as cfg
from scripts.v14_htf_revision_study import DB, DAY, V8_COINS, dataset_hash, run, study_signature
from scripts.v14_htf_study import summarize

ARCHIVE = PROJECT / "snapshots/2026-10-05-htf-followup"
OUT = PROJECT / "docs/V14_HTF_FOLLOWUP_STUDY.json"


def controls():
    prior = json.loads((ARCHIVE / "docs/V14_HTF_REVISION_STUDY.json").read_text())
    old = json.loads((ARCHIVE / "docs/V14_FREEZE.json").read_text())
    current = cfg.load_freeze()
    assert cfg.verify_freeze(current) == []
    for key in ("rules", "funding_interval_min", "baseline_fingerprint"):
        assert old[key] == current[key], key
    changed = {"app.strategies.v14.htf", "app.competition.v14_config", "app.live.v14_worker", "app.live.v14_engine"}
    code = cfg.code_fingerprints()
    assert all(code[k] == v for k, v in old["code"].items() if k not in changed)
    for key in ("execution", "params", "universe", "v8_coins", "warmup_days", "starting_balance", "leverage_ceiling", "sizing"):
        assert old["profile"][key] == current["profile"][key], key
    stat = DB.stat()
    assert dataset_hash(str(DB), stat.st_size, stat.st_mtime_ns) == prior["dataset"]["sha256"]
    for path, sha in prior["source_sha256"].items():
        if path.startswith("app/") and path not in (
                "app/strategies/v14/htf.py", "app/competition/v14_config.py", "app/live/v14_engine.py"):
            assert hashlib.sha256((PROJECT / path).read_bytes()).hexdigest() == sha, path
    for path in ("app/strategies/v14/htf.py", "app/competition/v14_config.py", "app/live/v14_engine.py"):
        assert hashlib.sha256((ARCHIVE / path).read_bytes()).hexdigest() == prior["source_sha256"][path]
    return prior


def statistics(rows, sid, mode):
    part = [r for r in rows if r["sid"] == sid]
    since, end = min(r["since"] for r in part), max(r["end"] for r in part)
    trades = [t for r in part if r["mode"] == mode for t in r["trades"]]
    stats = summarize(trades, since + (end - since) // 2, (end - since) / DAY)
    positive = sum(max(0, t["net"]) for t in trades)
    negative = -sum(min(0, t["net"]) for t in trades)
    stats.update(net_profit_factor=positive / negative if negative else None,
                 fees_usdt=sum(t["fees"] for t in trades), funding_usdt=sum(t["funding"] for t in trades))
    return stats


def main():
    prior = controls()
    before = [{**r, "mode": "BEFORE"} for r in prior["runs"] if r["mode"] == "NEW"]
    assert len(before) == 9
    signature = study_signature()
    jobs = [(sid, "NEW", coin, signature) for sid in cfg.SOURCES
            for coin in (V8_COINS if sid == "V14.1" else (None,))]
    rows = list(before)
    started = time.time()
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(run, job): job for job in jobs}
        for future in as_completed(futures):
            result = future.result()
            rows.append({**result, "mode": "AFTER"})
            print(f"{len(rows)-9}/9 {futures[future][:3]} {len(result['trades'])} trades; {time.time()-started:.0f}s", flush=True)
            OUT.with_suffix(".partial.json").write_text(json.dumps(rows))
    if study_signature() != signature:
        raise RuntimeError("Sources or dataset changed; comparison was not published")
    results = {sid: {mode: statistics(rows, sid, mode) for mode in ("BEFORE", "AFTER")} for sid in cfg.SOURCES}
    verdicts = {}
    for sid, arms in results.items():
        a, b = arms["BEFORE"], arms["AFTER"]
        better = all(a.get(k) is not None and b.get(k) is not None and b[k] > a[k]
                     for k in ("net_r_half1", "net_r_half2"))
        verdicts[sid] = "IMPROVES BOTH HALVES VS PRIOR V14" if better else "NO CONSISTENT IMPROVEMENT VS PRIOR V14"
    paths = ("app/strategies/v14/htf.py", "app/live/v14_engine.py", "app/live/v14_worker.py", "app/competition/v14_config.py",
             "scripts/v14_htf_revision_study.py", "scripts/v14_htf_followup_study.py", "scripts/v14_htf_study.py")
    OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                               "method": __doc__, "input_signature": signature, "before_dataset": prior["dataset"],
                               "before_source_sha256": prior["source_sha256"],
                               "after_source_sha256": {p: hashlib.sha256((PROJECT / p).read_bytes()).hexdigest() for p in paths},
                               "results": results, "verdicts": verdicts, "runs": rows}, indent=1) + "\n")
    print(json.dumps({"results": results, "verdicts": verdicts}, indent=2), flush=True)
    print(f"Wrote {OUT}; {time.time()-started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
