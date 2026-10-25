"""DEV-only: the stack and its parts at several leverages (to find what 20%/month needs on DEV)."""
from strategies import *
from engine import backtest
from events import FOMC, CPI
frames, idx = load(SYMS)
gap = gap_momentum(frames, idx, ["BTCUSDT"], 0.01, 24)
fomc = event_long(idx, SYMS, FOMC, 14, 0, 4, 1)
cpi = event_long(idx, SYMS, CPI, 8, 30, 24, 0)
st = stack(gap, fomc, cpi)
for name, W in [("stack", st), ("fomc", fomc)]:
    for L in (1, 2, 3, 5, 8, 12, 20):
        s = backtest(W, frames, L=L, start=DEV_START, end=DEV_END)
        print(f"{name} L{L:2d} mean {s['mean_month']:8.2f} med {s['median_month']:6.2f} worst {s['worst_month']:7.2f} "
              f"dd {s['max_dd_pct']:6.2f} net {s['net_pct']:10.1f} liq {s['liquidations']} top3 {s.get('top3_share_of_profit')}", flush=True)
