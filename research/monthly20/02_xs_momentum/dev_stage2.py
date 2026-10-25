"""DEV stage 2 (daily bars, full funding): leverage sweep + a BTC-trend regime filter on the DEV finalists."""
import json, sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/02_xs_momentum")
from engine import *
from signals import *
from dev_sweep import build


def regime_gate(P, score, ma=50):
    """Blank the score (no positions) when BTC closed below its `ma`-bar SMA at the previous close."""
    btc = P.c[:, P.syms.index("BTCUSDT")]
    sma = np.convolve(np.nan_to_num(btc), np.ones(ma) / ma, mode="full")[:len(btc)]
    up = np.r_[False, (btc > sma)[:-1]]
    up[:ma] = False
    out = score.copy(); out[~up] = np.nan
    return out


def yr(m, y):
    v = [x for k, x in m.items() if k.startswith(y)]
    return (np.prod([1 + x / 100 for x in v]) ** (1 / len(v)) - 1) * 100


if __name__ == "__main__":
    P, um, keep = build("1d")
    btc = P.syms.index("BTCUSDT")
    cands = {
        "A_resid28_LS10_w": (resid_btc(P, 28, 60, btc), dict(k_long=10, k_short=10, rebal=7, mode="mom")),
        "A2_ret14_LS10_w": (ret_lb(P, 14), dict(k_long=10, k_short=10, rebal=7, mode="mom")),
        "B_ret7_L3_d": (ret_lb(P, 7), dict(k_long=3, k_short=0, rebal=1, mode="mom")),
        "B_ret7_L3_d_btc50": (regime_gate(P, ret_lb(P, 7)), dict(k_long=3, k_short=0, rebal=1, mode="mom")),
        "C_ret7_L1_d_btc50": (regime_gate(P, ret_lb(P, 7)), dict(k_long=1, k_short=0, rebal=1, mode="mom")),
        "B_ret14_L3_w_btc50": (regime_gate(P, ret_lb(P, 14)), dict(k_long=3, k_short=0, rebal=7, mode="mom")),
    }
    out = []
    for name, (sc, p) in cands.items():
        for lev in [1, 2, 3, 5, 8, 10]:
            r = run(P, um, sc, dict(p, lev=lev), DEV_START, DEV_END)
            r2 = run(P, um, sc, dict(p, lev=lev), DEV_START, DEV_END, cost_mult=2.0)
            ys = [yr(r["months"], y) for y in ["2021", "2022", "2023", "2024"]]
            print(f"{name:20s} lev{lev:3d} mean {r['mean_month']:8.2f} med {r['median_month']:7.2f} geo {r['geo_month']:7.2f} worst {r['worst_month']:7.2f} "
                  f"dd {r['max_dd_pct']:6.1f} liq {r['liquidations']:4d} wiped {r['wiped']} 2x {r2['mean_month']:7.2f} | " + " ".join(f"{y:6.2f}" for y in ys), flush=True)
            out.append(dict(name=name, lev=lev, **{k: v for k, v in r.items()}, mean2x=r2["mean_month"]))
    (MY_DATA / "dev_stage2.json").write_text(json.dumps(out, default=float))
