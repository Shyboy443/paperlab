"""Feature engine: raw research tables -> one standardized 5-minute feature panel (features_5m) in the DuckDB store.

    python research/features.py

POINT-IN-TIME RULES (no look-ahead):
  * A row is a DECISION at D = bar_open + 5 min (the 5m bar's close). Every feature uses data KNOWN at D:
      - price/volume: the 1m bars that closed at or before D;
      - open interest and long/short ratio: the 5-minute snapshot stamped <= D - 5 min (one interval of safety lag:
        the venue's stamp may mark the interval start);
      - premium index: the 5m premium bar that closed at or before D;
      - funding: the last rate SETTLED at or before D;
      - Binance taker volume: the 5m bar that closed at or before D.
  * Rolling statistics (z-scores, percentiles, correlations) use trailing windows only.
  * Targets: fwd_<h> = ln(exit / entry) with entry = close of the 1m bar opening at D (a fill ~60 s after the
    decision, the paper engine's latency) and exit = close of the 1m bar opening at D + h. Never used as a feature.

Feature list (FEATURES): price baselines first (they are what every alternative feature must beat), then alternative
data. Each is also stored as a per-coin trailing z-score (z_<name>, 7-day window) for time-series studies.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
DB = PROJECT / "data" / "research" / "market.duckdb"
FIVE = 300_000
HORIZONS = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}
BASELINES = ["r_5m", "r_1h", "r_4h", "rv_1h", "vol_z"]
PRICE = BASELINES + ["rsi_14", "ma_slope", "vwap_dist", "range_pos_1d", "btc_corr_1d", "btc_beta_1d"]
ALT = ["oi_chg_15m", "oi_chg_1h", "oi_chg_4h", "ls_ratio", "ls_chg_1h", "premium", "premium_chg_1h", "funding",
       "taker_imb_5m", "taker_imb_1h", "oi_x_price_1h"]
FEATURES = PRICE + ALT
ZWIN = 7 * 288                                       # 7 days of 5m bars


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def symbol_panel(con: duckdb.DuckDBPyConnection, symbol: str, btc_r: pd.Series | None) -> pd.DataFrame:
    m = con.execute("SELECT ts, open, high, low, close, volume, turnover FROM candles_1m WHERE symbol = ? ORDER BY ts",
                    [symbol]).df().set_index("ts")
    if m.empty:
        return pd.DataFrame()
    # 5m bars from 1m: bar = floor(ts / 5m); D = bar + 5m
    g = m.groupby((m.index // FIVE) * FIVE)
    b = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "volume": g["volume"].sum(), "turnover": g["turnover"].sum(),
                      "n": g["close"].size()})
    b = b[b["n"] == 5].drop(columns="n")
    b.index.name = "bar"
    full = np.arange(b.index.min(), b.index.max() + FIVE, FIVE)
    b = b.reindex(full)
    b.index.name = "bar"
    D = b.index.values + FIVE
    c = b["close"]
    lr = np.log(c).diff()
    f = pd.DataFrame(index=b.index)
    f["symbol"] = symbol
    f["ts"] = D                                                   # decision time
    f["close"] = c
    f["r_5m"] = lr
    f["r_1h"] = np.log(c / c.shift(12))
    f["r_4h"] = np.log(c / c.shift(48))
    f["rv_1h"] = lr.rolling(12, min_periods=10).std()
    lv = np.log(b["turnover"].replace(0, np.nan))
    f["vol_z"] = (lv - lv.rolling(288, min_periods=200).mean()) / lv.rolling(288, min_periods=200).std()
    f["rsi_14"] = rsi(c)
    ema = c.ewm(span=50, adjust=False).mean()
    f["ma_slope"] = np.log(ema / ema.shift(6))
    vwap = b["turnover"].rolling(288, min_periods=200).sum() / b["volume"].rolling(288, min_periods=200).sum()
    rv_d = lr.rolling(288, min_periods=200).std() * np.sqrt(288)
    f["vwap_dist"] = np.log(c / vwap) / rv_d
    hi, lo = b["high"].rolling(288, min_periods=200).max(), b["low"].rolling(288, min_periods=200).min()
    f["range_pos_1d"] = (c - lo) / (hi - lo)
    if btc_r is not None:
        br = btc_r.reindex(b.index)
        f["btc_corr_1d"] = lr.rolling(288, min_periods=200).corr(br)
        f["btc_beta_1d"] = lr.rolling(288, min_periods=200).cov(br) / br.rolling(288, min_periods=200).var()
    else:
        f["btc_corr_1d"] = 1.0
        f["btc_beta_1d"] = 1.0

    def asof(table: str, col: str, lag_ms: int) -> pd.Series:
        s = con.execute(f"SELECT ts, {col} FROM {table} WHERE symbol = ? ORDER BY ts", [symbol]).df()
        if s.empty:
            return pd.Series(np.nan, index=b.index)
        s["known"] = s["ts"] + lag_ms
        left = pd.DataFrame({"D": D})
        out = pd.merge_asof(left, s[["known", col]].sort_values("known"), left_on="D", right_on="known", direction="backward",
                            tolerance=9 * 3_600_000 if table == "funding" else 3 * FIVE)
        return pd.Series(out[col].values, index=b.index)

    oi = asof("oi_5m", "oi", FIVE)                                # snapshot stamped <= D - 5m
    f["oi_chg_15m"] = np.log(oi / oi.shift(3))
    f["oi_chg_1h"] = np.log(oi / oi.shift(12))
    f["oi_chg_4h"] = np.log(oi / oi.shift(48))
    ls = asof("ls_5m", "buy_ratio", FIVE)
    f["ls_ratio"] = ls
    f["ls_chg_1h"] = ls - ls.shift(12)
    prem = asof("premium_5m", "premium", FIVE)                    # premium bar opening at ts closes at ts + 5m
    f["premium"] = prem
    f["premium_chg_1h"] = prem - prem.shift(12)
    fund = asof("funding", "rate", 0)                             # settled at ts: known at ts
    f["funding"] = fund
    tk = con.execute("SELECT ts, volume, taker_buy FROM taker_5m WHERE symbol = ? ORDER BY ts", [symbol]).df()
    if not tk.empty:
        tk = tk.set_index("ts").reindex(b.index)
        f["taker_imb_5m"] = 2 * tk["taker_buy"] / tk["volume"] - 1
        f["taker_imb_1h"] = 2 * tk["taker_buy"].rolling(12, min_periods=10).sum() / tk["volume"].rolling(12, min_periods=10).sum() - 1
    else:
        f["taker_imb_5m"] = np.nan
        f["taker_imb_1h"] = np.nan
    f["oi_x_price_1h"] = f["oi_chg_1h"] * np.sign(f["r_1h"])      # OI building WITH the move (+) or against it (-)

    for name in FEATURES:
        x = f[name]
        mu, sd = x.rolling(ZWIN, min_periods=288).mean(), x.rolling(ZWIN, min_periods=288).std()
        f["z_" + name] = (x - mu) / sd.replace(0, np.nan)

    # targets: entry = close of the 1m bar opening at D; exit = close of the 1m bar opening at D + h
    closes = m["close"]
    entry = closes.reindex(D).values
    for h, mins in HORIZONS.items():
        ex = closes.reindex(D + mins * 60_000).values
        f["fwd_" + h] = np.log(ex / entry)
    f["rv_1h_fwd_scale"] = f["rv_1h"]                             # for vol-normalized targets
    return f.reset_index(drop=True)


def main() -> None:
    t0 = time.time()
    con = duckdb.connect(str(DB))
    symbols = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM candles_1m ORDER BY 1").fetchall()]
    btc = symbol_panel(con, "BTCUSDT", None)
    btc_r = pd.Series(btc["r_5m"].values, index=btc["ts"].values - FIVE)
    parts = [btc]
    for s in symbols:
        if s == "BTCUSDT":
            continue
        parts.append(symbol_panel(con, s, btc_r))
        print(f"{s:14s} {len(parts[-1]):7d} rows  ({time.time() - t0:.0f}s)", flush=True)
    panel = pd.concat(parts, ignore_index=True)
    # market context (same value for every coin at a decision): BTC's own state
    ctx = btc[["ts", "r_1h", "r_4h", "rv_1h"]].rename(columns={"r_1h": "btc_r_1h", "r_4h": "btc_r_4h", "rv_1h": "btc_rv_1h"})
    ctx["btc_rv_1d"] = btc["r_5m"].rolling(288, min_periods=200).std().values * np.sqrt(288)
    ctx["btc_r_1d"] = np.log(btc["close"] / btc["close"].shift(288)).values
    # regimes, causal: BTC daily volatility vs its trailing 30-day terciles; trend from the 1-day return in vol units
    q = ctx["btc_rv_1d"]
    lo, hi = q.rolling(30 * 288, min_periods=7 * 288).quantile(1 / 3), q.rolling(30 * 288, min_periods=7 * 288).quantile(2 / 3)
    ctx["vol_regime"] = np.where(q > hi, "high", np.where(q < lo, "low", "mid"))
    tr = ctx["btc_r_1d"] / ctx["btc_rv_1d"]
    ctx["trend_regime"] = np.select([tr > 1.0, tr > 0.3, tr < -1.0, tr < -0.3], ["strong_up", "weak_up", "strong_down", "weak_down"], "range")
    panel = panel.merge(ctx, on="ts", how="left")
    panel = panel.replace([np.inf, -np.inf], np.nan)
    con.execute("CREATE OR REPLACE TABLE features_5m AS SELECT * FROM panel")
    n, ns = con.execute("SELECT count(*), count(DISTINCT symbol) FROM features_5m").fetchone()
    print(f"features_5m: {n:,} rows, {ns} symbols, {len(panel.columns)} columns in {time.time() - t0:.0f}s")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
