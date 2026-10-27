"""Build aligned daily return series 2021-01-01..2026-10-21 (UTC days) for the sleeves:
  W1  = Worker 1 r4_07 ensemble daily book at 1x (research/monthly20/r4_07_ensemble/_rbook_full_1x.npy)
  W2  = Worker 2 rule P (vol-scaled session momentum) at 1x, no 1%-volume cap (small account), as in worker2/session/run.py
  BTC = BTCUSDT perp price + funding (daily panel), used for the optional BTC trend sleeve (signal built in alloc.py)
Writes data/w3r1_meta_allocator/sleeves.npz."""
import sys, os
import numpy as np
from datetime import datetime, timezone

sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker2/session")
os.chdir(r"D:/Projects/BinanceBOt/paperlab/research/worker2/session")
from core import *  # noqa

OUT = r"D:/Projects/BinanceBOt/paperlab/data/w3r1_meta_allocator"
os.makedirs(OUT, exist_ok=True)

P = dict(SEL, sig_ref=0.01154)
c, tr, _ = run(signals(P, d0, d1), d0, d1, 5, liq_cap=False)
eq = np.array([e for _, e in c])
rP = eq[1:] / eq[:-1] - 1
w2 = {int(td[d]): rP[i] for i, d in enumerate(range(d0, d0 + len(rP)))}
c2, _, _ = run(signals(P, d0, d1), d0, d1, 5, liq_cap=False, cost_mult=2.0)
eq2 = np.array([e for _, e in c2])
rP2 = eq2[1:] / eq2[:-1] - 1
w2x = {int(td[d]): rP2[i] for i, d in enumerate(range(d0, d0 + len(rP2)))}
# trades per day for W2 (for reporting)
ntr = {}
for t in tr:
    ntr[int(td[t[0]])] = ntr.get(int(td[t[0]]), 0) + 1

z = np.load(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r2_trend/panel.npz")
day = z["day"].astype(np.int64)
rb = np.load(r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble/_rbook_full_1x.npy")
b = list(z["syms"]).index("BTCUSDT")
btcC, btcF = z["C"][:, b], np.nan_to_num(z["FUND"][:, b])

keep = [i for i, t in enumerate(day) if np.isfinite(rb[i]) and int(t) in w2]
days = day[keep]
r1 = rb[keep]
r2 = np.array([w2[int(t)] for t in days])
r2x = np.array([w2x[int(t)] for t in days])
n2 = np.array([ntr.get(int(t), 0) for t in days])
i0 = keep[0]
np.savez(OUT + "/sleeves.npz", day=days, w1=r1, w2=r2, w2_2x=r2x, w2_trades=n2,
         btc_close_full=btcC, btc_fund_full=btcF, day_full=day, first_idx=i0)
f = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")
print(len(days), f(days[0]), f(days[-1]), "contiguous:", bool(np.all(np.diff(days) == 86400000)),
      "W1 sum", r1.sum(), "W2 sum", r2.sum(), "corr", np.corrcoef(r1, r2)[0, 1])
