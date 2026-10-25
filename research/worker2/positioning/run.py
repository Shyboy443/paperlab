"""Worker 2 candidate: r4_03 positioning + pre-registered concentration fix (PREREG.md). Reproduces every number.
    python repro.py   # step 1: r4_03 reproduced from its own code
    python run.py     # primary rule ivcap_trim through the 8 checks + neighbours + Worker 1 correlation + size check
"""
import json, sys, time, types
from datetime import datetime, timezone
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker2/positioning")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_03_positioning")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
import signals as S
import engine_r2 as E
import engine_w2 as G
from common import DEV_START, DEV_END, TEST_START, TEST_END, summary, monthly_returns

HERE = r"D:/Projects/BinanceBOt/paperlab/research/worker2/positioning"
W1 = r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_07_ensemble"
PRIMARY = dict(fam="retail_contra", k=3, kL=5, kS=5, rb=7, vlook=30, vmin=15, cap=0.10, band=1.25)
FIXN = {"ivcap_trim_b150": dict(PRIMARY, band=1.5), "ivcap_trim_v60": dict(PRIMARY, vlook=60, vmin=30)}
GRID = [dict(PRIMARY, k=k) for k in (1, 2, 4, 5)] + [dict(PRIMARY, kL=n, kS=n) for n in (3, 4, 6, 8)] + \
       [dict(PRIMARY, rb=r) for r in (1, 3, 5, 10, 14)]
ALL_MONTHS = [f"{y}-{m:02d}" for y in range(2021, 2027) for m in range(1, 13) if (y, m) <= (2026, 10)]
P, UM, M = S.load()
T, N = P.c.shape
LR = np.full((T, N), np.nan)
with np.errstate(divide="ignore", invalid="ignore"):
    LR[1:] = np.log(P.c[1:] / P.c[:-1])
_VOL = {}


def vol(look, vmin):
    """std of daily log returns over the last `look` days ending at close i (NaN if fewer than vmin returns)."""
    if (look, vmin) not in _VOL:
        out = np.full((T, N), np.nan)
        for i in range(look, T):
            x = LR[i - look + 1:i + 1]
            n = (~np.isnan(x)).sum(0)
            with np.errstate(invalid="ignore", divide="ignore"):
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    s = np.nanstd(x, 0, ddof=1)
            out[i] = np.where(n >= vmin, s, np.nan)
        _VOL[(look, vmin)] = out
    return _VOL[(look, vmin)]


def weights(cfg, rand=None, um=None):
    """Score of r4_03; picks at the weekly rebalance from score[i-1]; weight ~ 1/vol[i-1], side sums to 50%, cap."""
    um = UM if um is None else um
    sc = S.scores(P, M, cfg["fam"], cfg["k"])
    V = vol(cfg["vlook"], cfg["vmin"])
    kL, kS, rb = cfg["kL"], cfg["kS"], cfg["rb"]
    W = np.zeros((T, N)); reb = np.zeros(T, bool)
    i0 = int(np.searchsorted(P.t, DEV_START)); cur = np.zeros(N)
    for i in range(i0, T):
        if (i - i0) % rb == 0:
            reb[i] = True
            s = sc[i - 1]; v = V[i - 1]
            idx = np.where(um[i] & ~np.isnan(s) & ~np.isnan(P.o[i]) & ~np.isnan(v) & (v > 0))[0]
            cur = np.zeros(N)
            if len(idx) >= max(2 * (kL + kS), 10):
                order = rand.permutation(idx) if rand is not None else idx[np.argsort(-s[idx], kind="stable")]
                for side, pick in ((1.0, order[:kL]), (-1.0, order[::-1][:kS])):
                    iv = 1.0 / v[pick]
                    cur[pick] = side * np.minimum(0.5 * iv / iv.sum(), cfg["cap"])
        W[i] = cur
    return W, reb


def run(cfg, start, end, WR=None, **kw):
    W, reb = WR if WR is not None else weights(cfg)
    return G.sim(P, W, start, end, rebal=reb, trim_band=cfg.get("band"), **kw)


def mvec(r):
    return np.array([r["months"].get(m, 0.0) for m in ALL_MONTHS])


def tstat(v):
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))))


def boot5(v, n=5000, b=3, seed=7):
    rng = np.random.default_rng(seed); L = len(v); out = []
    for _ in range(n):
        st = rng.integers(0, L - b + 1, size=L // b + 1)
        out.append(np.concatenate([v[x:x + b] for x in st])[:L].mean())
    return float(np.percentile(out, 5))


def brief(r):
    keep = {k: r[k] for k in ("median_month", "geo_month", "worst_month", "max_dd_pct", "months_up", "n_months",
                              "trades", "trims", "mean_month", "best_month", "net_pct", "liquidations", "wiped") if k in r}
    keep["months"] = r["months"]
    return keep


def stats_from_daily(ts, r):
    eq = np.cumprod(1 + r)
    s = summary([(int(ts[0]) - 1, 1.0)] + list(zip(map(int, ts), eq.tolist())))
    s.pop("months")
    return s


def combo_monthly(a, b):
    v = 0.5 * a + 0.5 * b
    eq = np.concatenate([[1.0], np.cumprod(1 + v / 100)])
    dd = 1 - eq / np.maximum.accumulate(eq)
    return dict(median_month=round(float(np.median(v)), 2), geo_month=round(float((eq[-1] ** (1 / len(v)) - 1) * 100), 2),
                mean_month=round(float(v.mean()), 2), worst_month=round(float(v.min()), 2),
                max_dd_pct_month_end=round(float(dd.max() * 100), 2), months_up=int((v > 0).sum()), n_months=len(v))


def daily_returns(res):
    c = res["_curve"]; ts = np.array([t for t, _ in c[1:]]); e = np.array([e for _, e in c])
    return ts, e[1:] / e[:-1] - 1


if __name__ == "__main__":
    t0 = time.time(); out = {"agent": "worker2_positioning", "prereg": "PREREG.md", "primary": PRIMARY}
    Wp = weights(PRIMARY)
    dev = run(PRIMARY, DEV_START, DEV_END, Wp); dev2 = run(PRIMARY, DEV_START, DEV_END, Wp, cost_mult=2)
    test = run(PRIMARY, TEST_START, TEST_END, Wp); test2 = run(PRIMARY, TEST_START, TEST_END, Wp, cost_mult=2)
    full = run(PRIMARY, DEV_START, TEST_END, Wp, record=True, record_orders=True)
    full2 = run(PRIMARY, DEV_START, TEST_END, Wp, cost_mult=2)
    for n, r in (("DEV", dev), ("DEV2x", dev2), ("TEST", test), ("TEST2x", test2), ("FULL", full), ("FULL2x", full2)):
        print(n, {k: v for k, v in brief(r).items() if k != "months"}, flush=True)
    v = mvec(full)
    checks = {}
    checks[1] = dict(pass_=bool(min(dev["net_pct"], test["net_pct"], dev2["net_pct"], test2["net_pct"]) > 0),
                     dev=dev["net_pct"], test=test["net_pct"], dev_2x=dev2["net_pct"], test_2x=test2["net_pct"])
    yrs = {y: round((np.prod([1 + full["months"].get(m, 0) / 100 for m in ALL_MONTHS if m.startswith(str(y))]) - 1) * 100, 2)
           for y in range(2021, 2027)}
    ex21 = (np.prod([1 + full["months"].get(m, 0) / 100 for m in ALL_MONTHS if m >= "2022-01"]) - 1) * 100
    checks[2] = dict(pass_=bool(sum(x > 0 for x in yrs.values()) >= 4 and ex21 > 0), years=yrs, from_2022=round(ex21, 2))
    t = tstat(v); b5 = boot5(v)
    checks[3] = dict(pass_=bool(t >= 2 and b5 > 0), t_stat_70m=round(t, 2), boot5_mean_month=round(b5, 3))
    srt = np.argsort(-v); net3 = (np.prod(1 + np.delete(v, srt[:3]) / 100) - 1) * 100
    checks[4] = dict(pass_=bool(net3 > 0), net_without_best3=round(net3, 2),
                     best3={ALL_MONTHS[i]: round(float(v[i]), 2) for i in srt[:3]})
    nb = []
    for name, cfg in [(f"grid_{i}", c) for i, c in enumerate(GRID)] + list(FIXN.items()):
        Wn = weights(cfg)
        d = run(cfg, DEV_START, DEV_END, Wn)["net_pct"]; te = run(cfg, TEST_START, TEST_END, Wn)["net_pct"]
        nb.append(dict(name=name, cfg={k: cfg[k] for k in ("k", "kL", "kS", "rb", "vlook", "band")}, dev=d, test=te,
                       ok=bool(d > 0 and te > 0)))
        print("neighbour", nb[-1], flush=True)
    frac = sum(x["ok"] for x in nb) / len(nb)
    checks[5] = dict(pass_=bool(frac >= 0.7), share_ok=round(frac, 3), n=len(nb), neighbours=nb)
    rng = np.random.default_rng(42); rnd = []
    for _ in range(300):
        rnd.append(run(PRIMARY, DEV_START, TEST_END, weights(PRIMARY, rand=rng))["net_pct"])
    beat = float(np.mean(np.array(rnd) < full["net_pct"]))
    checks[6] = dict(pass_=bool(beat >= 0.95), beats_share=round(beat, 3), random_median=round(float(np.median(rnd)), 2),
                     random_p95=round(float(np.percentile(rnd, 95)), 2), n_random=len(rnd))
    print("check6", checks[6], flush=True)
    pos = full["_positions"]; by_coin = {}
    for j, side, o, c, pnl in pos:
        by_coin[j] = by_coin.get(j, 0) + pnl
    top5 = sorted(by_coin, key=lambda j: -by_coin[j])[:5]
    Pq = types.SimpleNamespace(t=P.t, c=P.c, onboard=P.onboard, qv=P.qv.copy()); Pq.qv[:, top5] = 0
    um2 = E.universe_mask(Pq, 40)
    ex_univ = run(PRIMARY, DEV_START, TEST_END, weights(PRIMARY, um=um2))
    ex_zero = run(PRIMARY, DEV_START, TEST_END, Wp, exclude=top5)
    tot = sum(p[4] for p in pos); top_pos = sorted(pos, key=lambda p: -p[4])[:5]
    maxshare = top_pos[0][4] / tot

    def day(i):
        return datetime.fromtimestamp(P.t[min(i, T - 1)] / 1000, timezone.utc).strftime("%Y-%m-%d")
    checks[7] = dict(pass_=bool(ex_univ["net_pct"] > 0 and maxshare <= 0.10), top5_coins=[P.syms[j] for j in top5],
                     net_without_top5_reranked=ex_univ["net_pct"], net_without_top5_zeroed=ex_zero["net_pct"],
                     max_single_position_share=round(maxshare, 4), total_profit_usd=round(tot, 2),
                     top_positions=[dict(coin=P.syms[p[0]], side="long" if p[1] > 0 else "short", open=day(p[2]),
                                         share=round(p[4] / tot, 4)) for p in top_pos])
    arith = float(v.mean()); geo = full["geo_month"]
    checks[8] = dict(pass_=bool(geo > 0 and geo >= 0.5 * arith), geo=geo, arith=round(arith, 3))
    npass = sum(c["pass_"] for c in checks.values())
    for k, c in checks.items():
        print(k, "PASS" if c["pass_"] else "FAIL", {a_: b_ for a_, b_ in c.items() if a_ != "neighbours"}, flush=True)
    fixn = {}
    for name, cfg in FIXN.items():
        Wn = weights(cfg); f = run(cfg, DEV_START, TEST_END, Wn, record=True)
        ps = f["_positions"]; tt = sum(p[4] for p in ps)
        fixn[name] = dict(full=brief(f), dev=brief(run(cfg, DEV_START, DEV_END, Wn)),
                          test=brief(run(cfg, TEST_START, TEST_END, Wn)),
                          max_single_position_share=round(max(p[4] for p in ps) / tt, 4))
        print(name, fixn[name]["max_single_position_share"], {k: x for k, x in fixn[name]["full"].items() if k != "months"})
    # ---------------- Worker 1 correlation and 50/50 ----------------
    w1m = json.load(open(W1 + "/results.json"))["monthly_1x"]
    a = np.array([w1m[m] for m in ALL_MONTHS]); b = v
    corr = {"monthly_2021_2026": round(float(np.corrcoef(a, b)[0, 1]), 3)}
    sel = np.array([m >= "2022-01" for m in ALL_MONTHS])
    corr["monthly_2022_2026"] = round(float(np.corrcoef(a[sel], b[sel])[0, 1]), 3)
    rb1 = np.load(W1 + "/_rbook_full_1x.npy")
    sys.path.insert(0, W1)
    import ens  # Worker 1's own data class gives the day index of its daily book
    D1 = ens.Data(); d1 = dict(zip(D1.day.tolist(), rb1.tolist()))
    ts, r2 = daily_returns(full)
    day0 = ts - (ts % 86_400_000)
    r1 = np.array([d1.get(int(d), np.nan) for d in day0])
    ok = ~np.isnan(r1)
    ok22 = ok & (ts >= 1640995200000)
    corr["daily_2021_2026"] = round(float(np.corrcoef(r1[ok], r2[ok])[0, 1]), 3)
    corr["daily_2022_2026"] = round(float(np.corrcoef(r1[ok22], r2[ok22])[0, 1]), 3)
    corr["daily_days"] = int(ok.sum())
    m1 = monthly_returns([(int(ts[ok][0]) - 1, 1.0)] + list(zip(ts[ok].tolist(), np.cumprod(1 + r1[ok]).tolist())))
    corr["check_w1_daily_book_vs_its_monthly_1x_max_abs_diff_pct"] = round(max(abs(m1[k] - w1m[k]) for k in m1 if k in w1m), 3)
    combo = {}
    for lab, mask in (("full_2021_2026", ok), ("dev", ok & (ts < DEV_END)), ("test", ok & (ts >= TEST_START))):
        combo[lab] = dict(worker1=stats_from_daily(ts[mask], r1[mask]), worker2=stats_from_daily(ts[mask], r2[mask]),
                          combo_50_50_daily=stats_from_daily(ts[mask], 0.5 * r1[mask] + 0.5 * r2[mask]))
    combo["monthly_rebalanced_full"] = combo_monthly(a, b)
    print("corr", corr)
    for k_, x_ in combo.items():
        print("combo", k_, x_)
    # ---------------- size check at 500 USDT (and at 320 = 500 after the max drawdown) ----------------
    minn = {"BTCUSDT": 100.0, "ETHUSDT": 20.0}
    size = {}
    for E0 in (500.0, 320.0):
        opens = [(o[1], abs(o[5]) * E0) for o in full["_orders"] if o[4]]
        skip = [(j, n) for j, n in opens if n < minn.get(P.syms[j], 5.0)]
        adds = [abs(o[2]) / o[6] * E0 for o in full["_orders"] if not o[3] and not o[4]]
        size[str(int(E0))] = dict(
            opening_orders=len(opens), skipped=len(skip), skipped_share=round(len(skip) / len(opens), 4),
            skipped_by_coin={s: sum(1 for j, _ in skip if P.syms[j] == s) for s in sorted({P.syms[j] for j, _ in skip})},
            min_position_usdt=round(min(n for _, n in opens), 2),
            p5_position_usdt=round(float(np.percentile([n for _, n in opens], 5)), 2),
            median_position_usdt=round(float(np.median([n for _, n in opens])), 2),
            add_on_orders=len(adds), add_on_below_5usdt_share=round(float(np.mean(np.array(adds) < 5)), 4) if adds else None,
            note="trims and closes are reduce-only orders, which Binance exempts from the minimum notional")
    print("size", size)
    out.update(attempts=[dict(name="ivcap_trim", prereg=True, dev=brief(dev), test=brief(test), dev_double_cost=brief(dev2),
                              test_double_cost=brief(test2), full_2021_2026=brief(full), full_double_cost=brief(full2))],
               checks={str(k): {("pass" if a_ == "pass_" else a_): b_ for a_, b_ in c.items()} for k, c in checks.items()},
               checks_passed=npass, robust=npass == 8, verdict="PASS" if npass == 8 else "FAIL",
               monthly_1x={m: round(float(x), 2) for m, x in zip(ALL_MONTHS, v)}, fix_neighbours=fixn,
               worker1_correlation=corr, combo_with_worker1=combo, size_check=size,
               r4_03_reproduction=json.load(open(HERE + "/repro.json"))["compare_mine_vs_results_json"])
    json.dump(out, open(HERE + "/results.json", "w"), indent=1, default=float)
    print("checks passed", npass, "secs", round(time.time() - t0))
