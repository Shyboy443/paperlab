"""DEV-only: trade ONE block per day (b), all else flat. Gross and net per block. Shows where (if anywhere) the
same-block continuation beats the 0.14%/day round-trip cost."""
import json, time, warnings; warnings.filterwarnings("ignore")
import lib
out = []
for B in (1, 2, 4, 6, 8):
    for K in (20, 40):
        for b in range(24 // B):
            g = lib.run(B=B, K=K, k=5, traded=[b], start=lib.DEV_START, end=lib.DEV_END, cost_mult=0)
            n = lib.run(B=B, K=K, k=5, traded=[b], start=lib.DEV_START, end=lib.DEV_END, cost_mult=1)
            row = dict(B=B, K=K, b=b, utc=f"{b*B:02d}-{(b+1)*B:02d}", gross_geo=float(g["geo_month"]), net_geo=float(n["geo_month"]),
                       net_median=float(n["median_month"]), net_up=int(n["months_up"]))
            out.append(row); print(row, flush=True)
json.dump(out, open("dev_single.json", "w"), indent=1)
