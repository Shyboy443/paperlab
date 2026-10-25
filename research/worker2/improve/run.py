"""Worker 2 improvement round: variants R (residual momentum) and L (no shorts vs BTC uptrend), PREREG.md.
Re-uses session/run.py's windows() and all_checks() unchanged (exec'd up to its main block)."""
import json
import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker2/session")
import core
from core import *  # noqa

SRC = open(r"D:/Projects/BinanceBOt/paperlab/research/worker2/session/run.py", encoding="utf-8").read()
exec(SRC[:SRC.index("\nA = windows(P)")])           # defines P, windows, all_checks, check7, pick, KEYS

LR = np.diff(np.log(O), axis=0)                       # LR[t] = log(O[t+1]/O[t])
_res = {}


def resid_stats(d, W):
    """beta to BTC and residual hourly vol for every coin, from LR[b-W-1 .. b-2] (same bars as hvol)."""
    if (d, W) not in _res:
        b = d * 24
        X = LR[b - W - 1:b - 1]
        xb = X[:, BTC]
        ok = np.isfinite(X) & np.isfinite(xb)[:, None]
        n = ok.sum(0)
        xm = np.where(ok, X, 0); bm = np.where(ok, xb[:, None], 0)
        mx, mb = xm.sum(0) / np.maximum(n, 1), bm.sum(0) / np.maximum(n, 1)
        cov = (np.where(ok, (xm - mx) * (bm - mb), 0)).sum(0) / np.maximum(n - 1, 1)
        vb = (np.where(ok, (bm - mb) ** 2, 0)).sum(0) / np.maximum(n - 1, 1)
        beta = cov / np.where(vb > 0, vb, np.nan)
        e = np.where(ok, (xm - mx) - beta * (bm - mb), 0)
        sres = np.sqrt((e ** 2).sum(0) / np.maximum(n - 2, 1))
        bad = n <= 0.8 * W
        beta[bad] = np.nan; sres[bad] = np.nan
        _res[(d, W)] = (beta, sres)
    return _res[(d, W)]


_orig_signals = core.signals


def signals(p, d_from, d_to, exclude=()):
    v = p.get("variant")
    if v is None:
        return _orig_signals(p, d_from, d_to, exclude)
    if v == "L":
        rows = _orig_signals(p, d_from, d_to, exclude)
        return [r for r in rows if not (r[4] < 0 and np.log(O[r[0] * 24, BTC] / O[(r[0] - 30) * 24, BTC]) > 0)]
    H_, k, X, W = p["H"], p["k"], p["X"], p["W"]
    V = hv(W)
    rows = []
    for d in range(d_from, d_to):
        b = d * 24
        if b + X >= len(th):
            break
        js = ALT_ELIG[d]
        if len(exclude):
            js = np.array([j for j in js if j not in exclude], dtype=int)
        if len(js) == 0:
            continue
        beta, sres = resid_stats(d, W)
        with np.errstate(invalid="ignore", divide="ignore"):
            rb = np.log(C[b + H_ - 1, BTC] / O[b, BTC])
            res = np.log(C[b + H_ - 1, js] / O[b, js]) - beta[js] * rb
            z = res / (sres[js] * np.sqrt(H_))
        ok = np.isfinite(z) & (np.abs(z) >= k) & np.isfinite(O[b + H_, js]) & np.isfinite(V[d - d0, js])
        for j, zz in zip(js[ok], z[ok]):
            rows.append((d, b + H_, b + X, int(j), float(np.sign(zz)), size_mult(p, V[d - d0, j])))
    return rows


core.signals = signals                                 # sim() and the checks now see the variants

OUT = {}
for name in ("R", "L"):
    q = dict(P, variant=name)
    A = windows(q)
    chk = all_checks(q, A)
    OUT[name] = {"windows": {k: pick(v) for k, v in A.items()}, "years_1x": yearly(A["full"]["months"]),
                 "monthly_1x": A["full"]["months"], "checks": chk, "checks_passed": int(sum(x["pass"] for x in chk.values()))}
    print(name, "checks", OUT[name]["checks_passed"], {k: v["pass"] for k, v in chk.items()})
    for w in ("dev", "test", "full", "y2025_26_double_cost"):
        print("  ", w, pick(A[w]))
    print("   years", OUT[name]["years_1x"])
json.dump(OUT, open("results.json", "w"), indent=1, default=float)
