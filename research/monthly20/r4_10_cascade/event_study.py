"""DEV-only event study: raw forward returns (no costs) after FLUSH and BUILD events, to see if the hypothesis exists.
Writes event_study_dev.json. Never reads TEST bars (events with entry >= 2024-11-01 are dropped)."""
import json, sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_10_cascade")
import engine as E

coins = E.load_coins()
print("coins loaded", len(coins), flush=True)
out = []
for W in (3, 6, 12):
    feats = {s: E.features(c, W) for s, c in coins.items()}
    for mode in ("revert", "cont"):
        for Z in (2.5, 3.5, 5.0):
            for V in (3.0, 5.0):
                for D in (0.01, 0.02, 0.04):
                    fw = {h: [] for h in (6, 12, 36, 72, 144)}
                    n = 0
                    for s, c in coins.items():
                        last = -10 ** 9
                        for k, j, d in E.find_events(c, feats[s], dict(Z=Z, V=V, D=D, mode=mode)):
                            e = j + 2
                            if e - last < 12 or e + 144 >= c.n or not (E.DEV_START <= c.t[e] < E.DEV_END):
                                continue
                            last = e
                            n += 1
                            for h in fw:
                                fw[h].append(d * (c.o[e + h] / c.o[e] - 1))
                    row = {"W": W, "mode": mode, "Z": Z, "V": V, "D": D, "n": n}
                    for h, v in fw.items():
                        v = np.array(v)
                        row[f"m{h}"] = round(float(v.mean()) * 100, 3) if len(v) else None
                        row[f"t{h}"] = round(float(v.mean() / (v.std() + 1e-12) * np.sqrt(len(v))), 2) if len(v) > 2 else None
                    out.append(row)
                    print(row, flush=True)
json.dump(out, open(r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_10_cascade/event_study_dev.json", "w"), indent=0)
