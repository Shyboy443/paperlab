"""V11 time-stop study (operator, 2026-10-01: "don't the hard-coded trade close timers make many trades lose or close
before the profits?").

Live, 47% of V11 trades end on the time stop (avg -0.02 R): roughly flat, minus fees. Would they have reached a TP if held
longer, or the stop? Same entries and exits as live (each scanner's 25/50/25 ladder, after-TP1 lock, level fills, gap
check), 90 days, 20 USDT books, halts off, books reset daily -- only the time stop changes:

    X1    the live hold (V11.1 / V11.2 4 h, V11.3 2 h, V11.4 58 min)
    X2    twice as long        X4    four times as long        NONE    no time stop (stop / TPs / the daily reset)

The daily reset closes whatever is open at midnight UTC; for this study those exits COUNT (they are where a long hold
ends), and their number is reported.

DECISION RULE (fixed before running): per scanner, a longer hold replaces X1 only if its net R per trade beats X1's in
BOTH halves AND its profit factor is at least X1's; if several do, the best net R per trade over the whole window wins.

    python scripts/v11_hold_study.py
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))
DAY = 86_400_000
HOLDS = {"X1": 1.0, "X2": 2.0, "X4": 4.0, "NONE": None}
WORKERS = int(os.environ.get("V11_STUDY_WORKERS", "6"))


def run(args: tuple) -> dict:
    fid, hold = args
    from app.competition import v11_config as v11
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6
    from app.core.types import MarketRules
    from app.live.scan_engine import BE_COVER_BPS
    from app.strategies.v11.ladder import LOCK_AFTER_TP1, SHARES
    from app.strategies.v11.scan import ANCHOR
    from v11_activity_check import DATA, tape
    from v11_tp1_study import study_classes

    Study, Engine = study_classes(fid, SHARES[fid], LOCK_AFTER_TP1[fid], None, True)
    mult = HOLDS[hold]

    class Held(Study):
        def _signal(self, ctx, x, t):
            s = super()._signal(ctx, x, t)
            if s is not None and s.max_hold_s:
                s.max_hold_s = None if mult is None else int(s.max_hold_s * mult)
            return s

    uni = json.loads((DATA / "universe.json").read_text())
    syms = [u["symbol"] for u in uni["coins"]]
    end = int(uni["end_ms"])
    start = end - 95 * DAY
    since = start + 5 * DAY
    rules = {s: MarketRules(**r) for s, r in v11.load_freeze()["rules"].items()}
    settings = dataclasses.replace(v11.settings_v11(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
    k = Held.for_universe(syms)
    eng = Engine(settings, syms, rules=rules, seed=7, execution=v11.EXECUTION_V11, fees=FEES_V6, fee_source="schedule",
                 sizing=SizingV6(rules, jev=False), leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct,
                 be_cover_bps=BE_COVER_BPS)
    eng.portfolio.closed_trades = deque(maxlen=None)
    res = eng.run(k, tape(syms, ANCHOR, start, end), since_ms=since, leverage=v11.LEVERAGE_CEILING,
                  signal_tf=k.signal_tf, only_symbol=None, reset_at=list(range(since + DAY, end, DAY)))
    mid = since + (end - since) // 2
    out: dict = {"family": fid, "hold": hold}
    for name, lo, hi in (("half1", since, mid), ("half2", mid, end), ("all", since, end)):
        tr = [t for t in res.trades if lo <= t.entry_ts < hi and t.exit_kind != "end_of_run"]
        r = np.array([t.r_multiple for t in tr]) if tr else np.zeros(0)
        pos_, neg = r[r > 0].sum(), -r[r < 0].sum()
        out[name] = {"trades": len(tr), "per_day": round(len(tr) / ((hi - lo) / DAY), 1),
                     "win": round(float((r > 0).mean()), 3) if len(r) else None,
                     "net_r": round(float(r.mean()), 4) if len(r) else None,
                     "pf": round(float(pos_ / neg), 3) if neg > 0 else None,
                     "exits": dict(Counter(t.exit_kind for t in tr)),
                     "hold_min_median": round(float(np.median([t.hold_s / 60 for t in tr])), 1) if tr else None}
    return out


def main() -> None:
    fams = ("V11.3", "V11.1", "V11.2", "V11.4")
    jobs = [(f, h) for f in fams for h in HOLDS]
    with ProcessPoolExecutor(WORKERS) as ex:
        rows = list(ex.map(run, jobs))
    chosen = {}
    for f in sorted(fams):
        mine = [r for r in rows if r["family"] == f]
        base = next(r for r in mine if r["hold"] == "X1")
        ok = [r for r in mine if r is not base and r["half1"]["net_r"] > base["half1"]["net_r"]
              and r["half2"]["net_r"] > base["half2"]["net_r"] and (r["all"]["pf"] or 0) >= (base["all"]["pf"] or 0)]
        win = max(ok, key=lambda r: r["all"]["net_r"]) if ok else base
        chosen[f] = win["hold"]
        for r in mine:
            a = r["all"]
            print(f"{f} {r['hold']:4s} {a['per_day']:5.1f}/d win {a['win']} net R h1 {r['half1']['net_r']} | h2 "
                  f"{r['half2']['net_r']} | all {a['net_r']} pf {a['pf']} | median hold {a['hold_min_median']} min | "
                  f"exits {a['exits']} {'<= chosen' if r is win else ''}", flush=True)
    print("chosen:", chosen)
    (PROJECT / "docs" / "V11_HOLD_STUDY.json").write_text(json.dumps({"holds": HOLDS, "runs": rows, "chosen": chosen},
                                                                      indent=1))


if __name__ == "__main__":
    main()
