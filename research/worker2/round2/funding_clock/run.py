"""Pre-registered FC_SEL (PREREG.md) through the 8 ROBUSTNESS checks + extras. Runs TEST once."""
import json
import numpy as np
from datetime import datetime, timezone
from core import *
from dev_nb import SELP, nbrs

P = SELP
KEYS = ("median_month", "geo_month", "mean_month", "worst_month", "max_dd_pct", "months_up", "n_months", "net_pct", "trades", "trades_per_month", "t_stat")
pick = lambda s: {k: s.get(k) for k in KEYS}
day_of = lambda y, m, d=1: dayidx(int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000))
d25, d26, dOct = day_of(2025, 1), day_of(2026, 1), day_of(2026, 10)
OUT = {"params": P}
W = {}
for tag, (x, y) in (("dev", (d0, dT)), ("test", (dT, d1)), ("full", (d0, d1)), ("y2025_26", (d25, d1)), ("ytd2026", (d26, d1)), ("oct2026", (dOct, d1))):
    for cm, sfx in ((1, ""), (2, "_double_cost")):
        c, tr, _ = sim(P, x, y, cost_mult=cm)
        W[tag + sfx] = stats(c, tr)
    c, tr, _ = sim(P, x, y, conv="worst")
    W[tag + "_worst_funding"] = stats(c, tr)
OUT["windows"] = {k: pick(v) for k, v in W.items()}
full = W["full"]; months = full["months"]; v = np.array(list(months.values()))
OUT["monthly_1x"] = months
OUT["years_1x"] = yearly(months); OUT["years_2x_cost"] = yearly(W["full_double_cost"]["months"])
chk = {}
c1 = {k: W[k]["net_pct"] for k in ("dev", "dev_double_cost", "test", "test_double_cost")}
chk[1] = {"pass": all(x > 0 for x in c1.values()), "numbers": c1}
yr = OUT["years_1x"]; m22 = np.prod([1 + x / 100 for k, x in months.items() if k >= "2022-01"]) - 1
chk[2] = {"pass": bool(sum(x > 0 for x in yr.values()) >= 4 and m22 > 0), "numbers": {"years": yr, "2022-01..2026-10_pct": round(m22 * 100, 2)}}
rng = np.random.default_rng(7); nb_ = int(np.ceil(len(v) / 3)); boots = []
for _ in range(5000):
    st = rng.integers(0, len(v) - 2, nb_)
    boots.append(np.concatenate([v[s:s + 3] for s in st])[:len(v)].mean())
p5 = float(np.percentile(boots, 5)); tstat = float(v.mean() / v.std(ddof=1) * np.sqrt(len(v)))
chk[3] = {"pass": tstat >= 2 and p5 > 0, "numbers": {"t_stat": round(tstat, 2), "boot_p5_mean_month": round(p5, 2), "n_months": len(v)}}
g4 = np.prod([1 + x / 100 for x in sorted(v)[:-3]]) - 1
chk[4] = {"pass": bool(g4 > 0), "numbers": {"net_without_3_best_pct": round(float(g4) * 100, 2), "best3": [float(x) for x in sorted(v)[-3:]]}}
nbr = []
for n, q in nbrs(P):
    sd = summary(sim(q, d0, dT)[0]); st_ = summary(sim(q, dT, d1)[0])
    nbr.append({"name": n, "dev_net": sd["net_pct"], "test_net": st_["net_pct"], "dev_median": sd["median_month"], "test_median": st_["median_month"],
                "test_geo": st_["geo_month"]})
    print("nb", nbr[-1], flush=True)
share = sum(x["dev_net"] > 0 and x["test_net"] > 0 for x in nbr) / len(nbr)
chk[5] = {"pass": bool(share >= 0.7), "numbers": {"share_positive_both": round(share, 3), "grid": nbr}}
c, tr, rows = sim(P, d0, d1)
strat_net = summary(c)["net_pct"]
by = {}
for r in rows:
    by.setdefault(r[0], []).append(r)
rand = []
for rep in range(200):
    rr = []
    for key, rs in by.items():
        s = key[0]; ie, ix = rs[0][1], rs[0][2]
        pool = [j for j in ELIG[s // 24] if np.isfinite(O[ie, j]) and np.isfinite(O[min(ix, NB - 1), j])]
        pk = rng.choice(pool, size=min(len(rs), len(pool)), replace=False)
        rr += [(key, ie, ix, int(j), r[4]) for r, j in zip(rs, pk)]
    cr, _ = run(rr, P["K"], t_from=td[d0])
    rand.append(summary(cr)["net_pct"])
pct = float(np.mean(np.array(rand) < strat_net))
chk[6] = {"pass": pct >= 0.95, "numbers": {"strategy_net_pct": strat_net, "beats_share": round(pct, 3),
          "random_median_net_pct": round(float(np.median(rand)), 2), "random_p95_net_pct": round(float(np.percentile(rand, 95)), 2)}}
pc = {}
for t in tr:
    pc[t[1]] = pc.get(t[1], 0) + t[3]
top5 = [j for j, _ in sorted(pc.items(), key=lambda x: -x[1])[:5]]
net7 = summary(sim(P, d0, d1, exclude=set(top5))[0])["net_pct"]
total = c[-1][1] - c[0][1]
i = int(np.argmax([t[3] for t in tr])); sh = tr[i][3] / total
lg = np.array([np.log1p(t[4]) for t in tr])
chk[7] = {"pass": bool(net7 > 0 and sh <= 0.10), "numbers": {"top5_coins": [syms[j] for j in top5], "net_without_top5_pct": net7,
          "max_single_position_share_of_profit": round(float(sh), 4), "max_position_pct_of_equity": round(tr[i][4] * 100, 2),
          "max_position": {"coin": syms[tr[i][1]], "day": datetime.fromtimestamp(td[tr[i][0]] / 1000, timezone.utc).strftime("%Y-%m-%d")},
          "info_max_share_of_log_growth": round(float(lg.max() / lg.sum()), 4)}}
chk[8] = {"pass": bool(full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"]),
          "numbers": {"geo_month": full["geo_month"], "mean_month": full["mean_month"]}}
OUT["checks"] = {str(k): x for k, x in chk.items()}
OUT["checks_passed"] = int(sum(x["pass"] for x in chk.values())); OUT["robust"] = OUT["checks_passed"] == 8
# legs on TEST (info)
OUT["test_legs"] = {nm: pick(stats(*sim(q, dT, d1)[:2])) for nm, q in (("NEG_both", dict(P, POS=None)), ("POS_post", dict(P, NEG=None)))}
# 500 USDT feasibility: smallest notional at equity 500 = 500 * min over groups of 1/max(n,K)
nmax = max(len(rs) for rs in by.values())
OUT["usdt500"] = {"max_positions_in_one_group": nmax, "smallest_order_usdt_at_500": round(500 / max(nmax, P["K"]), 2),
                  "groups_per_month": round(len(by) / len(v), 1)}
# Worker 1 correlation
W1 = r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble/"
w1m = json.load(open(W1 + "results.json"))["monthly_1x"]
rb = np.load(W1 + "_rbook_full_1x.npy")
z = np.load(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r2_trend/panel.npz"); w1day = z["day"].astype(np.int64)
c_unc, _, _ = sim(P, d0, d1, liq_cap=False)
eqd = {}
for t, e in c_unc:
    eqd[(t - DATA_FROM) // DAY] = e
ds = np.arange(d0, d1); last = c_unc[0][1]; ser = []
for d in ds:
    last = eqd.get(d, last); ser.append(last)
ser = np.array(ser); rP = ser / np.r_[c_unc[0][1], ser[:-1]] - 1
dP = {int(td[d]): r for d, r in zip(ds, rP)}
w1d = {int(t): r for t, r in zip(w1day, rb) if np.isfinite(r)}
cd = sorted(set(dP) & set(w1d)); a = np.array([dP[t] for t in cd]); b = np.array([w1d[t] for t in cd])
mk = [datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m") for t in cd]; m22 = np.array([m >= "2022-01" for m in mk])
ms = sorted(set(months) & set(w1m))
corr = {}
for lab, lo in (("full", "2021-01"), ("2022_2026", "2022-01"), ("test", "2024-11")):
    mm = [m for m in ms if m >= lo]
    corr["monthly_" + lab] = round(float(np.corrcoef([months[m] for m in mm], [w1m[m] for m in mm])[0, 1]), 3)
corr["daily_full"] = round(float(np.corrcoef(a, b)[0, 1]), 3); corr["daily_2022_2026"] = round(float(np.corrcoef(a[m22], b[m22])[0, 1]), 3)
OUT["worker1_corr"] = corr
json.dump(OUT, open("results.json", "w"), indent=1, default=float)
for k in ("dev", "test", "full", "y2025_26_double_cost", "ytd2026", "oct2026", "test_worst_funding", "full_worst_funding", "test_double_cost"):
    print(k, OUT["windows"][k])
print("years", OUT["years_1x"], "2x", OUT["years_2x_cost"])
for k, x in OUT["checks"].items():
    print("check", k, x["pass"], {a_: b_ for a_, b_ in x["numbers"].items() if a_ != "grid"})
print("legs", OUT["test_legs"]); print("500", OUT["usdt500"]); print("W1", corr); print("passed", OUT["checks_passed"])
