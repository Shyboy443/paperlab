"""Round 2 "not luck" checks (ROBUSTNESS.md) for two trend-following candidates, at 1x.

    python research/monthly20/r2_trend/data_r2.py   # once: daily panel from agents 04/07 archive caches
    python research/monthly20/r2_trend/run.py       # every number in results.json / REPORT.md

Fixed BEFORE any check was run (pre-declared; nothing below was changed after seeing a result):

T1  EMA fast/slow cross on daily closes (5/100), in the market only while Wilder ADX(14) >= 25, long and short,
    universe = point-in-time monthly top-10 USDT perps by trailing 30-day quote volume (delisted coins included),
    equal risk per coin (inverse 30-day vol weights over the universe, 1x gross when all are in), positions resized
    to target on the 1st of each month (and on every open / flip).
T2  Agent 01's attempt E made unlevered: 60-day momentum sign (long if close > close 60 days ago, else short),
    point-in-time top-50, same inverse-vol 1x-gross weights, monthly resize.

Neighbour grids (check 5), every combination except the baseline itself, baseline costs:
    T1: fast {3,4,5,6,8} x slow {50,75,100,125,150} x ADX {20,22.5,25,27.5,30} x universe {10,20,30}
        x rebalance {daily, weekly, monthly}                              -> 1,124 neighbours
    T2: lookback {30,45,60,75,90} x universe {10,20,30,25,38,50,62,75} x rebalance {daily, weekly, monthly}
                                                                          -> 119 neighbours
Random versions (check 6): 500 per candidate. Each month, the strategy's coin -> position mapping is re-assigned
by a random permutation inside that month's universe: same number of longs and shorts on every day, same trade
and rebalance days, same weights rule and costs, random coins.
Check 7 drops the 5 coins with the largest summed trade profit from the universe (the next coins by volume fill
in) and reruns; a "position" is one coin from open to close (resizes inside it count as the same position).
"""
from __future__ import annotations
import json, math, random, sys, time
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from engine_r2 import *  # noqa
from common import monthly_returns

OUTD = Path(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r2_trend")
ALL_MONTHS = [f"{y}-{m:02d}" for y in range(2021, 2027) for m in range(1, 13) if f"{y}-{m:02d}" <= "2026-10"]
DEV_M = [m for m in ALL_MONTHS if m <= "2024-10"]
TEST_M = [m for m in ALL_MONTHS if m >= "2024-11"]

CANDS = {
    "T1": dict(kind="ema", fast=5, slow=100, adx=25, uni=10, rebal="monthly"),
    "T2": dict(kind="mom", look=60, uni=50, rebal="monthly"),
}


def jdef(o):
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    return float(o)


def months_of(curve):
    m = monthly_returns(curve)
    return {k: (m.get(k, -100.0 if curve[-1][1] < 1 else 0.0)) for k in ALL_MONTHS}


def net(ms, keys):
    g = 1.0
    for k in keys:
        g *= 1 + ms[k] / 100
    return (g - 1) * 100


def dd_of(curve):
    peak, dd = curve[0][1], 0.0
    for _, e in curve:
        peak = max(peak, e); dd = max(dd, 1 - e / peak)
    return dd * 100


def stats(ms, keys, curve=None):
    v = np.array([ms[k] for k in keys])
    g = 1 + net(ms, keys) / 100
    out = {"mean_month": round(float(v.mean()), 2), "median_month": round(float(np.median(v)), 2),
           "geo_month": round(((g ** (1 / len(v))) - 1) * 100 if g > 0 else -100.0, 2),
           "worst_month": round(float(v.min()), 2), "best_month": round(float(v.max()), 2),
           "months_up": int((v > 0).sum()), "n_months": len(v), "net_pct": round(net(ms, keys), 1)}
    if curve is not None:
        out["max_dd_pct"] = round(dd_of(curve), 1)
    return out


def window_curve(curve, start, end):
    return [(t, e) for t, e in curve if start - 1 <= t < end] or curve[:1]


def run(P, cfg, lev=1.0, cost=1.0, drop=(), trades=False, S=None, W=None):
    if S is None:
        S, W = P.build(cfg, drop)
    curve, book, nliq = P.sim(S, W, lev=lev, cost_mult=cost, rebal=cfg["rebal"], trades=trades)
    return months_of(curve), curve, book, nliq


def bootstrap_p5(v, block=3, n=5000, seed=7):
    rng = np.random.default_rng(seed)
    v = np.asarray(v); T = len(v); nb = math.ceil(T / block)
    starts = rng.integers(0, T, size=(n, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % T
    means = v[idx.reshape(n, -1)[:, :T]].mean(1)
    return float(np.percentile(means, 5))


def permuted(P, S, W, n, rng):
    """Same longs/shorts count, days and weights rule; coins re-assigned at random inside each month's universe."""
    U = P.universe(n)
    S2 = np.zeros_like(S)
    W2 = W.copy()
    ms = P.month_starts + [P.iend]
    for a, b in zip(ms[:-1], ms[1:]):
        members = np.where(U[a])[0]
        perm = rng.permutation(members)
        S2[a:b, perm] = S[a:b, members]
    S2 = S2 * P.has  # a random coin without a bar that day cannot be traded
    v = np.vstack([np.full((1, P.N), np.nan), P.vol[:-1]])
    S2[np.isnan(v) | (v <= 0)] = 0
    return S2, W2


def grid(name):
    out = []
    if name == "T1":
        for f in (3, 4, 5, 6, 8):
            for s in (50, 75, 100, 125, 150):
                for a in (20, 22.5, 25, 27.5, 30):
                    for n in (10, 20, 30):
                        for r in ("daily", "weekly", "monthly"):
                            c = dict(kind="ema", fast=f, slow=s, adx=a, uni=n, rebal=r)
                            if c != CANDS["T1"]:
                                out.append(c)
    else:
        for lk in (30, 45, 60, 75, 90):
            for n in (10, 20, 30, 25, 38, 50, 62, 75):
                for r in ("daily", "weekly", "monthly"):
                    c = dict(kind="mom", look=lk, uni=n, rebal=r)
                    if c != CANDS["T2"]:
                        out.append(c)
    return out


def check_all(P, name):
    cfg = CANDS[name]
    t0 = time.time()
    res = {"cfg": cfg, "checks": {}}
    ms, curve, book, nliq = run(P, cfg, trades=True)
    ms2, curve2, _, _ = run(P, cfg, cost=2.0)
    vals = [ms[k] for k in ALL_MONTHS]
    res["monthly_1x"] = {k: round(ms[k], 2) for k in ALL_MONTHS}
    res["full_1x"] = stats(ms, ALL_MONTHS, curve)
    res["full_1x"]["liquidations"] = nliq
    res["full_1x"]["trades"] = len(book)
    res["test_1x"] = stats(ms, TEST_M, window_curve(curve, TEST_START, TEST_END))
    res["test_1x_double_cost"] = stats(ms2, TEST_M, window_curve(curve2, TEST_START, TEST_END))
    res["dev_1x"] = stats(ms, DEV_M, window_curve(curve, DEV_START, DEV_END))
    res["full_1x_double_cost"] = stats(ms2, ALL_MONTHS, curve2)

    # 1 both halves
    n1 = {"dev_net": round(net(ms, DEV_M), 1), "test_net": round(net(ms, TEST_M), 1),
          "dev_net_2x_cost": round(net(ms2, DEV_M), 1), "test_net_2x_cost": round(net(ms2, TEST_M), 1)}
    res["checks"]["1"] = {"pass": all(v > 0 for v in n1.values()), "numbers": n1}
    # 2 most years
    yrs = {str(y): round(net(ms, [k for k in ALL_MONTHS if k.startswith(str(y))]), 1) for y in range(2021, 2027)}
    no21 = round(net(ms, [k for k in ALL_MONTHS if k >= "2022-01"]), 1)
    res["checks"]["2"] = {"pass": sum(v > 0 for v in yrs.values()) >= 4 and no21 > 0,
                          "numbers": {"years": yrs, "years_up": sum(v > 0 for v in yrs.values()),
                                      "net_2022_01_to_2026_10": no21}}
    # 3 statistically real
    v = np.array(vals)
    t = float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v))))
    p5 = bootstrap_p5(v)
    res["checks"]["3"] = {"pass": t >= 2.0 and p5 > 0,
                          "numbers": {"t_stat": round(t, 2), "boot_p5_mean_month": round(p5, 2),
                                      "mean_month": round(float(v.mean()), 2)}}
    # 4 not a few months
    best3 = sorted(ALL_MONTHS, key=lambda k: -ms[k])[:3]
    rest = [k for k in ALL_MONTHS if k not in best3]
    res["checks"]["4"] = {"pass": net(ms, rest) > 0,
                          "numbers": {"best3": {k: round(ms[k], 1) for k in best3},
                                      "net_without_best3": round(net(ms, rest), 1),
                                      "net_all": round(net(ms, ALL_MONTHS), 1)}}
    print(name, "base done", round(time.time() - t0), "s", flush=True)
    # 5 neighbours
    rows, okc = [], 0
    for c in grid(name):
        m, _, _, _ = run(P, c)
        dn, tn = net(m, DEV_M), net(m, TEST_M)
        ok = bool(dn > 0 and tn > 0)
        okc += ok
        rows.append({**c, "dev_net": round(dn, 1), "test_net": round(tn, 1), "full_net": round(net(m, ALL_MONTHS), 1),
                     "ok": ok})
    frac = okc / len(rows)
    res["checks"]["5"] = {"pass": frac >= 0.70,
                          "numbers": {"neighbours": len(rows), "positive_both": okc, "share": round(frac, 3),
                                      "share_dev_positive": round(sum(r["dev_net"] > 0 for r in rows) / len(rows), 3),
                                      "share_test_positive": round(sum(r["test_net"] > 0 for r in rows) / len(rows), 3),
                                      "median_full_net": float(np.median([r["full_net"] for r in rows]))}}
    (OUTD / f"{name}_neighbours.json").write_text(json.dumps(rows, indent=0, default=jdef))
    print(name, "grid done", okc, "/", len(rows), round(time.time() - t0), "s", flush=True)
    # 6 beats chance
    S, W = P.build(cfg)
    rng = np.random.default_rng(2026)
    rnd = []
    for _ in range(500):
        S2, W2 = permuted(P, S, W, cfg["uni"], rng)
        m, _, _, _ = run(P, cfg, S=S2, W=W2)
        rnd.append(net(m, ALL_MONTHS))
    real = net(ms, ALL_MONTHS)
    beat = float(np.mean(np.array(rnd) < real))
    res["checks"]["6"] = {"pass": beat >= 0.95,
                          "numbers": {"n_random": len(rnd), "share_beaten": round(beat, 3), "strategy_net": round(real, 1),
                                      "random_median_net": round(float(np.median(rnd)), 1),
                                      "random_p95_net": round(float(np.percentile(rnd, 95)), 1)}}
    print(name, "random done", beat, round(time.time() - t0), "s", flush=True)
    # 7 not a few coins or trades
    by_coin = {}
    for s, a, b, p in book:
        by_coin[s] = by_coin.get(s, 0.0) + p
    top5 = sorted(by_coin, key=lambda s: -by_coin[s])[:5]
    m7, _, _, _ = run(P, cfg, drop=tuple(top5))
    total = sum(p for *_, p in book)
    big = max(book, key=lambda x: x[3])
    share = big[3] / total * 100 if total > 0 else float("inf")
    day = lambda i: datetime.fromtimestamp(P.day[min(i, len(P.day) - 1)] / 1000, timezone.utc).strftime("%Y-%m-%d")
    res["checks"]["7"] = {"pass": net(m7, ALL_MONTHS) > 0 and share <= 10,
                          "numbers": {"top5_coins": {s: round(by_coin[s], 0) for s in top5},
                                      "net_without_top5": round(net(m7, ALL_MONTHS), 1),
                                      "total_trade_profit_usd": round(total, 0),
                                      "biggest_position": {"coin": big[0], "open": day(big[1]), "close": day(big[2]),
                                                           "pnl_usd": round(big[3], 0)},
                                      "biggest_position_share_pct": round(share, 1)}}
    # 8 compounds
    geo = res["full_1x"]["geo_month"]; mean = res["full_1x"]["mean_month"]
    res["checks"]["8"] = {"pass": geo > 0 and geo >= 0.5 * mean,
                          "numbers": {"geo_month": geo, "mean_month": mean,
                                      "geo_over_mean": round(geo / mean, 2) if mean else None}}
    res["robust"] = bool(all(c["pass"] for c in res["checks"].values()))
    # leverage table
    lt = []
    for L in (1.0, 1.5, 2.0):
        m, cv, _, nl = run(P, cfg, lev=L)
        m2, cv2, _, _ = run(P, cfg, lev=L, cost=2.0)
        lt.append({"lev": L, "full": stats(m, ALL_MONTHS, cv), "test": stats(m, TEST_M, window_curve(cv, TEST_START, TEST_END)),
                   "full_double_cost": stats(m2, ALL_MONTHS, cv2), "liquidations": nl})
    res["leverage_table"] = lt
    print(name, "all done", round(time.time() - t0), "s", flush=True)
    return res


def main():
    OUTD.mkdir(parents=True, exist_ok=True)
    P = Panel()
    out = {"agent": "r2_trend", "note": __doc__.split("Fixed BEFORE")[1].strip(), "candidates": {}}
    for name in (sys.argv[1:] or ["T1", "T2"]):
        r = check_all(P, name)
        out["candidates"][name] = r
        print(name, "ROBUST" if r["robust"] else "NOT robust",
              {k: v["pass"] for k, v in r["checks"].items()}, flush=True)
    # results.json in the round-2 format, per candidate
    rj = {"agent": "r2_trend", "rules": out["note"]}
    for name, r in out["candidates"].items():
        rj[name] = {"checks": r["checks"], "robust": r["robust"], "monthly_1x": r["monthly_1x"],
                    "leverage_table": r["leverage_table"], "cfg": r["cfg"], "full_1x": r["full_1x"],
                    "dev_1x": r["dev_1x"], "test_1x": r["test_1x"], "test_1x_double_cost": r["test_1x_double_cost"],
                    "full_1x_double_cost": r["full_1x_double_cost"]}
    (HERE / "results.json").write_text(json.dumps(rj, indent=1, default=jdef))


if __name__ == "__main__":
    main()
