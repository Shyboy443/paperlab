"""DEV-only variant scan (every variant printed). Picks are made from this output and frozen in PREREG.md."""
import json
from strategies import *
from engine import backtest
from events import FOMC, CPI

frames, idx = load(SYMS)
BASK = SYMS
res = {}
def run(name, W, L=1.0):
    s = backtest(W, frames, L=L, start=DEV_START, end=DEV_END)
    res[name] = s
    print(f"{name:40s} mean {s['mean_month']:6.2f} med {s['median_month']:6.2f} worst {s['worst_month']:7.2f} "
          f"dd {s['max_dd_pct']:6.2f} net {s['net_pct']:8.2f} tr {s['trades']} liq {s['liquidations']}", flush=True)

for coins, cn in [(["BTCUSDT"], "BTC"), (BASK, "BASK")]:
    for thr in (0.005, 0.01, 0.02):
        for hold in (6, 24, 48):
            run(f"gap {cn} thr{thr} hold{hold}", gap_momentum(frames, idx, coins, thr, hold))
    for b, a in [(4, 1), (24, 1), (4, 4), (1, 1)]:
        run(f"fomc {cn} pre{b} post{a}", event_long(idx, coins, FOMC, 14, 0, b, a))
    for b, a in [(4, 0), (24, 0), (4, 1)]:
        run(f"cpi {cn} pre{b} post{a}", event_long(idx, coins, CPI, 8, 30, b, a))
    run(f"hours21-22 {cn}", hours_long(idx, coins, [21, 22]))
    run(f"buyhold {cn}", hours_long(idx, coins, list(range(24))))
json.dump(res, open("dev_scan.json", "w"), indent=1, default=float)
