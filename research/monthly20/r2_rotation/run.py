"""Round-2 "not luck" checks for cross-sectional momentum rotation (agent 02's P4 rules at 1x), on a survivorship-free
universe built from the data.binance.vision archive (incl. delisted coins). Reproduces every number in results.json.

    python data.py     # once: builds data/monthly20_r2_rotation/panel_1d.npz from agent 07's local archive cache
    python sanity.py   # engine reproduces agent 02's P4 1x numbers on its own panel; survivors-only vs full archive
    python run.py      # the 8 checks (see ROBUSTNESS.md and PREREG.md)
"""
import json, math, sys, time
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
import engine_r2 as E
from data import build, OUT
from common import DEV_START, DEV_END, TEST_START, TEST_END, summary
from pathlib import Path

HERE = Path(__file__).resolve().parent
FULL_START = DEV_START
MAIN = dict(lb=14, k=10, rebal=7, univ=40)
KEYS = ("mean_month", "median_month", "geo_month", "worst_month", "best_month", "months_up", "n_months", "net_pct",
        "max_dd_pct", "liquidations", "trades", "top3_share_of_profit")
t0 = time.time()
D = build()
P = E.Panel(D)
UM = {}


def um(n):
    if n not in UM:
        UM[n] = E.universe_mask(P, n)
    return UM[n]


def params(k=10, rebal=7, lev=1.0, kS=None):
    return dict(k_long=k, k_short=k if kS is None else kS, rebal=rebal, lev=lev, mode="mom")


def short(r):
    out = {}
    for k in KEYS:
        if k in r:
            v = r[k]
            out[k] = v if (v is None or isinstance(v, bool)) else float(v)
    return out


def cfg_run(lb=14, k=10, rebal=7, univ=40, lev=1.0, kS=None, start=FULL_START, end=TEST_END, cost_mult=1.0, record=False):
    return E.run(P, um(univ), E.ret_lb(P, lb), params(k, rebal, lev, kS), start, end, cost_mult=cost_mult, record=record)


def compound(vals):
    return float((np.prod([1 + v / 100 for v in vals]) - 1) * 100)


res = {"agent": "r2_rotation", "candidate": "agent 02 P4 rules at 1x: 14-day return, long top 10 / short bottom 10 of the "
       "top-40 liquid USDT perps, rebalanced every 7 days, survivorship-free archive universe", "checks": {}}

# ---------- main runs ----------
full = cfg_run(record=True)
dev, test = cfg_run(start=DEV_START, end=DEV_END), cfg_run(start=TEST_START, end=TEST_END)
dev2, test2 = cfg_run(start=DEV_START, end=DEV_END, cost_mult=2), cfg_run(start=TEST_START, end=TEST_END, cost_mult=2)
months = full["months"]
assert len(months) == 70, len(months)
vals = np.array(list(months.values()))
res["monthly_1x"] = months
res["full_1x"], res["dev_1x"], res["test_1x"] = short(full), short(dev), short(test)
res["dev_1x_2xcost"], res["test_1x_2xcost"] = short(dev2), short(test2)
res["test_months_1x"] = test["months"]
print("full", short(full), "\ndev", short(dev), "\ntest", short(test), f"[{time.time()-t0:.0f}s]", flush=True)

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

# 4. not a few months
best3 = sorted(months, key=months.get, reverse=True)[:3]
rest = compound([v for k, v in months.items() if k not in best3])
c4 = dict(best3={k: round(months[k], 2) for k in best3}, net_without_best3=round(rest, 2), net_all=full["net_pct"])
res["checks"]["4"] = {"pass": bool(rest > 0), "numbers": c4}
print("checks 1-4", {k: res["checks"][k] for k in "1234"}, f"[{time.time()-t0:.0f}s]", flush=True)

# 5. neighbour grid (pre-declared in PREREG.md)
GRID = [("lb", v) for v in (7, 10, 18, 21, 28)] + [("k", v) for v in (5, 8, 12, 15)] + \
       [("rebal", v) for v in (1, 3, 5, 10, 14)] + [("univ", v) for v in (20, 30, 50, 60)]
nb_rows = []
for name, v in GRID:
    kw = dict(MAIN); kw[name] = v
    d = cfg_run(**kw, start=DEV_START, end=DEV_END); t = cfg_run(**kw, start=TEST_START, end=TEST_END)
    nb_rows.append(dict(param=name, value=v, dev_net=d["net_pct"], dev_mean=d["mean_month"], dev_geo=d["geo_month"], dev_dd=d["max_dd_pct"],
                        test_net=t["net_pct"], test_mean=t["mean_month"], test_geo=t["geo_month"], test_dd=t["max_dd_pct"],
                        both_positive=bool(d["net_pct"] > 0 and t["net_pct"] > 0)))
    print("nb", nb_rows[-1], flush=True)
nbp = int(sum(r["both_positive"] for r in nb_rows))
frac = nbp / len(nb_rows)
res["checks"]["5"] = {"pass": bool(frac >= 0.70), "numbers": dict(n=len(nb_rows), n_both_positive=nbp, frac=round(frac, 3), grid=nb_rows)}

# family members (not counted in check 5)
fam = []
for lb in (7, 14, 28):
    for rb in (1, 7):
        for k, kS, lab in ((10, 10, "LS10"), (10, 0, "long_top10"), (3, 0, "long_top3")):
            d = cfg_run(lb=lb, k=k, kS=kS, rebal=rb, start=DEV_START, end=DEV_END)
            t = cfg_run(lb=lb, k=k, kS=kS, rebal=rb, start=TEST_START, end=TEST_END)
            fam.append(dict(legs=lab, lb=lb, rebal=rb, dev_mean=d["mean_month"], dev_geo=d["geo_month"], dev_dd=d["max_dd_pct"], dev_net=d["net_pct"],
                            test_mean=t["mean_month"], test_geo=t["geo_month"], test_dd=t["max_dd_pct"], test_net=t["net_pct"]))
            print("fam", fam[-1], flush=True)
res["family_not_in_check5"] = fam
print(f"[{time.time()-t0:.0f}s]", flush=True)

# 6. beats chance: 500 random-pick versions
NR = 500
rand_net = []
um40 = um(40)
for s in range(NR):
    sc = np.random.default_rng(1000 + s).random(P.c.shape)
    r = E.run(P, um40, sc, params(), FULL_START, TEST_END)
    rand_net.append(r["net_pct"])
    if s % 50 == 0:
        print("rand", s, r["net_pct"], f"[{time.time()-t0:.0f}s]", flush=True)
rand_net = np.array(rand_net, dtype=float)
pct = float((rand_net < full["net_pct"]).mean() * 100)
c6 = dict(n_random=NR, strategy_net=full["net_pct"], random_median_net=round(float(np.median(rand_net)), 2),
          random_p95_net=round(float(np.percentile(rand_net, 95)), 2), random_p5_net=round(float(np.percentile(rand_net, 5)), 2),
          strategy_beats_pct=round(pct, 1))
res["checks"]["6"] = {"pass": bool(pct >= 95), "numbers": c6}
print("check6", c6, flush=True)

# 7. not a few coins or trades
pos = full["_positions"]
coin_pnl = np.zeros(len(P.syms)); coin_ret = np.zeros(len(P.syms))
for j, side, oi, ci, pnl, eq0 in pos:
    coin_pnl[j] += pnl; coin_ret[j] += pnl / eq0 * 100
top5 = np.argsort(-coin_pnl)[:5]
net_profit = full["_curve"][-1][1] - full["_curve"][0][1]
keep = np.setdiff1d(np.arange(len(P.syms)), top5)
P5 = E.Panel(D, keep)
r7 = E.run(P5, E.universe_mask(P5, 40), E.ret_lb(P5, 14), params(), FULL_START, TEST_END)
pos_sorted = sorted(pos, key=lambda x: -x[4])


def day(i):
    return datetime.fromtimestamp(int(P.t[min(i, len(P.t) - 1)]) / 1000, timezone.utc).strftime("%Y-%m-%d")


top_pos = [dict(coin=P.syms[j], side="long" if s > 0 else "short", opened=day(oi), closed=day(ci), pnl_usdt=round(float(pnl), 2),
                share_of_net_profit_pct=round(float(pnl / net_profit * 100), 2), pct_of_equity_at_open=round(float(pnl / eq0 * 100), 2))
           for j, s, oi, ci, pnl, eq0 in pos_sorted[:5]]
ret_contrib = sorted([pnl / eq0 * 100 for *_, pnl, eq0 in pos], reverse=True)
max_share = float(pos_sorted[0][4] / net_profit * 100)
c7 = dict(top5_coins={P.syms[j]: dict(pnl_usdt=round(float(coin_pnl[j]), 2), share_pct=round(float(coin_pnl[j] / net_profit * 100), 2),
                                      sum_pct_of_equity=round(float(coin_ret[j]), 2)) for j in top5},
          net_without_top5_coins=r7["net_pct"], mean_month_without_top5=r7["mean_month"], geo_month_without_top5=r7["geo_month"],
          full_net_profit_usdt=round(float(net_profit), 2), n_positions=len(pos), largest_position_share_pct=round(max_share, 2),
          top_positions=top_pos,
          info_equity_relative=dict(sum_all_pct=round(float(sum(ret_contrib)), 2), largest_pct=round(float(ret_contrib[0]), 2),
                                    largest_share_pct=round(float(ret_contrib[0] / sum(ret_contrib) * 100), 2) if sum(ret_contrib) > 0 else None))
res["checks"]["7"] = {"pass": bool(r7["net_pct"] > 0 and max_share <= 10), "numbers": c7}
print("check7", c7, flush=True)

# 8. compounds
c8 = dict(mean_month=full["mean_month"], geo_month=full["geo_month"], median_month=full["median_month"],
          ratio_geo_to_mean=round(float(full["geo_month"] / full["mean_month"]), 3) if full["mean_month"] else None)
res["checks"]["8"] = {"pass": bool(full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"]), "numbers": c8}

res["robust"] = bool(all(c["pass"] for c in res["checks"].values()))

# leverage table
lt = []
for lev in (1.0, 1.5, 2.0):
    row = {"lev": lev}
    for nm, a, b in (("full", FULL_START, TEST_END), ("dev", DEV_START, DEV_END), ("test", TEST_START, TEST_END)):
        r = cfg_run(lev=lev, start=a, end=b)
        row[nm] = short(r) | {"wiped": bool(r["wiped"])}
    rr = cfg_run(lev=lev, start=TEST_START, end=TEST_END, cost_mult=2)
    row["test_2xcost_mean"] = float(rr["mean_month"])
    lt.append(row); print("lev", row, flush=True)
res["leverage_table"] = lt
ntoday = int(D["trading_today"].sum())
res["universe_note"] = (f"{len(P.syms)} crypto USDT perps from the data.binance.vision archive ({ntoday} trading today, "
                        f"{len(P.syms) - ntoday} delisted/settling); {len(D['dropped'])} TradFi/index contracts dropped")
res["sanity"] = json.loads((OUT / "sanity.json").read_text()) if (OUT / "sanity.json").exists() else None
np.save(OUT / "random_net.npy", rand_net)
(HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
print("ROBUST", res["robust"], {k: v["pass"] for k, v in res["checks"].items()}, f"[{time.time()-t0:.0f}s]")
