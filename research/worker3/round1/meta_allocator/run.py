"""TEST run (once) + 8 checks for the pre-registered selection (PREREG.md Part B). Writes results.json."""
import json
import numpy as np
from alloc import *

SEL = dict(name="relmom_L30_t0.25", kind="relmom", L=30, tilt=0.25, freq=7)
BEN = dict(name="bench_5050", kind="fixed", w=[0.5, 0.5], freq=1)
Y25 = MK >= "2025-01"
Y26 = MK >= "2026-01"
FULL = np.ones(len(MK), bool)
OUT = {"selected": SEL}


def pick(s):
    return {k: v for k, v in s.items() if k != "months"}


def books(cfg):
    p1, t1, g1 = run_cfg(cfg)
    p2, _, _ = run_cfg(cfg, cost_mult=2.0)
    return p1, p2, t1, g1


def windows(cfg):
    p1, p2, t1, g1 = books(cfg)
    w = {}
    for tag, m in (("dev", DEV), ("test", TEST), ("full", FULL), ("y2025_26", Y25), ("ytd2026", Y26)):
        w[tag] = mstats(p1, m)
        w[tag + "_2x"] = mstats(p2, m)
    return w, p1, p2, t1, g1


W, p1, p2, turn, gross = windows(SEL)
WB, b1, b2, _, _ = windows(BEN)
R = sleeves()
for nm, i in (("W1", 0), ("W2", 1)):
    OUT[nm] = {k: pick(mstats(R[:, i], m)) for k, m in (("dev", DEV), ("test", TEST), ("full", FULL))}
OUT["sel"] = {k: pick(v) for k, v in W.items()}
OUT["bench_5050"] = {k: pick(v) for k, v in WB.items()}
OUT["monthly_sel"] = W["full"]["months"]
OUT["monthly_bench"] = WB["full"]["months"]
OUT["turnover_per_month"] = round(float(turn.sum() / W["full"]["n_months"]), 3)
OUT["rebalances_per_month"] = round(float((turn > 1e-9).sum() / W["full"]["n_months"]), 2)
OUT["w2_trades_per_month"] = round(float(Z["w2_trades"].sum() / W["full"]["n_months"]), 1)
T = rule_targets(R, SEL)
OUT["mean_w1_weight"] = {"dev": round(float(T[DEV, 0].mean()), 3), "test": round(float(T[TEST, 0].mean()), 3)}

# monthly correlation with W1 and W2
ms = list(W["full"]["months"])
m1 = mstats(R[:, 0])["months"]; m2 = mstats(R[:, 1])["months"]
for lab, lo in (("full", "2021-01"), ("test", "2024-11")):
    mm = [m for m in ms if m >= lo]
    a = [W["full"]["months"][m] for m in mm]
    OUT[f"corr_monthly_{lab}"] = {"W1": round(float(np.corrcoef(a, [m1[m] for m in mm])[0, 1]), 3),
                                  "W2": round(float(np.corrcoef(a, [m2[m] for m in mm])[0, 1]), 3)}

# ---------------- checks
chk = {}
c1 = {"dev": W["dev"]["net_pct"], "dev_2x": W["dev_2x"]["net_pct"], "test": W["test"]["net_pct"], "test_2x": W["test_2x"]["net_pct"]}
chk[1] = {"pass": all(v > 0 for v in c1.values()), "numbers": c1}
mon = W["full"]["months"]
yr = {}
for k, v in mon.items():
    yr[k[:4]] = yr.get(k[:4], 1.0) * (1 + v / 100)
yr = {k: round((v - 1) * 100, 2) for k, v in yr.items()}
m22 = np.prod([1 + v / 100 for k, v in mon.items() if k >= "2022-01"]) - 1
chk[2] = {"pass": bool(sum(v > 0 for v in yr.values()) >= 4 and m22 > 0), "numbers": {"years": yr, "2022_2026_pct": round(m22 * 100, 2)}}
v = np.array(list(mon.values()))
rng = np.random.default_rng(7)
nb = int(np.ceil(len(v) / 3))
boots = [np.concatenate([v[s:s + 3] for s in rng.integers(0, len(v) - 2, nb)])[:len(v)].mean() for _ in range(5000)]
p5 = float(np.percentile(boots, 5)); ts = float(v.mean() / v.std(ddof=1) * np.sqrt(len(v)))
chk[3] = {"pass": ts >= 2 and p5 > 0, "numbers": {"t_stat": round(ts, 2), "boot_p5": round(p5, 2), "n_months": len(v)}}
g4 = np.prod([1 + x / 100 for x in sorted(v)[:-3]]) - 1
chk[4] = {"pass": bool(g4 > 0), "numbers": {"net_without_3_best_pct": round(float(g4) * 100, 1)}}
nbr = []
for name, q in ([(f"L={L}", dict(SEL, L=L)) for L in (15, 22, 38, 45)] +
                [(f"tilt={t}", dict(SEL, tilt=t)) for t in (0.125, 0.1875, 0.3125, 0.375)] +
                [(f"freq={f}", dict(SEL, freq=f)) for f in (1, 3, 14)]):
    pp, _, _ = run_cfg(q)
    sd, st = mstats(pp, DEV), mstats(pp, TEST)
    nbr.append({"name": name, "dev_net": sd["net_pct"], "test_net": st["net_pct"], "dev_geo": sd["geo_month"],
                "test_geo": st["geo_month"], "test_median": st["median_month"], "test_up": st["months_up"],
                "test_dd": st["max_dd_pct"]})
sh = np.mean([x["dev_net"] > 0 and x["test_net"] > 0 for x in nbr])
chk[5] = {"pass": bool(sh >= 0.7), "numbers": {"share_positive_both": round(float(sh), 3), "grid": nbr,
          "share_beating_5050_test_geo": round(float(np.mean([x["test_geo"] > WB["test"]["geo_month"] for x in nbr])), 3)}}
full_net = W["full"]["net_pct"]
rand, rand_test = [], []
for rep in range(200):
    Tr = np.zeros_like(R)
    for s in range(0, len(R), 30):
        u = rng.uniform(); Tr[s:s + 30] = [u, 1 - u]
    pr, _, _ = simulate(R, Tr, 7)
    rand.append(mstats(pr)["net_pct"]); rand_test.append(mstats(pr, TEST)["geo_month"])
pct = float(np.mean(np.array(rand) < full_net))
chk[6] = {"pass": pct >= 0.95, "numbers": {"full_net": full_net, "beats_share": round(pct, 3),
          "random_median_net": round(float(np.median(rand)), 1), "random_p95_net": round(float(np.percentile(rand, 95)), 1),
          "info_test_geo_beats_share": round(float(np.mean(np.array(rand_test) < W["test"]["geo_month"])), 3)}}
# check 7 analog
wk = (np.arange(len(p1)) + 3) // 7  # 2021-01-01 is a Friday -> weeks start Monday
lg = np.log1p(p1)
wsum = np.bincount(wk, weights=lg)
net_wo5 = float(np.expm1(lg.sum() - np.sort(wsum)[-5:].sum()))
day_share = float(lg.max() / lg.sum())
chk[7] = {"pass": bool(net_wo5 > 0 and day_share <= 0.10), "numbers": {"sleeve_check7": {"W1": True, "W2": True},
          "net_without_5_best_weeks_pct": round(net_wo5 * 100, 1), "max_single_day_share_of_log_growth": round(day_share, 4)}}
f = W["full"]
chk[8] = {"pass": bool(f["geo_month"] > 0 and f["geo_month"] >= 0.5 * f["mean_month"]), "numbers": {"geo": f["geo_month"], "mean": f["mean_month"]}}
OUT["checks"] = {str(k): x for k, x in chk.items()}
OUT["checks_passed"] = int(sum(x["pass"] for x in chk.values()))
t = W["test"]
OUT["bar"] = {"a_8of8": OUT["checks_passed"] == 8, "b_geo_gt_5.33": t["geo_month"] > 5.33, "b_median_gt_3.84": t["median_month"] > 3.84,
              "c_up_ge_18": t["months_up"] >= 18, "c_dd_le_23": t["max_dd_pct"] <= 23,
              "d_2x_cost_2025_26_positive": W["y2025_26_2x"]["net_pct"] > 0}
json.dump(OUT, open("results.json", "w"), indent=1, default=float)
for k in ("dev", "test", "full", "test_2x", "y2025_26_2x", "ytd2026"):
    print("SEL ", k, pick(W[k]))
    print("BEN ", k, pick(WB[k]))
print("W1 test", OUT["W1"]["test"]); print("W2 test", OUT["W2"]["test"])
print("Oct 2026 sel/bench", W["full"]["months"].get("2026-10"), WB["full"]["months"].get("2026-10"))
for k, x in OUT["checks"].items():
    print("check", k, x["pass"], {a: b for a, b in x["numbers"].items() if a != "grid"})
for x in nbr: print("  nb", x)
print(OUT["bar"], OUT["corr_monthly_full"], OUT["corr_monthly_test"], OUT["turnover_per_month"], OUT["mean_w1_weight"])
print("TEST months sel :", {k: v for k, v in W["full"]["months"].items() if k >= "2024-11"})
print("TEST months 5050:", {k: v for k, v in WB["full"]["months"].items() if k >= "2024-11"})
