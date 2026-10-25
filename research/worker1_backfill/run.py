"""Worker 1 (r4_07 ensemble, FROZEN: legs R+M+T, inverse-vol 60d / every 7d, 1x) out-of-sample backfill on Binance SPOT.

Reuses r4_07_ensemble/ens.py (legs, engine, weights, stats) and r2_ml/run.py (features, model, walk-forward) unchanged;
only the data source and dates change (module globals re-pointed below). Shorts = synthetic perp shorts, funding = 0.

    python run.py build     # spot daily panel + 4h ML bars + ML universes (cache data/w1_backfill)
    python run.py wf <END>  # ML walk-forward predictions (strictly causal, training rows end 1 day before each month)
    python run.py report    # sanity (2021-01..06), backfill 2018-01..2020-12, joins -> REPORT numbers in results.json
Research only: no orders, no keys.
"""
from __future__ import annotations
import json, math, sys, time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

M20 = Path(r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
sys.path.insert(0, str(M20)); sys.path.insert(0, str(M20 / "r4_07_ensemble")); sys.path.insert(0, str(M20 / "r2_ml"))
import ens                      # noqa: E402  (frozen Worker 1 code)
import importlib.util
_spec = importlib.util.spec_from_file_location("r2ml", M20 / "r2_ml" / "run.py")
r2ml = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(r2ml)
from common import utc, HOUR, DAY  # noqa: E402

CACHE = Path(r"D:/Projects/BinanceBOt/paperlab/data/w1_backfill")
RAW = CACHE / "raw"
HERE = Path(__file__).resolve().parent
D0 = utc(2017, 8, 1)               # spot archive starts 2017-08
D1 = utc(2021, 7, 1)               # exclusive (sanity window ends 2021-06)
BAR = 4 * HOUR
EXCL = {"BULLUSDT", "BEARUSDT"}    # BTC 3x leveraged tokens (2020)

# ---- re-point r2_ml to the spot cache / dates (functions themselves unchanged)
MLDIR = CACHE / "ml"
r2ml.DD = MLDIR
r2ml.DATA_FROM = D0
r2ml.TIMES = np.arange(D0, D1, BAR, dtype=np.int64)
r2ml.UNI_FROM = utc(2017, 9, 1)    # = DATA_FROM + 1 month, as in r2_ml (2020-01 -> 2020-02)
# ---- re-point ens to the spot panel / spot predictions
ens.PANEL = CACHE / "panel.npz"
ens.MLD = MLDIR
ens.DATA_FROM_ML = D0


def symbols():
    s = sorted(p.stem for p in (RAW / "1d").glob("*.npy"))
    return [x for x in s if x not in EXCL and len(np.load(RAW / "1d" / f"{x}.npy")) and np.load(RAW / "1d" / f"{x}.npy")[0, 0] < D1]


def build():
    syms = symbols()
    nd = (D1 - D0) // DAY
    O = np.full((nd, len(syms)), np.nan); H = O.copy(); L = O.copy(); C = O.copy(); Q = np.zeros((nd, len(syms)))
    for j, s in enumerate(syms):
        a = np.load(RAW / "1d" / f"{s}.npy")
        a = a[(a[:, 0] >= D0) & (a[:, 0] < D1) & (a[:, 5] > 0)]   # zero-volume day = missing
        i = ((a[:, 0] - D0) // DAY).astype(int)
        O[i, j], H[i, j], L[i, j], C[i, j], Q[i, j] = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]
    np.savez_compressed(CACHE / "panel.npz", syms=np.array(syms), day=D0 + DAY * np.arange(nd), O=O, H=H, L=L, C=C,
                        QV=Q, FUND=np.zeros_like(Q))
    print("panel", len(syms), "symbols x", nd, "days", flush=True)
    # ML 4h bars (same layout as r2_ml bars4h: rows o h l c qv tbq; funding f = 0 on spot)
    (MLDIR / "bars4h").mkdir(parents=True, exist_ok=True)
    T = len(r2ml.TIMES)
    qv = {}
    for s in syms:
        p = RAW / "4h" / f"{s}.npy"
        a = np.load(p) if p.exists() else np.zeros((0, 7))
        a = a[(a[:, 5] > 0) & (a[:, 0] >= D0) & (a[:, 0] < D1)] if len(a) else a
        b = np.full((6, T), np.nan)
        if len(a):
            k = ((a[:, 0] - D0) // BAR).astype(int)
            for q, c in enumerate((1, 2, 3, 4, 5, 6)):
                b[q, k] = a[:, c]
        np.savez_compressed(MLDIR / "bars4h" / f"{s}.npz", b=b, f=np.zeros(T))
        qv[s] = b[4]
    uni = {}
    for at in r2ml.month_list(r2ml.UNI_FROM, D1):          # identical ranking rule to r2_ml.stage_build
        i1 = (at - D0) // BAR
        i0 = i1 - 180
        ranked = []
        for s, v in qv.items():
            w = v[max(i0, 0):i1]
            if np.isfinite(w).sum() >= 0.8 * 180:
                ranked.append((np.nansum(w), s))
        uni[r2ml.mkey(at)] = [s for _, s in sorted(ranked, reverse=True)[:r2ml.N_UNI]]
    (MLDIR / "universes.json").write_text(json.dumps(uni))
    print("ML universe sizes", {k: len(v) for k, v in uni.items()}, flush=True)


def wf():
    uni = json.loads((MLDIR / "universes.json").read_text())
    syms, times, A, F, valid, ranks, reg, names = r2ml.load(uni)
    months = r2ml.month_list(utc(2018, 1, 1), D1)
    P = r2ml.walk_forward(dict(H=6, win="all", seed=0), times, valid, ranks, reg, names, A, months, "spot")
    import shutil
    src = MLDIR / "pred_spot_H6_all_s0.npz"
    shutil.copy(src, MLDIR / "pred_full_H6_all_s0.npz")   # the file name ens.leg_ml reads


if __name__ == "__main__":
    {"build": build, "wf": wf}[sys.argv[1]]()
