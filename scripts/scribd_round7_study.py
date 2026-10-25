"""Scribd round 7 study (pre-registered in docs/SCRIBD_ROUND7_STUDY.md).

    python scripts/scribd_round7_study.py

Data: data/round3_cache/<SYM>_D1.npy (Yahoo daily, adjusted). Writes app/ytstrats/round7_results.json (served read-only
by /api/public/scribd-round7).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))

from app.ytstrats import round6 as R6, round7 as R7  # noqa: E402
from scribd_round6_study import ETF, compounded, evaluate, load, random_timing  # noqa: E402

ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
OUT = PROJECT / "app" / "ytstrats" / "round7_results.json"
LIQUID = {"SPY": 0.0001, "QQQ": 0.0001, "IWM": 0.0001, "DIA": 0.0001}


def curves(per: dict, win: dict, sym: str | None) -> dict:
    """CAGR / drawdown of the rule (single market) next to buy and hold of the same ETF, per window."""
    out = {}
    for w, (s0, s1) in win.items():
        row = {"rule": compounded(per[w], s0, s1)}
        if sym:
            b = load(ETF / f"{sym}_D1.npy")
            idx = np.flatnonzero((b.t >= s0) & (b.t < s1))
            yrs = (b.t[idx[-1]] - b.t[idx[0]]) / (365.25 * 86_400_000)
            path = b.c[idx]
            row["buy_and_hold"] = {"cagr_pct": float(((path[-1] / path[0]) ** (1 / yrs) - 1) * 100),
                                   "max_dd_pct": float((1 - path / np.maximum.accumulate(path)).max() * 100)}
            held = sum(int(np.searchsorted(b.t, x["exit_t"]) - np.searchsorted(b.t, x["t"])) for x in per[w])
            row["time_in_market_pct"] = float(held / max(1, len(idx)) * 100)
        out[w] = row
    return out


def leverage(fn, base, lev: str) -> dict:
    """Not a verdict: the same signals traded in a 3x ETF, 2010-03 -> 2026-10."""
    px = load(ETF / f"{lev}_D1.npy")
    s0, s1 = ms("2010-03-01"), ms("2026-10-21")
    rets = []
    for x in fn(base, 0, 0.0001, s0, s1):
        i, k = int(np.searchsorted(px.t, x["t"])), int(np.searchsorted(px.t, x["exit_t"]))
        rets.append(((px.c[k] * (1 - 0.0002)) / (px.c[i] * (1 + 0.0002)) - 1) * 100)
    eq = np.cumprod(1 + np.array(rets) / 100)
    yrs = (s1 - s0) / (365.25 * 86_400_000)
    idx = np.flatnonzero((px.t >= s0) & (px.t < s1))
    pth = px.c[idx]
    return {"etf": lev, "cagr_pct": float((eq[-1] ** (1 / yrs) - 1) * 100), "max_dd_pct": float((1 - eq / np.maximum.accumulate(np.r_[1.0, eq])[1:]).max() * 100),
            "worst_trade_pct": float(min(rets)), "buy_and_hold_cagr_pct": float(((pth[-1] / pth[0]) ** (1 / yrs) - 1) * 100),
            "buy_and_hold_max_dd_pct": float((1 - pth / np.maximum.accumulate(pth)).max() * 100)}


def main() -> int:
    rows = []
    vixy = {"VIXY": load(ETF / "VIXY_D1.npy")}
    wv = {"a": (ms("2011-01-01"), ms("2014-01-01")), "b": (ms("2014-01-01"), ms("2026-10-21"))}
    for key, fn, doc in (("grossmann_vxx_bb", R7.grossmann_vxx_bb, "Strategies for Trading Inverse Volatility, Grossmann Dec 2013 (250728698): Bollinger rule"),
                         ("grossmann_vxx_sma", R7.grossmann_vxx_sma, "Strategies for Trading Inverse Volatility, Grossmann Dec 2013 (250728698): 5/15 SMA variant")):
        rows.append(evaluate(key, doc, lambda b, c, s0, s1, fn=fn: fn(b, 0, c, s0, s1), vixy, {"VIXY": 0.0005}, wv, single=True,
                             extra=lambda per: {"curve": curves(per, wv, None), "note": "VIXY tracks the same index as VXX; short borrow fee not modelled"}))
    spy = {"SPY": load(ETF / "SPY_D1.npy")}
    wf = {"a": (ms("1993-02-01"), ms("2010-01-01")), "b": (ms("2010-01-01"), ms("2026-10-21"))}
    rows.append(evaluate("fosback_seasonal", "The Amazing Story of Stock Market Seasonality, Minton (40367053): Fosback's Seasonality Indicator",
                         lambda b, c, s0, s1: R7.fosback_seasonal(b, 0, c, s0, s1), spy, {"SPY": 0.0001}, wf, single=True,
                         extra=lambda per: {"curve": curves(per, wf, "SPY"),
                                            "random_timing": {w: random_timing([dict(x, asset="SPY") for x in per[w]], spy, {"SPY": 0.0001}, *wf[w]) for w in wf},
                                            "leverage_2010_2026": leverage(R7.fosback_seasonal, spy["SPY"], "UPRO")}))
    dia = {"DIA": load(ETF / "DIA_D1.npy")}
    wk = {"a": (ms("1998-02-01"), ms("2009-01-01")), "b": (ms("2009-01-01"), ms("2026-10-21"))}
    rows.append(evaluate("kaeppel_monthly10", "The Amazing Story of Stock Market Seasonality, Minton (40367053): Kaeppel's Monthly 10",
                         lambda b, c, s0, s1: R7.kaeppel_monthly10(b, 0, c, s0, s1), dia, {"DIA": 0.0001}, wk, single=True,
                         extra=lambda per: {"curve": curves(per, wk, "DIA"),
                                            "random_timing": {w: random_timing([dict(x, asset="DIA") for x in per[w]], dia, {"DIA": 0.0001}, *wk[w]) for w in wk},
                                            "leverage_2010_2026": leverage(R7.kaeppel_monthly10, dia["DIA"], "UDOW")}))
    etfs = {s: d for s in R6.PAGONIDIS_ETFS if (d := load(ETF / f"{s}_D1.npy")) is not None}
    wl = {"a": (ms("1995-01-01"), ms("2002-01-01")), "b": (ms("2002-01-01"), ms("2026-10-21"))}
    rows.append(evaluate("lunar_phase", "Are Investors Moonstruck? Lunar Phases and Stock Returns, Yuan, Zheng & Zhu 2001 (372211422)",
                         lambda b, c, s0, s1: R7.lunar_phase(b, 0, c, s0, s1), etfs, {s: LIQUID.get(s, 0.0005) for s in etfs}, wl))
    for r in rows:
        if "curve" in r:
            print(" ", r["key"], {w: {"rule_cagr": round(v["rule"].get("cagr_pct", 0), 1), "rule_dd": round(v["rule"].get("max_dd_pct", 0), 1),
                                      **({"bh_cagr": round(v["buy_and_hold"]["cagr_pct"], 1), "bh_dd": round(v["buy_and_hold"]["max_dd_pct"], 1),
                                          "in_mkt": round(v["time_in_market_pct"], 0)} if "buy_and_hold" in v else {})} for w, v in r["curve"].items()})
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pre_registration": "docs/SCRIBD_ROUND7_STUDY.md",
           "costs": {"etf": "0.01% per side SPY/DIA/QQQ/IWM, 0.05% country ETFs and VIXY (no short borrow fee)"}, "rules": rows,
           "headline": ("Both calendar rules pass the fixed test (Fosback seasonality on SPY, PF 1.56 after publication; Kaeppel's Monthly 10 on DIA, PF 1.57), "
                        "but after publication they beat random entry days only weakly (z 0.5 and 1.0): most of their profit is simply being invested in a rising market, "
                        "and they return less than buy and hold (4.9% and 8.2% a year vs 14% and 13%). Grossmann's VXX-short rules (claimed 96% a year) "
                        "fell from 53–71% a year before publication to 9–16% after, with 45–65% drawdowns. The lunar effect vanished after 2001. "
                        "Best winner stays round 3's IBS rule on QQQ.")}
    OUT.write_text(json.dumps(out, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
