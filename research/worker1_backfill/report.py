"""Sanity (2021-01..06 spot vs perp), backfill 2018-01..2020-12 at 1x and 2x costs, joins with the 2021..2026 series."""
from __future__ import annotations
import json, math, sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as RB                    # re-points ens / r2_ml to the spot cache
from run import ens, utc, HERE

t0 = time.time()
LEGP = dict(R=dict(lb=14, k=10, univ=40), M=dict(k=3, seed=0), T=dict(look=60, univ=50))   # = r4_07 run.LEGP
ORIG = json.loads((RB.M20 / "r4_07_ensemble" / "results.json").read_text())


def log(*a):
    print(*a, f"[{time.time()-t0:.0f}s]", flush=True)


def make_legs(D):
    out = []
    for k in "RMT":
        p = LEGP[k]
        F = (ens.leg_rotation(D, p["lb"], p["k"], p["univ"]) if k == "R" else
             ens.leg_ml(D, p["k"], p["seed"]) if k == "M" else ens.leg_mom(D, p["look"], p["univ"]))
        out.append((k, F, ens.trade_mask(D, F, k)))
    return out


def standalone(D, leg, start, end, cost_mult=1.0):
    r = ens.simulate(D, [leg], start, end, cost_mult=cost_mult)
    out = np.full(D.T, np.nan)
    out[r["i0"]:r["i0"] + len(r["ret"])] = r["ret"]
    return out, r


def first_active(F):
    nz = np.where((F != 0).any(1))[0]
    return int(nz[0]) if len(nz) else 10 ** 9


def weight_path_avail(Rm, firsts, start_i, lookback=60, every=7, min_obs=20):
    """ens.weight_path(scheme='IV') restricted to the legs that already have a signal (first_active <= d); legs not yet
    available get weight 0. Identical to ens.weight_path when all legs are available."""
    T, n = Rm.shape
    W = np.full((T, n), 1 / n); cur = np.full(n, 1 / n)
    for d in range(T):
        if (d - start_i) % every == 0:
            av = np.array([f <= d for f in firsts])
            if not av.any():
                cur = np.full(n, 1 / n)
            else:
                X = Rm[max(0, d - lookback):d][:, av]
                X = X[~np.isnan(X).any(1)]
                cur = np.zeros(n)
                if len(X) < min_obs:
                    cur[av] = 1 / av.sum()
                else:
                    sd = X.std(0, ddof=1) + 1e-9
                    cur[av] = (1 / sd) / (1 / sd).sum()
        W[d] = cur
    return W


def run_book(D, legs, start, end):
    i0 = D.idx(start)
    cols, firsts = [], []
    for l in legs:
        r, _ = standalone(D, l, start, end)
        f = max(first_active(l[1]), i0)
        r[:f] = np.nan
        cols.append(r); firsts.append(f)
    Rm = np.column_stack(cols)
    wp = weight_path_avail(Rm, firsts, i0)
    out = {}
    for cm in (1.0, 2.0):
        b = ens.simulate(D, legs, start, end, wpath=wp, cost_mult=cm)
        out[cm] = b | {"stats": ens.stats_of(b["curve"])}
    return out, wp, firsts


def compound(v):
    return float((np.prod([1 + x / 100 for x in v]) - 1) * 100)


def mstats(months: dict, curve=None):
    v = np.array(list(months.values()))
    eq = np.cumprod(1 + v / 100)
    if curve is not None:
        e = np.array([x for _, x in curve]); dd = float((1 - e / np.maximum.accumulate(e)).max() * 100); basis = "daily"
    else:
        e = np.r_[1.0, eq]; dd = float((1 - e / np.maximum.accumulate(e)).max() * 100); basis = "month-end"
    return dict(n_months=len(v), months_up=int((v > 0).sum()), median_month=round(float(np.median(v)), 2),
                mean_month=round(float(v.mean()), 2), geo_month=round(float((eq[-1] ** (1 / len(v)) - 1) * 100), 2),
                worst_month=round(float(v.min()), 2), best_month=round(float(v.max()), 2),
                compounded_pct=round(float((eq[-1] - 1) * 100), 2), max_dd_pct=round(dd, 2), dd_basis=basis)


def yearly(months, curve=None):
    out = {}
    for y in sorted({k[:4] for k in months}):
        m = {k: v for k, v in months.items() if k.startswith(y)}
        cv = None
        if curve is not None:
            a, b = utc(int(y), 1, 1), utc(int(y) + 1, 1, 1)
            cv = [(t, e) for t, e in curve if a - 1 <= t < b]
        out[y] = mstats(m, cv) | {"year_return_pct": round(compound(list(m.values())), 2)}
    return out


def rolling24(months):
    ks = list(months); v = np.array(list(months.values()))
    w = [int((v[i:i + 24] > 0).sum()) for i in range(len(v) - 23)]
    worst = int(np.argmin(w))
    return dict(n_windows=len(w), min_up=min(w), median_up=float(np.median(w)), max_up=max(w),
                worst_window=f"{ks[worst]}..{ks[worst + 23]}",
                windows_below_12=sum(x < 12 for x in w), windows_ge_14=sum(x >= 14 for x in w),
                series={ks[i + 23]: w[i] for i in range(len(w))})


def coverage(D, legs, start, end):
    i0, i1 = D.idx(start), D.idx(end)
    out = {}
    for k, F, _ in legs:
        n = (F[i0:i1] != 0).sum(1)
        mk = D.mkey[i0:i1]
        per = {}
        for y in sorted(set(m[:4] for m in mk)):
            s = np.array([m.startswith(y) for m in mk])
            per[y] = dict(days_with_signal_pct=round(float((n[s] > 0).mean() * 100), 1),
                          mean_coins_when_on=round(float(n[s][n[s] > 0].mean()), 1) if (n[s] > 0).any() else 0)
        fa = first_active(F[i0:])
        out[k] = dict(first_signal_day=datetime.fromtimestamp(int(D.day[i0 + fa]) / 1000, timezone.utc).strftime("%Y-%m-%d")
                      if i0 + fa < D.T else None, by_year=per)
    return out


def main():
    res = {"what": "Worker 1 (r4_07 ensemble R+M+T, IV 60d/7d, 1x) on Binance SPOT 2018-01..2020-12, out of sample"}
    D = ens.Data()
    log("spot panel", D.T, D.N)
    legs = make_legs(D)
    log("legs built")
    # -------------------------------------------------- backfill 2018-01..2020-12
    S, E = utc(2018, 1, 1), utc(2021, 1, 1)
    bk, wp, firsts = run_book(D, legs, S, E)
    i0, i1 = D.idx(S), D.idx(E)
    res["coverage"] = coverage(D, legs, S, E)
    res["leg_available_from"] = {l[0]: datetime.fromtimestamp(int(D.day[min(f, D.T - 1)]) / 1000, timezone.utc).strftime("%Y-%m-%d")
                                 for l, f in zip(legs, firsts)}
    res["mean_weights_by_year_RMT"] = {y: np.round(wp[i0:i1][np.array([m.startswith(y) for m in D.mkey[i0:i1]])].mean(0), 3).tolist()
                                       for y in ("2018", "2019", "2020")}
    for cm, tag in ((1.0, "1x"), (2.0, "1x_2xcost")):
        st = bk[cm]["stats"]
        res[f"backfill_{tag}"] = dict(monthly=st["months"], overall=mstats(st["months"], bk[cm]["curve"]),
                                      yearly=yearly(st["months"], bk[cm]["curve"]), liquidations=bk[cm]["liquidations"])
        log(tag, res[f"backfill_{tag}"]["overall"])
    # sensitivity (NOT Worker 1): the book without the ML leg, since the sanity check shows the spot ML leg is far
    # stronger than the perp one in the 2021-H1 overlap
    rt, _, _ = run_book(D, [legs[0], legs[2]], S, E)
    res["sensitivity_book_R_T_only"] = {tag: dict(overall=mstats(rt[cm]["stats"]["months"], rt[cm]["curve"]),
                                                  yearly={y: v["year_return_pct"] for y, v in yearly(rt[cm]["stats"]["months"]).items()})
                                        for cm, tag in ((1.0, "1x"), (2.0, "2xcost"))}
    log("R+T only", res["sensitivity_book_R_T_only"])
    res["legs_alone"] = {}
    for l in legs:
        for cm, tag in ((1.0, "1x"), (2.0, "2xcost")):
            _, r = standalone(D, l, S, E, cm)
            m = ens.stats_of(r["curve"])["months"]
            res["legs_alone"][f"{l[0]}_{tag}"] = dict(overall=mstats(m, r["curve"]), yearly=yearly(m, r["curve"]), monthly=m)
        log("leg", l[0], res["legs_alone"][f"{l[0]}_1x"]["overall"])
    # -------------------------------------------------- sanity 2021-01..06: spot pipeline vs perp (original) numbers
    SS, SE = utc(2021, 1, 1), utc(2021, 7, 1)
    sb, _, _ = run_book(D, legs, SS, SE)
    spot_m = sb[1.0]["stats"]["months"]
    spot_legs = {l[0]: ens.stats_of(standalone(D, l, SS, SE)[1]["curve"])["months"] for l in legs}
    del D, legs
    ens.PANEL = ens.PROJECT / "data" / "monthly20_r2_trend" / "panel.npz"
    ens.MLD = ens.PROJECT / "data" / "monthly20_r2_ml"
    ens.DATA_FROM_ML = 1577836800000
    DP = ens.Data()
    lp = make_legs(DP)
    pb, _, _ = run_book(DP, lp, SS, SE)
    perp_m = pb[1.0]["stats"]["months"]
    perp_legs = {l[0]: ens.stats_of(standalone(DP, l, SS, SE)[1]["curve"])["months"] for l in lp}
    orig_m = {k: v for k, v in ORIG["monthly_1x"].items() if "2021-01" <= k <= "2021-06"}
    ks = list(orig_m)
    a = np.array([spot_m[k] for k in ks]); b = np.array([orig_m[k] for k in ks])
    res["sanity_2021H1"] = dict(
        months=ks, original_perp_results_json=[orig_m[k] for k in ks], perp_rerun_fresh_start=[perp_m[k] for k in ks],
        spot_pipeline=[spot_m[k] for k in ks], diff_spot_minus_original=[round(x - y, 2) for x, y in zip(a, b)],
        mean_abs_diff=round(float(np.abs(a - b).mean()), 2), corr=round(float(np.corrcoef(a, b)[0, 1]), 3),
        compounded_original=round(compound(b), 2), compounded_spot=round(compound(a), 2),
        legs_spot={k: [v[m] for m in ks] for k, v in spot_legs.items()},
        legs_perp={k: [v[m] for m in ks] for k, v in perp_legs.items()})
    log("sanity", res["sanity_2021H1"])
    # perp 1x / 2x-cost monthly series 2021..2026 (same code path as run_test: weights from 1x standalone legs)
    FS, FE = ens.DEV_START, ens.TEST_END
    cols = [standalone(DP, l, FS, FE)[0] for l in lp]
    wpp = ens.weight_path(DP, np.column_stack(cols), "IV", 60, 7, start_i=DP.idx(FS))
    p1 = ens.stats_of(ens.simulate(DP, lp, FS, FE, wpath=wpp)["curve"])["months"]
    p2 = ens.stats_of(ens.simulate(DP, lp, FS, FE, wpath=wpp, cost_mult=2)["curve"])["months"]
    res["perp_rerun_max_abs_diff_vs_results_json"] = round(float(np.abs(np.array([p1[k] for k in ORIG["monthly_1x"]]) -
                                                                         np.array(list(ORIG["monthly_1x"].values()))).max()), 3)
    # -------------------------------------------------- joins
    j1 = dict(res["backfill_1x"]["monthly"]); j1.update(ORIG["monthly_1x"])
    j2 = dict(res["backfill_1x_2xcost"]["monthly"]); j2.update({k: round(v, 2) for k, v in p2.items()})
    for tag, j in (("joined_2018_2026_1x", j1), ("joined_2018_2026_2xcost", j2)):
        res[tag] = dict(overall=mstats(j), yearly=yearly(j), rolling24=rolling24(j), monthly=j)
        log(tag, res[tag]["overall"], {k: v for k, v in res[tag]["rolling24"].items() if k != "series"})
    (HERE / "results.json").write_text(json.dumps(res, indent=1, default=float))
    log("written")


if __name__ == "__main__":
    main()
