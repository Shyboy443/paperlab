"""DEV-only grid (2021-01..2024-10) over the lead-lag family, both timeframes. Writes dev_grid.json.
Selection rule (fixed before looking): among configs with geo month > 0 at DOUBLED costs and positive in >= 3 of the
4 DEV calendar years, take the highest DEV geometric mean month. Also report the best pure catch-up ('lag') config."""
import sys, json, itertools, time
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import DEV_START, DEV_END, summary
from engine import run, fill_months


def stats(R):
    S = summary(fill_months(R["curve"], DEV_START, DEV_END), [x[2] for x in R["trades"]])
    ys = {}
    for k, v in S["months"].items():
        ys[k[:4]] = ys.get(k[:4], 1) * (1 + v / 100)
    return {k: S[k] for k in ("mean_month", "median_month", "geo_month", "max_dd_pct", "net_pct", "trades", "top3_share_of_profit")} | \
        {"years": {y: round((v - 1) * 100, 1) for y, v in ys.items()}}


out = []
t0 = time.time()
for tf in ("1h", "15m"):
    Ls = (1, 2, 4) if tf == "1h" else (1, 2, 4, 8)
    Hs = (1, 2, 4) if tf == "1h" else (1, 2, 4, 8)
    rmaxs = (20, 50) if tf == "1h" else (20,)
    for leader, side, dirs, L, z, H, rmax in itertools.product(("BTC", "TOP5"), ("lag", "lead"), ("both", "down", "up"), Ls, (2.0, 3.0), Hs, rmaxs):
        kw = dict(tf=tf, leader=leader, side=side, dirs=dirs, L=L, z=z, H=H, K=5, rmin=1, rmax=rmax)
        a = stats(run(start=DEV_START, end=DEV_END, **kw))
        b = stats(run(start=DEV_START, end=DEV_END, cost_mult=2, **kw))
        out.append(dict(cfg=kw, dev=a, dev_2x=b))
    print(tf, "done", round(time.time() - t0), "s", flush=True)
json.dump(out, open("dev_grid.json", "w"), indent=0, default=float)

ok = [x for x in out if x["dev_2x"]["geo_month"] > 0 and sum(v > 0 for v in x["dev"]["years"].values()) >= 3]
ok.sort(key=lambda x: -x["dev"]["geo_month"])
print("eligible", len(ok), "of", len(out))
for x in ok[:15]:
    print(x["cfg"], x["dev"])
lag = sorted([x for x in out if x["cfg"]["side"] == "lag"], key=lambda x: -x["dev"]["geo_month"])
print("best catch-up configs:")
for x in lag[:8]:
    print(x["cfg"], x["dev"], "2x geo", x["dev_2x"]["geo_month"])
pos = sum(x["dev"]["net_pct"] > 0 for x in out if x["cfg"]["side"] == "lag")
print("catch-up configs net positive on DEV:", pos, "/", len(lag))
