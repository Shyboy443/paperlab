"""V11 feature study: what, if anything, predicts which coins do best over the next 1-24 hours? (docs/V11_PROTOCOL.md)

    python scripts/v11_scan_data.py        # 1m bars + funding / open interest / account ratio / premium (cached)
    python scripts/v11_feature_study.py

The rule-based scanners of scripts/v11_scan_study.py had no gross edge. This asks the question underneath: across the
30-coin universe, does any feature known at the decision instant rank the coins' FORWARD returns? At every hour close T
(entry at the open of the minute starting T + 60 s, exit at the close T + h, h = 1 / 4 / 8 / 24 h):

    r1 r4 r24 r72      the coin's own return over the last 1 / 4 / 24 / 72 h (momentum or reversal)
    idio4              its 4 h return minus beta x BTC's (the move that is the coin's own)
    volz               log(last hour's volume / its median hour over 7 days)
    rpos24             where the close sits in the 24 h high-low range (0 = at the low)
    rvol24             24 h realised volatility of hourly returns
    fund               the last SETTLED funding rate            prem   the last closed 1h premium index
    oi4 oi24           open-interest change over 4 / 24 h       ratio  long/short account ratio   dratio24 its 24h change

Metric: the cross-sectional Spearman rank IC between the feature and the coins' forward return MINUS the universe
mean (who beats whom, whatever the market does), per decision instant; t-statistic over NON-overlapping instants
(every h hours), separately in DISCOVERY (first half) and CONFIRMATION (second half).

Trading check for every feature and horizon: every h hours a long-short book goes long the 3 coins the feature ranks
best and short the 3 worst (sign taken from DISCOVERY only), holds h hours; costs: Bybit taker 0.055% + each coin's
half spread per side. Reported as net bps per position.

DECISION RULE (fixed before running): a feature x horizon is ROBUST if its IC has the same sign in both halves with
|t| >= 2 in each, AND the long-short book nets > 0 after costs in both halves. Robust features (if any) become the V11
ranker; none robust means no V11 bot can claim an edge from this universe and window.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
from v11_scan_study import DATA, MIN, TAKER, WARM_DAYS, load  # noqa: E402

HOUR = 60 * MIN
HORIZONS = (1, 4, 8, 24)
K = 3


def hourly(d: dict) -> dict:
    n = d["C"].shape[1] // 60
    r = lambda a: a[:, :n * 60].reshape(a.shape[0], n, 60)
    return {"O": r(d["O"])[:, :, 0], "H": r(d["H"]).max(2), "L": r(d["L"]).min(2), "C": r(d["C"])[:, :, -1],
            "V": r(d["V"]).sum(2)}


def step_series(points: list, grid_ms: np.ndarray) -> np.ndarray:
    """The last value stamped at or before each grid instant (NaN before the first)."""
    if not points:
        return np.full(len(grid_ms), np.nan)
    ts = np.array([p[0] for p in points], dtype=np.int64)
    v = np.array([p[1] for p in points], dtype=float)
    o = np.argsort(ts)
    ts, v = ts[o], v[o]
    i = np.searchsorted(ts, grid_ms, side="right") - 1
    out = np.where(i >= 0, v[np.clip(i, 0, None)], np.nan)
    return out


def features(d: dict, h1: dict) -> dict[str, np.ndarray]:
    """Feature f[:, j] is known at T_j = start + j hours (built only from hour bars < j and data stamped <= T_j)."""
    C, V, H, L = h1["C"], h1["V"], h1["H"], h1["L"]
    ns, n = C.shape
    sh = lambda a, k: np.concatenate([np.full((ns, k), np.nan), a[:, :-k]], axis=1)  # value k hours earlier
    last = sh(C, 1)                                        # close of the last CLOSED hour at T_j
    f: dict[str, np.ndarray] = {}
    for k in (1, 4, 24, 72):
        f[f"r{k}"] = last / sh(C, k + 1) - 1
    lr = np.log(C / sh(C, 1))
    bi = d["syms"].index("BTCUSDT")
    r4 = f["r4"]
    b4 = r4[bi]
    lrb = pd.Series(lr[bi])
    beta = np.stack([pd.Series(lr[k]).rolling(24 * 7).cov(lrb).to_numpy() / lrb.rolling(24 * 7).var().to_numpy()
                     for k in range(ns)])
    f["idio4"] = r4 - sh(beta, 1) * b4[None, :]
    med_v = pd.DataFrame(V.T).rolling(24 * 7).median().to_numpy().T
    f["volz"] = np.log(sh(V, 1) / sh(med_v, 2))
    hi24 = sh(pd.DataFrame(H.T).rolling(24).max().to_numpy().T, 1)
    lo24 = sh(pd.DataFrame(L.T).rolling(24).min().to_numpy().T, 1)
    f["rpos24"] = (last - lo24) / np.where(hi24 > lo24, hi24 - lo24, np.nan)
    f["rvol24"] = sh(pd.DataFrame(lr.T).rolling(24).std().to_numpy().T, 1)
    grid = d["start"] + np.arange(n) * HOUR
    pos = {s: json.loads((DATA / f"{s}.pos.json").read_text()) for s in d["syms"]}
    # an hourly positioning point stamped at the hour it opens is known only once that hour has closed
    series = {k: np.stack([step_series(pos[s][k], grid - (HOUR if k in ("oi", "ratio", "premium") else 0)) for s in d["syms"]])
              for k in ("funding", "oi", "ratio", "premium")}
    f["fund"] = series["funding"]
    f["prem"] = series["premium"]
    oi = series["oi"]
    f["oi4"] = oi / sh(oi, 4) - 1
    f["oi24"] = oi / sh(oi, 24) - 1
    f["ratio"] = series["ratio"]
    f["dratio24"] = series["ratio"] - sh(series["ratio"], 24)
    return f


def forward(d: dict, n: int, h: int) -> np.ndarray:
    """Entry at the open of minute T+60s, exit at the close of the last minute of hour j+h-1."""
    out = np.full((d["C"].shape[0], n), np.nan)
    j = np.arange(n)
    ok = (j + h) * 60 - 1 < d["C"].shape[1]
    e = j[ok] * 60 + 1
    x = (j[ok] + h) * 60 - 1
    out[:, ok] = d["C"][:, x] / d["O"][:, e] - 1
    return out


def rank_ic(f: np.ndarray, y: np.ndarray, cols: np.ndarray) -> np.ndarray:
    fr = pd.DataFrame(f[:, cols]).rank(axis=0)
    yr = pd.DataFrame(y[:, cols] - np.nanmean(y[:, cols], axis=0, keepdims=True)).rank(axis=0)
    ok = fr.notna() & yr.notna()
    fr, yr = fr.where(ok), yr.where(ok)
    fc, yc = fr - fr.mean(), yr - yr.mean()
    num = (fc * yc).sum()
    den = np.sqrt((fc ** 2).sum() * (yc ** 2).sum())
    ic = (num / den).to_numpy()
    ic[ok.sum().to_numpy() < 10] = np.nan
    return ic


def tstat(x: np.ndarray) -> tuple[float, float, int]:
    x = x[~np.isnan(x)]
    if len(x) < 5:
        return float("nan"), float("nan"), len(x)
    return float(x.mean()), float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), len(x)


def long_short(f: np.ndarray, y: np.ndarray, cols: np.ndarray, sign: float, hs: np.ndarray) -> dict:
    """Long the K best-ranked coins, short the K worst, at every instant in `cols`; net bps per position."""
    nets, gross = [], []
    for j in cols:
        v = sign * f[:, j]
        r = y[:, j]
        ok = ~np.isnan(v) & ~np.isnan(r)
        if ok.sum() < 2 * K + 4:
            continue
        idx = np.nonzero(ok)[0]
        o = idx[np.argsort(v[idx])]
        for k in o[-K:]:
            gross.append(r[k])
            nets.append(r[k] - 2 * (TAKER + hs[k]))
        for k in o[:K]:
            gross.append(-r[k])
            nets.append(-r[k] - 2 * (TAKER + hs[k]))
    x, g = np.array(nets), np.array(gross)
    return {"positions": len(x), "net_bps": round(float(x.mean() * 1e4), 2) if len(x) else None,
            "gross_bps": round(float(g.mean() * 1e4), 2) if len(g) else None}


def main() -> None:
    t0 = time.time()
    d = load()
    h1 = hourly(d)
    ns, n = h1["C"].shape
    f = features(d, h1)
    warm = WARM_DAYS * 24
    last_ok = n - max(HORIZONS) - 1
    mid = warm + (last_ok - warm) // 2
    print(f"{ns} coins, {n} hours; discovery hours {warm}-{mid}, confirmation {mid}-{last_ok}", flush=True)
    rep: dict = {"coins": d["syms"], "horizons_h": HORIZONS, "k": K, "split_hour": mid, "features": {}}
    robust = []
    print(f"{'feature':9s} {'h':>3s} | {'IC disc':>8s} {'t':>6s} | {'IC conf':>8s} {'t':>6s} | {'LS disc bps':>11s} {'LS conf bps':>11s}")
    for h in HORIZONS:
        y = forward(d, n, h)
        disc = np.arange(warm, mid - h, h)
        conf = np.arange(mid, last_ok, h)
        for name, x in f.items():
            ic = rank_ic(x, y, np.arange(n))
            m1, t1, n1 = tstat(ic[disc])
            m2, t2, n2 = tstat(ic[conf])
            sign = 1.0 if (m1 or 0) >= 0 else -1.0
            ls1 = long_short(x, y, disc, sign, d["hs"])
            ls2 = long_short(x, y, conf, sign, d["hs"])
            ok = (np.sign(m1) == np.sign(m2) and abs(t1) >= 2 and abs(t2) >= 2 and (ls1["net_bps"] or -1) > 0
                  and (ls2["net_bps"] or -1) > 0)
            rep["features"][f"{name}@{h}h"] = {"ic_disc": round(m1, 4), "t_disc": round(t1, 2), "n_disc": n1,
                                               "ic_conf": round(m2, 4), "t_conf": round(t2, 2), "n_conf": n2,
                                               "sign": sign, "ls_disc": ls1, "ls_conf": ls2, "robust": bool(ok)}
            if ok:
                robust.append(f"{name}@{h}h")
            print(f"{name:9s} {h:3d} | {m1:+8.4f} {t1:+6.2f} | {m2:+8.4f} {t2:+6.2f} | {ls1['net_bps'] or 0:+11.1f} "
                  f"{ls2['net_bps'] or 0:+11.1f} {'ROBUST' if ok else ''}", flush=True)
    rep["robust"] = robust
    rep["elapsed_s"] = round(time.time() - t0, 1)
    (PROJECT / "docs" / "V11_FEATURE_STUDY.json").write_text(json.dumps(rep, indent=1))
    print("robust:", robust or "NONE", f"({rep['elapsed_s']} s)")


if __name__ == "__main__":
    main()
