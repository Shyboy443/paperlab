"""DEV-only grid of risk overlays on the Worker 3 rule. Writes dev.json. Never touches TEST/HOLDOUT."""
import sys, time, json, itertools
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib6 as L
from lib6 import DEV_START, DEV_END
HERE = Path(__file__).resolve().parent
t0 = time.time()
BASE = {'book': 'LS', 'exit': 'D', 'chan': '30', 'univ': 20, 'k': 5}
D = L.L4.build(); P = L.L4.make_panel(D)
FAM = {
    "A_voltarget": [dict(vt=20), dict(vt=60), dict(vt=126)],
    "B_invvol": [dict(ivw=1)],
    "C_short": [dict(sstop=0.15), dict(sstop=0.25), dict(sstop=0.40), dict(sstop=0.60), dict(sage=90), dict(sage=180),
                dict(svol=2.0), dict(sw=0.35)],
    "D_ddbrake": [dict(ddb=0.15, ddr=0.075), dict(ddb=0.20, ddr=0.10), dict(ddb=0.25, ddr=0.125)],
}
rows = []
def ev(fam, ov):
    c = dict(BASE); c.update(ov)
    r = L.run5(P, c, DEV_START, DEV_END); r2 = L.run5(P, c, DEV_START, DEV_END, cost_mult=2)
    st = L.stats(r)
    row = dict(family=fam, overlay=ov, geo=float(st["geo"]), median=float(st["median"]), up=st["up"], max_dd=float(st["max_dd"]),
               net=float(st["net"]), net_2x=float(r2["net_pct"]), worst=float(st["worst"]), liq=r["liquidations"], stops=r["stops"],
               avg_scale=r["avg_scale"], turnover=float(st["turnover_x_per_month"]))
    rows.append(row); print(json.dumps(row), f"[{time.time()-t0:.0f}s]", flush=True)
    return row
base = ev("BASE", {})
win = {}
for fam, ovs in FAM.items():
    rs = [ev(fam, o) for o in ovs]
    ok = [r for r in rs if r["max_dd"] <= base["max_dd"]]
    win[fam] = max(ok or rs, key=lambda r: r["geo"])["overlay"]
print("family winners", win, flush=True)
fams = list(FAM)
for n in (2, 3):
    for combo in itertools.combinations(fams, n):
        if n == 3 and "C_short" not in combo:
            continue
        ov = {}
        for f in combo:
            ov.update(win[f])
        ev("+".join(combo), ov)
bg, bd = base["geo"], base["max_dd"]
cand = [r for r in rows[1:] if r["net_2x"] > 0]
s1 = [r for r in cand if r["geo"] > bg and r["max_dd"] <= bd + 1]
s2 = [r for r in cand if r["geo"] >= 0.97 * bg and r["max_dd"] <= bd - 3]
s3 = [r for r in cand if r["geo"] >= 0.75 * bg]
if s1:
    sel, rule = max(s1, key=lambda r: r["geo"]), "1: max geo with geo > base and DD <= base+1"
elif s2:
    sel, rule = min(s2, key=lambda r: r["max_dd"]), "2: min DD with geo >= 0.97 base and DD <= base-3"
else:
    sel, rule = max(s3, key=lambda r: r["geo"] / r["max_dd"]), "3: max geo/DD with geo >= 0.75 base"
out = dict(base=base, rows=rows, family_winners=win, n_configs=len(rows) - 1, selected=sel, rule=rule,
           selected_cfg={**BASE, **sel["overlay"]})
(HERE / "dev.json").write_text(json.dumps(out, indent=1))
print("SELECTED", rule, json.dumps(sel))
