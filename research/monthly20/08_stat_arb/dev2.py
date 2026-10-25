"""DEV-only round 2: residual-shock events (D) + drift control. Writes dev_grid2.json."""
import os, json, time, engine
if os.environ.get("CACHED_ONLY"):
    have = {p.stem for p in (engine.CACHE / "perp" / "1h").glob("*.json")}
    engine.COINS[:] = [c for c in engine.COINS if c in have and (engine.CACHE / "funding" / f"{c}.json").exists()]
print("coins", len(engine.COINS), engine.COINS, flush=True)
import strategies as S
from engine import sim, summary, DEV_START, DEV_END
D = engine.load(); r, E, B = S.residuals(D)
grid = []
def run(name, W):
    c, pp, info = sim(D, W, DEV_START, DEV_END)
    s = summary(c, pp); s.update(info)
    s["zero_cost_mean_month"] = summary(sim(D, W, DEV_START, DEV_END, cost_mult=0)[0])["mean_month"]
    s["double_cost_mean_month"] = summary(sim(D, W, DEV_START, DEV_END, cost_mult=2)[0])["mean_month"]
    grid.append({"name": name, "dev": s})
    print(f"{name:40s} mean {s['mean_month']:6.2f} med {s['median_month']:6.2f} worst {s['worst_month']:7.2f} net {s['net_pct']:8.1f} "
          f"dd {s['max_dd_pct']:5.1f} | nocost {s['zero_cost_mean_month']:5.2f} 2x {s['double_cost_mean_month']:5.2f}", flush=True)
run("D_control_all_alts_long_hedged", S.shock(D, r, E, B, control=True))
for hold in (12, 24, 48):
    for slots in (5, 10):
        run(f"D_shock_both_h{hold}_s{slots}", S.shock(D, r, E, B, hold=hold, slots=slots))
for hold in (24,):
    run(f"D_shock_uponly_h{hold}_s5", S.shock(D, r, E, B, hold=hold, slots=5, use_dn=False))
    run(f"D_shock_dnonly_h{hold}_s5", S.shock(D, r, E, B, hold=hold, slots=5, use_up=False))
    run(f"D_shock_both_z3_h{hold}_s5", S.shock(D, r, E, B, hold=hold, slots=5, up_z=3, dn_z=-3))
json.dump(grid, open("dev_grid2.json", "w"), indent=1, default=float)
