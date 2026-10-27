"""DEV-only grid (2021-01..2024-10). Never touches TEST. Writes dev_grid.json.
Grid: sig in {A,E,U, A-E,A-U,E-U, A-T,E-T,U-T} x sign {+1 mom, -1 rev} x L {7,14,30,60} x k {5,10} x rebal {1,7}; top-40, LS, 1x.
'T' (total return momentum, sign +1) rows are reference only.
Selection rule (fixed before running, same as round-2 high52):
  candidates = session rows with DEV geo month > 0, DEV daily corr with the Worker 1 book < 0.50 and with plain total-return
  momentum of the same L/k/rebal ('T') < 0.70, DEV max DD <= 40%, DEV net > 0 at doubled costs; pick highest DEV monthly t.
  fallback (only if none): drop the DD filter."""
import json, sys, time, itertools, warnings
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker3/round3/tug_of_war")
from lib import run_cfg, tstat, corr_w1, daily_returns, DEV_START, DEV_END
from pathlib import Path

HERE = Path(__file__).resolve().parent
t0 = time.time()
SIGS = ["A", "E", "U", "A-E", "A-U", "E-U", "A-T", "E-T", "U-T"]
Ls, ks, rbs = (7, 14, 30, 60), (5, 10), (1, 7)
ref = {}
rows = []
for L, k, rb in itertools.product(Ls, ks, rbs):
    r = run_cfg("T", L, k, 1, rb, start=DEV_START, end=DEV_END, record=True)
    ref[(L, k, rb)] = daily_returns(r["_curve"])[1]
    rows.append(dict(sig="T", sign=1, L=L, k=k, rebal=rb, median=r["median_month"], geo=r["geo_month"], mean=r["mean_month"],
                     up=r["months_up"], dd=r["max_dd_pct"], net=r["net_pct"], t=round(tstat(r["months"]), 3),
                     corr_w1=corr_w1(r["_curve"]), corr_T=1.0, ref=True))
for sig, sign, L, k, rb in itertools.product(SIGS, (1, -1), Ls, ks, rbs):
    r = run_cfg(sig, L, k, sign, rb, start=DEV_START, end=DEV_END, record=True)
    _, dr = daily_returns(r["_curve"])
    rows.append(dict(sig=sig, sign=sign, L=L, k=k, rebal=rb, median=r["median_month"], geo=r["geo_month"], mean=r["mean_month"],
                     up=r["months_up"], dd=r["max_dd_pct"], net=r["net_pct"], t=round(tstat(r["months"]), 3),
                     corr_w1=corr_w1(r["_curve"]), corr_T=round(float(np.corrcoef(dr, ref[(L, k, rb)])[0, 1]), 3), ref=False))
print(f"{len(rows)} rows [{time.time()-t0:.0f}s]", flush=True)


def pick(dd_filter):
    c = [x for x in rows if not x["ref"] and x["geo"] > 0 and x["corr_w1"] < 0.5 and x["corr_T"] < 0.7 and (x["dd"] <= 40 or not dd_filter)]
    for x in sorted(c, key=lambda x: -x["t"])[:15]:
        x["net_2xcost"] = run_cfg(x["sig"], x["L"], x["k"], x["sign"], x["rebal"], start=DEV_START, end=DEV_END, cost_mult=2)["net_pct"]
    ok = [x for x in c if x.get("net_2xcost", -1) > 0]
    return max(ok, key=lambda x: x["t"]) if ok else None


sel = pick(True); fb = False
if sel is None:
    sel = pick(False); fb = True
print("SELECTED", sel, "fallback" if fb else "")
srt = sorted([x for x in rows if not x["ref"]], key=lambda x: -x["t"])
print("top 15 session rows by DEV t:")
for x in srt[:15]:
    print(x)
print("reference T rows:")
for x in rows:
    if x["ref"]:
        print(x)
# summary by signal family
for sig in SIGS:
    for sign in (1, -1):
        g = [x["geo"] for x in rows if x["sig"] == sig and x["sign"] == sign]
        print(f"{sig:4s} {sign:+d}  mean DEV geo {np.mean(g):+.2f}  positive {sum(v > 0 for v in g)}/{len(g)}")
(HERE / "dev_grid.json").write_text(json.dumps(dict(rows=rows, selected=sel, fallback_used=fb), indent=1, default=float))
