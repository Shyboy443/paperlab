"""DEV-only: candidate config, its neighbour grid, conservative funding convention, leg split. No TEST."""
import json
from core import *
SELP = dict(pre=True, h1=3, h2=2, thr_neg=0.001, thr_pos=0.001, K=5, NEG="both", POS="post")
def nbrs(b):
    g = []
    for v in (0.0005, 0.00075, 0.00125, 0.0015):
        g.append((f"thr={v}", dict(b, thr_neg=v, thr_pos=v)))
    for v in (1, 2, 4, 5):
        g.append((f"h1={v}", dict(b, h1=v)))
    for v in (1, 3, 4):
        g.append((f"h2={v}", dict(b, h2=v)))
    for v in (3, 4, 6, 8):
        g.append((f"K={v}", dict(b, K=v)))
    return g
if __name__ == "__main__":
    def one(p, **kw):
        c, tr, _ = sim(p, d0, dT, **kw); s = stats(c, tr)
        return {k: s[k] for k in ("median_month", "geo_month", "mean_month", "max_dd_pct", "t_stat", "trades_per_month", "net_pct")}, yearly(s["months"])
    print("SEL", one(SELP))
    print("SEL worst-funding", one(SELP, conv="worst"))
    print("SEL 2x cost", one(SELP, cost_mult=2))
    print("SEL no vol cap", one(SELP, liq_cap=False))
    for nm, q in (("NEG only", dict(SELP, POS=None)), ("POS post only", dict(SELP, NEG=None)), ("NEG pre only", dict(SELP, NEG="pre", POS=None)), ("NEG post only", dict(SELP, NEG="post", POS=None))):
        print(nm, one(q))
    n = 0
    for nm, q in nbrs(SELP):
        r, y = one(q); n += r["net_pct"] > 0
        print("nb", nm, r, y)
    print("DEV positive neighbours", n, "/", len(nbrs(SELP)))
