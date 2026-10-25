"""Informational only (does not change check 7): top positions in USD and as % of equity at entry (compounding-neutral)."""
import json
from datetime import datetime, timezone
import numpy as np
import core as C
from run_sel import SEL  # noqa
W = C.weights(SEL["k"], SEL["q"], SEL["h"], SEL["signs"])
f = C.run(W, C.DEV_START, C.TEST_END, record=True)
cv = f["_curve"]; eqd = {int(t - C.G.STEP + 1): e for t, e in cv[1:]}
i0 = int(np.searchsorted(C.P.t, C.DEV_START))
pos = f["_positions"]; tot = sum(p[4] for p in pos)
rows = []
for j, side, o, c, pnl in pos:
    e0 = cv[o - i0][1]  # equity at the start of entry day
    rows.append(dict(sym=C.P.syms[j], side=int(side), entry=datetime.fromtimestamp(C.P.t[o] / 1000, timezone.utc).strftime("%Y-%m-%d"),
                     days=int(c - o), pnl_usd=round(pnl, 0), share_of_total=round(pnl / tot, 4), pnl_pct_of_equity=round(pnl / e0 * 100, 2)))
top = sorted(rows, key=lambda r: -r["pnl_usd"])[:8]
pct = np.array([r["pnl_pct_of_equity"] for r in rows])
srt = np.sort(pct)[::-1]
out = {"top_usd": top, "sum_pct_of_equity": round(float(pct.sum()), 2),
       "largest_position_share_of_summed_pct": round(float(srt[0] / pct.sum()), 4),
       "top_by_pct": sorted(rows, key=lambda r: -r["pnl_pct_of_equity"])[:5]}
for k, v in out.items():
    print(k, v)
json.dump(out, open("check7_info.json", "w"), indent=1, default=float)
