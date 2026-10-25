"""Diagnostic for the 2021-H1 sanity gap: spot ML leg trained only on rows from 2020-01 (= the perp run's history)."""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as RB
from run import ens, r2ml, utc, MLDIR
import report as REP

uni = json.loads((MLDIR / "universes.json").read_text())
syms, times, A, F, valid, ranks, reg, names = r2ml.load(uni)
v2 = valid.copy(); v2[times < utc(2020, 1, 1)] = False
P = r2ml.walk_forward(dict(H=6, win="all", seed=0), times, v2, ranks, reg, names, A,
                      r2ml.month_list(utc(2021, 1, 1), RB.D1), "spot_from2020")
import shutil
shutil.copy(MLDIR / "pred_full_H6_all_s0.npz", MLDIR / "_keep.npz")
shutil.copy(MLDIR / "pred_spot_from2020_H6_all_s0.npz", MLDIR / "pred_full_H6_all_s0.npz")
try:
    D = ens.Data()
    F2 = ens.leg_ml(D, 3, 0)
    leg = ("M", F2, ens.trade_mask(D, F2, "M"))
    m = ens.stats_of(REP.standalone(D, leg, utc(2021, 1, 1), utc(2021, 7, 1))[1]["curve"])["months"]
    print("spot M trained from 2020-01 only:", m)
    # perp-symbol overlap: spot M restricted to coins that also had a USDT perp in the perp ML universe
    puni = json.loads((ens.PROJECT / "data" / "monthly20_r2_ml" / "universes.json").read_text())
    print("universe overlap 2021-01..06:", {k: len(set(uni[k]) & set(puni[k])) for k in sorted(uni) if k >= "2021-01"})
finally:
    shutil.copy(MLDIR / "_keep.npz", MLDIR / "pred_full_H6_all_s0.npz")
    (MLDIR / "_keep.npz").unlink()
