"""Which data actually predicts returns? Price baselines vs alternative data, 30 Bybit perps, 4 horizons.

    python research/study_altdata.py          # needs research/ingest.py + research/features.py first

PRE-REGISTERED (written before the first run, 2026-10-03; the rules below are not tuned to the results):

Data: features_5m (research/features.py), every 5-minute decision over the store's window (~99 days, 30 coins), each
feature known at the decision, returns from a fill 60 s later. DISCOVERY = first half of the days, CONFIRMATION =
second half. Statistics are clustered by DAY (a daily mean IC, then a t-stat across days), so overlapping horizons and
the 30 coins inside one day do not inflate significance.

Two uses of a feature are tested separately:
  XS (cross-sectional, the scanner question): at each decision, rank the coins by the feature; Spearman IC with the
      coins' forward returns.
  TS (time-series, the single-coin question): the feature as a per-coin trailing 7-day z-score; Spearman IC with the
      forward return in volatility units, pooled over coins within a day.

A feature PASSES at a horizon (for XS or TS) only if ALL hold:
  1. SIGNAL      daily-mean IC has the same sign in both halves and |t| >= 2.5 in EACH half;
  2. NEW INFO    (alternative data only) its coefficient in a within-day pooled regression on the price baselines
                 (r_5m, r_1h, r_4h, rv_1h, vol_z) + the feature has the IC's sign with |t| >= 2.0 in EACH half;
  3. TRADEABLE   a simple rule using it (XS: long the top / short the bottom quintile, sign fixed by the DISCOVERY
                 IC, non-overlapping holds; TS: trade when |z| >= 2, one trade per coin per hold) is net-positive
                 after TAKER costs (2 x 5.5 bp fee + the coin's measured spread) in EACH half
                 -> TRADEABLE; only after MAKER costs (2 x 2 bp, an optimistic bound: no adverse selection)
                 -> MAKER-ONLY; otherwise INFORMATIVE (may still be useful as a risk filter, not as a trade signal).
A feature that fails 1 (or 2) is REMOVED from the candidate set. Redundancy: pairs with pooled |rho| > 0.7 are
reported; of a redundant pair only the one with the larger confirmation |t| is kept.
Regimes: each PASSING feature's IC is also reported by BTC volatility regime and trend regime, for information only
(no second selection on regimes).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "research"))
from features import ALT, BASELINES, FEATURES, HORIZONS  # noqa: E402

DB = PROJECT / "data" / "research" / "market.duckdb"
OUT = PROJECT / "docs" / "ALTDATA_STUDY.json"
DAY = 86_400_000
TAKER_FEE_RT, MAKER_FEE_RT = 2 * 0.00055, 2 * 0.0002
T_SIGNAL, T_NEWINFO, Z_TRADE = 2.5, 2.0, 2.0


def tstat(x: np.ndarray) -> tuple[float, float, int]:
    x = x[np.isfinite(x)]
    if len(x) < 5:
        return float("nan"), float("nan"), len(x)
    return float(x.mean()), float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), len(x)


def group_corr(df: pd.DataFrame, key: str, a: str, b: str) -> pd.Series:
    """Pearson corr of a, b within each group (inputs already ranked -> Spearman)."""
    g = df[[key, a, b]].dropna()
    g = g.assign(ab=g[a] * g[b], aa=g[a] ** 2, bb=g[b] ** 2)
    s = g.groupby(key).agg(n=(a, "size"), ma=(a, "mean"), mb=(b, "mean"), mab=("ab", "mean"), maa=("aa", "mean"),
                           mbb=("bb", "mean"))
    s = s[s["n"] >= 10]
    cov = s["mab"] - s["ma"] * s["mb"]
    den = np.sqrt((s["maa"] - s["ma"] ** 2) * (s["mbb"] - s["mb"] ** 2))
    return cov / den.replace(0, np.nan)


def halves(day_series: pd.Series, split_day: int) -> dict:
    v = day_series.dropna()
    a, b = v[v.index < split_day].values, v[v.index >= split_day].values
    m1, t1, n1 = tstat(a)
    m2, t2, n2 = tstat(b)
    return {"h1": round(m1, 4), "t1": round(t1, 2), "days1": n1, "h2": round(m2, 4), "t2": round(t2, 2), "days2": n2}


def within_day_coef(df: pd.DataFrame, y: str, xs: list[str], feat: str, split_day: int) -> dict:
    """Per day: pooled OLS of y on [baselines + feature] (all inputs pre-standardized) -> the feature's coefficient;
    then mean and t across days, per half (a Fama-MacBeth style test of NEW information)."""
    cols = [y] + xs + [feat]
    coefs = {}
    for day, g in df[["day"] + cols].dropna().groupby("day"):
        if len(g) < 200:
            continue
        X = np.column_stack([np.ones(len(g))] + [g[c].values for c in xs + [feat]])
        beta, *_ = np.linalg.lstsq(X, g[y].values, rcond=None)
        coefs[day] = beta[-1]
    return halves(pd.Series(coefs), split_day)


def main() -> None:
    t0 = time.time()
    con = duckdb.connect(str(DB), read_only=True)
    cols = ["symbol", "ts", "rv_1h", "vol_regime", "trend_regime"] + FEATURES + ["z_" + f for f in FEATURES] + \
           ["fwd_" + h for h in HORIZONS]
    df = con.execute(f"SELECT {', '.join(cols)} FROM features_5m").df()
    con.close()
    spreads = json.loads((PROJECT / "data" / "v11_scan" / "spreads.json").read_text())
    half_spread = {s: float(v.get("half_spread_bps", v) if isinstance(v, dict) else v) / 1e4 for s, v in spreads.items()} \
        if isinstance(spreads, dict) else {}
    df["day"] = df["ts"] // DAY
    days = np.sort(df["day"].unique())
    split_day = int(days[len(days) // 2])
    df["cost_taker"] = TAKER_FEE_RT + 2 * df["symbol"].map(half_spread).fillna(2e-4)
    print(f"loaded {len(df):,} rows, {df['symbol'].nunique()} coins, {len(days)} days, split at day {split_day} "
          f"({time.time() - t0:.0f}s)", flush=True)

    # cross-sectional ranks (pct) per decision, and vol-normalized TS targets
    for f in FEATURES:
        df["xr_" + f] = df.groupby("ts")[f].rank(pct=True)
    for h, mins in HORIZONS.items():
        df["xr_fwd_" + h] = df.groupby("ts")["fwd_" + h].rank(pct=True)
        df["vn_fwd_" + h] = df["fwd_" + h] / (df["rv_1h"] * np.sqrt(mins / 5))
        df["dv_fwd_" + h] = df.groupby("day")["vn_fwd_" + h].rank(pct=True)     # TS: ranks within the day
        df["xy_" + h] = df["xr_fwd_" + h] - 0.5
    for f in FEATURES:
        df["dz_" + f] = df.groupby("day")["z_" + f].rank(pct=True)
    for f in BASELINES + ALT:                                           # within-ts demeaned ranks for the XS regression
        df["xd_" + f] = df["xr_" + f] - 0.5
    print(f"ranked ({time.time() - t0:.0f}s)", flush=True)

    results, corr_pairs = {}, []
    for h, mins in HORIZONS.items():
        bars = mins // 5
        # non-overlapping decision grid for the trading rules
        grid = df[(df["ts"] // 300_000) % bars == 0]
        for f in FEATURES:
            r = {}
            # ---- XS
            ic = group_corr(df, "ts", "xr_" + f, "xr_fwd_" + h)
            daily = ic.groupby(ic.index // DAY).mean()
            xs = {"ic": halves(daily, split_day)}
            if f in ALT:
                xs["new_info"] = within_day_coef(df, "xy_" + h, ["xd_" + b for b in BASELINES], "xd_" + f, split_day)
            sign = np.sign(xs["ic"]["h1"]) or 1.0
            g = grid[["ts", "day", "xr_" + f, "fwd_" + h, "cost_taker"]].dropna()
            top = g[g["xr_" + f] >= 0.8].groupby("ts").agg(r=("fwd_" + h, "mean"), c=("cost_taker", "mean"), day=("day", "first"))
            bot = g[g["xr_" + f] <= 0.2].groupby("ts").agg(r=("fwd_" + h, "mean"), c=("cost_taker", "mean"))
            j = top.join(bot, rsuffix="_b", how="inner")
            gross = sign * (j["r"] - j["r_b"]) / 2                       # per position
            cost_t = (j["c"] + j["c_b"]) / 2
            xs["rule"] = {"gross_bp": halves((gross * 1e4).groupby(j["day"]).mean(), split_day),
                          "net_taker_bp": halves(((gross - cost_t) * 1e4).groupby(j["day"]).mean(), split_day),
                          "net_maker_bp": halves(((gross - MAKER_FEE_RT) * 1e4).groupby(j["day"]).mean(), split_day),
                          "trades_per_day": round(2 * len(j) / max(1, len(days)), 1)}
            r["xs"] = xs
            # ---- TS
            ic = group_corr(df, "day", "dz_" + f, "dv_fwd_" + h)
            ts = {"ic": halves(ic, split_day)}
            if f in ALT:
                ts["new_info"] = within_day_coef(df, "vn_fwd_" + h, ["z_" + b for b in BASELINES], "z_" + f, split_day)
            sign = np.sign(ts["ic"]["h1"]) or 1.0
            g = grid[["symbol", "ts", "day", "z_" + f, "fwd_" + h, "cost_taker"]].dropna()
            g = g[g["z_" + f].abs() >= Z_TRADE]
            gross = sign * np.sign(g["z_" + f]) * g["fwd_" + h]
            ts["rule"] = {"gross_bp": halves((gross * 1e4).groupby(g["day"]).mean(), split_day),
                          "net_taker_bp": halves(((gross - g["cost_taker"]) * 1e4).groupby(g["day"]).mean(), split_day),
                          "net_maker_bp": halves(((gross - MAKER_FEE_RT) * 1e4).groupby(g["day"]).mean(), split_day),
                          "trades_per_day": round(len(g) / max(1, len(days)), 1)}
            r["ts"] = ts
            for use in ("xs", "ts"):
                r[use]["verdict"] = verdict(r[use], f in ALT)
            results.setdefault(f, {})[h] = r
            print(f"{h:>3s} {f:15s} XS ic {r['xs']['ic']['h1']:+.4f}/{r['xs']['ic']['h2']:+.4f} "
                  f"t {r['xs']['ic']['t1']:+.1f}/{r['xs']['ic']['t2']:+.1f} net {r['xs']['rule']['net_taker_bp']['h1']:+.1f}/"
                  f"{r['xs']['rule']['net_taker_bp']['h2']:+.1f}bp -> {r['xs']['verdict']:12s} | TS ic "
                  f"{r['ts']['ic']['h1']:+.4f}/{r['ts']['ic']['h2']:+.4f} t {r['ts']['ic']['t1']:+.1f}/{r['ts']['ic']['t2']:+.1f} "
                  f"net {r['ts']['rule']['net_taker_bp']['h1']:+.1f}/{r['ts']['rule']['net_taker_bp']['h2']:+.1f}bp "
                  f"-> {r['ts']['verdict']}  ({time.time() - t0:.0f}s)", flush=True)

    # redundancy (pooled Spearman across the raw features)
    sample = df[FEATURES].sample(min(len(df), 300_000), random_state=7).rank()
    cm = sample.corr()
    for i, a in enumerate(FEATURES):
        for b in FEATURES[i + 1:]:
            if abs(cm.loc[a, b]) > 0.7:
                corr_pairs.append([a, b, round(float(cm.loc[a, b]), 3)])

    # regimes, for passing features only (information, not selection)
    regimes = {}
    for f, by_h in results.items():
        for h, r in by_h.items():
            for use in ("xs", "ts"):
                if r[use]["verdict"] in ("TRADEABLE", "MAKER-ONLY", "INFORMATIVE"):
                    regimes.setdefault(f"{f}|{h}|{use}", {})
                    for kind in ("vol_regime", "trend_regime"):
                        for lvl, sub in df.groupby(kind):
                            if use == "xs":
                                ic = group_corr(sub, "ts", "xr_" + f, "xr_fwd_" + h)
                                v = ic.groupby(ic.index // DAY).mean()
                            else:
                                v = group_corr(sub, "day", "dz_" + f, "dv_fwd_" + h)
                            m, t, n = tstat(v.values)
                            regimes[f"{f}|{h}|{use}"][f"{kind}={lvl}"] = {"ic": round(m, 4), "t": round(t, 2), "days": n}

    summary = {u: {h: {v: [f for f in FEATURES if results[f][h][u]["verdict"] == v]
                       for v in ("TRADEABLE", "MAKER-ONLY", "INFORMATIVE", "REMOVED")} for h in HORIZONS} for u in ("xs", "ts")}
    OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "rows": len(df),
                               "coins": int(df["symbol"].nunique()), "days": int(len(days)), "split_day": split_day,
                               "rules": __doc__.split("PRE-REGISTERED")[1], "summary": summary,
                               "redundant_pairs": corr_pairs, "regimes": regimes, "results": results}, indent=1))
    print("\nSUMMARY")
    for u in ("xs", "ts"):
        for h in HORIZONS:
            print(f"{u.upper()} {h:>3s} " + " | ".join(f"{v}: {', '.join(summary[u][h][v]) or '-'}"
                                                       for v in ("TRADEABLE", "MAKER-ONLY", "INFORMATIVE")))
    print("redundant pairs (|rho| > 0.7):", corr_pairs)
    print(f"wrote {OUT.relative_to(PROJECT)} in {time.time() - t0:.0f}s")


def verdict(r: dict, alt: bool) -> str:
    ic = r["ic"]
    ok = (np.sign(ic["h1"]) == np.sign(ic["h2"]) != 0 and abs(ic["t1"]) >= T_SIGNAL and abs(ic["t2"]) >= T_SIGNAL)
    if ok and alt:
        ni = r["new_info"]
        s = np.sign(ic["h1"])
        ok = (np.sign(ni["h1"]) == s and np.sign(ni["h2"]) == s and abs(ni["t1"]) >= T_NEWINFO and abs(ni["t2"]) >= T_NEWINFO)
    if not ok:
        return "REMOVED"
    rule = r["rule"]
    if rule["net_taker_bp"]["h1"] > 0 and rule["net_taker_bp"]["h2"] > 0:
        return "TRADEABLE"
    if rule["net_maker_bp"]["h1"] > 0 and rule["net_maker_bp"]["h2"] > 0:
        return "MAKER-ONLY"
    return "INFORMATIVE"


if __name__ == "__main__":
    sys.exit(main())
