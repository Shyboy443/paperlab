"""DEV-only diagnostic: forward residual return (after the next-open fill) by size of the last-hour residual shock."""
import os, json, numpy as np, pandas as pd, engine
have = {p.stem for p in (engine.CACHE / "perp" / "1h").glob("*.json")}
engine.COINS[:] = [c for c in engine.COINS if c in have and (engine.CACHE / "funding" / f"{c}.json").exists()]
import strategies as S
D = engine.load(); r, E, B = S.residuals(D)
O = D["o"]
m = (r.index >= engine.DEV_START) & (r.index < engine.DEV_END)
out = {}
for look in (1, 4):
    s = E.rolling(look).sum()
    z = s / E.rolling(720, min_periods=500).std() / np.sqrt(look)
    for h in (1, 4, 12, 24):
        rows = []
        for a in E.columns:
            fwd = O[a].shift(-1 - h) / O[a].shift(-1) - 1
            fb = O["BTCUSDT"].shift(-1 - h) / O["BTCUSDT"].shift(-1) - 1
            fe = O["ETHUSDT"].shift(-1 - h) / O["ETHUSDT"].shift(-1) - 1
            res = fwd - B[a]["BTCUSDT"] * fb - B[a]["ETHUSDT"] * fe
            df = pd.DataFrame({"z": z[a], "f": res})[m].dropna()
            rows.append(df)
        df = pd.concat(rows)
        for lo, hi in ((-99, -4), (-4, -3), (-3, -2), (2, 3), (3, 4), (4, 99)):
            sel = df[(df.z > lo) & (df.z <= hi)]
            out[f"look{look}_h{h}_z({lo},{hi}]"] = (len(sel), round(sel.f.mean() * 100, 3))
for k, v in out.items(): print(k, v)
