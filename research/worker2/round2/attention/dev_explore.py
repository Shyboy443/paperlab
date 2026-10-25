"""DEV-only exploration (2021-01..2024-10). Event study + small grid. Never touches TEST."""
import json, numpy as np
from core import *
VR, Z, SG = features(0, 30, 480)
out = {}
# event study: mean per-trade net return (cost 0.14% round trip, no funding) of following the 24h move, by vr bucket / sign / horizon
rows = []
for d in range(d0, dT):
    js = ALT_ELIG[d]; i = d - d0; b = d * 24
    for j in js:
        vr, z = VR[i, j], Z[i, j]
        if not (np.isfinite(vr) and np.isfinite(z)) or not np.isfinite(O[b, j]):
            continue
        f = [PX[b + 24 * h, j] / PX[b, j] - 1 for h in (1, 2, 3, 5)]
        rows.append((vr, z, *f, SG[i, j]))
A = np.array(rows)
print("events", len(A))
for lo, hi in ((0, 1), (1, 2), (2, 3), (3, 5), (5, 8), (8, 1e9)):
    for sgn, nm in ((1, "up"), (-1, "down")):
        m = (A[:, 0] >= lo) & (A[:, 0] < hi) & (np.sign(A[:, 1]) == sgn)
        if m.sum() < 30: continue
        # return of following the move (continuation), vol-normalised and raw
        r = sgn * A[m, 2:6]
        rn = r / (A[m, 6:7] * np.sqrt(24))
        line = f"vr[{lo},{hi}) {nm:4s} n={m.sum():6d} cont raw mean% " + " ".join(f"{x*100:6.2f}" for x in r.mean(0)) + \
               "  med% " + " ".join(f"{x*100:6.2f}" for x in np.median(r, 0)) + "  volunits " + " ".join(f"{x:5.2f}" for x in rn.mean(0))
        print(line)
# also bucket by |z| within vr>=3
print("--- vr>=3 by |z|")
for zlo, zhi in ((0, 1), (1, 2), (2, 3), (3, 99)):
    for sgn, nm in ((1, "up"), (-1, "down")):
        m = (A[:, 0] >= 3) & (np.abs(A[:, 1]) >= zlo) & (np.abs(A[:, 1]) < zhi) & (np.sign(A[:, 1]) == sgn)
        if m.sum() < 30: continue
        r = sgn * A[m, 2:6]; rn = r / (A[m, 6:7] * np.sqrt(24))
        print(f"|z|[{zlo},{zhi}) {nm:4s} n={m.sum():5d} cont mean% " + " ".join(f"{x*100:6.2f}" for x in r.mean(0)) + "  med% " + " ".join(f"{x*100:6.2f}" for x in np.median(r, 0)) + "  vu " + " ".join(f"{x:5.2f}" for x in rn.mean(0)))
