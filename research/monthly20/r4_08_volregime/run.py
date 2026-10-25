"""r4_08_volregime: pre-registered TEST run + the 8 "not luck" checks + leverage table. Reproduces results.json.

    python data4h.py     # once: 4h survivorship-free panel from agent 07's archive cache
    python dev_scan.py   # DEV-only grid + selection (dev_scan.json)
    python run.py        # this file (touches TEST; see PREREG.md)
"""
import json, math, sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_08_volregime")
import engine as E, data4h
from common import DEV_START, DEV_END, TEST_START, TEST_END

HERE = Path(__file__).resolve().parent
t0 = time.time()
D = data4h.build()
P = E.Panel(D)
SEL = json.loads((HERE / "dev_scan.json").read_text())["selected"]
MAIN = dict(SEL["params"])
assert SEL["name"] == "atrp_q0.05_htf_k4.0_H42_both_u60", SEL["name"]
KEYS = ("mean_month", "median_month", "geo_month", "worst_month", "best_month", "months_up", "n_months", "net_pct",
        "max_dd_pct", "liquidations", "trades", "win_rate", "top3_share_of_profit", "wiped", "n_long", "n_short")


def short(r):
    return {k: (r[k] if (r[k] is None or isinstance(r[k], (bool, np.bool_))) else float(r[k])) for k in KEYS if k in r}


def cfg(start, end, cost_mult=1.0, lev=1.0, record=False, panel=None, **over):
    p = dict(MAIN); p.update(over)
    return E.run(panel or P, p, start, end, cost_mult=cost_mult, lev=lev, record=record)


def compound(vals):
    return float((np.prod([1 + v / 100 for v in vals]) - 1) * 100)


def log(*a):
    print(*a, f"[{time.time()-t0:.0f}s]", flush=True)


res = {"agent": "r4_08_volregime",
       "idea": "Per coin on 4h bars: after ATR% compresses to the bottom 5% of its own 30-day history, enter the 20-bar breakout "
               "only in the direction of the 50-day EMA trend; 4-ATR chandelier trail, 7-day max hold; equal 1% risk per "
               "trade, max 8 positions, point-in-time top-60 USDT perps incl. delisted coins",
       "selected": SEL["name"], "params": MAIN, "checks": {}, "attempts": []}

# ---------- attempts ----------
full = cfg(DEV_START, TEST_END, record=True)
dev, test = cfg(DEV_START, DEV_END), cfg(TEST_START, TEST_END, record=True)
dev2, test2 = cfg(DEV_START, DEV_END, cost_mult=2), cfg(TEST_START, TEST_END, cost_mult=2)
log("full", short(full)); log("dev", short(dev)); log("test", short(test)); log("test2x", short(test2))
top_tr = max(x[4] for x in test["_trades"]); test_profit = test["_curve"][-1][1] - test["_curve"][0][1]
res["attempts"].append(dict(name="A1_1x", prereg=True, dev=short(dev) | {"months": dev["months"]}, test=short(test) | {"months": test["months"]},
                            test_double_cost=short(test2) | {"months": test2["months"]}, dev_double_cost=short(dev2),
                            largest_test_trade_share_of_profit_pct=round(float(top_tr / test_profit * 100), 2) if test_profit > 0 else None))
a2d, a2t, a2t2 = cfg(DEV_START, DEV_END, lev=7), cfg(TEST_START, TEST_END, lev=7), cfg(TEST_START, TEST_END, lev=7, cost_mult=2)
res["attempts"].append(dict(name="A2_7x", prereg=True, dev=short(a2d), test=short(a2t) | {"months": a2t["months"]}, test_double_cost=short(a2t2)))
log("A2 test", short(a2t))
months = full["months"]
assert len(months) == 70, len(months)
vals = np.array(list(months.values()))
res["monthly_1x"] = months
res["full_1x"] = short(full); res["dev_1x"] = short(dev); res["test_1x"] = short(test)
res["dev_1x_2xcost"] = short(dev2); res["test_1x_2xcost"] = short(test2)
res["ytd_2026_1x"] = dict(months={k: v for k, v in months.items() if k.startswith("2026")},
                          compounded_pct=round(compound([v for k, v in months.items() if k.startswith("2026")]), 2))

# 1. both halves
c1 = dict(dev_net=dev["net_pct"], test_net=test["net_pct"], dev_net_2xcost=dev2["net_pct"], test_net_2xcost=test2["net_pct"])
res["checks"]["1"] = {"pass": bool(all(v > 0 for v in c1.values())), "numbers": c1}
# 2. most years
years = {str(y): round(compound([v for k, v in months.items() if k.startswith(str(y))]), 2) for y in range(2021, 2027)}
ex2021 = round(compound([v for k, v in months.items() if k >= "2022-01"]), 2)
c2 = dict(years=years, positive_years=int(sum(v > 0 for v in years.values())), net_2022_01_to_2026_10=ex2021)
res["checks"]["2"] = {"pass": bool(c2["positive_years"] >= 4 and ex2021 > 0), "numbers": c2}
# 3. statistically real
tstat = vals.mean() / (vals.std(ddof=1) / math.sqrt(len(vals)))
rng = np.random.default_rng(7)
nb = math.ceil(len(vals) / 3)
starts = rng.integers(0, len(vals) - 3 + 1, size=(5000, nb))
boot = np.array([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in starts])
c3 = dict(mean_month=round(float(vals.mean()), 3), sd_month=round(float(vals.std(ddof=1)), 3), t_stat=round(float(tstat), 3),
          boot_p5_mean=round(float(np.percentile(boot, 5)), 3), boot_p50_mean=round(float(np.percentile(boot, 50)), 3))
res["checks"]["3"] = {"pass": bool(tstat >= 2.0 and c3["boot_p5_mean"] > 0), "numbers": c3}
for nm, a, b in (("dev", DEV_START, DEV_END), ("test", TEST_START, TEST_END)):
    v = np.array(list((dev if nm == "dev" else test)["months"].values()))
    res[f"t_stat_{nm}"] = round(float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v)))), 3)
# 4. not a few months
best3 = sorted(months, key=months.get, reverse=True)[:3]
rest = compound([v for k, v in months.items() if k not in best3])
c4 = dict(best3={k: round(months[k], 2) for k in best3}, net_without_best3=round(rest, 2), net_all=full["net_pct"])
res["checks"]["4"] = {"pass": bool(rest > 0), "numbers": c4}
log("checks 1-4", {k: res["checks"][k]["pass"] for k in "1234"})

# 5. neighbour grid (PREREG)
GRID = [("q", v) for v in (0.025, 0.0375, 0.0625, 0.075)] + [("k", v) for v in (2.0, 3.0, 5.0, 6.0)] + \
       [("H", v) for v in (21, 32, 52, 63)] + [("univ", v) for v in (30, 45, 75, 90)] + \
       [("W", v) for v in (90, 135, 225, 270)] + [("A", v) for v in (3, 4, 8, 9)] + \
       [("D", v) for v in (10, 15, 25, 30)] + [("E", v) for v in (150, 225, 375, 450)] + [("M", v) for v in (4, 6, 10, 12)]
rows = []
for nmp, v in GRID:
    d = cfg(DEV_START, DEV_END, **{nmp: v}); t = cfg(TEST_START, TEST_END, **{nmp: v})
    rows.append(dict(param=nmp, value=v, dev_net=d["net_pct"], dev_mean=d["mean_month"], dev_geo=d["geo_month"], dev_dd=d["max_dd_pct"],
                     test_net=t["net_pct"], test_mean=t["mean_month"], test_geo=t["geo_month"], test_dd=t["max_dd_pct"],
                     both_positive=bool(d["net_pct"] > 0 and t["net_pct"] > 0)))
    log("nb", rows[-1])
nbp = int(sum(r["both_positive"] for r in rows))
res["checks"]["5"] = {"pass": bool(nbp / len(rows) >= 0.70), "numbers": dict(n=len(rows), n_both_positive=nbp, frac=round(nbp / len(rows), 3), grid=rows)}

# DEV-scan family on TEST (descriptive only, not part of any check): how did the other 191 DEV configs do on TEST?
fam = []
for r in json.loads((HERE / "dev_scan.json").read_text())["rows"]:
    t = E.run(P, r["params"], TEST_START, TEST_END)
    fam.append(dict(name=r["name"], dev_geo=r["geo"], dev_eligible=r["eligible"], test_mean=float(t["mean_month"]),
                    test_geo=float(t["geo_month"]), test_net=float(t["net_pct"]), test_dd=float(t["max_dd_pct"])))
res["dev_family_on_test"] = dict(n=len(fam), n_test_net_pos=sum(f["test_net"] > 0 for f in fam),
                                 n_eligible=sum(f["dev_eligible"] for f in fam),
                                 n_eligible_test_net_pos=sum(f["test_net"] > 0 for f in fam if f["dev_eligible"]), rows=fam)
log("family", {k: v for k, v in res["dev_family_on_test"].items() if k != "rows"})

# 6. beats chance
NR = 300
rand = np.array([E.run(P, MAIN, DEV_START, TEST_END, rand_seed=s)["net_pct"] for s in range(NR)], dtype=float)
pct = float((rand < full["net_pct"]).mean() * 100)
res["checks"]["6"] = {"pass": bool(pct >= 95), "numbers": dict(n_random=NR, strategy_net=full["net_pct"],
                      random_median_net=round(float(np.median(rand)), 2), random_p95_net=round(float(np.percentile(rand, 95)), 2),
                      random_p5_net=round(float(np.percentile(rand, 5)), 2), strategy_beats_pct=round(pct, 1))}
log("check6", res["checks"]["6"])

# 7. not a few coins or trades
tr = full["_trades"]
coin_pnl = np.zeros(len(P.syms))
for j, side, oi, ci, pnl, eq0 in tr:
    coin_pnl[j] += pnl
top5 = np.argsort(-coin_pnl)[:5]
net_profit = full["_curve"][-1][1] - full["_curve"][0][1]
P5 = E.Panel(D, np.setdiff1d(np.arange(len(P.syms)), top5))
r7 = E.run(P5, MAIN, DEV_START, TEST_END)
srt = sorted(tr, key=lambda x: -x[4])


def day(i):
    return datetime.fromtimestamp(int(P.t[min(i, len(P.t) - 1)]) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


max_share = float(srt[0][4] / net_profit * 100) if net_profit > 0 else None
c7 = dict(top5_coins={P.syms[j]: dict(pnl_usdt=round(float(coin_pnl[j]), 2), share_pct=round(float(coin_pnl[j] / net_profit * 100), 2) if net_profit > 0 else None) for j in top5},
          net_without_top5_coins=r7["net_pct"], mean_month_without_top5=r7["mean_month"], geo_month_without_top5=r7["geo_month"],
          full_net_profit_usdt=round(float(net_profit), 2), n_trades=len(tr), largest_trade_share_pct=round(max_share, 2) if max_share is not None else None,
          top_trades=[dict(coin=P.syms[j], side="long" if s > 0 else "short", opened=day(oi), closed=day(ci), pnl_usdt=round(float(pnl), 2),
                           pct_of_equity_at_open=round(float(pnl / e0 * 100), 2)) for j, s, oi, ci, pnl, e0 in srt[:5]],
          largest_trade_pct_of_equity=round(float(max(x[4] / x[5] for x in tr) * 100), 2))
res["checks"]["7"] = {"pass": bool(r7["net_pct"] > 0 and max_share is not None and max_share <= 10), "numbers": c7}
log("check7", {k: v for k, v in c7.items() if k != "top_trades"})
# 8. compounds
c8 = dict(mean_month=full["mean_month"], geo_month=full["geo_month"], median_month=full["median_month"],
          ratio_geo_to_mean=round(float(full["geo_month"] / full["mean_month"]), 3) if full["mean_month"] else None)
res["checks"]["8"] = {"pass": bool(full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"]), "numbers": c8}
res["n_pass"] = int(sum(c["pass"] for c in res["checks"].values()))
res["robust"] = bool(res["n_pass"] == 8)

# side split (descriptive)
res["side_split_full"] = {nm: round(float(sum(x[4] for x in tr if (x[1] > 0) == (nm == "long"))), 2) for nm in ("long", "short")}

# leverage table 1..10
lt = []
for lev in range(1, 11):
    row = {"lev": lev}
    for nm, a, b in (("full", DEV_START, TEST_END), ("dev", DEV_START, DEV_END), ("test", TEST_START, TEST_END)):
        row[nm] = short(cfg(a, b, lev=lev))
    row["test_2xcost"] = short(cfg(TEST_START, TEST_END, lev=lev, cost_mult=2))
    lt.append(row); log("lev", lev, row["full"]["mean_month"], row["test"]["mean_month"], row["full"]["max_dd_pct"])
res["leverage_table"] = lt
need_full = next((r["lev"] for r in lt if r["full"]["mean_month"] >= 20), None)
need_test = next((r["lev"] for r in lt if r["test"]["mean_month"] >= 20), None)
res["leverage_needed_for_20pct"] = need_full
res["leverage_needed_for_20pct_test_only"] = need_test
pr = res["attempts"][0]
res["verdict"] = "PASS" if (test["mean_month"] >= 20 and test2["mean_month"] > 0 and not test["wiped"]
                            and (pr["largest_test_trade_share_of_profit_pct"] or 100) <= 50) else "FAIL"
res["best_honest"] = "A1_1x"
res["universe_note"] = f"{len(P.syms)} crypto USDT perps from data.binance.vision ({int(D['trading_today'].sum())} trading today), 4h bars"
(HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
log("ROBUST", res["robust"], res["n_pass"], {k: v["pass"] for k, v in res["checks"].items()}, "verdict", res["verdict"],
    "lev needed", need_full, need_test)
