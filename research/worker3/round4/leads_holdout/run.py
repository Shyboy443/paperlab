"""leads_holdout (Worker 3 round 4): lead A = Worker 1 + V2 crash protection, lead B = hks 12h periodicity, on the
UNSEEN holdout 2018-01..2020-12 (Binance SPOT, funding 0). See PREREG.md.

    python run.py repro     # plain W1 must reproduce worker1_backfill results.json backfill_1x; B data coverage only
    python run.py holdout   # the ONE holdout run of both leads + checks + portfolios -> results.json
Research only: no orders, no keys.
"""
from __future__ import annotations
import importlib.util, json, math, sys, time, types, warnings
from pathlib import Path
import numpy as np
warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
RS = Path(r"D:/Projects/BinanceBOt/paperlab/research")
sys.path.insert(0, str(RS / "worker1_backfill"))
import report as RP                       # noqa: E402  (re-points r4_07 ens to the spot cache; main() not run)
from report import ens, utc                # noqa: E402

t0 = time.time()
S, E = utc(2018, 1, 1), utc(2021, 1, 1)
MID = "2019-07"
W1BF = json.loads((RS / "worker1_backfill/results.json").read_text())
W2BF = json.loads((RS / "worker2/backfill/results.json").read_text())["backfill_2018_2020"]


def log(*a):
    print(*a, f"[{time.time()-t0:.0f}s]", flush=True)


def compound(v):
    return float((np.prod([1 + x / 100 for x in v]) - 1) * 100)


def boot_p5(vals, seed=7):
    rng = np.random.default_rng(seed)
    nb = math.ceil(len(vals) / 3)
    st = rng.integers(0, len(vals) - 3 + 1, size=(5000, nb))
    return float(np.percentile([np.concatenate([vals[s:s + 3] for s in row])[:len(vals)].mean() for row in st], 5))


def mstat(months, dd=None, trades_pm=None):
    v = np.array(list(months.values()), float); eq = np.cumprod(1 + v / 100)
    pk = np.maximum.accumulate(np.r_[1, eq])[1:]
    out = dict(median=round(float(np.median(v)), 2), geo=round(float((eq[-1] ** (1 / len(v)) - 1) * 100), 2),
               mean=round(float(v.mean()), 2), up=f"{int((v > 0).sum())}/{len(v)}", net=round(float((eq[-1] - 1) * 100), 2),
               max_dd=dd if dd is not None else round(float((1 - eq / pk).max() * 100), 2),
               dd_basis="intra-period" if dd is not None else "month-end",
               t=round(float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v)))), 2) if v.std() > 0 else None,
               worst=round(float(v.min()), 2), best=round(float(v.max()), 2))
    if trades_pm is not None:
        out["trades_per_month"] = trades_pm
    return out


def common_checks(m1, m2):
    """checks 1-4 and 8 from the 1x and 2x-cost monthly series of the one full-holdout run."""
    ch = {}
    h = lambda m, a: compound([x for k, x in m.items() if (k < MID) == a])
    c1 = dict(h1_1x=h(m1, True), h2_1x=h(m1, False), h1_2x=h(m2, True), h2_2x=h(m2, False))
    ch["1"] = dict(pass_=all(x > 0 for x in c1.values()), numbers={k: round(v, 2) for k, v in c1.items()})
    yrs = {y: round(compound([x for k, x in m1.items() if k.startswith(y)]), 2) for y in ("2018", "2019", "2020")}
    ex = round(compound([x for k, x in m1.items() if k >= "2019-01"]), 2)
    ch["2"] = dict(pass_=sum(x > 0 for x in yrs.values()) >= 2 and ex > 0, numbers=dict(years=yrs, net_2019_2020=ex))
    v = np.array(list(m1.values()), float)
    t = float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v)))) if v.std() > 0 else 0.0
    p5 = boot_p5(v)
    ch["3"] = dict(pass_=bool(t >= 2 and p5 > 0), numbers=dict(t=round(t, 3), boot_p5=round(p5, 3)))
    b3 = sorted(m1, key=m1.get, reverse=True)[:3]
    rest = compound([x for k, x in m1.items() if k not in b3])
    ch["4"] = dict(pass_=rest > 0, numbers=dict(best3={k: m1[k] for k in b3}, net_without=round(rest, 2)))
    eq = np.prod(1 + v / 100); g = (eq ** (1 / len(v)) - 1) * 100; mn = v.mean()
    ch["8"] = dict(pass_=bool(g > 0 and g >= 0.5 * mn), numbers=dict(geo=round(g, 2), mean=round(mn, 2)))
    return ch


# =========================================================================== lead A: W1 + V2
LEGP = RP.LEGP
D = None


def make_leg(kind, drop=(), rng=None):
    p = LEGP[kind]
    if kind == "R":
        F = ens.leg_rotation(D, p["lb"], p["k"], p["univ"], drop=drop, rng=rng)
    elif kind == "M":
        F = ens.leg_ml(D, p["k"], p["seed"], drop=drop, rng=rng)
    else:
        F = ens.leg_mom(D, p["look"], p["univ"], drop=drop, rng=rng)
    return (kind, F, ens.trade_mask(D, F, kind))


def w1_weights(legs):
    """= worker1_backfill report.run_book weight path (availability-aware IV 60d / 7d from 2018-01-01)."""
    i0 = D.idx(S)
    cols, firsts = [], []
    for l in legs:
        r, _ = RP.standalone(D, l, S, E)
        f = max(RP.first_active(l[1]), i0)
        r[:f] = np.nan
        cols.append(r); firsts.append(f)
    return RP.weight_path_avail(np.column_stack(cols), firsts, i0)


def v2_scale(kinds, bear=365, reb=30, cut=0.5, every=7, use_reb=True):
    """copy of w3r2 up.py v2_scale; re-set days = W1's own (every 7 days from the run start 2018-01-01)."""
    I_START = D.idx(S)
    j = D.col["BTCUSDT"]; C = D.Cf[:, j]
    n = len(kinds)
    Sc = np.ones((D.T, n)); cur = np.ones(n)
    mom = np.array([k in ("R", "T") for k in kinds])
    for d in range(D.T):
        if (d - I_START) % every == 0:
            cur = np.ones(n)
            if d - 1 - max(bear, reb) >= 0 and np.isfinite(C[d - 1 - bear]):
                is_bear = C[d - 1] / C[d - 1 - bear] < 1
                is_reb = C[d - 1] / C[d - 1 - reb] > 1
                if is_bear and (is_reb or not use_reb):
                    cur[mom] = cut
        Sc[d] = cur
    return Sc


def simA(legs, wp, cm=1.0, record=False):
    r = ens.simulate(D, legs, S, E, wpath=wp, cost_mult=cm, record=record)
    r["stats"] = ens.stats_of(r["curve"])
    return r


def lead_a_repro():
    global D
    D = ens.Data()
    legs = [make_leg(k) for k in "RMT"]
    wp = w1_weights(legs)
    r = simA(legs, wp)
    ref = W1BF["backfill_1x"]["monthly"]
    diff = max(abs(r["stats"]["months"][k] - v) for k, v in ref.items())
    log("W1 repro max abs month diff", diff, r["stats"]["geo_month"], r["stats"]["median_month"])
    return legs, wp, diff


def trades_pm(ep):
    return round(sum(1 for e in ep if e[1] >= D.idx(S)) / 36, 1)


def lead_a():
    legs, wp, diff = lead_a_repro()
    assert diff <= 0.011, diff
    out = {"repro_max_abs_month_diff_vs_backfill_1x": diff}
    kinds = "RMT"
    wv2 = wp * v2_scale(kinds)
    i0, i1 = D.idx(S), D.idx(E)
    cut_days = (wv2[i0:i1].sum(1) < wp[i0:i1].sum(1) - 1e-9)
    out["v2_cut_days"] = int(cut_days.sum())
    out["v2_cut_months"] = sorted(set(D.mkey[i0:i1][cut_days].tolist()))
    btc_ok = np.isfinite(D.Cf[:, D.col["BTCUSDT"]])
    out["v2_flag_computable_from"] = str(D.mkey[np.argmax(btc_ok) + 366])
    res = {}
    for nm, w in (("W1", wp), ("W1_V2", wv2)):
        r1, r2, g = simA(legs, w, record=True), simA(legs, w, 2.0), simA(legs, w, 0.0)
        res[nm] = dict(r1=r1, r2=r2)
        out[nm] = dict(x1=mstat(r1["stats"]["months"], r1["stats"]["max_dd_pct"], trades_pm(r1["episodes"])),
                       x2=mstat(r2["stats"]["months"], r2["stats"]["max_dd_pct"]),
                       gross=mstat(g["stats"]["months"], g["stats"]["max_dd_pct"]),
                       monthly_1x=r1["stats"]["months"], monthly_2x=r2["stats"]["months"],
                       years={y: round(compound([x for k, x in r1["stats"]["months"].items() if k.startswith(y)]), 2)
                              for y in ("2018", "2019", "2020")})
        log(nm, out[nm]["x1"], "2x", out[nm]["x2"])
    a, b = out["W1_V2"]["x1"], out["W1"]["x1"]
    hh = dict(geo_better=a["geo"] > b["geo"], median_ge=a["median"] >= b["median"], dd_ok=a["max_dd"] <= b["max_dd"] + 2,
              geo_2x_not_worse=out["W1_V2"]["x2"]["geo"] >= out["W1"]["x2"]["geo"])
    out["head_to_head"] = hh
    out["verdict"] = ("YES" if all(hh.values()) else "PARTLY" if (hh["geo_better"] or hh["median_ge"]) else "NO")
    diffm = {k: round(out["W1_V2"]["monthly_1x"][k] - out["W1"]["monthly_1x"][k], 2) for k in out["W1"]["monthly_1x"]}
    out["monthly_diff_V2_minus_W1"] = {k: v for k, v in diffm.items() if abs(v) > 0.005}
    # ---- checks for both W1 and W1_V2 (W1's own holdout checks are context)
    nbs = ([(f"bear={x}", dict(bear=x)) for x in (180, 270, 540, 730)] + [(f"reb={x}", dict(reb=x)) for x in (14, 21, 45, 60)]
           + [(f"cut={x}", dict(cut=x)) for x in (0.0, 0.25, 0.75)] + [("bear_only", dict(use_reb=False))])
    for nm, w in (("W1_V2", wv2), ("W1", wp)):
        r1 = res[nm]["r1"]
        ch = common_checks(r1["stats"]["months"], res[nm]["r2"]["stats"]["months"])
        if nm == "W1_V2":
            grid = []
            for gn, p in nbs:
                rr = simA(legs, wp * v2_scale(kinds, **p))
                grid.append(dict(name=gn, net=rr["stats"]["net_pct"], geo=rr["stats"]["geo_month"], median=rr["stats"]["median_month"],
                                 max_dd=rr["stats"]["max_dd_pct"], beats_W1_geo=rr["stats"]["geo_month"] > out["W1"]["x1"]["geo"]))
            fr = sum(g["net"] > 0 for g in grid) / len(grid)
            ch["5"] = dict(pass_=fr >= 0.7, numbers=dict(frac_net_pos=round(fr, 3), frac_beat_W1_geo=round(sum(g["beats_W1_geo"] for g in grid) / len(grid), 3), grid=grid))
            log("A neighbours", fr)
        else:
            ch["5"] = dict(pass_=None, numbers="n/a for plain W1 (frozen; its grid was judged on 2021-2026)")
        rnd = []
        for s in range(200):
            rng = np.random.default_rng(5000 + s)
            lr = [make_leg(k, rng=rng) for k in kinds]
            rnd.append(simA(lr, w)["stats"]["net_pct"])
            if s % 50 == 0:
                log(nm, "rand", s, rnd[-1])
        rnd = np.array(rnd); strat = r1["stats"]["net_pct"]
        beat = float((rnd < strat).mean() * 100)
        ch["6"] = dict(pass_=beat >= 95, numbers=dict(strategy_net=strat, random_median=round(float(np.median(rnd)), 2),
                                                     random_p95=round(float(np.percentile(rnd, 95)), 2), beats_pct=beat))
        ep = r1["episodes"]; by = {}
        for j, a_, b_, p in ep:
            by[D.syms[j]] = by.get(D.syms[j], 0.0) + p
        top5 = sorted(by, key=by.get, reverse=True)[:5]
        total = sum(p for *_, p in ep); big = max(ep, key=lambda x: x[3])
        ld = [make_leg(k, drop=tuple(top5)) for k in kinds]
        wd = w1_weights(ld)
        if nm == "W1_V2":
            wd = wd * v2_scale(kinds)
        r7 = simA(ld, wd)
        share = big[3] / total * 100 if total > 0 else float("inf")
        ch["7"] = dict(pass_=bool(r7["stats"]["net_pct"] > 0 and share <= 10),
                       numbers=dict(top5={s_: round(by[s_], 0) for s_ in top5}, net_without_top5=r7["stats"]["net_pct"],
                                    biggest_position=dict(coin=D.syms[big[0]], pnl=round(big[3], 0)), biggest_share_pct=round(share, 2)))
        for c in ch.values():
            c["pass"] = c.pop("pass_")
        out[nm]["checks"] = dict(sorted(ch.items()))
        out[nm]["n_pass"] = f'{sum(c["pass"] is True for c in ch.values())}/{sum(c["pass"] is not None for c in ch.values())}'
        log(nm, "checks", {k: c["pass"] for k, c in out[nm]["checks"].items()})
    return out


# =========================================================================== lead B: hks on the spot hourly backfill
def load_hks():
    sys.path.insert(0, str(RS / "worker2/backfill"))
    import w2bf as W
    PN = W.Panel()
    base = types.ModuleType("base")
    base.O, base.C, base.U40, base.td, base.th, base.syms = PN.O, PN.C, PN.U40, PN.td, PN.th, PN.syms
    base.FCUM = np.zeros((PN.O.shape[0] + 1, PN.O.shape[1]))
    base.ND = len(PN.td); base.dayidx = PN.dayidx; base.FEE, base.SLIP = W.FEE, W.SLIP
    sys.modules["base"] = base
    spec = importlib.util.spec_from_file_location("hkslib", RS / "worker3/round3/hks_periodicity/lib.py")
    lib = importlib.util.module_from_spec(spec); spec.loader.exec_module(lib)
    assert lib.O is PN.O
    return PN, lib


def hks_coverage(PN, lib):
    d0, d1 = PN.dayidx(S), PN.dayidx(E)
    R, F, V, Ebl, s, e = lib.blocks(12)
    Sc = lib.score(12, 40)
    el = (V & np.isfinite(Sc))[d0:d1]          # eligible coins per (day, block)
    n = el.sum(2).min(1)
    mk = [PN.td[d] for d in range(d0, d1)]
    from datetime import datetime, timezone
    mk = np.array([datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m") for t in mk])
    u40 = PN.U40[d0:d1].sum(1)
    by = {m: dict(top40_size=round(float(u40[mk == m].mean()), 1), eligible_mean=round(float(n[mk == m].mean()), 1),
                  days_tradable_pct=round(float((n[mk == m] >= 6).mean() * 100), 1)) for m in sorted(set(mk))}
    first = mk[np.argmax(n >= 6)] if (n >= 6).any() else None
    # 1h holes inside the top-40: share of top-40 coin-hours with no bar
    hh = []
    for d in range(d0, d1, 7):
        js = np.where(PN.U40[d])[0]
        hh.append(np.isnan(PN.O[d * 24:(d + 1) * 24, js]).mean() if len(js) else 0)
    return dict(first_tradable_month=str(first), months=by, missing_1h_files=PN.missing_1h,
                top40_coin_hours_missing_pct=round(float(np.mean(hh) * 100), 3), symbols_ever_top40=len(PN.syms))


MAINB = dict(B=12, K=40, k=3, buf=3.0)


def lead_b():
    PN, lib = load_hks()
    out = {"coverage": hks_coverage(PN, lib), "config": MAINB}
    log("B coverage first tradable", out["coverage"]["first_tradable_month"])
    def run(**kw):
        r = lib.run(start=S, end=E, mode="mom", **dict(MAINB, **kw))
        m = r["months"]
        if "2021-01" in m:   # the last 12h block of 2020-12-31 ends at 2021-01-01 00:00 -> fold it into 2020-12
            x = m.pop("2021-01"); m["2020-12"] = round(((1 + m["2020-12"] / 100) * (1 + x / 100) - 1) * 100, 2)
        return r
    r1, r2, g = run(record=True), run(cost_mult=2.0), run(cost_mult=0.0)
    out["x1"] = mstat(r1["months"], r1["max_dd_pct"], round(r1["trades"] / 36, 1))
    out["x2"] = mstat(r2["months"], r2["max_dd_pct"])
    out["gross"] = mstat(g["months"], g["max_dd_pct"])
    out["monthly_1x"], out["monthly_2x"], out["monthly_gross"] = r1["months"], r2["months"], g["months"]
    out["years"] = {nm: {y: round(compound([x for k, x in m.items() if k.startswith(y)]), 2) for y in ("2018", "2019", "2020")}
                    for nm, m in (("1x", r1["months"]), ("2x", r2["months"]), ("gross", g["months"]))}
    out["cost_drag_geo_pp"] = round(out["gross"]["geo"] - out["x1"]["geo"], 2)
    log("B 1x", out["x1"], "\n  2x", out["x2"], "\n  gross", out["gross"])
    ch = common_checks(r1["months"], r2["months"])
    GRID = [("K", v) for v in (20, 30, 50, 60)] + [("k", v) for v in (2, 4, 5)] + [("buf", v) for v in (2.0, 2.5, 3.5, 4.0)] + [("B", v) for v in (6, 8)]
    grid = []
    for nm, v in GRID:
        rr = run(**{nm: v}); gg = run(cost_mult=0.0, **{nm: v})
        grid.append(dict(param=nm, value=v, net=rr["net_pct"], geo=rr["geo_month"], median=rr["median_month"], gross_geo=gg["geo_month"]))
    fr = sum(x["net"] > 0 for x in grid) / len(grid)
    ch["5"] = dict(pass_=fr >= 0.7, numbers=dict(frac_net_pos=round(fr, 3), frac_gross_pos=round(sum(x["gross_geo"] > 0 for x in grid) / len(grid), 3), grid=grid))
    log("B neighbours", fr)
    rn = np.array([run(rand_seed=1000 + s)["net_pct"] for s in range(200)], float)
    rg = np.array([run(rand_seed=1000 + s, cost_mult=0.0)["net_pct"] for s in range(50)], float)
    pct = float((rn < r1["net_pct"]).mean() * 100)
    ch["6"] = dict(pass_=pct >= 95, numbers=dict(beats_pct=pct, rand_median=round(float(np.median(rn)), 2),
                                                 rand_p95=round(float(np.percentile(rn, 95)), 2), strat=r1["net_pct"],
                                                 gross_strat=g["net_pct"], gross_rand_median_50=round(float(np.median(rg)), 2),
                                                 gross_beats_pct_50=float((rg < g["net_pct"]).mean() * 100)))
    log("B c6", ch["6"]["numbers"])
    pos = r1["_positions"]; cp = np.zeros(lib.N)
    for d, b, j, side, pnl in pos:
        cp[j] += pnl
    netp = r1["_curve"][-1][1] - r1["_curve"][0][1]
    top5 = np.argsort(-cp)[:5]
    r7 = run(exclude=tuple(int(j) for j in top5))
    mx = max(p[4] for p in pos) / netp * 100 if netp > 0 else float("inf")
    ch["7"] = dict(pass_=bool(r7["net_pct"] > 0 and netp > 0 and mx <= 10),
                   numbers=dict(top5={lib.syms[j]: round(float(cp[j]), 4) for j in top5}, net_without_top5=r7["net_pct"],
                                largest_position_share_pct=round(float(mx), 2) if np.isfinite(mx) else None))
    for c in ch.values():
        c["pass"] = bool(c.pop("pass_"))
    out["checks"] = dict(sorted(ch.items()))
    out["n_pass"] = sum(c["pass"] for c in ch.values())
    out["verdict"] = ("YES" if r1["net_pct"] > 0 and r2["net_pct"] > 0 and out["n_pass"] >= 6 else
                      "PARTLY" if r1["net_pct"] > 0 else "NO")
    log("B checks", {k: c["pass"] for k, c in out["checks"].items()}, out["verdict"])
    return out


# =========================================================================== portfolios
def portfolios(A, Bm):
    W1 = W1BF["backfill_1x"]["monthly"]; W2 = W2BF["1x"]["monthly"]; V2 = A["W1_V2"]["monthly_1x"]
    ks = sorted(W1)
    assert ks == sorted(W2) == sorted(Bm) == sorted(V2) and len(ks) == 36
    mix = lambda *ss: {k: sum(s[k] for s in ss) / len(ss) for k in ks}
    out = {nm: mstat(m) for nm, m in (("W1", W1), ("W1_V2", V2), ("W2", W2), ("B_hks", Bm),
                                         ("W1_W2_5050", mix(W1, W2)), ("W1_W2_B_thirds", mix(W1, W2, Bm)),
                                         ("W1V2_W2_5050_info", mix(V2, W2)))}
    c = lambda a, b: round(float(np.corrcoef([a[k] for k in ks], [b[k] for k in ks])[0, 1]), 3)
    out["corr"] = dict(W1_W2=c(W1, W2), W1_B=c(W1, Bm), W2_B=c(W2, Bm), W1_W1V2=c(W1, V2))
    return out


if __name__ == "__main__":
    st = sys.argv[1]
    if st == "repro":
        _, _, diff = lead_a_repro()
        log("REPRODUCES:", diff <= 0.011)
        PN, lib = load_hks()
        cov = hks_coverage(PN, lib)
        (HERE / "coverage_B.json").write_text(json.dumps(cov, indent=1, default=float))
        log("B coverage", {k: v for k, v in cov.items() if k != "months"})
    elif st == "holdout":
        res = {"agent": "w3r4_leads_holdout", "holdout": "2018-01..2020-12", "prereg": "PREREG.md"}
        res["A"] = lead_a()
        (HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
        res["B"] = lead_b()
        res["portfolio"] = portfolios(res["A"], res["B"]["monthly_1x"])
        log("portfolio", res["portfolio"])
        (HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
        log("DONE")
