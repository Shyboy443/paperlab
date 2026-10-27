import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib6 as L
from lib6 import DEV_START, DEV_END
HERE = Path(__file__).resolve().parent
B = json.loads(Path(r"D:/Projects/BinanceBOt/paperlab/research/worker3/round5/high52_plus/dev.json").read_text())["selected"]["cfg"]
D = L.L4.build(); P = L.L4.make_panel(D)
G = [("base", {}, 1e4), ("band .10", dict(band=.10), 1e4), ("band .25", dict(band=.25), 1e4), ("band .50", dict(band=.5), 1e4),
     ("buf 6", dict(buf=6), 1e4), ("buf 7", dict(buf=7), 1e4), ("buf 8", dict(buf=8), 1e4), ("every 2", dict(every=2), 1e4),
     ("band .25+buf 7", dict(band=.25, buf=7), 1e4), ("band .10+buf 7", dict(band=.10, buf=7), 1e4),
     ("minn 5 @500", dict(minn=5), 500), ("minn 10 @500", dict(minn=10), 500), ("minn 10 @2000", dict(minn=10), 2000),
     ("binance @500", dict(binance=True), 500), ("binance @2000", dict(binance=True), 2000), ("binance @10k", dict(binance=True), 1e4),
     ("binance+band .25 @500", dict(binance=True, band=.25), 500), ("binance+buf 7 @500", dict(binance=True, buf=7), 500),
     ("binance+band .25+buf 7 @500", dict(binance=True, band=.25, buf=7), 500), ("binance+band .25 @2000", dict(binance=True, band=.25), 2000),
     ("binance+band .25+buf 7 @2000", dict(binance=True, band=.25, buf=7), 2000)]
rows = []
for n, (name, o, eq0) in enumerate(G, 1):
    c = dict(B); c.update(o)
    r = L.run5(P, c, DEV_START, DEV_END, record=True, equity0=eq0); r2 = L.run5(P, c, DEV_START, DEV_END, cost_mult=2, equity0=eq0)
    st = L.stats(r)
    row = dict(n=n, name=name, opts=o, equity0=eq0, selectable=n <= 10, dev=st, net2x=r2["net_pct"], max_pos=L.max_pos_share(r),
               orders_pm=r["orders_per_month"], xstat=r["xstat"], liq=r["liquidations"], cost_pct_of_eq0=round(r["cost_usd"] / eq0 * 100, 1))
    rows.append(row); print(json.dumps(row, default=float), flush=True)
sel = [r for r in rows if r["selectable"] and r["dev"]["max_dd"] <= 28.3 and (r["max_pos"] == r["max_pos"] and r["max_pos"] <= 15)]
base = rows[0]["dev"]
best = max(r["dev"]["geo"] for r in sel)
pool = sorted([r for r in sel if r["dev"]["geo"] >= best - 0.10], key=lambda r: r["dev"]["turnover_x_per_month"])
pick = pool[0]
if best <= base["geo"] + 0.10:
    alt = [r for r in sel if r["dev"]["geo"] >= base["geo"] - 0.10 and r["dev"]["max_dd"] <= base["max_dd"] - 3]
    if alt:
        pick = min(alt, key=lambda r: r["dev"]["max_dd"])
print("SELECTED", pick["name"], pick["opts"])
(HERE / "dev.json").write_text(json.dumps(dict(rows=rows, selected=dict(name=pick["name"], cfg={**B, **pick["opts"]})), indent=1, default=float))
with open(HERE / "devtable.md", "w") as f:
    f.write("| # | config | start USDT | median | geo | up | max DD | t | turnover x/mo | orders/mo | skipped entries | net 1x | net 2x | top pos % | liq |\n|" + "---|" * 15 + "\n")
    for r in rows:
        d = r["dev"]
        f.write(f"| {r['n']} | {r['name']} | {int(r['equity0'])} | {d['median']:+.2f} | {d['geo']:+.2f} | {d['up']} | {d['max_dd']:.1f}% | {d['t']} | {d['turnover_x_per_month']} | {r['orders_pm']} | {r['xstat']['skipped_entries']} | {d['net']:+.0f}% | {r['net2x']:+.0f}% | {r['max_pos']} | {r['liq']} |\n")
