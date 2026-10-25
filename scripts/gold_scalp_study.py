"""Gold Scalping Strategy (1-Min TF) study (pre-registered in docs/GOLD_SCALP_STUDY.md).

    python scripts/mt5_export.py --m1 XAUUSD --start 2024-10-01      # once MT5 holds the long 1-minute history
    python scripts/gold_scalp_study.py [--source mt5|dukascopy]

Writes app/ytstrats/gold_results.json (served read-only by /api/public/gold-scalp). With less than the two pre-registered
years of data it writes a PRELIMINARY result (no verdict) over whatever window exists.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ytstrats import engine, gold  # noqa: E402

ms = lambda s: int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)  # noqa: E731
Y1, Y2, END = ms("2024-10-20"), ms("2025-10-20"), ms("2026-10-20")
OUT = PROJECT / "app" / "ytstrats" / "gold_results.json"


def us_dst(t_ms: np.ndarray) -> np.ndarray:
    """True where US daylight saving time is in effect (2nd Sunday of March 07:00 UTC -> 1st Sunday of November 06:00 UTC)."""
    out = np.zeros(len(t_ms), bool)
    years = {dt.datetime.fromtimestamp(x / 1000, dt.timezone.utc).year for x in (t_ms[0], t_ms[-1])}
    for y in range(min(years), max(years) + 1):
        mar = dt.datetime(y, 3, 8, 7, tzinfo=dt.timezone.utc)
        mar += dt.timedelta(days=(6 - mar.weekday()) % 7)
        nov = dt.datetime(y, 11, 1, 6, tzinfo=dt.timezone.utc)
        nov += dt.timedelta(days=(6 - nov.weekday()) % 7)
        out |= (t_ms >= mar.timestamp() * 1000) & (t_ms < nov.timestamp() * 1000)
    return out


def load(source: str) -> tuple[np.ndarray, str]:
    if source == "mt5":
        utc = PROJECT / "data" / "mt5_cache" / "XAUUSD_M1.npy"          # scripts/mt5_export_tf.py: already in UTC
        if utc.exists():
            a = np.load(utc)
            return a[:, :6], f"MT5 FusionMarkets-Live ({utc.name}, UTC)"
        f = PROJECT / "data" / "mt5_cache" / "XAUUSD_m1_full.npy"
        if not f.exists():
            f = PROJECT / "data" / "mt5_cache" / "XAUUSD_m1.npy"
        a = np.load(f)
        # Fusion Markets server time = UTC+2, UTC+3 while US daylight saving is on (the New York close convention)
        a = a.copy()
        a[:, 0] -= np.where(us_dst(a[:, 0] - 3 * 3_600_000), 3, 2) * 3_600_000
        return a, f"MT5 FusionMarkets-Live ({f.name})"
    a = np.load(PROJECT / "data" / "dukascopy" / "XAUUSD_m1.npy")
    return a, "Dukascopy XAUUSD BID 1-minute"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="mt5")
    a = ap.parse_args()
    bars, source = load(a.source)
    t, o, h, l, c = (bars[:, k] for k in range(5))
    sig = gold.signals(t, o, h, l, c)
    full = bool(t[0] <= Y1 - 2 * 86_400_000 and t[-1] >= END - 7 * 86_400_000)
    windows = {"y1": (Y1, Y2), "y2": (Y2, END)} if full else {"window": (int(t[0] + 2 * 86_400_000), int(t[-1] + 1))}
    rows = []
    for tp in gold.TPS:
        for sl in gold.SLS:
            res = {}
            for w, (s0, s1) in windows.items():
                tr = gold.replay(t, o, h, l, c, sig, tp, sl, s0, s1)
                months: dict[str, float] = {}
                for x in tr:
                    k = dt.datetime.fromtimestamp(x["t"] / 1000, dt.timezone.utc).strftime("%Y-%m")
                    months[k] = months.get(k, 0.0) + x["net_r"]
                res[w] = {"net": engine.summarize(tr), "gross": engine.summarize(tr, "gross_r"), "months_net_r": months,
                          "mean_r_ci_low": engine.bootstrap_low(np.array([x["net_r"] for x in tr])),
                          "ties": sum(1 for x in tr if x["result"].startswith("stop (candle")),
                          "recent": tr[-20:]}
            row = {"tp_pips": tp, "sl_pips": sl, **res}
            if full:
                s1, s2 = res["y1"]["net"], res["y2"]["net"]
                m2 = list(res["y2"]["months_net_r"].values())
                row["checks"] = {
                    "y1_positive": bool(s1.get("trades") and s1["mean_r"] > 0 and (s1["profit_factor"] or 0) >= 1.10),
                    "y2_positive": bool(s2.get("trades") and s2["mean_r"] > 0 and (s2["profit_factor"] or 0) >= 1.15 and s2["trades"] >= 100),
                    "y2_ci_above_zero": bool(res["y2"]["mean_r_ci_low"] > 0),
                    "y2_half_the_months": bool(m2 and sum(1 for v in m2 if v > 0) >= len(m2) / 2)}
                row["verdict"] = "PROVEN (candidate for forward test)" if all(row["checks"].values()) else "FAILED"
            else:
                row["verdict"] = "PRELIMINARY (not the pre-registered 2 years)"
            n = res[list(windows)[-1]]["net"]
            print(f"TP {tp:2d} SL {sl:2d}: {n.get('trades', 0):5d} trades  WR {n.get('win_rate_pct', 0):5.1f}%  "
                  f"net {n.get('goa_per_100', 0):+7.1f}R/100  PF {n.get('profit_factor') or 0:.2f}  {row['verdict']}", flush=True)
            rows.append(row)
    sens = gold.replay(t, o, h, l, c, sig, 15, 12, *list(windows.values())[-1], pip=1.0)
    out = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "source": source,
           "complete": full, "windows": {k: [dt.datetime.fromtimestamp(v / 1000, dt.timezone.utc).strftime("%Y-%m-%d") for v in w]
                                         for k, w in windows.items()},
           "rules": {"sessions_utc": ["07:00-10:00", "13:00-16:00"], "ema": [9, 21], "rsi": 14, "cross_window": gold.CROSS_WINDOW,
                     "lookback": gold.LOOKBACK, "body_min": gold.BODY_MIN, "max_per_day": gold.MAX_PER_DAY, "pip_usd": gold.PIP},
           "costs_usd": {"spread": gold.SPREAD, "commission_per_side": gold.COMMISSION, "slippage_per_side": gold.SLIPPAGE},
           "pre_registration": "docs/GOLD_SCALP_STUDY.md", "pairs": rows,
           "sensitivity_pip_1usd_tp15_sl12": engine.summarize(sens)}
    OUT.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
