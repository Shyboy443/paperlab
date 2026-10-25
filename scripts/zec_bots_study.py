"""ZEC bots study (docs/ZEC_BOTS_STUDY.md): the six TradingView strategies of app/zecbots/rules.py.

    python scripts/zec_bots_study.py [--cache DIR]

1. Reproduction: long-only spot ZECUSDT, 2024-10-11 .. 2026-10-11, the research package's cash broker (next-open fills,
   0.10 % fee + 0.02 % slippage). Must match the study table the user supplied.
2. The bots as they would run: ZECUSDT USD-M perpetual, 1x, long-only and long + short, 30 % disaster stop, funding at
   the recorded rate; taker (0.05 % + 0.02 %) and maker (0.02 % + 0.01 %) costs. Y1 / Y2, months, weeks, October 2026.
Public Binance data only. Writes docs/zec_bots/study.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.autoresearch.client import Client  # noqa: E402
from app.macdtrio import sim  # noqa: E402
from app.tvdesks import broker  # noqa: E402
from app.zecbots import rules  # noqa: E402

ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
WARM, Y1, Y2, END = ms("2024-06-01T00:00"), ms("2024-10-11T00:00"), ms("2025-10-11T00:00"), ms("2026-10-11T00:00")
OCT = ms("2026-10-01T00:00")
TABLE = {"emarsi": (4381.41, 45.05, 47), "momentum": (4079.93, 55.80, 12), "emacross": (3023.56, 52.00, 156),
         "macross": (2951.76, 57.17, 70), "scalpslayer": (2729.02, 68.07, 8), "wilder": (2092.90, 65.56, 35)}
COSTS = {"taker": (0.0005, 0.0002), "maker": (0.0002, 0.0001)}
OUT = PROJECT / "docs" / "zec_bots"
BACKTEST = PROJECT / "app" / "zecbots" / "backtest.json"
SHOWN = "long-only taker"            # what the bots run (long-only), with the conservative taker costs


def card(times, equity, metrics, extra):
    """The dashboard's backtest summary for one curve (same shape as app/macdtrio/backtest.json)."""
    y1 = [e for t, e in zip(times, equity) if t <= Y2]
    mo = periods(times, equity, 10_000.0, "%Y-%m")
    wk = periods(times, equity, 10_000.0, "%G-W%V")
    med = lambda v: sorted(v)[len(v) // 2] if v else 0.0  # noqa: E731
    out = {"size": 1.0, "net2y": (equity[-1] / 10_000.0 - 1) * 100, "y1": (y1[-1] / 10_000.0 - 1) * 100 if y1 else 0.0,
           "y2": (equity[-1] / y1[-1] - 1) * 100 if y1 else 0.0, "dd": metrics["max_drawdown_pct"],
           "months": len(mo), "pos_months": sum(1 for v in mo.values() if v > 0), "worst_month": min(mo.values()),
           "best_month": max(mo.values()), "median_month": med(list(mo.values())), "weeks": len(wk),
           "pos_weeks": sum(1 for v in wk.values() if v > 0), "worst_week": min(wk.values()), "median_week": med(list(wk.values())),
           "monthly": mo, "weekly": wk, "window": ["2024-10-11", "2026-10-11"],
           "costs": "commission 0.05% + slippage 0.02% per side (conservative: the bots use maker orders), funding, "
                    "30% disaster stop, 1x, long-only, full size",
           "caveat": "ZEC was the best coin of a 2-year study, picked with hindsight; ZEC itself rose about 3,500% "
                     "in that window, which carries most of these results"}
    out.update(extra)
    return out


def dd_of(eq):
    peak, dd = 10_000.0, 0.0
    for e in eq:
        peak = max(peak, e)
        dd = max(dd, (peak - e) / peak)
    return dd * 100


def klines(c: Client, venue: str, interval: str, tf: int, start: int, end: int) -> list[dict]:
    out, cur = {}, start
    while cur < end:
        rows = c.get(venue, "klines", {"symbol": "ZECUSDT", "interval": interval, "startTime": cur, "endTime": end - 1,
                                       "limit": 1000}, weight=5)
        if not rows:
            break
        for r in rows:
            if int(r[0]) + tf <= end:
                out[int(r[0])] = {"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])}
        nxt = int(rows[-1][0]) + tf
        if nxt <= cur:
            break
        cur = nxt
    return [out[t] for t in sorted(out)]


def funding(c: Client, start: int, end: int) -> list[list]:
    out, cur = {}, start
    while cur < end:
        rows = c.get("perp", "fundingRate", {"symbol": "ZECUSDT", "startTime": cur, "endTime": end - 1, "limit": 1000}, weight=1)
        if not rows:
            break
        for r in rows:
            out[int(r["fundingTime"])] = [int(r["fundingTime"]), float(r["fundingRate"]), float(r.get("markPrice") or 0) or None]
        nxt = int(rows[-1]["fundingTime"]) + 1
        if nxt <= cur:
            break
        cur = nxt
    return [out[t] for t in sorted(out)]


def periods(times, equity, start_eq, fmt):
    last = {}
    for t, e in zip(times, equity):
        last[datetime.fromtimestamp((t - 1) / 1000, timezone.utc).strftime(fmt)] = e
    out, prev = {}, start_eq
    for k in sorted(last):
        out[k] = round((last[k] / prev - 1) * 100, 2)
        prev = last[k]
    return out


def summary(r, tf):
    m = r["metrics"]
    months = periods(r["times"], r["equity"], 10_000.0, "%Y-%m")
    weeks = periods(r["times"], r["equity"], 10_000.0, "%G-W%V")
    return {"net_pct": round(m["net_return_pct"], 2), "dd_pct": round(m["max_drawdown_pct"], 2), "trades": m["trades"],
            "stops": m["stops"], "pf": round(m["profit_factor"] or 0, 2), "win_pct": round(m["win_rate_pct"] or 0, 1),
            "funding": round(m["funding"], 2), "fees": round(m["fees"], 2),
            "worst_month": min(months.items(), key=lambda kv: kv[1]) if months else None,
            "worst_week": min(weeks.items(), key=lambda kv: kv[1]) if weeks else None,
            "months_up": sum(1 for v in months.values() if v > 0), "months": months, "weeks": weeks}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(PROJECT / "data" / "zec_bots_cache"))
    a = ap.parse_args()
    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    c = Client(threading.Event())
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    data = {}
    for venue in ("spot", "perp"):
        for iv, tf in (("4h", rules.H4), ("1h", rules.H1)):
            f = cache / f"{venue}_{iv}.json"
            if not f.exists():
                f.write_text(json.dumps(klines(c, venue, iv, tf, WARM, now // tf * tf)))
            data[(venue, iv)] = json.loads(f.read_text())
    f = cache / "funding.json"
    if not f.exists():
        f.write_text(json.dumps(funding(c, Y1 - 86_400_000, now)))
    fund_raw = json.loads(f.read_text())
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "bots": {}}
    cards, curves = {}, {}
    for key, (name, fn, tf, iv, _author) in rules.RULES.items():
        sbars = [b for b in data[("spot", iv)] if b["t"] < END]
        buy, sell = fn(sbars)
        rep = broker.replay(sbars, buy, sell, Y1, END)["metrics"]
        want = TABLE[key]
        print(f"\n== {name} ({iv})  spot long-only: {rep['net_return_pct']:+,.2f}%  DD {rep['max_drawdown_pct']:.2f}%  "
              f"trades {rep['trades']}   | table {want[0]:+,.2f}% DD {want[1]:.2f}% trades {want[2]}", flush=True)
        pbars = data[("perp", iv)]
        opens = {b["t"]: b["o"] for b in pbars}
        fund = [(t, r, m or opens.get(t // tf * tf) or 0.0) for t, r, m in fund_raw]
        sig = rules.to_sig(*fn(pbars))
        row = {"name": name, "interval": iv, "spot_long_only": {"net_pct": round(rep["net_return_pct"], 2),
               "dd_pct": round(rep["max_drawdown_pct"], 2), "trades": rep["trades"]}, "table": want, "perp": {}}
        for shorts in (False, True):
            for cost, (fee, slip) in COSTS.items():
                kw = dict(sig=sig, tf=tf, fee=fee, slip=slip, shorts=shorts)
                two = sim.simulate(pbars, fund, Y1, END, **kw)
                y1 = sim.simulate(pbars, fund, Y1, Y2, **kw)["metrics"]
                y2 = sim.simulate(pbars, fund, Y2, END, **kw)["metrics"]
                octo = sim.simulate(pbars, fund, OCT, now // tf * tf, close_at_end=False, **kw)
                s = summary(two, tf)
                s.update(y1_pct=round(y1["net_return_pct"], 2), y1_dd=round(y1["max_drawdown_pct"], 2),
                         y2_pct=round(y2["net_return_pct"], 2), y2_dd=round(y2["max_drawdown_pct"], 2),
                         oct2026_pct=round(octo["metrics"]["net_return_pct"], 2),
                         oct2026_weeks=periods(octo["times"], octo["equity"], 10_000.0, "%G-W%V"))
                tag = ("long+short" if shorts else "long-only") + " " + cost
                row["perp"][tag] = s
                if tag == SHOWN:
                    m = two["metrics"]
                    curves[key] = dict(zip(two["times"], two["equity"]))
                    cards[key] = card(two["times"], two["equity"], m, {
                        "name": name, "interval": iv, "pf": m["profit_factor"], "trades": m["trades"],
                        "spot_reproduction": row["spot_long_only"]})
                print(f"   perp {tag:<20} 2y {s['net_pct']:+10,.1f}%  DD {s['dd_pct']:5.1f}%  Y1 {s['y1_pct']:+9,.1f}%  "
                      f"Y2 {s['y2_pct']:+8,.1f}% (DD {s['y2_dd']:4.1f}%)  trades {s['trades']:4d}  stops {s['stops']}  "
                      f"PF {s['pf']:.2f}  worst month {s['worst_month'][1]:+.1f}%  worst week {s['worst_week'][1]:+.1f}%  "
                      f"Oct-26 {s['oct2026_pct']:+.1f}%", flush=True)
        out["bots"][key] = row
    for key, s in out["bots"].items():
        ls = s["perp"]["long+short taker"]
        cards[key]["long_short"] = {"net2y": ls["net_pct"], "dd": ls["dd_pct"]}
    ts = sorted(set.intersection(*[set(c) for c in curves.values()]))
    eq = [sum(c[t] for c in curves.values()) / len(curves) for t in ts]
    cards["PORTFOLIO"] = card(ts, eq, {"max_drawdown_pct": dd_of(eq)}, {"name": "all six together (equal money)"})
    pb = [b for b in data[("perp", "4h")] if Y1 <= b["t"] < END]
    cards["BUY_HOLD"] = {"net2y": (pb[-1]["c"] / pb[0]["o"] - 1) * 100,
                         "dd": dd_of([10_000.0 * b["c"] / pb[0]["o"] for b in pb]), "name": "ZEC buy & hold (perp, no costs)"}
    BACKTEST.write_text(json.dumps(cards, indent=1))
    print("\nportfolio (six together): %+.1f%%  DD %.1f%%   ZEC buy & hold %+.1f%%  DD %.1f%%" % (
        cards["PORTFOLIO"]["net2y"], cards["PORTFOLIO"]["dd"], cards["BUY_HOLD"]["net2y"], cards["BUY_HOLD"]["dd"]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "study.json").write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
