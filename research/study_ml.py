"""Does machine learning beat the simplest rule? Walk-forward test on the cross-sectional (scanner) question.

    python research/study_ml.py

PRE-REGISTERED (2026-10-03, before the first run):
  Question: rank the 30 coins at each 5-minute decision by their next-15m / next-1h return (the V11 scanner question).
  Models, all re-fitted on every fold from TRAINING data only:
    SIMPLE  the single feature with the largest |daily XS IC| on the training window, its sign from training;
    RIDGE   linear regression (L2) on the cross-sectional ranks of every feature + BTC context;
    GBM     sklearn HistGradientBoostingRegressor on the same inputs (shallow: depth 3, 200 iterations, lr 0.05).
  Walk-forward: train on the trailing 28 days, skip 1 day (purge: no training label overlaps the test), test the
  next 7 days, step 7 days, until the data ends. Hyper-parameters are fixed here, never tuned on test folds.
  Metrics on the concatenated test folds: daily-mean XS Spearman IC (t across days), and the long-top-quintile /
  short-bottom-quintile rule on a non-overlapping grid, per position, net of TAKER costs (2 x 5.5 bp + spread).
  KEEP ML only if its test IC beats SIMPLE's in BOTH halves of the test period AND its net-of-taker rule beats
  SIMPLE's and is > 0 over the whole test period. Otherwise the verdict is "ML NOT JUSTIFIED".
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "research"))
from features import FEATURES  # noqa: E402
from study_altdata import TAKER_FEE_RT, group_corr, tstat  # noqa: E402

DB = PROJECT / "data" / "research" / "market.duckdb"
OUT = PROJECT / "docs" / "ML_VS_SIMPLE_STUDY.json"
DAY = 86_400_000
TRAIN_D, PURGE_D, TEST_D = 28, 1, 7
CONTEXT = ["btc_r_1h", "btc_r_4h", "btc_rv_1h", "btc_rv_1d", "btc_r_1d"]
HORIZONS = {"15m": 3, "1h": 12}


def main() -> None:
    t0 = time.time()
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute(f"SELECT symbol, ts, {', '.join(FEATURES + CONTEXT)}, fwd_15m, fwd_1h FROM features_5m").df()
    con.close()
    spreads = json.loads((PROJECT / "data" / "v11_scan" / "spreads.json").read_text())
    hs = {s: float(v.get("half_spread_bps", v) if isinstance(v, dict) else v) / 1e4 for s, v in spreads.items()} if isinstance(spreads, dict) else {}
    df["cost"] = TAKER_FEE_RT + 2 * df["symbol"].map(hs).fillna(2e-4)
    df["day"] = df["ts"] // DAY
    X_cols = []
    for f in FEATURES:
        df["x_" + f] = df.groupby("ts")[f].rank(pct=True) - 0.5
        X_cols.append("x_" + f)
    X_cols += CONTEXT
    for h in HORIZONS:
        df["y_" + h] = df.groupby("ts")["fwd_" + h].rank(pct=True) - 0.5
    days = np.sort(df["day"].unique())
    print(f"{len(df):,} rows, {len(days)} days ({time.time() - t0:.0f}s)", flush=True)
    out = {}
    for h, bars in HORIZONS.items():
        preds = []
        start = 0
        while start + TRAIN_D + PURGE_D + 1 <= len(days):
            tr_days = days[start:start + TRAIN_D]
            te_days = days[start + TRAIN_D + PURGE_D:start + TRAIN_D + PURGE_D + TEST_D]
            if len(te_days) == 0:
                break
            tr = df[df["day"].isin(tr_days)].dropna(subset=["y_" + h])
            te = df[df["day"].isin(te_days)].dropna(subset=["y_" + h]).copy()
            # SIMPLE: best single feature on the training window
            best, best_ic = None, 0.0
            for f in FEATURES:
                ic = group_corr(tr, "ts", "x_" + f, "y_" + h)
                m = ic.groupby(ic.index // DAY).mean().mean()
                if np.isfinite(m) and abs(m) > abs(best_ic):
                    best, best_ic = f, m
            te["p_simple"] = np.sign(best_ic) * te["x_" + best]
            trf = tr.dropna(subset=X_cols)
            sub = trf.sample(min(len(trf), 400_000), random_state=7)
            ridge = Ridge(alpha=10.0).fit(sub[X_cols].values, sub["y_" + h].values)
            gbm = HistGradientBoostingRegressor(max_depth=3, max_iter=200, learning_rate=0.05, random_state=7)
            gbm.fit(sub[X_cols].values, sub["y_" + h].values)
            ok = te[X_cols].notna().all(axis=1)
            te["p_ridge"] = np.nan
            te["p_gbm"] = np.nan
            te.loc[ok, "p_ridge"] = ridge.predict(te.loc[ok, X_cols].values)
            te.loc[ok, "p_gbm"] = gbm.predict(te.loc[ok, X_cols].values)
            preds.append(te[["symbol", "ts", "day", "fwd_" + h, "y_" + h, "cost", "p_simple", "p_ridge", "p_gbm"]].assign(simple_feature=best))
            print(f"{h} fold train {len(tr_days)}d test {len(te_days)}d simple={best} ({best_ic:+.4f})  "
                  f"({time.time() - t0:.0f}s)", flush=True)
            start += TEST_D
        P = pd.concat(preds, ignore_index=True)
        split = int(np.sort(P["day"].unique())[P["day"].nunique() // 2])
        res = {"folds": len(preds), "simple_features_chosen": P.groupby("simple_feature")["day"].nunique().to_dict()}
        grid = P[(P["ts"] // 300_000) % bars == 0]
        for model in ("p_simple", "p_ridge", "p_gbm"):
            Q = P.assign(r=P.groupby("ts")[model].rank(pct=True), yy=P["y_" + h])
            ic = group_corr(Q, "ts", "r", "yy")
            daily = ic.groupby(ic.index // DAY).mean()
            a, b = daily[daily.index < split].values, daily[daily.index >= split].values
            G = grid.assign(r=grid.groupby("ts")[model].rank(pct=True)).dropna(subset=["r"])
            top = G[G["r"] >= 0.8].groupby("ts").agg(t=("fwd_" + h, "mean"), c=("cost", "mean"), day=("day", "first"))
            bot = G[G["r"] <= 0.2].groupby("ts").agg(b=("fwd_" + h, "mean"), cb=("cost", "mean"))
            j = top.join(bot, how="inner")
            net = ((j["t"] - j["b"]) / 2 - (j["c"] + j["cb"]) / 2) * 1e4
            gross = (j["t"] - j["b"]) / 2 * 1e4
            res[model] = {"ic_all": [round(x, 4) if isinstance(x, float) else x for x in tstat(daily.values)],
                          "ic_h1": round(float(np.nanmean(a)), 4), "ic_h2": round(float(np.nanmean(b)), 4),
                          "gross_bp_per_position": round(float(gross.mean()), 2),
                          "net_taker_bp_per_position": round(float(net.mean()), 2),
                          "net_t": round(tstat(net.groupby(j["day"]).mean().values)[1], 2)}
            print(f"{h} {model:9s} IC {res[model]['ic_h1']:+.4f}/{res[model]['ic_h2']:+.4f} (t {res[model]['ic_all'][1]:+.1f}) "
                  f"gross {res[model]['gross_bp_per_position']:+.2f}bp net {res[model]['net_taker_bp_per_position']:+.2f}bp", flush=True)
        s = res["p_simple"]
        verdicts = {}
        for model in ("p_ridge", "p_gbm"):
            m = res[model]
            keep = (m["ic_h1"] > s["ic_h1"] and m["ic_h2"] > s["ic_h2"] and m["net_taker_bp_per_position"] > s["net_taker_bp_per_position"]
                    and m["net_taker_bp_per_position"] > 0)
            verdicts[model] = "KEEP" if keep else "ML NOT JUSTIFIED"
        res["verdict"] = verdicts
        out[h] = res
        print(f"{h} verdict: {verdicts}", flush=True)
    OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                               "rules": __doc__.split("PRE-REGISTERED")[1], "results": out}, indent=1, default=str))
    print(f"wrote {OUT.relative_to(PROJECT)} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
