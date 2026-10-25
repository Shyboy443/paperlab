"""Diagnostic: spot ML leg P&L by coin / side (episodes), 2021-H1 and 2018-2020."""
import sys, json
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as RB
from run import ens, utc
D = ens.Data()
F = ens.leg_ml(D, 3, 0); leg = ("M", F, ens.trade_mask(D, F, "M"))
for a, b in ((utc(2021,1,1), utc(2021,7,1)), (utc(2018,1,1), utc(2021,1,1))):
    r = ens.simulate(D, [leg], a, b, record=True)
    by = {}; side = {1: 0.0, -1: 0.0}
    i0 = D.idx(a)
    for j, s, e, p in r["episodes"]:
        by[D.syms[j]] = by.get(D.syms[j], 0) + p
        side[int(np.sign(F[s, j])) or 1] += p
    tot = sum(by.values())
    print(a, "total", round(tot), "long/short", {k: round(v) for k, v in side.items()}, "n_eps", len(r["episodes"]))
    print(" top", [(k, round(v)) for k, v in sorted(by.items(), key=lambda x: -x[1])[:8]])
    print(" bottom", [(k, round(v)) for k, v in sorted(by.items(), key=lambda x: x[1])[:5]])
