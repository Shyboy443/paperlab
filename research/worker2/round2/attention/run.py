"""Worker 2 round 2 / attention: the ONE pre-registered config (PREREG.md) through the 8 checks + extras.
Runs TEST once. Writes results.json."""
import json, warnings
import numpy as np
from datetime import datetime, timezone
warnings.filterwarnings("ignore")
from core import *
from common import utc

SIGREF = 0.013290511904605978  # DEV median, dev_grid.json
P = dict(hoff=0, Lb=30, W=480, thr=3.0, zmin=1.5, X=3, K=3, mode="mom", sig_ref=SIGREF)
KEYS = ("median_month", "geo_month", "mean_month", "worst_month", "best_month", "max_dd_pct", "months_up", "n_months", "net_pct", "trades", "t_stat")
pick = lambda s: {k: s.get(k) for k in KEYS}
d25, d26, dOct = dayidx(utc(2025, 1, 1)), dayidx(utc(2026, 1, 1)), dayidx(utc(2026, 10, 1))
OUT = {"params": P}

A = {}
for tag, (x, y) in (("dev", (d0, dT)), ("test", (dT, d1)), ("full", (d0, d1)), ("y2025_26", (d25, d1)),
                    ("ytd2026", (d26, d1)), ("oct2026", (dOct, d1))):
    for cm, sfx in ((1, ""), (2, "_double_cost")):
        c, tr, _ = sim(P, x, y, cost_mult=cm)
        A[tag + sfx] = stats(c, tr)
OUT["windows"] = {k: pick(v) for k, v in A.items()}
full = A["full"]; months = full["months"]
v = np.array(list(months.values()))
OUT["years_1x"] = yearly(months); OUT["years_2x_cost"] = yearly(A["full_double_cost"]["months"])
OUT["monthly_1x"] = months
for k in ("dev", "test", "full"):
    OUT["windows"][k]["trades_per_month"] = round(A[k]["trades"] / A[k]["n_months"], 1)

chk = {}
c1 = {k: A[k]["net_pct"] for k in ("dev", "dev_double_cost", "test", "test_double_cost")}
chk[1] = {"pass": all(x > 0 for x in c1.values()), "numbers": c1}
yr = OUT["years_1x"]
m22 = np.prod([1 + x / 100 for k, x in months.items() if k >= "2022-01"]) - 1
chk[2] = {"pass": bool(sum(x > 0 for x in yr.values()) >= 4 and m22 > 0), "numbers": {"years": yr, "2022-01..2026-10_pct": round(m22 * 100, 2)}}
rng = np.random.default_rng(7)
nb_ = int(np.ceil(len(v) / 3)); boots = []
for _ in range(5000):
    st = rng.integers(0, len(v) - 2, nb_)
    boots.append(np.concatenate([v[s:s + 3] for s in st])[:len(v)].mean())
p5 = float(np.percentile(boots, 5)); tstat = float(v.mean() / v.std(ddof=1) * np.sqrt(len(v)))
chk[3] = {"pass": tstat >= 2 and p5 > 0, "numbers": {"t_stat": round(tstat, 2), "boot_p5_mean_month": round(p5, 2), "n_months": len(v)}}
g4 = np.prod([1 + x / 100 for x in sorted(v)[:-3]]) - 1
chk[4] = {"pass": bool(g4 > 0), "numbers": {"net_without_3_best_pct": round(float(g4) * 100, 2), "best3": [float(x) for x in sorted(v)[-3:]]}}

grid = [("thr", x) for x in (1.5, 2.25, 3.75, 4.5)] + [("zmin", x) for x in (0.75, 1.125, 1.875, 2.25)] + \
       [("X", x) for x in (1.5, 2.25, 3.75, 4.5)] + [("K", x) for x in (2, 4, 5)] + [("Lb", x) for x in (15, 22, 38, 45)] + \
       [("W", x) for x in (240, 360, 600, 720)] + [("hoff", x) for x in (6, 12, 18)]
nbr = []
for n, x in grid:
    q = dict(P, **{n: x})
    sd = stats(*sim(q, d0, dT)[:2]); st_ = stats(*sim(q, dT, d1)[:2])
    nbr.append({"name": f"{n}={x}", "dev_net": sd["net_pct"], "test_net": st_["net_pct"], "dev_median": sd["median_month"],
                "test_median": st_["median_month"], "dev_geo": sd["geo_month"], "test_geo": st_["geo_month"]})
    print("nb", nbr[-1], flush=True)
share = sum(x["dev_net"] > 0 and x["test_net"] > 0 for x in nbr) / len(nbr)
chk[5] = {"pass": bool(share >= 0.7), "numbers": {"share_positive_both": round(share, 3), "grid": nbr}}

c, tr, rows = sim(P, d0, d1)
strat_net = summary(c)["net_pct"]
VR_, Z_, SG_ = features(P["hoff"], P["Lb"], P["W"])
by = {}
for r in rows:
    by.setdefault(r[0], []).append(r)
rand = []
for rep in range(200):
    rr = []
    for d, rs in by.items():
        pool = [j for j in ALT_ELIG[d] if np.isfinite(O[rs[0][1], j])]
        pk = rng.choice(pool, size=min(len(rs), len(pool)), replace=False)
        rr += [(d, r[1], r[2], int(j), r[4], size_mult(SIGREF, SG_[d - d0, j])) for r, j in zip(rs, pk)]
    rand.append(summary(run(rr, d0, d1, P)[0])["net_pct"])
pct = float(np.mean(np.array(rand) < strat_net))
chk[6] = {"pass": pct >= 0.95, "numbers": {"strategy_net_pct": strat_net, "beats_share": round(pct, 3),
          "random_median_net_pct": round(float(np.median(rand)), 2), "random_p95_net_pct": round(float(np.percentile(rand, 95)), 2)}}

pc = {}
for t in tr:
    pc[t[1]] = pc.get(t[1], 0) + t[3]
top5 = [j for j, _ in sorted(pc.items(), key=lambda x: -x[1])[:5]]
net7 = summary(sim(P, d0, d1, exclude=set(top5))[0])["net_pct"]
total = c[-1][1] - c[0][1]
i = int(np.argmax([t[3] for t in tr]))
sh = tr[i][3] / total
lg = np.array([np.log1p(t[4]) for t in tr])
chk[7] = {"pass": bool(net7 > 0 and 0 < sh <= 0.10), "numbers": {
    "top5_coins": [syms[j] for j in top5], "net_without_top5_pct": net7, "max_single_position_share_of_profit": round(float(sh), 4),
    "max_position": {"coin": syms[tr[i][1]], "day": datetime.fromtimestamp(td[tr[i][0]] / 1000, timezone.utc).strftime("%Y-%m-%d"),
                     "pct_of_equity": round(tr[i][4] * 100, 2), "ret_pct": round(tr[i][6] * 100, 2)},
    "info_max_share_of_log_growth": round(float(lg.max() / lg.sum()), 4)}}
chk[8] = {"pass": bool(full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"]),
          "numbers": {"geo_month": full["geo_month"], "mean_month": full["mean_month"]}}
OUT["checks"] = {str(k): x for k, x in chk.items()}
OUT["checks_passed"] = int(sum(x["pass"] for x in chk.values()))
OUT["robust"] = OUT["checks_passed"] == 8

# trade mix and 500 USDT feasibility
sz = np.array([t[5] for t in tr])
OUT["trades_info"] = {"n": len(tr), "longs": int(sum(t[2] > 0 for t in tr)), "shorts": int(sum(t[2] < 0 for t in tr)),
                      "size_pct_equity_min": round(float(sz.min() * 100), 2), "size_pct_equity_p5": round(float(np.percentile(sz, 5) * 100), 2),
                      "size_pct_equity_median": round(float(np.median(sz) * 100), 2),
                      "smallest_order_usdt_at_500": round(float(sz.min() * 500), 2),
                      "share_orders_below_5usdt_at_500": round(float(np.mean(sz * 500 < 5)), 4),
                      "avg_gross_exposure": round(float(sz.sum() * P["X"] / (d1 - d0)), 3)}

# Worker 1 correlation
W1 = r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble/"
w1m = json.load(open(W1 + "results.json"))["monthly_1x"]
rb = np.load(W1 + "_rbook_full_1x.npy")
w1day = np.load(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r2_trend/panel.npz")["day"].astype(np.int64)
eq = np.array([e for _, e in c])
rP = eq[1:] / eq[:-1] - 1
dP = {int(td[d]): rP[k] for k, d in enumerate(range(d0, d0 + len(rP)))}
w1d = {int(t): r for t, r in zip(w1day, rb) if np.isfinite(r)}
cd = sorted(set(dP) & set(w1d))
a = np.array([dP[t] for t in cd]); b = np.array([w1d[t] for t in cd])
mk = np.array([datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m") for t in cd])
corr = {}
ms = sorted(set(months) & set(w1m))
for lab, lo in (("full_2021_2026", "2021-01"), ("2022_2026", "2022-01"), ("test", "2024-11")):
    mm = [m for m in ms if m >= lo]
    corr["monthly_" + lab] = round(float(np.corrcoef([months[m] for m in mm], [w1m[m] for m in mm])[0, 1]), 3)
corr["daily_full"] = round(float(np.corrcoef(a, b)[0, 1]), 3)
corr["daily_2022_2026"] = round(float(np.corrcoef(a[mk >= "2022-01"], b[mk >= "2022-01"])[0, 1]), 3)
OUT["worker1_corr"] = corr
json.dump(OUT, open("results.json", "w"), indent=1, default=float)

for k, x in OUT["windows"].items():
    print(k, x)
print("years", OUT["years_1x"], "2x", OUT["years_2x_cost"])
for k, x in OUT["checks"].items():
    print("check", k, x["pass"], {a_: b_ for a_, b_ in x["numbers"].items() if a_ != "grid"})
print("trades", OUT["trades_info"]); print("W1", corr); print("passed", OUT["checks_passed"])
