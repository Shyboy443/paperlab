"""DEV-only scan (2021-01..2024-10). Never touches TEST.

Grid (192 configs), fixed: W=180 bars (30 d) squeeze look-back, A=6 bars (24 h) arming window, D=20-bar breakout,
E=300-bar (50 d) EMA for the HTF direction, rp=1% risk per trade, pc=25% max notional per position, M=8 positions, 1x.
  sq   in {bbw, atrp, range6}     q in {0.05, 0.15}     dir in {htf, bar}     k in {2.5, 4}
  H    in {42 bars (7 d), 180 bars (30 d)}               side in {both, long}  univ in {30, 60}

Selection rule (declared before the scan was run):
  eligible = DEV trades >= 200, DEV net > 0 in BOTH halves (2021-01..2022-12 and 2023-01..2024-10) at baseline costs,
             DEV net > 0 at doubled costs.
  selected = eligible config with the highest DEV geometric mean month; if none is eligible, the config with the
             highest DEV geometric mean month overall is still carried to TEST (and labelled as failing the rule).
"""
import itertools, json, sys, time
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r4_08_volregime")
import engine as E, data4h
from common import DEV_START, DEV_END, utc
from pathlib import Path

HERE = Path(__file__).resolve().parent
MID = utc(2023, 1, 1)
BASE = dict(W=180, A=6, D=20, E=300, rp=0.01, pc=0.25, M=8)
GRID = dict(sq=["bbw", "atrp", "range6"], q=[0.05, 0.15], dir=["htf", "bar"], k=[2.5, 4.0], H=[42, 180],
            side=["both", "long"], univ=[30, 60])


def configs():
    keys = list(GRID)
    for vals in itertools.product(*[GRID[k] for k in keys]):
        yield dict(BASE, **dict(zip(keys, vals)))


def name(p):
    return f"{p['sq']}_q{p['q']}_{p['dir']}_k{p['k']}_H{p['H']}_{p['side']}_u{p['univ']}"


if __name__ == "__main__":
    t0 = time.time()
    P = E.Panel(data4h.build())
    rows = []
    for p in configs():
        d = E.run(P, p, DEV_START, DEV_END)
        a = E.run(P, p, DEV_START, MID); b = E.run(P, p, MID, DEV_END)
        d2 = E.run(P, p, DEV_START, DEV_END, cost_mult=2)
        rows.append(dict(name=name(p), params=p, trades=d["trades"], mean=float(d["mean_month"]), median=float(d["median_month"]),
                         geo=float(d["geo_month"]), net=float(d["net_pct"]), dd=float(d["max_dd_pct"]), net_h1=float(a["net_pct"]),
                         net_h2=float(b["net_pct"]), net_2x=float(d2["net_pct"]), win=float(d["win_rate"]),
                         n_long=d["n_long"], n_short=d["n_short"]))
        r = rows[-1]
        r["eligible"] = bool(r["trades"] >= 200 and r["net_h1"] > 0 and r["net_h2"] > 0 and r["net_2x"] > 0)
        print(f"{r['name']:45s} tr={r['trades']:5d} mean={r['mean']:6.2f} geo={r['geo']:6.2f} dd={r['dd']:5.1f} "
              f"h1={r['net_h1']:7.1f} h2={r['net_h2']:7.1f} 2x={r['net_2x']:7.1f} {'ELIG' if r['eligible'] else ''} "
              f"[{time.time()-t0:.0f}s]", flush=True)
    el = [r for r in rows if r["eligible"]]
    pick = max(el or rows, key=lambda r: r["geo"])
    out = dict(n=len(rows), n_eligible=len(el), n_net_pos=sum(r["net"] > 0 for r in rows),
               n_net2x_pos=sum(r["net_2x"] > 0 for r in rows), selected=pick, selected_passes_rule=bool(el),
               rows=sorted(rows, key=lambda r: -r["geo"]))
    (HERE / "dev_scan.json").write_text(json.dumps(out, indent=1, default=float))
    print("eligible", len(el), "/", len(rows), "net>0", out["n_net_pos"], "selected", pick["name"], pick["geo"])
