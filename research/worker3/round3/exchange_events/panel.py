"""Compact hourly open / quote-volume / funding panel of ALL 901 USDT perps in agent 07's cache (incl. delisted), 2021-01..TEST_END.
Used for the random-coin check (6) and for the strategy engine. Cache: data/w3r3_exchange_events/panel.npz"""
import json, sys
from pathlib import Path
import numpy as np
from evlib import V07, HOUR, TEST_END
from common import utc
OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r3_exchange_events/panel.npz")
T0 = utc(2020, 12, 1)


def make():
    if OUT.exists():
        z = np.load(OUT); return {k: z[k] for k in z.files}
    syms = sorted(p.stem for p in (V07 / "1h").glob("*.json"))
    th = np.arange(T0, TEST_END, HOUR, dtype=np.int64); T, N = len(th), len(syms)
    O = np.full((T, N), np.nan, np.float32); C = O.copy(); QV = O.copy(); F = np.zeros((T, N), np.float32)
    for j, s in enumerate(syms):
        a = np.array([r[:7] for r in json.loads((V07 / "1h" / f"{s}.json").read_text())], dtype=float).reshape(-1, 7)
        a = a[(a[:, 0] >= T0) & (a[:, 0] < TEST_END)]
        if len(a):
            i = ((a[:, 0].astype(np.int64) - T0) // HOUR).astype(int)
            O[i, j], C[i, j], QV[i, j] = a[:, 1], a[:, 4], a[:, 6]
        fp = V07 / "funding" / f"{s}.json"
        f = json.loads(fp.read_text()) if fp.exists() else []
        if f:
            fa = np.array(f, dtype=float); fi = ((fa[:, 0].astype(np.int64) - T0) // HOUR).astype(int)
            ok = (fi >= 0) & (fi < T); np.add.at(F[:, j], fi[ok], fa[ok, 1])
        if j % 100 == 0: print("panel", j, N, flush=True)
    out = dict(th=th, syms=np.array(syms), O=O, C=C, QV=QV, F=F)
    np.savez(OUT, **out); return out


if __name__ == "__main__":
    P = make(); print(P["O"].shape)
