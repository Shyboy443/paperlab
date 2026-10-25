"""DEV sweep on 4h bars: hours-to-days lookbacks, rebalance every 4h or daily."""
import json, sys, itertools
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/02_xs_momentum")
from engine import *
from signals import *
from dev_sweep import build
from dev_stage2 import regime_gate, yr
from panel4h import panel_4h


def build4h():
    D, um_day, keep = build("1d")
    P = panel_4h(keep)
    P.load_funding = Panel.load_funding.__get__(P)
    P.load_funding()
    day_idx = ((P.t - DATA_FROM) // DAY).astype(int)
    return P, um_day[day_idx], keep


if __name__ == "__main__":
    P, um, keep = build4h()
    print("missing 1h:", len(P.missing), P.missing[:10], flush=True)
    out = []
    for L, mode, (kl, ks), rb, gate in itertools.product([1, 3, 6, 18], ["mom", "rev"], [(3, 0), (1, 0), (5, 5)], [1, 6], [False, True]):
        sc = ret_lb(P, L)
        if gate:
            sc = regime_gate(P, sc, 50 * 6)
        p = dict(k_long=kl, k_short=ks, rebal=rb, lev=1.0, mode=mode)
        r = run(P, um, sc, p, DEV_START, DEV_END)
        ys = [yr(r["months"], y) for y in ["2021", "2022", "2023", "2024"]]
        print(f"ret{L}x4h {mode} L{kl} S{ks} rb{rb} gate{int(gate)} mean {r['mean_month']:7.2f} med {r['median_month']:6.2f} geo {r['geo_month']:6.2f} dd {r['max_dd_pct']:5.1f} | "
              + " ".join(f"{y:6.2f}" for y in ys), flush=True)
        out.append(dict(L=L, gate=gate, **p, **r))
    (MY_DATA / "dev_4h.json").write_text(json.dumps(out, default=float))
