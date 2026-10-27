"""TEST once + 8 checks + Worker 1/2 correlation for the pre-registered config (PREREG.md)."""
import json, time
import numpy as np
from datetime import datetime, timezone
from core import *

t0 = time.time()
P = dict(feat="rmom", L=14, skip=0, k=8, R=7, B=60)
ms = lambda *a: int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)
d25, d26 = dayidx(ms(2025, 1, 1)), dayidx(ms(2026, 1, 1))
KEYS = ("median_month", "geo_month", "mean_month", "months_up", "n_months", "max_dd_pct", "t_stat", "trades_per_month", "net_pct", "worst_month")
pick = lambda s: {k: (float(s[k]) if s.get(k) is not None else None) for k in KEYS}


def windows(p):
    A = {}
    for tag, (x, y) in (("dev", (d0, dT)), ("test", (dT, d1)), ("full", (d0, d1)), ("y2025_26", (d25, d1)), ("ytd2026", (d26, d1))):
        for cm, sfx in ((1, ""), (2, "_double_cost")):
            c, _, n = sim(p, x, y, cost_mult=cm)
            A[tag + sfx] = stats(c, n)
    return A


OUT = {"config": P}
A = windows(P)
months = {k: float(x) for k, x in A["full"]["months"].items()}; v = np.array(list(months.values()))
chk = {}
c1 = {k: float(A[k]["net_pct"]) for k in ("dev", "dev_double_cost", "test", "test_double_cost")}
chk[1] = {"pass": bool(all(x > 0 for x in c1.values())), "numbers": c1}
yr = yearly(months)
m22 = np.prod([1 + x / 100 for k, x in months.items() if k >= "2022-01"]) - 1
chk[2] = {"pass": bool(sum(x > 0 for x in yr.values()) >= 4 and m22 > 0), "numbers": {"years": yr, "2022-01..2026-10_pct": round(float(m22) * 100, 2)}}
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
for nm, vals in (("L", (7, 10, 18, 21)), ("k", (4, 6, 10, 12)), ("R", (3, 5, 10, 14)), ("B", (30, 45, 75, 90))):
    for x in vals:
        q = dict(P, **{nm: x})
        sd, st_ = stats(sim(q, d0, dT)[0]), stats(sim(q, dT, d1)[0])
        nbr.append({"name": f"{nm}={x}", "dev_net": float(sd["net_pct"]), "test_net": float(st_["net_pct"]),
                    "dev_median": float(sd["median_month"]), "test_median": float(st_["median_month"]), "test_geo": float(st_["geo_month"])})
share = sum(x["dev_net"] > 0 and x["test_net"] > 0 for x in nbr) / len(nbr)
chk[5] = {"pass": bool(share >= 0.7), "numbers": {"share_positive_both": round(share, 3), "grid": nbr}}
print("nbrs done", round(time.time() - t0), flush=True)
c, pos, _ = sim(P, d0, d1)
strat = float(stats(c)["net_pct"])
rand = [float(stats(sim(P, d0, d1, rng=np.random.default_rng(s))[0])["net_pct"]) for s in range(200)]
pct = float(np.mean(np.array(rand) < strat))
chk[6] = {"pass": bool(pct >= 0.95), "numbers": {"strategy_net_pct": strat, "beats_share": round(pct, 3),
          "random_median_net_pct": round(float(np.median(rand)), 2), "random_p95_net_pct": round(float(np.percentile(rand, 95)), 2)}}
print("random done", round(time.time() - t0), flush=True)
pc = {}
for j, sd_, a, b, pnl in pos:
    pc[j] = pc.get(j, 0) + pnl
top5 = [j for j, _ in sorted(pc.items(), key=lambda x: -x[1])[:5]]
net7 = float(stats(sim(P, d0, d1, exclude=top5)[0])["net_pct"])
total = c[-1][1] - c[0][1]
best = max(pos, key=lambda x: x[4])
sh = best[4] / total if total > 0 else None
chk[7] = {"pass": bool(net7 > 0 and sh is not None and sh <= 0.10), "numbers": {"top5_coins": [syms[j] for j in top5], "net_without_top5_pct": net7,
          "max_single_position_share_of_profit": None if sh is None else round(float(sh), 4),
          "max_position": {"coin": syms[best[0]], "pnl_usd": round(float(best[4]), 1), "total_profit_usd": round(float(total), 1)}}}
F = A["full"]
chk[8] = {"pass": bool(F["geo_month"] > 0 and F["geo_month"] >= 0.5 * F["mean_month"]), "numbers": {"geo_month": float(F["geo_month"]), "mean_month": float(F["mean_month"])}}
OUT["checks"] = {str(k): x for k, x in chk.items()}
OUT["checks_passed"] = int(sum(x["pass"] for x in chk.values()))
OUT["windows"] = {k: pick(s) for k, s in A.items()}
OUT["years_1x"] = yr; OUT["years_2x_cost"] = yearly(A["full_double_cost"]["months"])
OUT["monthly_1x"] = months
OUT["october_2026_pct"] = months.get("2026-10")
mins = []
for d in range(d0, d1, 7):
    w = select(P, d)
    if w is not None:
        mins.append(np.abs(w[w != 0]).min())
OUT["usdt500"] = {"smallest_weight_pct": round(float(min(mins)) * 100, 2), "smallest_order_usdt_at_500": round(float(min(mins)) * 500, 2),
                  "min_notional_ok": bool(min(mins) * 500 >= 5),
                  "note": "16 positions of 31.25 USDT at 500 USDT; drift-rebalance deltas below 5 USDT must be skipped or rounded"}
R_ = r"D:/Projects/BinanceBOt/paperlab/research/"
w1m = json.load(open(R_ + "monthly20/r4_07_ensemble/results.json"))["monthly_1x"]
w2m = json.load(open(R_ + "worker2/session/results.json"))["primary"]["monthly_1x"]
corr = {}
for nm, wm in (("w1", w1m), ("w2", w2m)):
    mm = sorted(set(months) & set(wm))
    for lab, lo in (("full", "2021-01"), ("2022_2026", "2022-01"), ("test", "2024-11")):
        m_ = [m for m in mm if m >= lo]
        corr[f"{nm}_monthly_{lab}"] = round(float(np.corrcoef([months[m] for m in m_], [wm[m] for m in m_])[0, 1]), 3)
OUT["corr"] = corr
tm = [m for m in sorted(months) if m >= "2024-11" and m in w1m and m in w2m]


def mstats(arr):
    a = np.array(arr); g = (np.prod(1 + a / 100) ** (1 / len(a)) - 1) * 100
    eq = np.concatenate([[1], np.cumprod(1 + a / 100)]); dd = float(np.max(1 - eq / np.maximum.accumulate(eq)))
    return {"median": round(float(np.median(a)), 2), "geo": round(float(g), 2), "mean": round(float(a.mean()), 2),
            "up": int((a > 0).sum()), "n": len(a), "monthly_dd_pct": round(dd * 100, 1)}


OUT["portfolio_test_monthly_rebal"] = {"W1+W2": mstats([(w1m[m] + w2m[m]) / 2 for m in tm]),
                                       "W1+W2+X": mstats([(w1m[m] + w2m[m] + months[m]) / 3 for m in tm]),
                                       "X": mstats([months[m] for m in tm]), "W1": mstats([w1m[m] for m in tm])}
json.dump(OUT, open("results.json", "w"), indent=1, default=float)
for k, s in OUT["windows"].items():
    print(k, s)
for k, x in OUT["checks"].items():
    print("check", k, x["pass"], {a_: b_ for a_, b_ in x["numbers"].items() if a_ != "grid"})
for x in chk[5]["numbers"]["grid"]:
    print("  nb", x)
print("years", yr, OUT["years_2x_cost"]); print("oct", OUT["october_2026_pct"], "500", OUT["usdt500"], "corr", corr)
print("portfolio", OUT["portfolio_test_monthly_rebal"])
print("test months", {m: round(months[m], 2) for m in tm})
print("time", round(time.time() - t0))
