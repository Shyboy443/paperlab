"""DEV-only exploration grid (2021-01..2024-10). Writes dev_scan.json. TEST is never touched here."""
import itertools, json, sys
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/06_intraday_breakout")
from engine import *  # noqa
from common import utc

H1_END = utc(2023, 1, 1)  # DEV split: 2021-22 vs 2023-24/10


def grid():
    for slot, R, W, H, k, tr in itertools.product([0, 28, 54], [2, 4], [8, 16], [32, 96, 192], [1.5, 3.0, 5.0], [0, 1]):
        yield {"kind": "orb", "slot": slot, "R": R, "W": W, "H": H, "k": k, "trend": tr}
    for n_, m, H, k, tr in itertools.product([20, 80], [4, 8, 16], [16, 64, 192], [1.5, 3.0, 5.0], [0, 1]):
        yield {"kind": "squeeze", "n": n_, "m": m, "H": H, "k": k, "trend": tr}
    for H, k, tr, W in itertools.product([32, 96, 192], [1.5, 3.0, 5.0], [0, 1], [32, 96]):
        yield {"kind": "pdhl", "H": H, "k": k, "trend": tr, "W": W}


if __name__ == "__main__":
    rows = []
    for p in grid():
        tr = all_trades(p, DEV_START, DEV_END)
        a = [x for x in tr if x["et"] < H1_END]
        b = [x for x in tr if x["et"] >= H1_END]
        row = {"p": p, "all": unlev_stats(tr), "h1": unlev_stats(a), "h2": unlev_stats(b),
               "maker_dream_bp": unlev_stats(tr, cost_mult=0.0)["mean_net_bp"]}
        rows.append(row)
        print(json.dumps(row), flush=True)
    json.dump(rows, open(r"D:/Projects/BinanceBOt/paperlab/research/monthly20/06_intraday_breakout/dev_scan.json", "w"), indent=0)
