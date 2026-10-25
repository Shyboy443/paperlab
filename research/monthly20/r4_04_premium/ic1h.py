"""1h horizon screen on DEV only: gross (pre-cost) next-hour return of the cross-sectional premium fade/follow
portfolio (k=5 long cheapest / k=5 short richest of the top-40 universe), its turnover per hour, and the cost that
turnover implies. If gross < cost the 1h horizon is dead before any backtest. Writes ic1h.json."""
import json, sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import DEV_START, DEV_END, DAY, HOUR, FEE, SLIP, PROJECT
from data import V07
import engine as E, signals as S
from panel import make

D1 = make(24); P1 = E.Panel(D1); U1 = E.universe_daily(P1, 40)
syms = P1.syms
t = np.arange(DEV_START - 30 * DAY, DEV_END, HOUR, dtype=np.int64)
T, N = len(t), len(syms)
c = np.full((T, N), np.nan, np.float32); pm = np.full((T, N), np.nan, np.float32)
for j, s in enumerate(syms):
    a = np.array([r[:5] for r in json.loads((V07 / "1h" / f"{s}.json").read_text())], float)
    a = a[(a[:, 0] >= t[0]) & (a[:, 0] < t[-1] + HOUR)]
    c[((a[:, 0] - t[0]) // HOUR).astype(int), j] = a[:, 4]
    b = json.loads((PROJECT / "data" / "monthly20_r4_premium" / "1h" / f"{s}.json").read_text())
    if b:
        b = np.array(b, float); b = b[(b[:, 0] >= t[0]) & (b[:, 0] < t[-1] + HOUR)]
        pm[((b[:, 0] - t[0]) // HOUR).astype(int), j] = b[:, 4]
di = ((t - P1.t[0]) // DAY).astype(int)
U = U1[di]
ret = np.full_like(c, np.nan); ret[:-1] = c[1:] / c[:-1] - 1  # return over hour i+1 for a position taken at close of i
out = {}
for fam, w, L in [("LEVEL", 1, None), ("LEVEL", 4, None), ("Z", 1, 168), ("Z", 4, 168), ("MOM", 1, None), ("MOM", 4, None)]:
    Pm = type("X", (), {})(); Pm.pm = pm.astype(float)
    sc = S.score(Pm, fam, w, L)
    sc = np.full_like(sc, np.nan); sc[:-1] = S.score(Pm, fam, w, L)[1:]  # undo shift: score known at close of hour i
    i0 = int(np.searchsorted(t, DEV_START))
    W = S.xs_weights(sc[i0:], U[i0:], 5, "fade")
    g = np.nansum(W * np.nan_to_num(ret[i0:]), 1)
    turn = np.abs(np.diff(W, axis=0)).sum(1)
    cost = turn.mean() * (FEE + SLIP)
    out[f"{fam}_w{w}_L{L}"] = dict(gross_fade_bp_per_hour=round(g.mean() * 1e4, 3), turnover_per_hour=round(turn.mean(), 3),
                                    cost_bp_per_hour=round(cost * 1e4, 3), net_bp_per_hour=round((g.mean() - cost) * 1e4, 3))
    print(fam, w, out[f"{fam}_w{w}_L{L}"], flush=True)
(HERE / "ic1h.json").write_text(json.dumps(out, indent=1))
