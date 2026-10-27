import sys
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import summary, monthly_returns


def st(curve, trades=None):
    s = summary(curve, [t["pnl"] for t in trades] if trades else None)
    v = np.array(list(s["months"].values()))
    s["t_stat"] = round(float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))), 2) if len(v) > 2 and v.std() > 0 else None
    if trades is not None:
        s["trades_per_month"] = round(len(trades) / max(1, s["n_months"]), 2)
    return s


def line(name, s):
    return (f"{name:<28} med {s['median_month']:+6.2f} geo {s['geo_month']:+6.2f} mean {s['mean_month']:+6.2f} "
            f"up {s['months_up']}/{s['n_months']} DD {s['max_dd_pct']:5.1f} t {s['t_stat']} tr/mo {s.get('trades_per_month')} net {s['net_pct']:+.1f}")
