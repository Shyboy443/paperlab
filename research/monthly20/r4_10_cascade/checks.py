"""Shared runners and the 8 'not luck' checks (ROBUSTNESS.md) for r4_10_cascade."""
import math, sys
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_10_cascade")
import engine as E
from common import summary, monthly_returns, DEV_START, DEV_END, TEST_START, TEST_END
import data_cascade as DC

FEATS = {}


def run(coins, p, lev=1.0, cost_mult=1.0, start=DEV_START, end=TEST_END, exclude=(), trades=None):
    if trades is None:
        trades = E.gen_trades(coins, p, lev, cost_mult, start, end, FEATS, exclude)
    curve, acc = E.portfolio(coins, trades, p["K"], lev, start, end)
    s = summary(curve, [t["pnl"] for t in acc])
    s["liquidations"] = sum(t["liq"] for t in acc)
    return curve, acc, s


def tstat(vals):
    v = np.array(vals)
    return float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v)))) if len(v) > 2 and v.std() > 0 else 0.0


def block_boot(vals, n=5000, block=3, seed=7):
    rng = np.random.default_rng(seed)
    v = np.array(vals); L = len(v)
    nb = math.ceil(L / block)
    starts = rng.integers(0, L - block + 1, size=(n, nb))
    idx = (starts[:, :, None] + np.arange(block)).reshape(n, -1)[:, :L]
    return float(np.percentile(v[idx].mean(1), 5))


def comp(vals):
    return float(np.prod([1 + x / 100 for x in vals]) - 1) * 100


def random_versions(coins, p, acc, n=200, lev=1.0, seed=11):
    """Same entry times, directions, exits rules and sizing; the coin is drawn at random from that quarter's universe."""
    u = DC.universe()
    qs = sorted(u)
    qms = [E._qms(q) for q in qs]
    rng = np.random.default_rng(seed)
    nets = []
    for it in range(n):
        tr = []
        for t in sorted(acc, key=lambda r: r["et"]):
            qi = int(np.searchsorted(qms, t["et"], "right") - 1)
            pool = [s for s in u[qs[qi]] if s in coins]
            for _ in range(10):
                s = pool[rng.integers(len(pool))]
                c = coins[s]
                e = (t["et"] - c.t0) // E.B5
                if 0 < e < c.n and c.real[e]:
                    r = E.sim_trade(c, int(e), t["d"], p, lev)
                    if r is not None:
                        tr.append(r)
                        break
        tr.sort(key=lambda r: r["et"])
        curve, _ = E.portfolio(coins, tr, p["K"], lev, DEV_START, TEST_END)
        nets.append((curve[-1][1] / curve[0][1] - 1) * 100)
    return nets


def eight_checks(coins, p, neighbours):
    res = {}
    dev = run(coins, p, start=DEV_START, end=DEV_END)[2]
    test = run(coins, p, start=TEST_START, end=TEST_END)[2]
    dev2 = run(coins, p, cost_mult=2, start=DEV_START, end=DEV_END)[2]
    test2 = run(coins, p, cost_mult=2, start=TEST_START, end=TEST_END)[2]
    curve, acc, full = run(coins, p)
    full2 = run(coins, p, cost_mult=2)[2]
    months = full["months"]
    vals = list(months.values())
    res["1"] = {"pass": all(x["net_pct"] > 0 for x in (dev, test, dev2, test2)),
                "numbers": {"dev": dev["net_pct"], "test": test["net_pct"], "dev_2x": dev2["net_pct"], "test_2x": test2["net_pct"]}}
    years = {}
    for k, v in months.items():
        years.setdefault(k[:4], []).append(v)
    yr = {y: round(comp(v), 2) for y, v in years.items()}
    since22 = comp([v for k, v in months.items() if k >= "2022-01"])
    res["2"] = {"pass": sum(v > 0 for v in yr.values()) >= 4 and since22 > 0, "numbers": {"years": yr, "2022_2026": round(since22, 2)}}
    ts, b5 = tstat(vals), block_boot(vals)
    res["3"] = {"pass": ts >= 2.0 and b5 > 0, "numbers": {"t": round(ts, 2), "boot_p5_mean": round(b5, 3), "n_months": len(vals)}}
    rest = sorted(vals)[:-3]
    res["4"] = {"pass": comp(rest) > 0, "numbers": {"net_without_3_best": round(comp(rest), 2)}}
    nb = []
    for name, q in neighbours:
        d = run(coins, q, start=DEV_START, end=DEV_END)[2]["net_pct"]
        t = run(coins, q, start=TEST_START, end=TEST_END)[2]["net_pct"]
        nb.append({"name": name, "dev": d, "test": t, "both": d > 0 and t > 0})
    frac = sum(x["both"] for x in nb) / len(nb)
    res["5"] = {"pass": frac >= 0.7, "numbers": {"frac_both_positive": round(frac, 3), "grid": nb}}
    rnd = random_versions(coins, p, acc)
    pct = sum(full["net_pct"] > x for x in rnd) / len(rnd)
    res["6"] = {"pass": pct >= 0.95, "numbers": {"beats_frac": round(pct, 3), "random_median": round(float(np.median(rnd)), 2),
                                               "random_p95": round(float(np.percentile(rnd, 95)), 2), "strategy": full["net_pct"]}}
    by = {}
    for t in acc:
        by[t["sym"]] = by.get(t["sym"], 0) + t["pnl"]
    top5 = [s for s, _ in sorted(by.items(), key=lambda x: -x[1])[:5]]
    ex = run(coins, p, exclude=set(top5))[2]
    total = sum(t["pnl"] for t in acc)
    mx = max((t["pnl"] for t in acc), default=0)
    share = mx / total * 100 if total > 0 else None
    res["7"] = {"pass": ex["net_pct"] > 0 and share is not None and share <= 10,
                "numbers": {"top5": top5, "net_without_top5": ex["net_pct"], "max_trade_share_pct": None if share is None else round(share, 1)}}
    am, gm = full["mean_month"], full["geo_month"]
    res["8"] = {"pass": gm > 0 and gm >= 0.5 * am, "numbers": {"arith": am, "geo": gm}}
    return res, {"dev": dev, "test": test, "dev_2x": dev2, "test_2x": test2, "full": full, "full_2x": full2,
                 "full_ts": round(ts, 2), "acc": acc, "curve": curve}
