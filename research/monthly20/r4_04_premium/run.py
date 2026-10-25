"""Pre-registered TEST run + the 8 luck checks + leverage table + 2026 YTD for the ONE selected config in prereg.json.

    python research/monthly20/r4_04_premium/run.py
Writes results.json. Reads prereg.json (written together with PREREG.md before this file was ever run).
"""
import json, sys, time
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import DEV_START, DEV_END, TEST_START, TEST_END, summary
import engine as E, signals as S
from dev import load, weights, tstat, name

PR = json.loads((HERE / "prereg.json").read_text())
SEL = PR["selected"]
t0 = time.time()
cache = {}


def get(tf):
    if tf not in cache:
        cache[tf] = load(tf)
    return cache[tf]


def strip(r):
    return {k: v for k, v in r.items() if not k.startswith("_")}


def go(c, start, end, lev=1.0, cm=1.0, U=None, record=False):
    P, U0 = get(c["tf"])
    W = weights(P, U0 if U is None else U, c)
    return E.run(P, W, c["rb"], start, end, lev=lev, cost_mult=cm, record=record)


P, U = get(SEL["tf"])
W = weights(P, U, SEL)
res = {"agent": "r4_04_premium", "idea": PR["idea"], "selected": SEL}
dev = E.run(P, W, SEL["rb"], DEV_START, DEV_END); dev2 = E.run(P, W, SEL["rb"], DEV_START, DEV_END, cost_mult=2)
test = E.run(P, W, SEL["rb"], TEST_START, TEST_END); test2 = E.run(P, W, SEL["rb"], TEST_START, TEST_END, cost_mult=2)
full = E.run(P, W, SEL["rb"], DEV_START, TEST_END, record=True); full2 = E.run(P, W, SEL["rb"], DEV_START, TEST_END, cost_mult=2)
for k, r in (("dev", dev), ("test", test), ("full", full)):
    r["t_stat"] = round(tstat(r["months"]), 2)
print("DEV", dev["mean_month"], "TEST", test["mean_month"], test["net_pct"], "FULL", full["mean_month"], full["net_pct"], flush=True)
res["attempts"] = [{"name": name(SEL), "prereg": True, "dev": strip(dev), "dev_double_cost": strip(dev2), "test": strip(test),
                    "test_double_cost": strip(test2), "full": strip(full), "full_double_cost": strip(full2)}]
months = full["months"]; vals = np.array(list(months.values()))
checks = {}
# 1 both halves
checks[1] = {"pass": bool(dev["net_pct"] > 0 and dev2["net_pct"] > 0 and test["net_pct"] > 0 and test2["net_pct"] > 0),
             "numbers": {"dev": dev["net_pct"], "dev_2x": dev2["net_pct"], "test": test["net_pct"], "test_2x": test2["net_pct"]}}
# 2 most years
yr = {}
for k, v in months.items():
    yr.setdefault(k[:4], 1.0); yr[k[:4]] *= 1 + v / 100
yr = {k: round((v - 1) * 100, 2) for k, v in yr.items()}
ex21 = np.prod([1 + v / 100 for k, v in months.items() if k >= "2022-01"]) - 1
checks[2] = {"pass": bool(sum(v > 0 for v in yr.values()) >= 4 and ex21 > 0), "numbers": {"years": yr, "2022_on": round(ex21 * 100, 2)}}
# 3 statistically real
rng = np.random.default_rng(7)
nb = int(np.ceil(len(vals) / 3)); boots = []
for _ in range(5000):
    st = rng.integers(0, len(vals) - 2, nb)
    smp = np.concatenate([vals[s:s + 3] for s in st])[:len(vals)]
    boots.append(smp.mean())
p5 = float(np.percentile(boots, 5)); ts = tstat(months)
checks[3] = {"pass": bool(ts >= 2 and p5 > 0), "numbers": {"t": round(ts, 2), "boot_p5_mean_month": round(p5, 3), "n_months": len(vals)}}
# 4 drop 3 best months
rest = np.sort(vals)[:-3]
nr = np.prod(1 + rest / 100) - 1
checks[4] = {"pass": bool(nr > 0), "numbers": {"net_without_3_best": round(nr * 100, 2)}}
# 5 neighbours
nb_rows = []
for c in PR["neighbours"]:
    d = go(c, DEV_START, DEV_END); te = go(c, TEST_START, TEST_END)
    nb_rows.append({"name": name(c), "dev": d["net_pct"], "test": te["net_pct"], "ok": bool(d["net_pct"] > 0 and te["net_pct"] > 0)})
    print("nb", nb_rows[-1], flush=True)
frac = sum(r["ok"] for r in nb_rows) / len(nb_rows)
checks[5] = {"pass": bool(frac >= 0.7), "numbers": {"share_ok": round(frac, 3), "rows": nb_rows}}
# 6 beats chance: random coin picks with the same weights per bar among the same eligible set
valid = ~np.isnan(S.score(P, SEL["fam"], SEL["w"], SEL.get("L")))
elig = U & valid
rnd = []
rng = np.random.default_rng(11)
nz_rows = np.where((W != 0).any(1))[0]
for b in range(200):
    R = np.zeros_like(W)
    for i in nz_rows:
        w = W[i][W[i] != 0]
        idx = np.where(elig[i])[0]
        if len(idx) >= len(w):
            R[i, rng.choice(idx, len(w), replace=False)] = w
    rnd.append(E.run(P, R, SEL["rb"], DEV_START, TEST_END)["net_pct"])
    if b % 50 == 0:
        print("random", b, rnd[-1], round(time.time() - t0), flush=True)
beat = float(np.mean(np.array(rnd) < full["net_pct"]))
checks[6] = {"pass": bool(beat >= 0.95), "numbers": {"beats_share": round(beat, 3), "random_median_net": round(float(np.median(rnd)), 2),
                                                      "random_p95_net": round(float(np.percentile(rnd, 95)), 2), "strategy_net": full["net_pct"]}}
# 7 top-5 coins removed, single-position share
pos = full["_positions"]
by_coin = {}
for j, side, o_, c_, pnl in pos:
    by_coin[j] = by_coin.get(j, 0) + pnl
top5 = [j for j, _ in sorted(by_coin.items(), key=lambda x: -x[1])[:5]]
U5 = U.copy(); U5[:, top5] = False
r5 = go(SEL, DEV_START, TEST_END, U=U5)
total = sum(p[4] for p in pos)
maxpos = max(p[4] for p in pos)
share = maxpos / total if total > 0 else float("inf")
checks[7] = {"pass": bool(r5["net_pct"] > 0 and total > 0 and share <= 0.10),
             "numbers": {"top5": [P.syms[j] for j in top5], "net_without_top5": r5["net_pct"], "total_profit": round(total, 2),
                         "max_single_position_share": round(share, 3) if total > 0 else None, "positions": len(pos)}}
# 8 compounds
g, a = full["geo_month"], full["mean_month"]
checks[8] = {"pass": bool(g > 0 and g >= 0.5 * a), "numbers": {"geo_month": g, "mean_month": a}}
res["checks"] = checks
res["n_pass"] = sum(v["pass"] for v in checks.values())
res["robust"] = res["n_pass"] == 8
res["monthly_1x"] = months
# leverage table, full 2021..2026 and TEST
lt = []
need = None
for lev in [1, 1.5, 2, 3, 4, 5, 6, 8, 10]:
    rf = E.run(P, W, SEL["rb"], DEV_START, TEST_END, lev=lev); rt = E.run(P, W, SEL["rb"], TEST_START, TEST_END, lev=lev)
    row = {"lev": lev, "full_mean": rf["mean_month"], "full_median": rf["median_month"], "full_geo": rf["geo_month"],
           "full_dd": rf["max_dd_pct"], "full_worst": rf["worst_month"], "full_liq": rf["liquidations"], "full_wiped": rf["wiped"],
           "test_mean": rt["mean_month"], "test_geo": rt["geo_month"], "test_dd": rt["max_dd_pct"], "test_liq": rt["liquidations"]}
    lt.append(row); print("lev", row, flush=True)
    if need is None and rt["mean_month"] >= 20 and not rt["wiped"]:
        need = lev
res["leverage_table"] = lt
res["leverage_needed_for_20pct"] = need
res["ytd_2026"] = {k: v for k, v in months.items() if k >= "2026-01"}
res["ytd_2026_net"] = round((np.prod([1 + v / 100 for k, v in months.items() if k >= "2026-01"]) - 1) * 100, 2)
res["best_honest"] = name(SEL)
res["verdict"] = "PASS" if (test["mean_month"] >= 20 and test2["mean_month"] > 0 and not test["wiped"]) else "FAIL"
(HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
print("checks", {k: v["pass"] for k, v in checks.items()}, "ytd", res["ytd_2026_net"], "secs", round(time.time() - t0))
