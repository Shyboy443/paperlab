"""V11 activity + fidelity check: the V11 scanner strategies through the LIVE engine on the study's 90 days.

    python scripts/v11_activity_check.py [--days 90] [--family V11.1]

The same ReplayEngine, execution (fills at the 1m open after the 60 s window), Bybit fees, AGGRESSIVE_V6 legal sizing
and the V11 1000 USDT books the forward bots use, fed the cached 1m bars of the 30-coin universe (data/v11_scan,
scripts/v11_scan_data.py) as ONE tape in the live feed's order: each minute every coin, BTCUSDT (the anchor) last.
Halts are off and every book is reset to 1000 USDT at each UTC midnight, so every family trades the whole window (a
losing book would otherwise stop early and hide the rest); results are in R per trade.

It answers two questions before anything is frozen: how often each scanner trades (activity), and whether the engine
reproduces the vectorised study (docs/V11_SCAN_STUDY.json) closely enough to trust it (fidelity: same order of trades
per day and R per trade). The observed half-spread is not known historically: the engine models it.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

DATA = PROJECT / "data" / "v11_scan"
MIN = 60_000
DAY = 86_400_000


def tape(syms: list[str], anchor: str, start: int, end: int):
    """1m Candles minute by minute, every coin in a fixed order with the anchor last (the V11 feed's order)."""
    from app.core.types import Candle
    order = sorted(s for s in syms if s != anchor) + [anchor]
    arr = {}
    for s in order:
        a = np.load(DATA / f"{s}.npy")
        a = a[(a[:, 0] >= start) & (a[:, 0] < end)]
        arr[s] = (a, 0)
    idx = {s: 0 for s in order}
    for m in range(start, end, MIN):
        for s in order:
            a = arr[s][0]
            i = idx[s]
            if i < len(a) and int(a[i, 0]) == m:
                r = a[i]
                idx[s] = i + 1
                yield Candle(s, "1m", m, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), m + MIN - 1,
                             True, float(r[6]), 0, "historical", 0.0)


def run_family(args: tuple) -> dict:
    fid, days = args
    from app.backtest.replay import ReplayEngine
    from app.competition import v11_config as v11
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6
    from app.core.types import MarketRules
    from app.strategies.v11.scan import ANCHOR, load_v11_scanners
    uni = json.loads((DATA / "universe.json").read_text())
    syms = [u["symbol"] for u in uni["coins"]]
    end = int(uni["end_ms"])
    start = end - (days + v11.WARMUP_DAYS) * DAY
    since = start + v11.WARMUP_DAYS * DAY
    rules = {u["symbol"]: MarketRules(u["symbol"], float(u["rules"]["tick"]), float(u["rules"]["step"]),
                                      float(u["rules"]["min_qty"]), float(u["rules"]["min_notional"]))
             for u in uni["coins"]}
    settings = dataclasses.replace(v11.settings_v11(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
    cls = load_v11_scanners()[fid].for_universe(syms)
    eng = ReplayEngine(settings, syms, rules=rules, seed=7, execution=v11.EXECUTION_V11, fees=FEES_V6,
                       fee_source="schedule", sizing=SizingV6(rules, jev=False), leverage_policy="needed",
                       max_risk_pct=AGGRESSIVE_V6.max_risk_pct, gate=None, cost_gate=None)
    from collections import deque
    eng.portfolio.closed_trades = deque(maxlen=None)       # the portfolio keeps 5000 by default; count them all
    t0 = time.time()
    res = eng.run(cls, tape(syms, ANCHOR, start, end), since_ms=since, leverage=v11.LEVERAGE_CEILING,
                  signal_tf=cls.signal_tf, only_symbol=None, reset_at=list(range(since + DAY, end, DAY)))
    mid = since + (end - since) // 2
    out = {"family": fid, "name": cls.name, "days": days, "elapsed_s": round(time.time() - t0, 1),
           "signals": res.signals, "rejects": dict(sorted(res.rejects.items(), key=lambda kv: -kv[1])[:8])}
    for name, lo, hi in (("discovery", since, mid), ("confirmation", mid, end), ("all", since, end)):
        tr = [t for t in res.trades if lo <= t.entry_ts < hi and t.exit_kind not in ("end_of_data", "reset")]
        r = np.array([t.r_multiple for t in tr]) if tr else np.zeros(0)
        net = sum(t.net for t in tr)
        pos, neg = r[r > 0].sum(), -r[r < 0].sum()
        span = (hi - lo) / DAY
        out[name] = {"trades": len(tr), "per_day": round(len(tr) / span, 2), "win": round(float((r > 0).mean()), 3) if len(r) else None,
                     "net_r": round(float(r.mean()), 4) if len(r) else None,
                     "pf_r": round(float(pos / neg), 3) if neg > 0 else None, "net_usdt": round(net, 2),
                     "coins": len({t.symbol for t in tr})}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--family", action="append")
    args = ap.parse_args()
    from app.strategies.v11.scan import load_v11_scanners
    fams = args.family or list(load_v11_scanners())
    with ProcessPoolExecutor(len(fams)) as ex:
        rows = list(ex.map(run_family, [(f, args.days) for f in fams]))
    for r in rows:
        a, d, c = r["all"], r["discovery"], r["confirmation"]
        print(f"{r['family']} {r['name'][:34]:34s} {a['trades']:5d} trades {a['per_day']:5.1f}/d win {a['win']} "
              f"R/trade {a['net_r']} (disc {d['net_r']} | conf {c['net_r']}) pf {a['pf_r']} coins {a['coins']} "
              f"[{r['elapsed_s']} s] rejects {r['rejects']}", flush=True)
    if not args.family:
        (PROJECT / "docs" / "V11_ACTIVITY_CHECK.json").write_text(json.dumps({"days": args.days, "families": rows}, indent=1))


if __name__ == "__main__":
    main()
