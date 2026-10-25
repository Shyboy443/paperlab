"""Runs the ONE selected config (scan_dev.json eligible[0]) through TEST once + the 8 checks. Writes results.json."""
import json, sys
import numpy as np
from datetime import datetime, timezone
from core2 import *

scan = json.load(open("scan_dev.json"))
if scan["eligible"]:
    r0 = scan["eligible"][0]; label = "selected (eligible[0])"
else:
    r0 = sorted([r for r in scan["all"] if not r["excluded"]], key=lambda r: -r["t_daily"])[0]; label = "INFO ONLY: no config passed the DEV bar; best t_daily shown"
P = dict(BASE, **{k: r0[k] for k in ("E", "H", "X", "mode", "days")})
KEYS = ("mean_month", "median_month", "geo_month", "worst_month", "max_dd_pct", "months_up", "n_months", "net_pct", "trades", "t_stat")
pick = lambda s: {k: s.get(k) for k in KEYS}
ms_ = lambda y, m, d=1: dayidx(int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000))
d25, d26, dOct = ms_(2025, 1), ms_(2026, 1), ms_(2026, 10)

A = {}
for tag, (x, y) in (("dev", (d0, dT)), ("test", (dT, d1)), ("full", (d0, d1)), ("y2025_26", (d25, d1)), ("ytd2026", (d26, d1)), ("oct2026", (dOct, d1))):
    for cm, sfx in ((1, ""), (2, "_double_cost")):
        c, tr, _, _ = sim2(P, x, y, cost_mult=cm)
        A[tag + sfx] = stats(c, tr)
full = A["full"]; months = full["months"]; v = np.array(list(months.values()))
chk = {}
c1 = {k: A[k]["net_pct"] for k in ("dev", "dev_double_cost", "test", "test_double_cost")}
chk[1] = {"pass": all(x > 0 for x in c1.values()), "numbers": c1}
yr = yearly(months)
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
chk[4] = {"pass": bool(g4 > 0), "numbers": {"net_without_3_best_pct": round(float(g4) * 100, 2)}}
nbr = []
for n, q in neighbours2(P):
    sd, st_ = summary(sim2(q, d0, dT)[0]), summary(sim2(q, dT, d1)[0])
    nbr.append({"name": n, "dev_net": sd["net_pct"], "test_net": st_["net_pct"], "dev_median": sd["median_month"], "test_median": st_["median_month"]})
sh = sum(x["dev_net"] > 0 and x["test_net"] > 0 for x in nbr) / len(nbr)
chk[5] = {"pass": bool(sh >= 0.7), "numbers": {"share_positive_both": round(sh, 3), "grid": nbr}}
c, tr, _, rows = sim2(P, d0, d1)
strat_net = summary(c)["net_pct"]; V = hv(P["W"]); by = {}
for r in rows:
    by.setdefault(r[0], []).append(r)
rand = []
for rep in range(200):
    rr = []
    for d, rs in by.items():
        pool = [j for j in ALT_ELIG[d] if np.isfinite(O[rs[0][1], j])]
        pk = rng.choice(pool, size=min(len(rs), len(pool)), replace=False)
        rr += [(d, r[1], r[2], int(j), r[4], size_mult(P, V[d - d0, j])) for r, j in zip(rs, pk)]
    rand.append(summary(run(rr, d0, d1, P["K"])[0])["net_pct"])
pct = float(np.mean(np.array(rand) < strat_net))
chk[6] = {"pass": pct >= 0.95, "numbers": {"strategy_net_pct": strat_net, "beats_share": round(pct, 3),
          "random_median_net_pct": round(float(np.median(rand)), 2), "random_p95_net_pct": round(float(np.percentile(rand, 95)), 2)}}
pc = {}
for t in tr:
    pc[t[1]] = pc.get(t[1], 0) + t[3]
top5 = [j for j, _ in sorted(pc.items(), key=lambda x: -x[1])[:5]]
net7 = summary(sim2(P, d0, d1, exclude=set(top5))[0])["net_pct"]
total = c[-1][1] - c[0][1]; i = int(np.argmax([t[3] for t in tr])); share = tr[i][3] / total if total > 0 else float("inf")
chk[7] = {"pass": bool(net7 > 0 and share <= 0.10), "numbers": {"top5": [syms[j] for j in top5], "net_without_top5_pct": net7,
          "max_single_position_share": round(float(share), 4), "max_pos_coin": syms[tr[i][1]]}}
chk[8] = {"pass": bool(full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"]),
          "numbers": {"geo_month": full["geo_month"], "mean_month": full["mean_month"]}}
chk = {str(k): x for k, x in chk.items()}
npass = int(sum(x["pass"] for x in chk.values()))

# correlations (daily book without volume cap, as session/run.py)
def dmap(p):
    cu, _, _ = run(signals2(p, d0, d1) if "E" in p else signals(p, d0, d1), d0, d1, p["K"], liq_cap=False)
    eq = np.array([e for _, e in cu]); r = eq[1:] / eq[:-1] - 1
    return {int(td[d]): r[i] for i, d in enumerate(range(d0, d0 + len(r)))}, cu
dP, cuP = dmap(P); dW2, _ = dmap(W2)
W1 = r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble/"
w1m = json.load(open(W1 + "results.json"))["monthly_1x"]
rb = np.load(W1 + "_rbook_full_1x.npy")
w1day = np.load(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r2_trend/panel.npz")["day"].astype(np.int64)
w1d = {int(t): r for t, r in zip(w1day, rb) if np.isfinite(r)}
mk = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m")
def corr_d(x, y, lo="2021-01"):
    ks = sorted(k for k in set(x) & set(y) if mk(k) >= lo)
    return round(float(np.corrcoef([x[k] for k in ks], [y[k] for k in ks])[0, 1]), 3)
def to_m(x):
    m = {}
    for k, r in x.items():
        m[mk(k)] = m.get(mk(k), 1.0) * (1 + r)
    return {k: (g - 1) * 100 for k, g in m.items()}
mP, mW2 = to_m(dP), to_m(dW2)
def corr_m(x, y, lo):
    ks = sorted(k for k in set(x) & set(y) if k >= lo)
    return round(float(np.corrcoef([x[k] for k in ks], [y[k] for k in ks])[0, 1]), 3)
corr = {"w1_monthly_2022_2026": corr_m(mP, w1m, "2022-01"), "w1_monthly_full": corr_m(mP, w1m, "2021-01"),
        "w1_daily_2022_2026": corr_d(dP, w1d, "2022-01"), "w1_daily_full": corr_d(dP, w1d),
        "w2_daily_full": corr_d(dP, dW2), "w2_daily_test": corr_d(dP, dW2, "2024-11"), "w2_monthly_full": corr_m(mP, mW2, "2021-01")}
# 500 USDT: smallest order = 500 * min notional/eq among TEST trades
trT = sim2(P, dT, d1)[1]
fr = np.array([t[5] for t in trT])
small = {"min_order_usdt_at_500": round(float(fr.min() * 500), 2), "p5_order_usdt": round(float(np.percentile(fr, 5) * 500), 2),
         "share_below_5usdt": round(float((fr * 500 < 5).mean()), 4), "median_order_usdt": round(float(np.median(fr) * 500), 2)}
tpm = round(A["test"]["trades"] / A["test"]["n_months"], 1)
T = A["test"]
bar = {"a_8of8": npass == 8, "b_median": T["median_month"] >= 2.22, "b_geo": T["geo_month"] >= 3.02,
       "c_w1corr": abs(corr["w1_monthly_2022_2026"]) <= 0.3, "d_500usdt": small["share_below_5usdt"] == 0}
res = {"label": label, "params": P, "dev_scan_row": r0, "windows": {k: pick(x) for k, x in A.items()},
       "years_1x": yr, "years_2x_cost": yearly(A["full_double_cost"]["months"]), "monthly_1x": months, "checks": chk,
       "checks_passed": npass, "robust": npass == 8, "corr": corr, "size_500usdt": small, "trades_per_month_test": tpm,
       "replacement_bar": bar, "replace": all(bar.values()) and label.startswith("selected")}
json.dump(res, open("results.json", "w"), indent=1, default=float)
print(label, P)
for k in ("dev", "test", "full", "y2025_26_double_cost", "ytd2026", "oct2026"):
    print(k, pick(A[k]))
for k, x in chk.items():
    print("check", k, x["pass"], {a: b for a, b in x["numbers"].items() if a != "grid"})
print(corr, small, tpm, bar, "REPLACE" if res["replace"] else "NO")
