"""DEV-only selection: for the 8 best daily configs by DEV t-stat (positive at doubled cost), run their neighbour grid
on DEV, plus a funding-free decomposition. Selected = highest DEV t among candidates whose DEV neighbours are >= 80%
net positive. Writes dev_select.json. Never touches TEST."""
import json, sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import DEV_START, DEV_END
import engine as E
from dev import load, weights, tstat, name


def scale(v, f, lo=1):
    return max(lo, int(round(v * f)))


def neighbours(c):
    out = []
    for key in ("w", "L", "k"):
        if c.get(key) is None:
            continue
        seen = {c[key]}
        for f in (0.5, 0.75, 1.25, 1.5):
            v = scale(c[key], f, 2 if key == "k" else 1)
            if key == "L":
                v = max(v, 10)
            if v in seen:
                continue
            seen.add(v); out.append(dict(c, **{key: v}))
    for rb in (1, 2, 3, 5, 7, 10):
        if rb != c["rb"]:
            out.append(dict(c, rb=rb))
    return out


if __name__ == "__main__":
    grid = [r for r in json.loads((HERE / "dev_grid.json").read_text()) if r["tf"] == 24 and r["net2"] > 0 and r["geo"] > 0]
    grid.sort(key=lambda r: -r["t"])
    P, U = load(24)
    keys = ("tf", "kind", "fam", "w", "L", "k", "rb", "mode", "thr")
    rows = []
    for r in grid[:8]:
        c = {k: r[k] for k in keys if k in r}
        nb = []
        for n in neighbours(c):
            W = weights(P, U, n)
            x = E.run(P, W, n["rb"], DEV_START, DEV_END)
            nb.append((name(n), x["net_pct"]))
        W = weights(P, U, c)
        fz = P.fund.copy(); P.fund[:] = 0
        nf = E.run(P, W, c["rb"], DEV_START, DEV_END); P.fund[:] = fz
        lo = E.run(P, np.where(W > 0, W, 0), c["rb"], DEV_START, DEV_END)
        sh = E.run(P, np.where(W < 0, W, 0), c["rb"], DEV_START, DEV_END)
        share = sum(v > 0 for _, v in nb) / len(nb)
        row = dict(config=c, name=name(c), dev_t=r["t"], dev_mean=r["mean"], dev_net=r["net"], nb_share_pos=round(share, 3),
                   nb=nb, net_no_funding=nf["net_pct"], mean_no_funding=nf["mean_month"], long_leg_net=lo["net_pct"], short_leg_net=sh["net_pct"])
        rows.append(row)
        print(row["name"], row["dev_t"], row["dev_mean"], "nb+", row["nb_share_pos"], "nofund", nf["net_pct"], "L", lo["net_pct"], "S", sh["net_pct"], flush=True)
    ok = [r for r in rows if r["nb_share_pos"] >= 0.8]
    sel = max(ok, key=lambda r: r["dev_t"]) if ok else None
    (HERE / "dev_select.json").write_text(json.dumps({"candidates": rows, "selected": sel and sel["config"]}, indent=1))
    print("SELECTED", sel and sel["name"])
