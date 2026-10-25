"""The ML leg (research/monthly20/r2_ml A_hgb_H6_k3): features, monthly re-training and daily predictions.

Features, target, model and training rule are copied unchanged from research/monthly20/r2_ml/run.py:
- 4h bars of the month's universe (top 40 USDT perps by trailing 30-day quote volume, decided at the month start) + BTC;
- 20 cross-sectional rank features + 6 market features (standardised on the training rows);
- HistGradientBoostingRegressor on every 3rd 4h bar of ALL history whose 24h-ahead label ends a day before the month
  starts; re-trained on the 1st of every month;
- one decision a day, at 00:00 UTC (the 20:00 bar's close), executed at the 00:00 open: long the 3 best / short the 3
  worst scores.
History up to 2026-10 (archive data incl. delisted coins) ships in app/ensemble/seed/ml_train.npz
(scripts/ensemble_export.py); newer rows come from the server's own 4h bars.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

HOUR = 3_600_000
DAY = 24 * HOUR
BAR = 4 * HOUR
H = 6
EMBARGO = DAY
STRIDE = 3
DATA_FROM = 1577836800000          # 2020-01-01: bar indices (for the every-3rd-bar rule) count from here
SEED = Path(__file__).resolve().parent / "seed"


def mkey(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m")


def make_model(seed: int = 0):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=150, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=2000,
                                         l2_regularization=1.0, early_stopping=False, random_state=seed)


def features(times: np.ndarray, A: dict[str, np.ndarray], F: np.ndarray, syms: list[str], uni: dict[str, list[str]]):
    """research/monthly20/r2_ml/run.py features(), unchanged. A: o h l c qv tbq (T x N), F: funding per bar."""
    import pandas as pd
    c = pd.DataFrame(A["c"]); h = pd.DataFrame(A["h"]); l = pd.DataFrame(A["l"])
    qv = pd.DataFrame(A["qv"]); tbq = pd.DataFrame(A["tbq"]); f = pd.DataFrame(F)
    lc = np.log(c); r1 = lc.diff()
    per = {}
    for n in (1, 3, 6, 18, 42, 180):
        per[f"r{n}"] = lc - lc.shift(n)
    for n in (6, 42, 180):
        per[f"vol{n}"] = r1.rolling(n).std()
    per["vr"] = per["vol6"] / per["vol42"]
    per["qvz"] = np.log(qv.rolling(6).mean() / qv.rolling(180).mean())
    per["size"] = np.log(qv.rolling(180).mean())
    per["tb6"] = tbq.rolling(6).sum() / qv.rolling(6).sum() - 0.5
    per["tb42"] = tbq.rolling(42).sum() / qv.rolling(42).sum() - 0.5
    rng = h.rolling(42).max() - l.rolling(42).min()
    per["rpos"] = (c - l.rolling(42).min()) / rng
    per["hl6"] = np.log(h / l).rolling(6).mean()
    per["fund6"] = f.rolling(6).sum()
    per["fund18"] = f.rolling(18).sum()
    bj = syms.index("BTCUSDT")
    per["res6"] = per["r6"].sub(per["r6"][bj], axis=0)
    per["res42"] = per["r42"].sub(per["r42"][bj], axis=0)
    T, N = c.shape
    U = np.zeros((T, N), bool)
    col = {s: j for j, s in enumerate(syms)}
    nxt = np.append(times[1:], times[-1] + BAR)
    keys = np.array([mkey(int(t)) for t in nxt])
    for k in np.unique(keys):
        rows = np.where(keys == k)[0]
        for s in uni.get(k, []):
            if s in col:
                U[rows, col[s]] = True
    valid = U & np.isfinite(A["c"]) & np.isfinite(np.vstack([A["o"][1:], np.full((1, N), np.nan)]))
    for v in per.values():
        valid &= np.isfinite(v.values)
    ranks = {}
    for k, v in per.items():
        m = v.where(valid)
        ranks[k] = (m.rank(axis=1, pct=True) - 0.5).values
    reg = {"btc_r6": per["r6"][bj].values, "btc_r42": per["r42"][bj].values, "btc_r180": per["r180"][bj].values,
           "btc_vol42": per["vol42"][bj].values,
           "breadth42": per["r42"].where(valid).mean(axis=1).values,
           "disp6": per["r6"].where(valid).std(axis=1).values}
    names = list(ranks) + list(reg)
    return valid, ranks, reg, names


def targets(A: dict[str, np.ndarray], valid: np.ndarray):
    import pandas as pd
    o = A["o"]; T, N = o.shape
    fwd = np.full((T, N), np.nan)
    fwd[: T - 1 - H] = np.log(o[1 + H:T] / o[1:T - H])
    m = pd.DataFrame(np.where(valid, fwd, np.nan))
    return (m.rank(axis=1, pct=True) - 0.5).values


def design(ranks, reg, names, rows_t, rows_j) -> np.ndarray:
    X = np.empty((len(rows_t), len(names)), dtype=np.float32)
    for q, k in enumerate(names):
        X[:, q] = ranks[k][rows_t, rows_j] if k in ranks else reg[k][rows_t]
    return X


def seed_meta() -> dict[str, Any]:
    return json.loads((SEED / "meta.json").read_text())


def seed_rows() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    z = np.load(SEED / "ml_train.npz")
    return z["X"], z["y"], z["t"]


def seed_preds() -> dict[int, dict[str, float]]:
    return {int(k): v for k, v in json.loads((SEED / "ml_preds.json").read_text()).items()}


def new_rows(times, A, valid, ranks, reg, names, after: int, month_start: int):
    """Training rows from the server's own bars: bar open > `after` (the shipped rows end there), label ending a day
    before `month_start`, every 3rd bar counted from 2020-01-01 (as the research did)."""
    y = targets(A, valid)
    T = len(times)
    tend = np.full(T, np.iinfo(np.int64).max, dtype=np.int64)
    tend[: T - 1 - H] = times[1 + H:T]
    idx = ((times - DATA_FROM) // BAR).astype(np.int64)
    sel = np.where((tend <= month_start - EMBARGO) & (times > after) & (idx % STRIDE == 0))[0]
    ok = valid & np.isfinite(y)
    rt, rj = np.nonzero(ok[sel])
    rt = sel[rt]
    return design(ranks, reg, names, rt, rj), y[rt, rj].astype(np.float32), times[rt].astype(np.int64)


class Model:
    """A fitted month model: the regressor plus the training mean / sd of the market (non-rank) features."""

    def __init__(self, names: list[str], rank_names: list[str]):
        self.names = names
        self.reg_cols = [q for q, k in enumerate(names) if k not in rank_names]
        self.mdl = None
        self.mu = self.sd = None
        self.month: str | None = None
        self.rows = 0

    def fit(self, X: np.ndarray, y: np.ndarray, month: str) -> "Model":
        X = X.copy()
        self.mu, self.sd = X[:, self.reg_cols].mean(0), X[:, self.reg_cols].std(0) + 1e-9
        X[:, self.reg_cols] = (X[:, self.reg_cols] - self.mu) / self.sd
        self.mdl = make_model(0).fit(X, y)
        self.month, self.rows = month, len(y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = X.copy()
        X[:, self.reg_cols] = (X[:, self.reg_cols] - self.mu) / self.sd
        return self.mdl.predict(X)
