"""Scribd scalping study (pre-registered in docs/SCRIBD_SCALPING_STUDY.md).

    python scripts/scribd_study.py [--only key,key]

Data: data/mt5_cache/<SYM>_<M1|M5|M15>.npy (scripts/mt5_export_tf.py, UTC) and Binance perpetuals for crypto.
Writes app/ytstrats/scribd_results.json (served read-only by /api/public/scribd-strategies).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ytstrats import engine, scalp, scribd  # noqa: E402

ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
FULL = {"a": (ms("2024-10-20"), ms("2025-10-20")), "b": (ms("2025-10-20"), ms("2026-10-20"))}
M5W = {"a": (ms("2025-06-24"), ms("2026-02-19")), "b": (ms("2026-02-19"), ms("2026-10-20"))}
M1W = {"a": (ms("2026-07-16"), ms("2026-09-01")), "b": (ms("2026-09-01"), ms("2026-10-20"))}
MT5 = PROJECT / "data" / "mt5_cache"
CRYPTO = PROJECT / "data" / "yt_strats_cache"
OUT = PROJECT / "app" / "ytstrats" / "scribd_results.json"
MT5_SLIP, MT5_COMM = 0.00002, 0.000025
CRYPTO_COST = 0.0005 + 0.0002


def aggregate(a: np.ndarray, minutes: int, base: int) -> np.ndarray:
    if minutes == base:
        return a[:, :6]
    step, per = minutes * 60_000, minutes // base
    key = (a[:, 0] // step).astype(np.int64)
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], len(a)]
    rows = [(key[s] * step, a[s, 1], a[s:e, 2].max(), a[s:e, 3].min(), a[e - 1, 4], a[s:e, 5].sum())
            for s, e in zip(starts, ends) if e - s == per]
    return np.array(rows, dtype=float)


def btc_5m() -> np.ndarray:
    f = CRYPTO / "BTCUSDT_5m.npy"
    if not f.exists():
        from app.autoresearch.client import Client
        c = Client(threading.Event())
        rows, cur, end = {}, ms("2024-09-01"), ms("2026-10-20")
        while cur < end:
            part = c.get("perp", "klines", {"symbol": "BTCUSDT", "interval": "5m", "startTime": cur, "endTime": end - 1, "limit": 1500}, weight=10)
            if not part:
                break
            for r in part:
                rows[int(r[0])] = (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            nxt = int(part[-1][0]) + 300_000
            if nxt <= cur:
                break
            cur = nxt
        np.save(f, np.array([rows[k] for k in sorted(rows)], dtype=float))
    return np.load(f)


def load(asset: str, tf: int) -> tuple[np.ndarray, float, str]:
    """(bars t,o,h,l,c,v at tf minutes, per-side cost share, window set)."""
    if asset in scribd.PIP:                                              # MT5 forex / gold
        src = {1: "M1", 5: "M5", 15: "M15", 60: "M15"}[tf]
        a = np.load(MT5 / f"{asset}_{src}.npy")
        base = {"M1": 1, "M5": 5, "M15": 15}[src]
        bars = aggregate(a, tf, base)
        m15 = np.load(MT5 / f"{asset}_M15.npy")
        spread_frac = float(np.mean(m15[:, 6] / m15[:, 4]))
        cost = spread_frac / 2 + MT5_SLIP + MT5_COMM
        # the full two-year windows whenever the export covers them (MT5's bar cap lifted), else the preliminary split
        win = "full" if (tf >= 15 or bars[0, 0] <= FULL["a"][0] + 3 * 86_400_000) else ("m5" if tf == 5 else "m1")
        return bars, cost, win
    if tf == 5 and asset == "BTC":
        return btc_5m()[:, :6], CRYPTO_COST, "full"
    a = np.load(CRYPTO / f"{asset}USDT_15m.npy")
    return aggregate(a, tf, 15), CRYPTO_COST, "full"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    windows = {"full": FULL, "m5": M5W, "m1": M1W}
    rows = []
    for st in scribd.STRATEGIES:
        if only and st.key not in only:
            continue
        per = {"a": [], "b": []}
        gross = {"a": [], "b": []}                                       # the same trades with zero costs
        by_asset: dict[str, float | None] = {}
        win_name = None
        for asset in st.assets:
            bars, cost, win_name = load(asset, st.tf)
            b = scribd.Bars(bars, st.tf)
            plan = st.fn(b, scribd.PIP.get(asset, 0.0))
            for w, (s0, s1) in windows[win_name].items():
                tr = scalp.replay(b.t, b.o, b.h, b.l, b.c, plan, s0, s1, cost)
                for x in tr:
                    x["asset"] = asset
                per[w] += tr
                gross[w] += scalp.replay(b.t, b.o, b.h, b.l, b.c, plan, s0, s1, 0.0)
                if w == "b":
                    by_asset[asset] = float(sum(x["ret_pct"] for x in tr)) if tr else None
        sa, sb = scalp.summarize(per["a"]), scalp.summarize(per["b"])
        low = engine.bootstrap_low(np.array([x["ret_pct"] for x in per["b"]]))
        traded = [v for v in by_asset.values() if v is not None]
        min_b = 50 if win_name == "m1" else 100
        checks = {
            "a_positive": bool(sa.get("trades") and sa["mean_ret_pct"] > 0 and (sa["profit_factor"] or 0) >= 1.10),
            "b_positive": bool(sb.get("trades") and sb["mean_ret_pct"] > 0 and (sb["profit_factor"] or 0) >= 1.15 and sb["trades"] >= min_b),
            "b_ci_above_zero": bool(low > 0),
            "b_half_the_assets": bool(traded and sum(1 for v in traded if v > 0) >= len(traded) / 2),
        }
        passed = all(checks.values())
        verdict = ("PASS" if passed else "FAIL") + ("" if win_name == "full" else " (PRELIMINARY: " + win_name.upper() + " data)")
        w = windows[win_name]
        fmt = lambda x: datetime.fromtimestamp(x / 1000, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
        row = {"key": st.key, "doc": st.doc, "assets": list(st.assets), "tf_min": st.tf, "note": st.note, "rule": (st.fn.__doc__ or "").strip(),
               "windows": {k: [fmt(v[0]), fmt(v[1])] for k, v in w.items()}, "window_set": win_name,
               "a": sa, "b": {**sb, "mean_ci_low": low, "by_asset_ret_pct": by_asset},
               "gross": {"a": scalp.summarize(gross["a"]), "b": scalp.summarize(gross["b"])},
               "cost_per_side_pct": round(cost * 100, 5), "checks": checks, "verdict": verdict,
               "recent_b_trades": sorted(per["b"], key=lambda x: x["t"])[-25:]}
        rows.append(row)
        f2 = lambda s: f"{s.get('trades', 0):5d} tr WR {s.get('win_rate_pct', 0):5.1f}% mean {s.get('mean_ret_pct', 0):+.4f}% PF {s.get('profit_factor') or 0:4.2f}"  # noqa: E731
        gb = row["gross"]["b"]
        print(f"{st.key:<22} {st.tf:>3}m {win_name:>4} | A {f2(sa)} | B {f2(sb)} | B gross {gb.get('mean_ret_pct', 0):+.4f}% | {verdict}", flush=True)
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pre_registration": "docs/SCRIBD_SCALPING_STUDY.md",
           "costs": {"mt5": "half the mean 15m spread + 0.002% slippage + 0.0025% commission per side", "crypto": "0.05% + 0.02% per side"},
           "strategies": rows}
    OUT.write_text(json.dumps(out, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
