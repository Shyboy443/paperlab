"""TEST once + 8 checks + Worker 1 correlation for the pre-registered config (PREREG.md)."""
import json, time
import numpy as np
from datetime import datetime, timezone
from core import *

t0 = time.time()
P = dict(feat="max1d", L=14, k=8, R=7, sizing="erisk")
INFO = dict(P, feat="neg_smax")
ms = lambda *a: int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)
d25, d26 = dayidx(ms(2025, 1, 1)), dayidx(ms(2026, 1, 1))
KEYS = ("median_month", "geo_month", "mean_month", "max_dd_pct", "t_stat", "trades_per_month", "net_pct", "worst_month", "months_up", "n_months")
pick = lambda s: {k: s.get(k) for k in KEYS}


def windows(p):
    A = {}
    for tag, (x, y) in (("dev", (d0, dT)), ("test", (dT, d1)), ("full", (d0, d1)), ("y2025_26", (d25, d1)), ("ytd2026", (d26, d1))):
        for cm, sfx in ((1, ""), (2, "_double_cost")):
            c, _, n = sim(p, x, y, cost_mult=cm)
            A[tag + sfx] = stats(c, n)
    return A


OUT = {"config": P}
A = windows(P)
months = A["full"]["months"]; v = np.array(list(months.values()))
chk = {}
c1 = {k: A[k]["net_pct"] for k in ("dev", "dev_double_cost", "test", "test_double_cost")}
chk[1] = {"pass": bool(all(x > 0 for x in c1.values())), "numbers": c1}
yr = yearly(months)
m22 = np.prod([1 + x / 100 for k, x in months.items() if k >= "2022-01"]) - 1
chk[2] = {"pass": bool(sum(x > 0 for x in yr.values()) >= 4 and m22 > 0), "numbers": {"years": yr, "2022-01..2026-10_pct": round(m22 * 100, 2)}}
rng = np.random.default_rng(7)
nb_ = int(np.ceil(len(v) / 3)); boots = []
for _ in range(5000):
    st = rng.integers(0, len(v) - 2, nb_)
    boots.append(np.concatenate([v[s:s + 3] for s in st])[:len(v)].mean())
p5 = float(np.percentile(boots, 5)); tstat = float(v.mean() / v.std(ddof=1) * np.sqrt(len(v)))
chk[3] = {"pass": bool(tstat >= 2 and p5 > 0), "numbers": {"t_stat": round(tstat, 2), "boot_p5_mean_month": round(p5, 2), "n_months": len(v)}}
g4 = np.prod([1 + x / 100 for x in sorted(v)[:-3]]) - 1
chk[4] = {"pass": bool(g4 > 0), "numbers": {"net_without_3_best_pct": round(float(g4) * 100, 2), "best3": [float(x) for x in sorted(v)[-3:]]}}
nbr = []
for nm, q in ([("L", dict(P, L=x)) for x in (7, 10, 18, 21)] + [("k", dict(P, k=x)) for x in (4, 6, 10, 12)] + [("R", dict(P, R=x)) for x in (3, 5, 10, 14)]):
    sd, st_ = stats(sim(q, d0, dT)[0]), stats(sim(q, dT, d1)[0])
    nbr.append({"name": f"{nm}={q[nm]}", "dev_net": sd["net_pct"], "test_net": st_["net_pct"], "dev_median": sd["median_month"], "test_median": st_["median_month"]})
share = sum(x["dev_net"] > 0 and x["test_net"] > 0 for x in nbr) / len(nbr)
chk[5] = {"pass": bool(share >= 0.7), "numbers": {"share_positive_both": round(share, 3), "grid": nbr}}
print("nbrs done", round(time.time() - t0), flush=True)
c, pos, _ = sim(P, d0, d1)
strat = stats(c)["net_pct"]
rand = [stats(sim(P, d0, d1, rng=np.random.default_rng(s))[0])["net_pct"] for s in range(200)]
pct = float(np.mean(np.array(rand) < strat))
chk[6] = {"pass": bool(pct >= 0.95), "numbers": {"strategy_net_pct": strat, "beats_share": round(pct, 3),
          "random_median_net_pct": round(float(np.median(rand)), 2), "random_p95_net_pct": round(float(np.percentile(rand, 95)), 2)}}
print("random done", round(time.time() - t0), flush=True)
pc = {}
for j, sd_, a, b, pnl in pos:
    pc[j] = pc.get(j, 0) + pnl
top5 = [j for j, _ in sorted(pc.items(), key=lambda x: -x[1])[:5]]
net7 = stats(sim(P, d0, d1, exclude=top5)[0])["net_pct"]
total = c[-1][1] - c[0][1]
best = max(pos, key=lambda x: x[4])
sh = best[4] / total if total > 0 else None
chk[7] = {"pass": bool(net7 > 0 and sh is not None and sh <= 0.10), "numbers": {"top5_coins": [syms[j] for j in top5], "net_without_top5_pct": net7,
          "max_single_position_share_of_profit": None if sh is None else round(float(sh), 4),
          "max_position": {"coin": syms[best[0]], "pnl_usd": round(float(best[4]), 1), "total_profit_usd": round(float(total), 1)}}}
F = A["full"]
chk[8] = {"pass": bool(F["geo_month"] > 0 and F["geo_month"] >= 0.5 * F["mean_month"]), "numbers": {"geo_month": F["geo_month"], "mean_month": F["mean_month"]}}
OUT["checks"] = {str(k): x for k, x in chk.items()}
OUT["checks_passed"] = int(sum(x["pass"] for x in chk.values()))
OUT["windows"] = {k: pick(s) for k, s in A.items()}
OUT["years_1x"] = yr; OUT["years_2x_cost"] = yearly(A["full_double_cost"]["months"])
OUT["monthly_1x"] = months
OUT["october_2026_pct"] = months.get("2026-10")
# 500 USDT feasibility: smallest target weight over all rebalances x 500
mins = []
for d in range(d0, d1, 7):
    w = select(P, d)
    if w is not None:
        mins.append(np.abs(w[w != 0]).min())
OUT["usdt500"] = {"smallest_weight_pct": round(float(min(mins)) * 100, 2), "median_smallest_weight_pct": round(float(np.median(mins)) * 100, 2),
                  "smallest_order_usdt_at_500": round(float(min(mins)) * 500, 2), "min_notional_ok": bool(min(mins) * 500 >= 5)}
# Worker 1 correlation
W1 = r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble/"
w1m = json.load(open(W1 + "results.json"))["monthly_1x"]
rb = np.load(W1 + "_rbook_full_1x.npy")
w1day = np.load(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r2_trend/panel.npz")["day"].astype(np.int64)
eq = np.array([e for _, e in c]); tt = [t for t, _ in c]
dP = {tt[i] - 86400000: eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))}  # return of the day starting at tt[i]-1d
w1d = {int(t): r for t, r in zip(w1day, rb) if np.isfinite(r)}
cd = sorted(set(dP) & set(w1d)); a = np.array([dP[t] for t in cd]); b = np.array([w1d[t] for t in cd])
mk = np.array([datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m") for t in cd])
mm = sorted(set(months) & set(w1m))
corr = {}
for lab, lo in (("full", "2021-01"), ("2022_2026", "2022-01"), ("test", "2024-11")):
    m_ = [m for m in mm if m >= lo]
    corr["monthly_" + lab] = round(float(np.corrcoef([months[m] for m in m_], [w1m[m] for m in m_])[0, 1]), 3)
    s_ = mk >= lo
    corr["daily_" + lab] = round(float(np.corrcoef(a[s_], b[s_])[0, 1]), 3)
OUT["worker1_corr"] = corr
# info-only reversed vol-scaled lottery
B = windows(INFO)
OUT["info_reversed_smax"] = {"config": INFO, "windows": {k: pick(s) for k, s in B.items()}, "years_1x": yearly(B["full"]["months"])}
bm = B["full"]["months"]
OUT["info_reversed_smax"]["worker1_monthly_corr_2022_2026"] = round(float(np.corrcoef([bm[m] for m in mm if m >= "2022-01"], [w1m[m] for m in mm if m >= "2022-01"])[0, 1]), 3)
json.dump(OUT, open("results.json", "w"), indent=1, default=float)
for k, s in OUT["windows"].items():
    print(k, s)
for k, x in OUT["checks"].items():
    print("check", k, x["pass"], {a_: b_ for a_, b_ in x["numbers"].items() if a_ != "grid"})
for x in chk[5]["numbers"]["grid"]:
    print("  nb", x)
print("years", yr, OUT["years_2x_cost"]); print("oct", OUT["october_2026_pct"], "500", OUT["usdt500"], "w1", corr)
for k in ("dev", "test", "full", "y2025_26_double_cost"):
    print("INFO", k, OUT["info_reversed_smax"]["windows"][k])
print("INFO w1", OUT["info_reversed_smax"]["worker1_monthly_corr_2022_2026"], OUT["info_reversed_smax"]["years_1x"])
print("time", round(time.time() - t0))
