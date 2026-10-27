"""DEV-only: the hysteresis (buffer) variants tried after the plain grid (cost-reduction attempt)."""
import json, warnings; warnings.filterwarnings("ignore")
import lib
out = []
for B, K, k in [(12, 5, 3), (12, 40, 3), (8, 20, 10), (6, 40, 5), (4, 40, 3)]:
    for buf in (1.0, 2.0, 3.0):
        g = lib.run(B=B, K=K, k=k, buf=buf, start=lib.DEV_START, end=lib.DEV_END, cost_mult=0)
        n = lib.run(B=B, K=K, k=k, buf=buf, start=lib.DEV_START, end=lib.DEV_END)
        out.append(dict(B=B, K=K, k=k, buf=buf, gross_geo=float(g["geo_month"]), net_geo=float(n["geo_month"]),
                        net_median=float(n["median_month"]), net_up=int(n["months_up"]), trades_mo=round(n["trades"] / n["n_months"])))
        print(out[-1], flush=True)
json.dump(out, open("dev_buf.json", "w"), indent=1)
