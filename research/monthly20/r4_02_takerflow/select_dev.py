"""DEV-only: record the selected config and the pre-declared neighbour grid (prereg.json), and the DEV numbers of
the selection and its neighbours with the real engine. TEST is not touched."""
import json, sys
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_02_takerflow")
import lib as Lb
from strat import build_W
from common import DEV_START, DEV_END

SEL = dict(kind="XS", tf="1d", score="share", dir="follow", L=3, rb=3, U=40, k=10, kS=10)
NB = [{"L": v} for v in (1, 2, 4, 5)] + [{"k": v, "kS": v} for v in (5, 8, 12, 15)] + \
     [{"rb": v} for v in (1, 2, 4, 5, 7)] + [{"U": v} for v in (20, 30, 50, 60)] + [{"tf": "4h", "L": 18, "rb": 18}]
pr = {"idea": "Cross-sectional taker-buy share: every 3 days, among the 40 most liquid USDT perps (point-in-time, delisted "
      "coins included), go long the 10 coins with the highest share of taker-BUY quote volume over the last 3 daily bars "
      "and short the 10 with the lowest; equal weight, 50/50 gross, 1x.", "selected": SEL, "neighbours": NB}
json.dump(pr, open("prereg.json", "w"), indent=1)
out = []
for ch in [{}] + NB:
    c = dict(SEL); c.update(ch)
    P = Lb.panel(c["tf"])
    r = Lb.run_units(P, build_W(P, c), DEV_START, DEV_END)
    r2 = Lb.run_units(P, build_W(P, c), DEV_START, DEV_END, cost_mult=2)
    row = dict(change=ch, mean=r["mean_month"], median=r["median_month"], geo=r["geo_month"], net=r["net_pct"],
               dd=r["max_dd_pct"], net2x=r2["net_pct"])
    out.append(row); print(row, flush=True)
json.dump(out, open("dev_selection.json", "w"), indent=1)
