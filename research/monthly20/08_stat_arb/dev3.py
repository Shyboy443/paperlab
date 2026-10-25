"""DEV-only round 3: basket-hedged shocks; split DEV 2021 vs 2022-24. Writes dev_grid3.json."""
import os, json, numpy as np, engine
if os.environ.get("CACHED_ONLY"):
    have = {p.stem for p in (engine.CACHE / "perp" / "1h").glob("*.json")}
    engine.COINS[:] = [c for c in engine.COINS if c in have and (engine.CACHE / "funding" / f"{c}.json").exists()]
print("coins", len(engine.COINS), flush=True)
import strategies as S
from engine import sim, summary, DEV_START, DEV_END
D = engine.load(); r, E, B = S.residuals(D)
grid = []
def run(name, W):
    c, pp, info = sim(D, W, DEV_START, DEV_END)
    s = summary(c, pp); s.update(info)
    s["double_cost_mean_month"] = summary(sim(D, W, DEV_START, DEV_END, cost_mult=2)[0])["mean_month"]
    m = s["months"]; a = [v for k, v in m.items() if k < "2022"]; b = [v for k, v in m.items() if k >= "2022"]
    s["mean_2021"], s["mean_2022_24"] = round(np.mean(a), 2), round(np.mean(b), 2)
    grid.append({"name": name, "dev": s})
    print(f"{name:40s} mean {s['mean_month']:6.2f} med {s['median_month']:6.2f} worst {s['worst_month']:7.2f} dd {s['max_dd_pct']:5.1f} "
          f"2021 {s['mean_2021']:6.2f} 22-24 {s['mean_2022_24']:6.2f} 2x {s['double_cost_mean_month']:5.2f}", flush=True)
run("D_control_all_alts_long_hedged", S.shock(D, r, E, B, control=True, slots=5))
run("D_shock_both_h24_s5", S.shock(D, r, E, B, hold=24, slots=5))
for hold in (12, 24, 48):
    run(f"D2_basket_both_h{hold}_s5", S.shock_vs_basket(D, r, E, B, hold=hold, slots=5))
run("D2_basket_dnonly_h24_s5", S.shock_vs_basket(D, r, E, B, hold=24, slots=5, use_up=False))
run("D2_basket_uponly_h24_s5", S.shock_vs_basket(D, r, E, B, hold=24, slots=5, use_dn=False))
json.dump(grid, open("dev_grid3.json", "w"), indent=1, default=float)
