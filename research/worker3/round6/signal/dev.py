"""DEV-only grid for round-6 signal (PREREG.md). Writes dev.json and devtable.md."""
import json, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import lib6 as L, sig
t0 = time.time()
BASE = json.load(open(HERE.parents[1] / "round5/high52_plus/dev.json"))["selected"]["cfg"]
P = L.L4.make_panel(L.L4.build())


def ev(name, spec):
    kw = sig.build(P, spec); c = dict(BASE); c.update(kw["cfg"])
    a = dict(score_override=kw["score_override"], long_score=kw["long_score"], short_score=kw["short_score"])
    r = L.run5(P, c, L.DEV_START, L.DEV_END, record=True, **a)
    r2 = L.run5(P, c, L.DEV_START, L.DEV_END, cost_mult=2, **a)
    d = dict(id=name, spec=spec, dev=L.stats(r), dev_net_2x=r2["net_pct"], max_pos_share=L.max_pos_share(r), liq=r["liquidations"])
    print(json.dumps(d, default=float), f"[{time.time()-t0:.0f}s]", flush=True)
    return d


def elig(d):
    up = int(d["dev"]["up"].split("/")[0]); m = d["max_pos_share"]
    return d["dev_net_2x"] > 0 and d["dev"]["max_dd"] <= 30 and up >= 25 and m == m and m <= 15


def improves(d, ref):
    g, dd = d["dev"]["geo"], d["dev"]["max_dd"]
    return g >= ref["dev"]["geo"] + 0.25 or (g >= ref["dev"]["geo"] - 0.15 and dd <= ref["dev"]["max_dd"] - 4)


S1 = [("0", "base"), ("A1", "ratio"), ("A2", "avgratio"), ("A3", "blitz"), ("B1", "volsc"), ("B2a", "jump=0.4"),
      ("B2b", "jump=0.5"), ("B2c", "jump=0.6"), ("B3", "jumpup=0.5"), ("C1", "s7neg"), ("C2", "schan=60"),
      ("C3", "schan=90"), ("D1", "age=60"), ("D2", "age=90"), ("D3", "age=180")]
g = [ev(*x) for x in S1]
base = g[0]
imp = [d for d in g[1:] if elig(d) and improves(d, base)]
fam = {}
for d in sorted(imp, key=lambda d: (-d["dev"]["geo"], d["dev"]["max_dd"])):
    fam.setdefault(d["id"][0], d)
g2 = []
if len(fam) >= 2:
    import itertools
    F = list(fam.values())
    combos = [c for n in range(2, len(F) + 1) for c in itertools.combinations(F, n)]
    combos = sorted(combos, key=len)[:8]
    for cb in combos:
        # order the parts so that score overrides come before exclusions/short tweaks
        order = {"A": 0, "B": 1, "C": 2, "D": 3}
        parts = sorted(cb, key=lambda d: (order[d["id"][0]], d["id"]))
        if parts[0]["id"] == "B1" and any(p["id"][0] == "A" for p in parts):
            continue
        g2.append(ev("+".join(p["id"] for p in parts), "+".join(p["spec"] for p in parts)))
best1 = sorted(imp, key=lambda d: (-d["dev"]["geo"], d["dev"]["max_dd"]))[0] if imp else None
sel = best1
for d in g2:
    if elig(d) and best1 and improves(d, best1) and (sel is best1 or d["dev"]["geo"] > sel["dev"]["geo"]):
        sel = d
res = dict(base=base, stage1=g, improving=[d["id"] for d in imp], stage2=g2, selected=sel, n_configs=len(g) + len(g2))
(HERE / "dev.json").write_text(json.dumps(res, indent=1, default=float))
rows = ["| id | spec | median | geo | up | max DD | t | trades/mo | net 2x | top pos % | liq | elig | improves |", "|" + "---|" * 13]
for d in g + g2:
    s = d["dev"]
    rows.append(f"| {d['id']} | {d['spec']} | {s['median']:+.2f} | {s['geo']:+.2f} | {s['up']} | {s['max_dd']:.1f}% | {s['t']:.2f} | "
                f"{s['trades_per_month']:.0f} | {d['dev_net_2x']:+.0f}% | {d['max_pos_share']} | {d['liq']} | {elig(d)} | {improves(d, base)} |")
(HERE / "devtable.md").write_text("\n".join(rows) + "\n")
print("\n".join(rows)); print("SELECTED", sel and sel["id"], f"done [{time.time()-t0:.0f}s]")
