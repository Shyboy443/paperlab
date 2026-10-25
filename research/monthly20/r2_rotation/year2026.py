"""This year's daily, weekly and monthly results of the rotation strategy (P4 rules at 1x, survivorship-free universe).

Runs the same full 2021..2026 walk as run.py (so the weekly rebalance days are the strategy's own), then cuts 2026 out.
Writes year2026.json and year2026_data.js (read by year2026.html) next to this file.
Months and weeks are calendar UTC days: each daily bar counts on the day it covers (not on the day it closes).

    python research/monthly20/r2_rotation/year2026.py
"""
import json, sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import engine_r2 as E
from data import build
from common import DEV_START, TEST_END

HERE = Path(__file__).resolve().parent
YEAR = 2026
P = E.Panel(build())
UM = E.universe_mask(P, 40)
SCORE = E.ret_lb(P, 14)
PARAMS = dict(k_long=10, k_short=10, rebal=7, lev=1.0, mode="mom")


def ymd(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def year_view(cost_mult):
    r = E.run(P, UM, SCORE, PARAMS, DEV_START, TEST_END, cost_mult=cost_mult, record=True)
    curve = r["_curve"]  # (end-of-day ms, equity)
    y0 = int(datetime(YEAR, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    start_eq = [e for t, e in curve if t <= y0][-1]
    pts = [(t, e) for t, e in curve if t > y0]
    days, prev = [], start_eq
    for t, e in pts:
        d = ymd(t - 1)  # the bar that ended at t
        days.append({"date": d.strftime("%Y-%m-%d"), "ret": (e / prev - 1) * 100, "equity": e / start_eq * 1000})
        prev = e

    def group(keyf):
        out, eq0 = OrderedDict(), 1000.0
        for d in days:
            k = keyf(d["date"])
            if k not in out:
                out[k] = {"start": eq0, "end": eq0, "days": 0, "first": d["date"]}
            out[k]["end"] = d["equity"]; out[k]["days"] += 1; out[k]["last"] = d["date"]
            eq0 = d["equity"]
        return [{"key": k, "from": v["first"], "to": v["last"], "days": v["days"],
                 "ret": (v["end"] / v["start"] - 1) * 100, "equity": v["end"]} for k, v in out.items()]

    week = lambda s: "%d-W%02d" % datetime.strptime(s, "%Y-%m-%d").isocalendar()[:2]
    months, weeks = group(lambda s: s[:7]), group(week)
    eq = [d["equity"] for d in days]
    peak, dd = 1000.0, 0.0
    for e in eq:
        peak = max(peak, e); dd = max(dd, 1 - e / peak)
    rets = [d["ret"] for d in days]
    stats = {"end": eq[-1], "net": eq[-1] / 10 - 100, "max_dd": dd * 100, "days": len(days),
             "days_up": sum(x > 0 for x in rets), "best_day": max(rets), "worst_day": min(rets),
             "weeks_up": sum(w["ret"] > 0 for w in weeks), "weeks": len(weeks),
             "months_up": sum(m["ret"] > 0 for m in months), "months": len(months)}
    # positions open at the end of the data
    held = sorted([(P.syms[j], int(s)) for j, s, o, c, *_ in r["_positions"] if c >= len(P.t) - 1 or c >= int(np.searchsorted(P.t, TEST_END))],
                  key=lambda x: -x[1])
    return {"days": days, "weeks": weeks, "months": months, "stats": stats, "holding_now": held}


out = {"strategy": "Momentum rotation, 1x: every 7 days long the top-10 / short the bottom-10 14-day performers of the top-40 "
       "liquid USDT perps (delisted coins included)", "year": YEAR,
       "normal": year_view(1.0), "double": year_view(2.0)}
(HERE / "year2026.json").write_text(json.dumps(out, indent=1, default=float))
(HERE / "year2026_data.js").write_text("window.Y26=" + json.dumps(out, default=float) + ";\n")
n = out["normal"]["stats"]
print({k: round(v, 2) if isinstance(v, float) else v for k, v in n.items()})
for m in out["normal"]["months"]:
    print(m["key"], round(m["ret"], 2), round(m["equity"]))
print("double-cost net", round(out["double"]["stats"]["net"], 2))
print("holding now:", out["normal"]["holding_now"])
