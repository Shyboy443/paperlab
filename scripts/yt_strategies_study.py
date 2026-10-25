"""YouTube strategy ranking study (pre-registered in docs/YT_STRATEGIES_STUDY.md).

    python scripts/yt_strategies_study.py [--cache DIR] [--only key,key]

Public Binance USD-M klines only. Writes app/ytstrats/results.json (served read-only by /api/public/yt-strategies).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.autoresearch.client import Client  # noqa: E402
from app.ytstrats import engine, rules  # noqa: E402

ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
WARM, Y1, Y2, END = ms("2024-08-01T00:00"), ms("2024-10-20T00:00"), ms("2025-10-20T00:00"), ms("2026-10-20T00:00")
COINS = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "TRX", "BCH", "NEAR", "ATOM", "ETC",
         "FIL", "APT", "ARB", "OP")
M15 = 15 * 60_000
OUT = PROJECT / "app" / "ytstrats" / "results.json"


MT5 = ("XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
       "US30", "US500", "NAS100", "UK100", "GER40")           # the operator's MT5 terminal (scripts/mt5_export.py)
INDICES = {"US30", "US500", "NAS100", "UK100", "GER40"}
MT5_COMMISSION, MT5_SLIP = 0.000025, 0.00002     # raw-spread account: ~2.25 USD per lot per side (FX, metals); +0.002 % slip


def mt5_costs(sym: str, spec: dict) -> tuple[float, float]:
    """(fee, slip) per side as a share of the price: commission (none on index CFDs) and half the mean recorded spread
    plus slippage."""
    half_spread = spec["symbols"][sym]["spread_mean_frac"] / 2
    return (0.0 if sym in INDICES else MT5_COMMISSION), half_spread + MT5_SLIP


def fetch_15m(c: Client, sym: str, end: int, venue: str = "perp") -> np.ndarray:
    rows, cur = {}, WARM
    while cur < end:
        part = c.get(venue, "klines", {"symbol": sym, "interval": "15m", "startTime": cur, "endTime": end - 1, "limit": 1500}, weight=10)
        if not part:
            break
        for r in part:
            if int(r[0]) + M15 <= end:
                rows[int(r[0])] = (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
        nxt = int(part[-1][0]) + M15
        if nxt <= cur:
            break
        cur = nxt
    return np.array([rows[t] for t in sorted(rows)], dtype=float)


def aggregate(a: np.ndarray, minutes: int) -> np.ndarray:
    """15m rows (t,o,h,l,c,v) -> complete bars of `minutes` (a bar with a missing 15m piece is dropped)."""
    if minutes == 15:
        return a
    step, per = minutes * 60_000, minutes // 15
    key = (a[:, 0] // step).astype(np.int64)
    out = []
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], len(a)]
    for s, e in zip(starts, ends):
        if e - s != per:
            continue
        g = a[s:e]
        out.append((key[s] * step, g[0, 1], g[:, 2].max(), g[:, 3].min(), g[-1, 4], g[:, 5].sum()))
    return np.array(out, dtype=float)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(PROJECT / "data" / "yt_strats_cache"))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    c = Client(threading.Event())
    end = min(END, int(time.time() * 1000) // M15 * M15)
    data: dict[str, dict] = {}

    def load(name: str, raw: np.ndarray) -> None:
        data[name] = {tf: aggregate(raw, tf) for tf in (15, 30, 60, 240)}
        data[name]["fine"] = (raw[:, 0], raw[:, 2], raw[:, 3])

    for coin in COINS:
        f = cache / f"{coin}USDT_15m.npy"
        if not f.exists():
            np.save(f, fetch_15m(c, coin + "USDT", end))
            print("fetched", coin, flush=True)
        load(coin, np.load(f))
    mt5_dir = PROJECT / "data" / "mt5_cache"
    spec = json.loads((mt5_dir / "spec.json").read_text())
    for sym in MT5:
        load(sym, np.load(mt5_dir / f"{sym}_15m.npy"))
    costs = {**{coin: (engine.FEE, engine.SLIP) for coin in COINS}, **{sym: mt5_costs(sym, spec) for sym in MT5}}
    only = set(filter(None, a.only.split(",")))
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "markets": {"crypto": {"assets": COINS, "source": "Binance USD-M perpetuals", "costs": "0.05% taker + 0.02% slippage per side"},
                       "mt5": {"assets": MT5, "source": f"MT5 {spec['server']} (operator's terminal, read-only)",
                               "costs": "half the mean recorded spread + 0.002% slippage per side, + 0.0025% commission (not on indices)"},
                       "forex": {"assets": rules.FOREX, "source": f"MT5 {spec['server']}", "costs": "as mt5"}},
           "windows": {"y1": ["2024-10-20", "2025-10-20"], "y2": ["2025-10-20", datetime.fromtimestamp(end / 1000, timezone.utc).strftime("%Y-%m-%d")]},
           "pre_registration": "docs/YT_STRATEGIES_STUDY.md", "strategies": []}
    fmt = lambda s: f"{s.get('trades', 0):5d} tr  WR {s.get('win_rate_pct', 0):5.1f}%  {s.get('goa_per_100', 0):+7.1f}R/100"  # noqa: E731
    for st in rules.STRATEGIES:
        if only and st.key not in only:
            continue
        markets = {"forex": st.assets} if st.assets else {"crypto": COINS, "mt5": MT5}   # a named asset: only that
        for market, universe in markets.items():
            per = {"y1": [], "y2": []}
            by_asset: dict = {}
            for name in universe:
                b = data[name][st.tf]
                t, o, h, l, cl, v = (b[:, k] for k in range(6))
                sig, stop = st.fn(o, h, l, cl, v)
                fee, slip = costs[name]
                for w, (s0, s1) in (("y1", (Y1, Y2)), ("y2", (Y2, end))):
                    tr = engine.replay(t, o, h, l, cl, sig, stop, st.targets, s0, s1, fee=fee, slip=slip,
                                       fine=data[name]["fine"] if st.tf > 15 else None, tf_ms=st.tf * 60_000)
                    for x in tr:
                        x["asset"] = name
                    per[w] += tr
                    if w == "y2":
                        by_asset[name] = float(sum(x["net_r"] for x in tr)) if tr else None
            s1, s2 = engine.summarize(per["y1"]), engine.summarize(per["y2"])
            g1, g2 = engine.summarize(per["y1"], "gross_r"), engine.summarize(per["y2"], "gross_r")
            low = engine.bootstrap_low(np.array([x["net_r"] for x in per["y2"]]))
            traded = [v for v in by_asset.values() if v is not None]
            checks = {
                "y1_positive": bool(s1.get("trades") and s1["mean_r"] > 0 and (s1["profit_factor"] or 0) >= 1.10),
                "y2_positive": bool(s2.get("trades") and s2["mean_r"] > 0 and (s2["profit_factor"] or 0) >= 1.15 and s2["trades"] >= 100),
                "y2_ci_above_zero": bool(low > 0),
                "y2_half_the_assets": bool(traded and sum(1 for v in traded if v > 0) >= len(traded) / 2),
            }
            row = {"key": st.key, "market": market, "title": st.title, "tf_min": st.tf, "assets": list(universe),
                   "targets": st.targets, "rule": st.rule, "claim": {"win_rate_pct": st.claim_wr, "goa_per_100": st.claim_goa},
                   "y1": {"net": s1, "gross": g1}, "y2": {"net": s2, "gross": g2, "mean_r_ci_low": low, "by_asset_net_r": by_asset},
                   "checks": checks, "verdict": "PROVEN (candidate for forward test)" if all(checks.values()) else "FAILED",
                   "recent_y2_trades": sorted(per["y2"], key=lambda x: x["t"])[-30:]}
            out["strategies"].append(row)
            print(f"{st.key:<18} {market:<6} {st.tf:>3}m claim WR {st.claim_wr:>3}% GOA {st.claim_goa:+6.1f} | Y2 gross {fmt(g2)} | "
                  f"Y2 net {fmt(s2)} PF {s2.get('profit_factor') or 0:4.2f} | Y1 net {s1.get('goa_per_100', 0):+7.1f}R/100 | {row['verdict']}",
                  flush=True)
    OUT.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
