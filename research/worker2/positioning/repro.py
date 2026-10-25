"""Step 1: reproduce r4_03's selected result from its own code (does not write into r4_03)."""
import sys, json, time
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_03_positioning")
t0 = time.time()
import run as R  # r4_03 run.py; its __main__ block does not execute on import
from common import DEV_START, DEV_END, TEST_START, TEST_END
W = R.weights(R.SEL)
out = {}
for name, (a, b) in dict(dev=(DEV_START, DEV_END), test=(TEST_START, TEST_END), full=(DEV_START, TEST_END)).items():
    out[name] = {k: v for k, v in R.brief(R.run(R.SEL, a, b, W)).items() if k != "months"}
ref = json.load(open(r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_03_positioning/results.json"))["attempts"][0]
cmp = {}
for name, key in (("dev", "dev"), ("test", "test"), ("full", "full_2021_2026")):
    cmp[name] = {k: (out[name][k], ref[key][k]) for k in ("mean_month", "median_month", "geo_month", "max_dd_pct", "net_pct", "trades")}
    print(name, cmp[name])
json.dump(dict(reproduced=out, compare_mine_vs_results_json=cmp), open("repro.json", "w"), indent=1, default=float)
print("secs", round(time.time() - t0))
