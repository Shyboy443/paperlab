"""r3: regime-gated momentum rotation. Follows PREREG.md exactly.

    python run.py dev     # stage 1: the 6 gates on DEV only, winner chosen by the pre-registered rule -> dev_selection.json
    python run.py test    # stage 2 (once): TEST for all 6, haircut, the 8 checks on the winner, 2026 view -> results.json,
                          #                 year2026.json, year2026_data.js
"""
import json, math, sys, time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r3_regime_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import engine_r3 as E
import gates as G
from data import build
from common import DEV_START, DEV_END, TEST_START, TEST_END, PROJECT, monthly_returns

HERE = Path(__file__).resolve().parent
OUT = PROJECT / "data" / "monthly20_r3_regime_rotation"
OUT.mkdir(parents=True, exist_ok=True)
STAGE = sys.argv[1] if len(sys.argv) > 1 else "dev"
MAIN = dict(lb=14, k=10, rebal=7, univ=40)
KEYS = ("mean_month", "median_month", "geo_month", "worst_month", "best_month", "months_up", "n_months", "net_pct",
        "max_dd_pct", "liquidations", "trades", "time_in_market", "avg_exposure")
t0 = time.time()
D = build()
P = E.Panel(D)
UM = {}


def um(n, PP=None):
    PP = P if PP is None else PP
    key = (id(PP), n)
    if key not in UM:
        UM[key] = E.universe_mask(PP, n)
    return UM[key]


def short(r):
    return {k: (r[k] if isinstance(r[k], (bool, type(None))) else round(float(r[k]), 4)) for k in KEYS if k in r}


def gate(name, PP=None, base=None, **gp):
    PP = P if PP is None else PP
    base = dict(MAIN) if base is None else base
    return G.GATES[name](PP, um(base["univ"], PP), base, **gp)


def brun(size=None, start=DEV_START, end=TEST_END, cost_mult=1.0, record=False, PP=None, score=None, lb=14, k=10, rebal=7, univ=40):
    PP = P if PP is None else PP
    sc = E.ret_lb(PP, lb) if score is None else score
    return E.run(PP, um(univ, PP), sc, dict(k_long=k, k_short=k, rebal=rebal, lev=1.0, mode="mom"), start, end,
                 cost_mult=cost_mult, record=record, size=size)


def score_dev(r, start=DEV_START, end=DEV_END):
    yrs = (end - start) / (365.25 * 86400_000)
    cagr = ((1 + r["net_pct"] / 100) ** (1 / yrs) - 1) * 100
    return cagr, cagr / max(r["max_dd_pct"], 1e-9)


def compound(vals):
    return float((np.prod([1 + v / 100 for v in vals]) - 1) * 100)


NAMES = list(G.GATES)
SIZES = {n: gate(n) for n in NAMES}
np.savez_compressed(OUT / "gates_main.npz", t=P.t, **SIZES)

if STAGE == "dev":
    rows = {}
    ref = brun(start=DEV_START, end=DEV_END)
    c, sc = score_dev(ref)
    rows["ALWAYS_ON"] = short(ref) | {"cagr": round(c, 2), "score_cagr_per_dd": round(sc, 4)}
    for n in NAMES:
        r = brun(SIZES[n], DEV_START, DEV_END)
        c, sc = score_dev(r)
        rows[n] = short(r) | {"cagr": round(c, 2), "score_cagr_per_dd": round(sc, 4)}
        print(n, rows[n], flush=True)
    elig = [n for n in NAMES if rows[n]["time_in_market"] >= 0.5]
    winner = max(elig, key=lambda n: rows[n]["score_cagr_per_dd"])
    out = dict(rule="DEV CAGR / DEV maxDD, time_in_market >= 50%", dev=rows, eligible=elig, winner=winner,
               beats_always_on_on_dev=bool(rows[winner]["score_cagr_per_dd"] > rows["ALWAYS_ON"]["score_cagr_per_dd"]),
               written=datetime.now(timezone.utc).isoformat())
    (HERE / "dev_selection.json").write_text(json.dumps(out, indent=1))
    print("ALWAYS_ON", rows["ALWAYS_ON"]); print("WINNER", winner, out["beats_always_on_on_dev"])
    sys.exit(0)

# ======================= stage 2: TEST once + checks =======================
sel = json.loads((HERE / "dev_selection.json").read_text())
W = sel["winner"]
SW = SIZES[W]
res = {"agent": "r3_regime_rotation", "prereg": "PREREG.md", "dev_selection": sel, "winner": W}

# TEST for all candidates
tests = {}
for n in ["ALWAYS_ON"] + NAMES:
    s = None if n == "ALWAYS_ON" else SIZES[n]
    t1, t2 = brun(s, TEST_START, TEST_END), brun(s, TEST_START, TEST_END, cost_mult=2)
    f = brun(s, DEV_START, TEST_END)
    fm = np.array(list(f["months"].values()))
    tests[n] = dict(test=short(t1), test_months=t1["months"], test_2xcost=short(t2), full=short(f),
                    full_t_stat=round(float(fm.mean() / (fm.std(ddof=1) / math.sqrt(len(fm)))), 3))
    print("TEST", n, tests[n]["test"], tests[n]["full_t_stat"], flush=True)
res["all_candidates"] = {n: dict(dev=sel["dev"][n], **tests[n]) for n in tests}
hair = 2.0 * math.sqrt(math.log(len(NAMES)) + 1)
res["haircut"] = dict(n_candidates=len(NAMES), t_required=round(hair, 3), winner_full_t=tests[W]["full_t_stat"],
                      always_on_full_t=tests["ALWAYS_ON"]["full_t_stat"], passes=bool(tests[W]["full_t_stat"] >= hair))

# ---------- the 8 checks on the winner ----------
res["checks"] = {}
full = brun(SW, record=True)
dev, test = brun(SW, DEV_START, DEV_END), brun(SW, TEST_START, TEST_END)
dev2, test2 = brun(SW, DEV_START, DEV_END, 2), brun(SW, TEST_START, TEST_END, 2)
months = full["months"]; assert len(months) == 70, len(months)
vals = np.array(list(months.values()))
res["monthly_1x"] = months
res["full_1x"], res["dev_1x"], res["test_1x"] = short(full), short(dev), short(test)
res["dev_1x_2xcost"], res["test_1x_2xcost"] = short(dev2), short(test2)
c1 = dict(dev_net=dev["net_pct"], test_net=test["net_pct"], dev_net_2xcost=dev2["net_pct"], test_net_2xcost=test2["net_pct"])
res["checks"]["1"] = {"pass": bool(all(v > 0 for v in c1.values())), "numbers": c1}
years = {str(y): round(compound([v for k, v in months.items() if k.startswith(str(y))]), 2) for y in range(2021, 2027)}
ex2021 = round(compound([v for k, v in months.items() if k >= "2022-01"]), 2)
c2 = dict(years=years, positive_years=int(sum(v > 0 for v in years.values())), net_2022_01_to_2026_10=ex2021)
res["checks"]["2"] = {"pass": bool(c2["positive_years"] >= 4 and ex2021 > 0), "numbers": c2}
tstat = vals.mean() / (vals.std(ddof=1) / math.sqrt(len(vals)))
rng = np.random.default_rng(7)
nb = math.ceil(len(vals) / 3)
starts = rng.integers(0, len(vals) - 3 + 1, size=(5000, nb))
boot = np.array([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in starts])
c3 = dict(mean_month=round(float(vals.mean()), 3), sd_month=round(float(vals.std(ddof=1)), 3), t_stat=round(float(tstat), 3),
          boot_p5_mean=round(float(np.percentile(boot, 5)), 3), boot_p50_mean=round(float(np.percentile(boot, 50)), 3))
res["checks"]["3"] = {"pass": bool(tstat >= 2.0 and c3["boot_p5_mean"] > 0), "numbers": c3}
best3 = sorted(months, key=months.get, reverse=True)[:3]
rest = compound([v for k, v in months.items() if k not in best3])
res["checks"]["4"] = {"pass": bool(rest > 0), "numbers": dict(best3={k: round(months[k], 2) for k in best3},
                                                            net_without_best3=round(rest, 2), net_all=full["net_pct"])}
print("checks 1-4", {k: res["checks"][k]["pass"] for k in "1234"}, f"[{time.time()-t0:.0f}s]", flush=True)

# 5. neighbours
nb_rows = []
def nb_eval(label, size_fn, **runkw):
    s = size_fn()
    d = brun(s, DEV_START, DEV_END, **runkw); t = brun(s, TEST_START, TEST_END, **runkw)
    nb_rows.append(dict(neighbour=label, dev_net=d["net_pct"], dev_dd=d["max_dd_pct"], dev_tim=round(d["time_in_market"], 3),
                        test_net=t["net_pct"], test_dd=t["max_dd_pct"], test_tim=round(t["time_in_market"], 3),
                        both_positive=bool(d["net_pct"] > 0 and t["net_pct"] > 0)))
    print("nb", nb_rows[-1], flush=True)
for p, v in G.NEIGHBOURS[W]:
    nb_eval(f"gate {p}={v}", lambda: gate(W, **{p: v}))
for p, vs in (("lb", (7, 10, 18, 21, 28)), ("k", (5, 8, 12, 15)), ("rebal", (1, 3, 5, 10, 14)), ("univ", (20, 30, 50, 60))):
    for v in vs:
        base = dict(MAIN); base[p] = v
        nb_eval(f"base {p}={v}", lambda: gate(W, base=base), **{p: v})
nbp = int(sum(r["both_positive"] for r in nb_rows))
res["checks"]["5"] = {"pass": bool(nbp / len(nb_rows) >= 0.70),
                      "numbers": dict(n=len(nb_rows), n_both_positive=nbp, frac=round(nbp / len(nb_rows), 3), grid=nb_rows)}

# 6. beats chance: (a) random gates with same time in market, (b) random coins with the real gate
i0 = int(np.searchsorted(P.t, DEV_START)); i1 = int(np.searchsorted(P.t, TEST_END))
reb = np.arange(i0, i1, 7)
NR = 500
ra, rb_ = [], []
for s in range(NR):
    g = np.random.default_rng(2000 + s)
    sz = SW.copy(); sz[reb] = g.permutation(SW[reb])
    ra.append(brun(sz)["net_pct"])
    sc = np.random.default_rng(1000 + s).random(P.c.shape)
    rb_.append(brun(SW, score=sc)["net_pct"])
    if s % 100 == 0:
        print("rand", s, ra[-1], rb_[-1], f"[{time.time()-t0:.0f}s]", flush=True)
ra, rb_ = np.array(ra), np.array(rb_)
np.save(OUT / "random_gate_net.npy", ra); np.save(OUT / "random_coin_net.npy", rb_)
pa, pb = float((ra < full["net_pct"]).mean() * 100), float((rb_ < full["net_pct"]).mean() * 100)
c6 = dict(n_random=NR, strategy_net=full["net_pct"], time_in_market=round(full["time_in_market"], 3),
          a_random_gate=dict(median_net=round(float(np.median(ra)), 2), p95_net=round(float(np.percentile(ra, 95)), 2),
                             p5_net=round(float(np.percentile(ra, 5)), 2), beats_pct=round(pa, 1)),
          b_random_coins=dict(median_net=round(float(np.median(rb_)), 2), p95_net=round(float(np.percentile(rb_, 95)), 2),
                              p5_net=round(float(np.percentile(rb_, 5)), 2), beats_pct=round(pb, 1)))
res["checks"]["6"] = {"pass": bool(pa >= 95 and pb >= 95), "numbers": c6}
print("check6", c6, flush=True)

# 7. not a few coins or trades
pos = full["_positions"]
coin_pnl = np.zeros(len(P.syms))
for j, side, oi, ci, pnl, eq0 in pos:
    coin_pnl[j] += pnl
top5 = np.argsort(-coin_pnl)[:5]
net_profit = full["_curve"][-1][1] - full["_curve"][0][1]
P5 = E.Panel(D, np.setdiff1d(np.arange(len(P.syms)), top5))
s5 = gate(W, PP=P5)
r7 = brun(s5, PP=P5)


def day(i):
    return datetime.fromtimestamp(int(P.t[min(i, len(P.t) - 1)]) / 1000, timezone.utc).strftime("%Y-%m-%d")


pos_sorted = sorted(pos, key=lambda x: -x[4])
max_share = float(pos_sorted[0][4] / net_profit * 100) if net_profit > 0 else None
c7 = dict(top5_coins={P.syms[j]: dict(pnl_usdt=round(float(coin_pnl[j]), 2),
                                      share_pct=round(float(coin_pnl[j] / net_profit * 100), 2) if net_profit > 0 else None) for j in top5},
          net_without_top5_coins=r7["net_pct"], full_net_profit_usdt=round(float(net_profit), 2), n_positions=len(pos),
          largest_position_share_pct=round(max_share, 2) if max_share is not None else None,
          top_positions=[dict(coin=P.syms[j], side="long" if s > 0 else "short", opened=day(oi), closed=day(ci),
                              pnl_usdt=round(float(pnl), 2), pct_of_equity_at_open=round(float(pnl / eq0 * 100), 2))
                         for j, s, oi, ci, pnl, eq0 in pos_sorted[:5]])
res["checks"]["7"] = {"pass": bool(r7["net_pct"] > 0 and max_share is not None and max_share <= 10), "numbers": c7}
print("check7", {k: v for k, v in c7.items() if k != "top_positions"}, flush=True)

c8 = dict(mean_month=full["mean_month"], geo_month=full["geo_month"], median_month=full["median_month"],
          ratio_geo_to_mean=round(float(full["geo_month"] / full["mean_month"]), 3) if full["mean_month"] else None)
res["checks"]["8"] = {"pass": bool(full["geo_month"] > 0 and full["geo_month"] >= 0.5 * full["mean_month"]), "numbers": c8}
res["robust"] = bool(all(c["pass"] for c in res["checks"].values()))

# ---------- drawdown avoided, weekly counts ----------
ao = brun(None, record=True)
def weekly(curve, size):
    eq = {t: e for t, e in curve}
    out = []
    for a, b in zip(reb[:-1], reb[1:]):
        e0, e1 = eq.get(int(P.t[a])), eq.get(int(P.t[b]))
        if e0 and e1:
            out.append(dict(start=day(a), ret=(e1 / e0 - 1) * 100, on=float(size[a]) if size is not None else 1.0))
    return out
wg, wa = weekly(full["_curve"], SW), weekly(ao["_curve"], None)
res["weekly_full"] = dict(
    gated=dict(weeks=len(wg), on=sum(w["on"] > 0 for w in wg), up=sum(w["ret"] > 1e-9 for w in wg),
               down=sum(w["ret"] < -1e-9 for w in wg), flat=sum(abs(w["ret"]) <= 1e-9 for w in wg)),
    always_on=dict(weeks=len(wa), up=sum(w["ret"] > 0 for w in wa), down=sum(w["ret"] <= 0 for w in wa)),
    always_on_return_in_on_weeks=round(compound([a["ret"] for a, g in zip(wa, wg) if g["on"] > 0]), 2),
    always_on_return_in_off_weeks=round(compound([a["ret"] for a, g in zip(wa, wg) if g["on"] == 0]), 2))
res["drawdown"] = dict(always_on_full_maxdd=ao["max_dd_pct"], gated_full_maxdd=full["max_dd_pct"],
                       always_on_full_net=ao["net_pct"], gated_full_net=full["net_pct"])

# ---------- 2026 calendar-day view (same format as r2_rotation/year2026_data.js) ----------
YEAR = 2026
def ymd(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc)
def year_view(size, cost_mult):
    r = brun(size, cost_mult=cost_mult, record=True)
    curve = r["_curve"]
    y0 = int(datetime(YEAR, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    start_eq = [e for t, e in curve if t <= y0][-1]
    days, prev = [], start_eq
    for t, e in curve:
        if t <= y0:
            continue
        d = ymd(t - 1)
        bar = int(np.searchsorted(P.t, t - 1, side="right") - 1)
        days.append({"date": d.strftime("%Y-%m-%d"), "ret": (e / prev - 1) * 100, "equity": e / start_eq * 1000})
        prev = e
    def group(keyf):
        out, eq0 = OrderedDict(), 1000.0
        for d in days:
            k = keyf(d["date"])
            if k not in out:
                out[k] = {"start": eq0, "end": eq0, "days": 0, "first": d["date"]}
            out[k]["end"] = d["equity"]; out[k]["days"] += 1; out[k]["last"] = d["date"]
            eq0 = d["equity"]
        return [{"key": k, "from": v["first"], "to": v["last"], "days": v["days"],
                 "ret": (v["end"] / v["start"] - 1) * 100, "equity": v["end"]} for k, v in out.items()]
    week = lambda s: "%d-W%02d" % datetime.strptime(s, "%Y-%m-%d").isocalendar()[:2]
    months_, weeks_ = group(lambda s: s[:7]), group(week)
    peak, dd = 1000.0, 0.0
    for d in days:
        peak = max(peak, d["equity"]); dd = max(dd, 1 - d["equity"] / peak)
    rets = [d["ret"] for d in days]
    stats = {"end": days[-1]["equity"], "net": days[-1]["equity"] / 10 - 100, "max_dd": dd * 100, "days": len(days),
             "days_up": sum(x > 0 for x in rets), "best_day": max(rets), "worst_day": min(rets),
             "weeks_up": sum(w["ret"] > 0 for w in weeks_), "weeks": len(weeks_),
             "months_up": sum(m["ret"] > 0 for m in months_), "months": len(months_)}
    iend = int(np.searchsorted(P.t, TEST_END))
    held = sorted([(P.syms[j], int(s)) for j, s, o, c, *_ in r["_positions"] if c >= iend], key=lambda x: -x[1])
    rb26 = [i for i in reb if P.t[i] >= y0]
    gate_weeks = [{"date": day(i), "on": float(size[i]) if size is not None else 1.0} for i in rb26]
    stats["rebalances"] = len(rb26); stats["rebalances_on"] = sum(g["on"] > 0 for g in gate_weeks)
    return {"days": days, "weeks": weeks_, "months": months_, "stats": stats, "holding_now": held, "gate_weeks": gate_weeks}

y = {"strategy": f"Momentum rotation, 1x, regime-gated ({W}): every 7 days long top-10 / short bottom-10 14-day performers "
     f"of the top-40 liquid USDT perps (delisted coins included), flat in cash when the {W} gate is off", "year": YEAR,
     "normal": year_view(SW, 1.0), "double": year_view(SW, 2.0)}
y_ao = year_view(None, 1.0)
(HERE / "year2026.json").write_text(json.dumps(y, indent=1, default=float))
(HERE / "year2026_data.js").write_text("window.Y26=" + json.dumps(y, default=float) + ";\n")
res["year2026"] = dict(gated_months={m["key"]: round(m["ret"], 2) for m in y["normal"]["months"]},
                       always_on_months={m["key"]: round(m["ret"], 2) for m in y_ao["months"]},
                       gated_stats={k: (round(v, 2) if isinstance(v, float) else v) for k, v in y["normal"]["stats"].items()},
                       always_on_stats={k: (round(v, 2) if isinstance(v, float) else v) for k, v in y_ao["stats"].items()},
                       gate_weeks=y["normal"]["gate_weeks"], double_cost_net=round(y["double"]["stats"]["net"], 2))
# 2026 for every candidate (calendar days), for the honest table
res["year2026_all_candidates"] = {}
for n in ["ALWAYS_ON"] + NAMES:
    v = year_view(None if n == "ALWAYS_ON" else SIZES[n], 1.0)
    res["year2026_all_candidates"][n] = dict(net=round(v["stats"]["net"], 2), max_dd=round(v["stats"]["max_dd"], 2),
                                            on=f'{v["stats"]["rebalances_on"]}/{v["stats"]["rebalances"]}')
(HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
print("ROBUST", res["robust"], {k: v["pass"] for k, v in res["checks"].items()}, res["haircut"], f"[{time.time()-t0:.0f}s]")
print("2026", res["year2026"]["gated_months"], res["year2026"]["gated_stats"])
print("2026 all", res["year2026_all_candidates"])
