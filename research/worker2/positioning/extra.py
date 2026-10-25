"""Information only (not part of any check): (a) position profit shares measured in % of equity at the position's open
instead of USD (removes the compounding tilt toward late positions); (b) 500 USDT skip share of the UNFIXED r4_03 rule."""
import json, sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/worker2/positioning")
import run as R
from common import DEV_START, TEST_END, TEST_START
out = {}
f = R.run(R.PRIMARY, DEV_START, TEST_END, record=True)
eqd = {int(t): e for t, e in f["_curve"]}
ts = [t for t, _ in f["_curve"]]
def eq_before(i):  # equity at the close before day i
    return f["_curve"][i - int(np.searchsorted(R.P.t, DEV_START))][1]
pos = f["_positions"]
rel = sorted([(p[4] / eq_before(p[2]) * 100, R.P.syms[p[0]], int(p[2])) for p in pos], reverse=True)
tot = sum(x[0] for x in rel)
out["primary_equity_pct_shares"] = dict(total_pct_points=round(tot, 2), max_share=round(rel[0][0] / tot, 4),
                                        top5=[(c, round(x / tot, 4)) for x, c, _ in rel[:5]])
test_share = sum(p[4] for p in pos if R.P.t[p[2]] >= TEST_START) / sum(p[4] for p in pos)
out["primary_usd_profit_share_from_test_positions"] = round(test_share, 3)
# unfixed rule size check
import importlib.util
spec = importlib.util.spec_from_file_location("r4run", r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_03_positioning/run.py")
R4 = importlib.util.module_from_spec(spec); spec.loader.exec_module(R4)
import engine_w2 as G
W, reb = R4.weights(R4.SEL)
u = G.sim(R4.P, W, DEV_START, TEST_END, rebal=reb, record_orders=True)
opens = [o for o in u["_orders"] if o[4]]
mn = {"BTCUSDT": 100.0, "ETHUSDT": 20.0}
sk = [o for o in opens if abs(o[5]) * 500 < mn.get(R4.P.syms[o[1]], 5.0)]
out["unfixed_r4_03_at_500"] = dict(opening_orders=len(opens), skipped=len(sk), skipped_share=round(len(sk) / len(opens), 4),
                                   coins=sorted({R4.P.syms[o[1]] for o in sk}))
print(json.dumps(out, indent=1))
json.dump(out, open(r"D:/Projects/BinanceBOt/paperlab/research/worker2/positioning/extra.json", "w"), indent=1)
