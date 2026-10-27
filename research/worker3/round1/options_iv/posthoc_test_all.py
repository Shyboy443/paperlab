"""POST-HOC diagnostic only (after the single pre-registered TEST run): every DEV-grid config on TEST. No selection."""
import json, numpy as np, core
D = core.load(); g = json.load(open("dev_grid.json")); out = []
for r in g:
    b = core.brief(core.simulate(D, r["p"], core.TEST_START, core.TEST_END))
    out.append(dict(p=r["p"], dev_t=r["t_stat"], dev_geo=r["geo_month"], test_geo=float(b["geo_month"]), test_median=float(b["median_month"]),
                    test_up=int(b["months_up"]), test_dd=float(b["max_dd_pct"])))
json.dump(out, open("posthoc_test_all.json", "w"), indent=1)
dt = np.array([x["dev_t"] for x in out]); tg = np.array([x["test_geo"] for x in out])
print("rank corr dev_t vs test_geo", round(float(np.corrcoef(np.argsort(np.argsort(dt)), np.argsort(np.argsort(tg)))[0, 1]), 3))
for fam in ("vrp", "ivchg", "ivlvl", "trend_iv"):
    f = [x for x in out if x["p"]["fam"] == fam]
    print(fam, len(f), "test geo>0:", sum(x["test_geo"] > 0 for x in f), "best test geo", max(x["test_geo"] for x in f), "median", round(float(np.median([x["test_geo"] for x in f])), 2))
print("any config with test geo>5.33 and median>3.84:", sum(x["test_geo"] > 5.33 and x["test_median"] > 3.84 for x in out))
top = sorted(out, key=lambda x: -x["dev_t"])[:10]
for x in top: print(x)
