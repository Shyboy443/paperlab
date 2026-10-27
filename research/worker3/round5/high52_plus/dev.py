"""DEV-only grid (PREREG.md): 36 stage-1 + 4 stage-2 configs, selection rule. Writes dev.json. No TEST/HOLDOUT here."""
import json, sys, time, itertools
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib5 as L
HERE = Path(__file__).resolve().parent
t0 = time.time()
P = L.L4.make_panel(L.L4.build())


def ev(cfg):
    r = L.run5(P, cfg, L.DEV_START, L.DEV_END, record=True)
    r2 = L.run5(P, cfg, L.DEV_START, L.DEV_END, cost_mult=2)
    s = L.stats(r)
    d = dict(cfg=cfg, dev=s, dev_net_2x=r2["net_pct"], max_pos_share=L.max_pos_share(r))
    print(json.dumps(d, default=float), f"[{time.time()-t0:.0f}s]", flush=True)
    return d


def elig(d, dd=35, ps=10):
    up = int(d["dev"]["up"].split("/")[0])
    return d["dev_net_2x"] > 0 and d["dev"]["max_dd"] <= dd and up >= 23 and (d["max_pos_share"] == d["max_pos_share"]) \
        and d["max_pos_share"] <= ps


S1 = [dict(book=b, exit=e, chan=c) for b, e, c in itertools.product(["LS", "LO", "GC", "GL", "GLS", "TILT"], ["D", "H", "C"], ["30", "BL"])]
g1 = [ev(c) for c in S1]
E = [d for d in g1 if elig(d)]
relaxed = False
if not E:
    E = [d for d in g1 if elig(d, 40, 15)]; relaxed = True
res = dict(stage1=g1, eligible1=[d["cfg"] for d in E], relaxed=relaxed)
if E:
    w = sorted(E, key=lambda d: (-d["dev"]["geo"], -d["dev"]["t"]))[0]
    base = w["cfg"]
    S2 = [dict(base, univ=20, k=5), dict(base, univ=80, k=10), dict(base, vol=True), dict(base, trim=True)]
    g2 = [ev(c) for c in S2]
    better = [d for d in g2 if elig(d, *( (40, 15) if relaxed else (35, 10))) and d["dev"]["geo"] >= w["dev"]["geo"] + 0.20]
    sel = sorted(better, key=lambda d: -d["dev"]["geo"])[0] if better else w
    res.update(stage1_winner=w, stage2=g2, selected=sel)
    print("STAGE1 WINNER", w, "\nSELECTED", sel)
else:
    res["selected"] = None; print("NOTHING ELIGIBLE")
res["n_configs"] = len(g1) + len(res.get("stage2", []))
(HERE / "dev.json").write_text(json.dumps(res, indent=1, default=float))
print(f"done [{time.time()-t0:.0f}s]")
