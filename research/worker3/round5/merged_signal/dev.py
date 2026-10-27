"""DEV-only stage: every pre-registered config on DEV (2021-01..2024-10), 1x and 2x costs. Writes dev.json."""
import json, sys, time
import numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib, configs
HERE = Path(__file__).resolve().parent
t0 = time.time()
D = lib.load("perp")
res = {}
only = sys.argv[1:] or list(configs.ALL)
for name in only:
    cfg = configs.ALL[name]
    legs, scheme = configs.build(D, cfg)
    wp = lib.weights_for(D, legs, scheme)
    r1 = lib.run(D, legs, lib.DEV_START, lib.DEV_END, wpath=wp, record=True)
    r2 = lib.run(D, legs, lib.DEV_START, lib.DEV_END, wpath=wp, cost_mult=2)
    b = lib.brief(r1); b["net_2x"] = r2["stats"]["net_pct"]; b["geo_2x"] = r2["stats"]["geo_month"]
    b["positions_per_month"] = round(len(r1["episodes"]) / r1["stats"]["n_months"], 1)
    b["months"] = r1["stats"]["months"]
    res[name] = b
    print(name, {k: v for k, v in b.items() if k != "months"}, f"[{time.time()-t0:.0f}s]", flush=True)
out = HERE / "dev.json"
old = json.loads(out.read_text()) if out.exists() else {}
old.update(res)
out.write_text(json.dumps(old, indent=1))
