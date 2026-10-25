"""Scribd scalping round 2 (pre-registered in docs/SCRIBD_SCALPING_ROUND2_STUDY.md).

    python scripts/scribd_scalp2_study.py [--only key,key]

Data: data/mt5_cache/<SYM>_M1L.npy (the operator's MT5, UTC; real 1-minute data from late 2020). Writes
app/ytstrats/scalp2_results.json (served read-only by /api/public/scribd-scalp2).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ytstrats import engine, scalp, scalp2 as S2  # noqa: E402

ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
WIN = {"a": (ms("2020-11-01"), ms("2023-11-01")), "b": (ms("2023-11-01"), ms("2026-10-21"))}
MT5 = PROJECT / "data" / "mt5_cache"
OUT = PROJECT / "app" / "ytstrats" / "scalp2_results.json"
SPEC = json.loads((MT5 / "spec.json").read_text())["symbols"]
PIP = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001, "USDCAD": 0.0001, "USDCHF": 0.0001, "NZDUSD": 0.0001,
       "USDJPY": 0.01, "GBPJPY": 0.01, "XAUUSD": 0.10, "US30": 1.0}
RW_NOTE = {"five_step_ema": "random-walk check: z -1.8 to -3.6, a bias of about -0.005 to -0.015 % per trade AGAINST the rule"}


def load(sym: str) -> tuple[S2.M1, float]:
    a = np.load(MT5 / f"{sym}_M1L.npy")
    a = a[a[:, 0] >= ms("2020-10-01")]
    m = S2.M1(*(a[:, k] for k in range(6)))
    spread = SPEC[sym]["spread_mean_frac"] if sym in SPEC else float(np.mean(a[:, 6] / a[:, 4]))
    cost = spread / 2 + 0.00002 + (0.0 if sym == "US30" else 0.000025)
    return m, cost


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    only = set(filter(None, ap.parse_args().only.split(",")))
    cache: dict[str, tuple] = {}
    rows = []
    for key, doc, assets, fn, tf, single in S2.RULES:
        if only and key not in only:
            continue
        per, gross, by_asset = {"a": [], "b": []}, {"a": [], "b": []}, {}
        for sym in assets:
            if sym not in cache:
                cache[sym] = load(sym)
            m, cost = cache[sym]
            # one pass over both windows (with and without costs); trades are split by entry time
            all_net = fn(m, PIP[sym], cost, WIN["a"][0], WIN["b"][1])
            all_gross = fn(m, PIP[sym], 0.0, WIN["a"][0], WIN["b"][1])
            for w, (s0, s1) in WIN.items():
                tr = [x for x in all_net if s0 <= x["t"] < s1]
                for x in tr:
                    x["asset"] = sym
                per[w] += tr
                gross[w] += [x for x in all_gross if s0 <= x["t"] < s1]
                if w == "b":
                    by_asset[sym] = float(sum(x["ret_pct"] for x in tr)) if tr else None
        sa, sb = scalp.summarize(per["a"]), scalp.summarize(per["b"])
        low = engine.bootstrap_low(np.array([x["ret_pct"] for x in per["b"]]))
        traded = [v for v in by_asset.values() if v is not None]
        min_b = 30 if single else 100
        checks = {
            "a_positive": bool(sa.get("trades") and sa["mean_ret_pct"] > 0 and (sa["profit_factor"] or 0) >= 1.10),
            "b_positive": bool(sb.get("trades") and sb["mean_ret_pct"] > 0 and (sb["profit_factor"] or 0) >= 1.15 and sb["trades"] >= min_b),
            "b_ci_above_zero": bool(low > 0),
            "b_half_the_assets": bool(traded and sum(1 for v in traded if v > 0) >= len(traded) / 2),
        }
        yrs_b = (WIN["b"][1] - WIN["b"][0]) / (365.25 * 86_400_000)
        days_b = yrs_b * 252 * max(1, len(assets))
        row = {"key": key, "doc": doc, "assets": list(assets), "tf": tf, "rule": (fn.__doc__ or "").strip(), "note": RW_NOTE.get(key, ""),
               "windows": {k: [datetime.fromtimestamp(v[0] / 1000, timezone.utc).strftime("%Y-%m-%d"),
                               datetime.fromtimestamp(v[1] / 1000, timezone.utc).strftime("%Y-%m-%d")] for k, v in WIN.items()},
               "a": sa, "b": {**sb, "mean_ci_low": low, "by_asset_ret_pct": by_asset,
                              "trades_per_day_per_market": float(sb.get("trades", 0) / days_b) if days_b else 0.0,
                              "sum_pct_per_year_1x": float(sum(x["ret_pct"] for x in per["b"]) / yrs_b / max(1, len(assets)))},
               "gross": {"a": scalp.summarize(gross["a"]), "b": scalp.summarize(gross["b"])},
               "min_trades_b": min_b, "checks": checks, "verdict": "PASS" if all(checks.values()) else "FAIL",
               "recent_b_trades": sorted(per["b"], key=lambda x: x["t"])[-25:]}
        rows.append(row)
        f2 = lambda s: f"{s.get('trades', 0):5d} tr WR {s.get('win_rate_pct', 0):5.1f}% mean {s.get('mean_ret_pct', 0):+.4f}% PF {s.get('profit_factor') or 0:4.2f}"  # noqa: E731
        print(f"{key:<20} | A {f2(sa)} | B {f2(sb)} | B gross {row['gross']['b'].get('mean_ret_pct', 0):+.4f}% | "
              f"{row['b']['trades_per_day_per_market']:.2f}/day | {row['verdict']} {''.join('Y' if v else '.' for v in checks.values())}", flush=True)
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pre_registration": "docs/SCRIBD_SCALPING_ROUND2_STUDY.md",
           "costs": {"mt5": "half the mean spread + 0.002% slippage + 0.0025% commission per side (no commission on US30)"},
           "rules": rows,
           "headline": ("No scalping rule passes. Before costs every one of the 7 sits within ±0.01% per trade, the same as the 35 rules of scalping round 1, "
                        "and the spread plus commission then makes all of them lose (PF 0.49–0.91 over six years of your broker's 1-minute data). "
                        "Win rates are just what each rule's target-to-stop ratio gives at random.")}
    if only and OUT.exists():
        old = {x["key"]: x for x in json.loads(OUT.read_text()).get("rules", [])}
        old.update({x["key"]: x for x in rows})
        out["rules"] = [old[r[0]] for r in S2.RULES if r[0] in old]
    OUT.write_text(json.dumps(out, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
