"""DEV-only grid (2021-01..2024-10). Never touches TEST. Writes dev_grid.json.
Grid: stat {skew, kurt} x W {7, 14, 28} days x k {5, 10} x univ {20, 40}, weekly rebalance, 1x LS; each in the
literature sign ('lit') and the reverse sign ('rev', reference only, NOT selectable).
Selection rule (fixed before running): among 'lit' rows with DEV geo month > 0, DEV net > 0 at doubled costs and DEV
max DD <= 40%, pick the highest DEV monthly t-stat. If none qualifies, the family is a DEV FAIL (still run the
best-t 'lit' row on TEST once, labelled as such)."""
import json, sys, time, itertools
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker3/round3/realized_skew")
from rs import run_cfg, tstat, corr_w1, daily_returns, DEV_START, DEV_END, moments, max_scores, um, P
from pathlib import Path

HERE = Path(__file__).resolve().parent
t0 = time.time()
rows = []
for stat, W, k, univ, sign in itertools.product(("skew", "kurt"), (7, 14, 28), (5, 10), (20, 40), ("lit", "rev")):
    r = run_cfg(stat, W, k, 7, univ, sign, start=DEV_START, end=DEV_END, record=True)
    row = dict(stat=stat, W=W, k=k, univ=univ, sign=sign, median=r["median_month"], geo=r["geo_month"], mean=r["mean_month"],
               up=r["months_up"], n=r["n_months"], dd=r["max_dd_pct"], net=r["net_pct"], t=round(tstat(r["months"]), 3),
               trades=r["trades"], corr_w1=corr_w1(r["_curve"]))
    rows.append(row)
    print(row, f"[{time.time()-t0:.0f}s]", flush=True)

c = [x for x in rows if x["sign"] == "lit" and x["geo"] > 0 and x["dd"] <= 40]
for x in c:
    x["net_2xcost"] = run_cfg(x["stat"], x["W"], x["k"], 7, x["univ"], "lit", start=DEV_START, end=DEV_END, cost_mult=2)["net_pct"]
ok = [x for x in c if x["net_2xcost"] > 0]
sel = max(ok, key=lambda x: x["t"]) if ok else None
dev_fail = sel is None
if sel is None:
    sel = max([x for x in rows if x["sign"] == "lit"], key=lambda x: x["t"])
print("SELECTED", sel, "DEV-FAIL" if dev_fail else "")

# overlap with MAX: mean weekly cross-sectional Spearman rank corr inside the top-40 (DEV weekly dates)
def rank(v):
    o = np.argsort(np.argsort(v)); return o.astype(float)
u40 = um(40)
ov = {}
i0, i1 = int(np.searchsorted(P.t, DEV_START)), int(np.searchsorted(P.t, DEV_END))
for W in (7, 14, 28):
    mh, md = max_scores(W)
    sk = moments(W)["skew"]; ku = moments(W)["kurt"]
    acc = {"skew_vs_MAXh": [], "skew_vs_MAXd": [], "kurt_vs_MAXh": [], "skew_vs_kurt": []}
    for d in range(i0, i1, 7):
        m = u40[d] & ~np.isnan(sk[d]) & ~np.isnan(mh[d]) & ~np.isnan(md[d])
        if m.sum() < 10:
            continue
        for key, a, b in (("skew_vs_MAXh", sk, mh), ("skew_vs_MAXd", sk, md), ("kurt_vs_MAXh", ku, mh), ("skew_vs_kurt", sk, ku)):
            acc[key].append(np.corrcoef(rank(a[d][m]), rank(b[d][m]))[0, 1])
    ov[W] = {k: round(float(np.mean(v)), 3) for k, v in acc.items()}
print("overlap", ov)
(HERE / "dev_grid.json").write_text(json.dumps(dict(rows=rows, selected=sel, dev_fail=dev_fail, max_overlap_dev=ov), indent=1, default=float))
print(f"done [{time.time()-t0:.0f}s]")
