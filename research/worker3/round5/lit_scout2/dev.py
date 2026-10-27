"""DEV-only grid for both ideas (2021-01..2024-10). Never touches TEST/HOLDOUT windows. Writes dev.json."""
import json, sys, time, itertools
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib5 as L
HERE = Path(__file__).resolve().parent; t0 = time.time()
D, P, H, HP = L.panels()
res = {"TSD": {}, "CTR": {}}
for univ, tv, band in itertools.product((20, 40), (0.5, 1.0, 2.0), (0.0, 0.25)):
    cfg = dict(univ=univ, tv=tv, band=band); name = f"TSD_u{univ}_tv{tv}_b{band}"
    r1 = L.run_tsd(P, cfg, L.DEV_START, L.DEV_END, record=True); r2 = L.run_tsd(P, cfg, L.DEV_START, L.DEV_END, cost_mult=2)
    netp = r1["_curve"][-1][1] - r1["_curve"][0][1]
    mx = max(p[4] for p in r1["_positions"]) / netp * 100 if netp > 0 else None
    res["TSD"][name] = dict(cfg=cfg, dev=L.stats(r1), dev_net_2x=r2["net_pct"], dev_max_pos_share=mx)
    print(name, res["TSD"][name]["dev"], "2x", r2["net_pct"], "maxpos", mx, f"[{time.time()-t0:.0f}s]", flush=True)
for fs, Wk in itertools.product(("p", "pv"), (26, 52, 104)):
    cfg = dict(fs=fs, Wk=Wk); name = f"CTR_{fs}_W{Wk}"
    r1 = L.run_ctr(P, cfg, L.DEV_START, L.DEV_END, record=True); r2 = L.run_ctr(P, cfg, L.DEV_START, L.DEV_END, cost_mult=2)
    mx = L.L4.max_pos_share(r1)
    res["CTR"][name] = dict(cfg=cfg, dev=L.stats(r1), dev_net_2x=r2["net_pct"], dev_max_pos_share=mx)
    print(name, res["CTR"][name]["dev"], "2x", r2["net_pct"], "maxpos", mx, f"[{time.time()-t0:.0f}s]", flush=True)
(HERE / "dev.json").write_text(json.dumps(res, indent=1, default=float))
print("done", f"[{time.time()-t0:.0f}s]")
