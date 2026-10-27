"""Step 1 (diagnostics, report only) + Step 2 (DEV variant grid, selection rule from PREREG.md). Writes learn.json."""
import json, sys, time
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib
from lib import DEV_START, DEV_END, TEST_START, TEST_END, DAY

HERE = Path(__file__).resolve().parent
t0 = time.time()
D = lib.build(); P = lib.make_panel(D)
WIN = {"DEV": (DEV_START, DEV_END), "TEST": (TEST_START, TEST_END)}


def daily(curve):
    eq = np.array([e for _, e in curve]); ts = np.array([t for t, _ in curve])
    return ts[:-1], eq[1:] / eq[:-1] - 1


def diagnostics(Pn, wins, var="ORIG"):
    out = {}
    b = Pn.syms.index("BTCUSDT")
    cb = pd.Series(Pn.c[:, b])
    above = lib._shift((cb > cb.rolling(100, min_periods=100).mean()).values.astype(float))
    bvol = lib._shift(np.log(cb).diff().rolling(30, min_periods=25).std().values)
    qv30 = lib._shift(pd.DataFrame(np.nan_to_num(Pn.qv)).rolling(30).sum().values)
    U = lib.um(Pn, 40)
    for name, (s, e) in wins.items():
        r = lib.run_var(Pn, var, s, e, record=True)
        r0 = lib.run_var(Pn, var, s, e, record=True, cost_mult=0)
        rl = lib.run_var(Pn, var, s, e, kL=10, kS=0, lev=0.5)
        rs = lib.run_var(Pn, var, s, e, kL=0, kS=10, lev=0.5)
        d = {"book": lib.stats(r), "gross_zero_cost": lib.stats(r0),
             "long_leg_half_gross": lib.stats(rl), "short_leg_half_gross": lib.stats(rs)}
        d["cost_share_of_gross_log_return"] = round(1 - np.log(1 + r["net_pct"] / 100) / np.log(1 + r0["net_pct"] / 100), 3) \
            if r0["net_pct"] > 0 else None
        ts, dr = daily(r["_curve"])
        ii = ((ts - Pn.t[0]) // DAY).astype(int)
        ab = above[ii]; bv = bvol[ii]

        def bucket(mask):
            x = dr[mask]
            return dict(days=int(mask.sum()), mean_day_pct=round(float(x.mean() * 100), 3),
                        approx_month_pct=round(float(x.mean() * 100 * 30.4), 2),
                        t=round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2) if len(x) > 2 else None)
        d["btc_regime"] = {"BTC>SMA100": bucket(ab == 1), "BTC<=SMA100": bucket(ab == 0)}
        ok = ~np.isnan(bv); q1, q2 = np.nanpercentile(bv[ok], [33.33, 66.67])
        d["btc_vol_tercile"] = {"low": bucket(ok & (bv <= q1)), "mid": bucket(ok & (bv > q1) & (bv <= q2)), "high": bucket(ok & (bv > q2))}
        # positions
        pos = r["_positions"]
        netp = r["_curve"][-1][1] - r["_curve"][0][1]
        rows = []
        for j, side, oi, ci, pnl, eq0 in pos:
            age = (Pn.t[oi] - Pn.onboard[j]) / DAY
            mem = np.where(U[oi])[0]
            rk = int((qv30[oi, mem] > qv30[oi, j]).sum()) + 1
            rows.append((side, age, rk, ci - oi, pnl, pnl / eq0 * 100 if eq0 > 0 else 0))
        A = np.array(rows, float)

        def grp(mask):
            x = A[mask]
            return dict(n=int(mask.sum()), profit_share_pct=round(float(x[:, 4].sum() / netp * 100), 1),
                        win_rate=round(float((x[:, 4] > 0).mean() * 100), 1),
                        mean_ret_pct_of_equity=round(float(x[:, 5].mean()), 4))
        d["by_side_positions"] = {"long": grp(A[:, 0] > 0), "short": grp(A[:, 0] < 0)}
        d["by_age_days"] = {"<60": grp(A[:, 1] < 60), "60-180": grp((A[:, 1] >= 60) & (A[:, 1] < 180)),
                            "180-365": grp((A[:, 1] >= 180) & (A[:, 1] < 365)), ">=365": grp(A[:, 1] >= 365)}
        d["by_age_note"] = "age = days since first archived bar (perp data floor 2020-01-01; spot floor 2017-08-17)"
        d["by_liquidity_rank"] = {"1-13": grp(A[:, 2] <= 13), "14-26": grp((A[:, 2] > 13) & (A[:, 2] <= 26)), "27-40": grp(A[:, 2] > 26)}
        win, los = A[:, 4] > 0, A[:, 4] <= 0
        top = A[:, 4] >= np.percentile(A[:, 4], 95)
        d["holding_days"] = dict(winners_median=float(np.median(A[win, 3])), winners_mean=round(float(A[win, 3].mean()), 1),
                                 losers_median=float(np.median(A[los, 3])), losers_mean=round(float(A[los, 3].mean()), 1),
                                 top5pct_winners_median=float(np.median(A[top, 3])),
                                 profit_share_held_le3d=round(float(A[A[:, 3] <= 3, 4].sum() / netp * 100), 1),
                                 profit_share_held_gt10d=round(float(A[A[:, 3] > 10, 4].sum() / netp * 100), 1))
        srt = sorted(pos, key=lambda p: -p[4])[:5]
        d["top5_positions"] = [(Pn.syms[p[0]], "L" if p[1] > 0 else "S", round(p[4] / netp * 100, 2)) for p in srt]
        cp = np.zeros(len(Pn.syms))
        for p in pos:
            cp[p[0]] += p[4]
        d["top5_coins"] = {Pn.syms[j]: round(float(cp[j] / netp * 100), 2) for j in np.argsort(-cp)[:5]}
        out[name] = d
        print(name, "done", f"[{time.time()-t0:.0f}s]", flush=True)
    return out


if __name__ == "__main__":
    res = {"diagnostics_ORIG": diagnostics(P, WIN)}
    # Step 2: DEV grid
    grid = {}
    for v in lib.VARIANTS:
        d1 = lib.run_var(P, v, DEV_START, DEV_END, record=True)
        d2 = lib.run_var(P, v, DEV_START, DEV_END, cost_mult=2)
        te = lib.run_var(P, v, TEST_START, TEST_END, record=True)
        grid[v] = dict(dev=lib.stats(d1), dev_net_2x=d2["net_pct"], dev_max_pos_share=lib.max_pos_share(d1),
                       test_context=lib.stats(te), test_max_pos_share_context=lib.max_pos_share(te))
        print(v, grid[v]["dev"], grid[v]["dev_net_2x"], grid[v]["dev_max_pos_share"], "| TEST", grid[v]["test_context"]["geo"],
              grid[v]["test_max_pos_share_context"], flush=True)
    og = grid["ORIG"]["dev"]["geo"]
    elig = [v for v in grid if v != "ORIG" and grid[v]["dev_net_2x"] > 0 and grid[v]["dev"]["max_dd"] <= 40
            and grid[v]["dev"]["geo"] >= og - 0.5]
    sel = []
    if elig:
        v1 = sorted(elig, key=lambda v: (grid[v]["dev_max_pos_share"], -grid[v]["dev"]["t"]))[0]; sel.append(v1)
        rest = [v for v in elig if v != v1]
        if rest:
            sel.append(sorted(rest, key=lambda v: -grid[v]["dev"]["t"])[0])
    res.update(dev_grid=grid, eligible=elig, selected=sel)
    print("eligible", elig, "SELECTED", sel)
    (HERE / "learn.json").write_text(json.dumps(res, indent=1, default=float))
    print(f"done [{time.time()-t0:.0f}s]")
