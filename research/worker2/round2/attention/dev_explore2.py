"""DEV-only: excess (vs same-day top-40 alt equal-weight) forward returns after volume shocks."""
import numpy as np
from core import *
VR, Z, SG = features(0, 30, 480)
rows = []
for d in range(d0, dT):
    js = ALT_ELIG[d]; i = d - d0; b = d * 24
    js = js[np.isfinite(O[b, js])]
    F = np.array([PX[b + 24 * h, js] / PX[b, js] - 1 for h in (1, 2, 3, 5)]).T
    mu = np.nanmean(F, 0)
    for k, j in enumerate(js):
        if np.isfinite(VR[i, j]) and np.isfinite(Z[i, j]):
            rows.append((VR[i, j], Z[i, j], *(F[k] - mu), *F[k], d))
A = np.array(rows)
yr = np.array([str(np.datetime64(int(td[int(x)]), 'ms'))[:4] for x in A[:, -1]])
def show(m, lab):
    r = A[m, 2:6]
    print(f"{lab:28s} n={m.sum():5d} excess mean% " + " ".join(f"{x*100:6.2f}" for x in r.mean(0)) + "  med% " + " ".join(f"{x*100:6.2f}" for x in np.median(r, 0)) +
          f"  raw5d mean {A[m, 9].mean()*100:.2f}" + "  t5=%.1f" % (r[:, 3].mean() / r[:, 3].std() * np.sqrt(m.sum())))
for thr in (2, 3, 5, 8):
    for sgn, nm in ((1, "up"), (-1, "down"), (0, "any")):
        m = (A[:, 0] >= thr) & ((np.sign(A[:, 1]) == sgn) if sgn else True)
        show(m, f"vr>={thr} {nm}")
print("--- vr>=3 any, by year")
for y in sorted(set(yr)):
    for sgn, nm in ((1, "up"), (-1, "down")):
        show((A[:, 0] >= 3) & (yr == y) & (np.sign(A[:, 1]) == sgn), f"{y} vr>=3 {nm}")
print("--- low volume vr<0.5")
show(A[:, 0] < 0.5, "vr<0.5")
