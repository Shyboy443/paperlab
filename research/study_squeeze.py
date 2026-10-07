"""The operator's own hypothesis, tested properly: "funding very negative + open interest jumps + price falls fast ->
what next?" (a short squeeze set-up: shorts pile in on a drop; a bounce forces them out).

    python research/study_squeeze.py

PRE-REGISTERED (2026-10-03). The thresholds were fixed in research/queries/funding_oi_flush.sql BEFORE its first run
and are NOT tuned here; this script only adds the checks a raw average lacks.
  EVENT      z_funding <= -1.5 AND z_oi_chg_1h >= +1.5 AND z_r_1h <= -1.5 (per-coin trailing 7-day z-scores, all
             known at the decision); one event per coin per HOLD window (no overlapping trades on a coin).
  TRADE      LONG at the next minute's close (the paper engine's 60 s latency), exit after HOLD = 1h or 4h.
  RETURNS    raw, and market-adjusted (minus the mean return of all 30 coins over the same window), net of TAKER
             costs (2 x 5.5 bp + the coin's spread); a MAKER bound (2 x 2 bp) for information.
  STATISTICS averaged per DAY first (events cluster on crash days), t across days; both halves of the window.
  CANDIDATE only if, for a hold: (1) raw net > 0 AND market-adjusted net > 0 in EACH half; (2) day-clustered t >= 2.0 on
             the whole window (raw net); (3) still net > 0 with every threshold moved by +-0.25 (8 variants);
             (4) still net > 0 without its 3 best days. A CANDIDATE goes to a forward PAPER experiment, never live.
  Diagnostics (no selection): the same event without the funding leg, without the OI leg, and by BTC regime.
"""
from __future__ import annotations

import itertools
import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "research"))
from study_altdata import TAKER_FEE_RT, MAKER_FEE_RT, tstat  # noqa: E402

DB = PROJECT / "data" / "research" / "market.duckdb"
OUT = PROJECT / "docs" / "SQUEEZE_STUDY.json"
DAY = 86_400_000
HOLDS = {"1h": 3_600_000, "4h": 14_400_000}
BASE = {"fz": -1.5, "oz": 1.5, "pz": -1.5}


def events(df: pd.DataFrame, fz: float | None, oz: float | None, pz: float, hold_ms: int) -> pd.DataFrame:
    m = df["z_r_1h"] <= pz
    if fz is not None:
        m &= df["z_funding"] <= fz
    if oz is not None:
        m &= df["z_oi_chg_1h"] >= oz
    ev = df[m].sort_values(["symbol", "ts"])
    keep, last = [], {}
    for i, s, t in zip(ev.index, ev["symbol"].values, ev["ts"].values):
        if t >= last.get(s, -1) + hold_ms:                         # one trade per coin per hold window
            keep.append(i)
            last[s] = t
    return ev.loc[keep]


def score(ev: pd.DataFrame, h: str, split: int) -> dict:
    if ev.empty:
        return {"events": 0}
    raw = ev["fwd_" + h] - ev["cost"]
    adj = ev["fwd_" + h] - ev["mkt_" + h] - ev["cost"]
    day = ev["ts"] // DAY
    out = {"events": int(len(ev)), "days": int(day.nunique()), "coins": int(ev["symbol"].nunique())}
    for name, x in (("raw_net_bp", raw), ("adj_net_bp", adj), ("raw_maker_bp", ev["fwd_" + h] - MAKER_FEE_RT)):
        d = (x * 1e4).groupby(day).mean()
        m, t, n = tstat(d.values)
        out[name] = {"all": round(m, 1), "t": round(t, 2),
                     "h1": round(float(d[d.index < split].mean()), 1) if (d.index < split).any() else None,
                     "h2": round(float(d[d.index >= split].mean()), 1) if (d.index >= split).any() else None}
    d = (raw * 1e4).groupby(day).mean().sort_values()
    out["raw_net_bp_without_best3_days"] = round(float(d.iloc[:-3].mean()), 1) if len(d) > 3 else None
    out["win_rate"] = round(float((raw > 0).mean()), 3)
    return out


def main() -> None:
    t0 = time.time()
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute("SELECT symbol, ts, z_funding, z_oi_chg_1h, z_r_1h, fwd_1h, fwd_4h, vol_regime, trend_regime "
                     "FROM features_5m").df()
    con.close()
    for h in HOLDS:
        df["mkt_" + h] = df.groupby("ts")["fwd_" + h].transform("mean")
    spreads = json.loads((PROJECT / "data" / "v11_scan" / "spreads.json").read_text())
    hs = {s: float(v.get("half_spread_bps", v) if isinstance(v, dict) else v) / 1e4 for s, v in spreads.items()} if isinstance(spreads, dict) else {}
    df["cost"] = TAKER_FEE_RT + 2 * df["symbol"].map(hs).fillna(2e-4)
    df = df.dropna(subset=["z_funding", "z_oi_chg_1h", "z_r_1h"])
    days = np.sort((df["ts"] // DAY).unique())
    split = int(days[len(days) // 2])
    res = {}
    for h, hold in HOLDS.items():
        r = {"base": score(events(df.dropna(subset=["fwd_" + h]), BASE["fz"], BASE["oz"], BASE["pz"], hold), h, split)}
        variants = {}
        for d in itertools.product((-0.25, 0.25), repeat=3):
            k = f"fz{BASE['fz'] + d[0]:+.2f} oz{BASE['oz'] + d[1]:+.2f} pz{BASE['pz'] + d[2]:+.2f}"
            variants[k] = score(events(df.dropna(subset=["fwd_" + h]), BASE["fz"] + d[0], BASE["oz"] + d[1], BASE["pz"] + d[2], hold), h, split)
        r["variants"] = variants
        r["diag_no_funding_leg"] = score(events(df.dropna(subset=["fwd_" + h]), None, BASE["oz"], BASE["pz"], hold), h, split)
        r["diag_no_oi_leg"] = score(events(df.dropna(subset=["fwd_" + h]), BASE["fz"], None, BASE["pz"], hold), h, split)
        r["diag_price_only"] = score(events(df.dropna(subset=["fwd_" + h]), None, None, BASE["pz"], hold), h, split)
        ev = events(df.dropna(subset=["fwd_" + h]), BASE["fz"], BASE["oz"], BASE["pz"], hold)
        r["by_regime"] = {f"{k}": score(g, h, split) for k, g in ev.groupby("vol_regime")}
        b = r["base"]
        c1 = all((b[x]["h1"] or -1) > 0 and (b[x]["h2"] or -1) > 0 for x in ("raw_net_bp", "adj_net_bp"))
        c2 = b["raw_net_bp"]["t"] >= 2.0
        c3 = all(v.get("events", 0) > 0 and v["raw_net_bp"]["all"] > 0 for v in variants.values())
        c4 = (b["raw_net_bp_without_best3_days"] or -1) > 0
        r["checks"] = {"both_halves_raw_and_adjusted": c1, "day_t_ge_2": c2, "robust_to_thresholds": c3,
                       "survives_without_best_3_days": c4}
        r["verdict"] = "CANDIDATE (forward paper test)" if all((c1, c2, c3, c4)) else "NOT A CANDIDATE"
        res[h] = r
        print(f"\n== hold {h}: {r['verdict']}  checks {r['checks']}")
        print(f"base: {b['events']} events on {b['days']} days, {b['coins']} coins, win {b['win_rate']}")
        for x in ("raw_net_bp", "adj_net_bp", "raw_maker_bp"):
            print(f"  {x:13s} all {b[x]['all']:+7.1f} (t {b[x]['t']:+.2f})  half1 {b[x]['h1']}  half2 {b[x]['h2']}")
        print(f"  without the best 3 days: {b['raw_net_bp_without_best3_days']}")
        print("  threshold variants (raw net bp, events):",
              ", ".join(f"{v['raw_net_bp']['all']:+.0f}/{v['events']}" for v in variants.values()))
        for k in ("diag_no_funding_leg", "diag_no_oi_leg", "diag_price_only"):
            v = r[k]
            print(f"  {k:20s} {v['events']:5d} events  raw net {v['raw_net_bp']['all']:+6.1f} (t {v['raw_net_bp']['t']:+.2f})  "
                  f"adj {v['adj_net_bp']['all']:+6.1f}  halves {v['raw_net_bp']['h1']}/{v['raw_net_bp']['h2']}")
        for k, v in r["by_regime"].items():
            print(f"  vol regime {k:5s} {v['events']:4d} events raw net {v['raw_net_bp']['all']:+6.1f}")
    OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                               "rules": __doc__.split("PRE-REGISTERED")[1], "results": res}, indent=1))
    print(f"\nwrote {OUT.relative_to(PROJECT)} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
