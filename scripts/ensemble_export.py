"""Export the research inputs the live Ensemble needs (run once, locally; research caches are not on the server).

Writes app/ensemble/seed/:
  ml_train.npz   the ML leg's walk-forward training rows (features exactly as research/monthly20/r2_ml/run.py builds
                 them, archive data incl. delisted coins) whose label ends <= CUTOFF - 1 day, every 3rd 4h bar:
                 X (raw, not standardised), y (rank target), t (4h bar open ms). The server appends newer rows from
                 its own 4h bars and re-trains on the 1st of every month, as the research did.
  ml_preds.json  the research walk-forward predictions (A_hgb_H6_k3, seed 0) for decisions from PRED_FROM on, used
                 for the paper replay before the server's own model takes over: {"<decision day ms>": {sym: score}}.
  meta.json      names, cutoff, versions.

    python scripts/ensemble_export.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "monthly20" / "r2_ml"))
sys.path.insert(0, str(ROOT / "research" / "monthly20"))
import run as ML  # noqa: E402  (research/monthly20/r2_ml/run.py)

CUTOFF = ML.utc(2026, 10, 1)          # the month whose model the export reproduces (training rows end a day before)
PRED_FROM = ML.utc(2026, 3, 1)
H = 6
OUT = ROOT / "app" / "ensemble" / "seed"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    uni = json.loads((ML.DD / "universes.json").read_text())
    syms, times, A, F, valid, ranks, reg, names = ML.load(uni)
    y, _ = ML.targets(A, valid, H)
    T = len(times)
    tend = np.full(T, np.iinfo(np.int64).max, dtype=np.int64)
    tend[: T - 1 - H] = times[1 + H:T]
    lab_ok = valid & np.isfinite(y)
    tr_t = np.where(tend <= CUTOFF - ML.EMBARGO)[0]
    tr_t = tr_t[tr_t % ML.TRAIN_STRIDE == 0]
    rt, rj = np.nonzero(lab_ok[tr_t])
    rt = tr_t[rt]
    X = ML.design(ranks, reg, names, rt, rj)
    np.savez_compressed(OUT / "ml_train.npz", X=X.astype(np.float32), y=y[rt, rj].astype(np.float32),
                        t=times[rt].astype(np.int64), sym=np.array([syms[j] for j in rj]))
    z = np.load(ML.DD / f"pred_full_H{H}_all_s0.npz")
    preds = {}
    for t, s in zip(z["t"], z["s"]):
        day = int(times[int(t) + 1])                      # the decision is executed at the next bar's open (00:00 UTC)
        if day < PRED_FROM:
            continue
        preds[str(day)] = {syms[j]: round(float(v), 7) for j, v in enumerate(s) if np.isfinite(v)}
    (OUT / "ml_preds.json").write_text(json.dumps(preds, separators=(",", ":")))
    import sklearn
    meta = {"names": names, "rank_features": [k for k in names if k in ranks], "cutoff": CUTOFF, "H": H,
            "stride": ML.TRAIN_STRIDE, "data_from": int(ML.DATA_FROM), "rows": int(len(rt)),
            "sklearn": sklearn.__version__, "pred_from": PRED_FROM, "pred_days": len(preds),
            "universes": {k: v for k, v in uni.items() if k >= "2026-01"}}
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1))
    print("rows", len(rt), "features", len(names), "pred days", len(preds), "last pred",
          max(preds) if preds else None)


if __name__ == "__main__":
    main()
