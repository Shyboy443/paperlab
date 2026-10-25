"""Export 15-minute candles + spread statistics from the operator's locally logged-in MetaTrader 5 terminal (read-only:
no orders, no account changes, no credentials touched). Windows only (the MetaTrader5 package attaches to the running
terminal). Writes data/mt5_cache/<SYMBOL>_15m.npy (t_ms, o, h, l, c, tick_volume) and data/mt5_cache/spec.json.

    python scripts/mt5_export.py [--start 2024-08-01] [--end 2026-10-20]

Times are the broker's server time (as MT5 shows them), stored as epoch milliseconds.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
SYMBOLS = ("XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
           "US30", "US500", "NAS100", "UK100", "GER40")
TERMINAL = r"C:\Program Files\MetaTrader 5\terminal64.exe"


def main() -> int:
    import MetaTrader5 as mt5
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-08-01")
    ap.add_argument("--end", default="2026-10-20")
    ap.add_argument("--m1", default="", help="also export 1-minute candles of this symbol, month by month (needs MT5's "
                                             "Tools > Options > Charts > Max. bars in chart = Unlimited for long history)")
    a = ap.parse_args()
    utc = dt.timezone.utc
    t0 = dt.datetime.fromisoformat(a.start).replace(tzinfo=utc)
    t1 = dt.datetime.fromisoformat(a.end).replace(tzinfo=utc)
    out = PROJECT / "data" / "mt5_cache"
    out.mkdir(parents=True, exist_ok=True)
    if not mt5.initialize(path=TERMINAL):
        raise SystemExit(f"MT5 terminal not available: {mt5.last_error()}")
    spec = {"server": mt5.account_info().server, "exported_utc": dt.datetime.now(utc).isoformat(timespec="seconds"), "symbols": {}}
    try:
        for s in SYMBOLS:
            if not mt5.symbol_select(s, True):
                print("missing", s)
                continue
            info = mt5.symbol_info(s)
            r = mt5.copy_rates_range(s, mt5.TIMEFRAME_M15, t0, t1)
            if r is None or not len(r):
                print("no data", s)
                continue
            arr = np.column_stack([r["time"].astype(float) * 1000, r["open"], r["high"], r["low"], r["close"], r["tick_volume"].astype(float)])
            np.save(out / f"{s}_15m.npy", arr)
            sp = r["spread"].astype(float) * info.point / r["close"]               # spread as a share of the price
            spec["symbols"][s] = {"bars": int(len(r)), "point": info.point, "contract": info.trade_contract_size,
                                  "spread_mean_frac": float(sp.mean()), "spread_p90_frac": float(np.percentile(sp, 90)),
                                  "first": int(arr[0, 0]), "last": int(arr[-1, 0])}
            print(f"{s:7} {len(r)} bars, mean spread {sp.mean() * 100:.4f}% of price")
        if a.m1:
            mt5.symbol_select(a.m1, True)
            info = mt5.symbol_info(a.m1)
            parts, cur = [], t0
            while cur < t1:
                nxt = (cur.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
                r = mt5.copy_rates_range(a.m1, mt5.TIMEFRAME_M1, cur, min(nxt, t1))
                if r is not None and len(r):
                    parts.append(np.column_stack([r["time"].astype(float) * 1000, r["open"], r["high"], r["low"], r["close"],
                                                  r["tick_volume"].astype(float), r["spread"].astype(float) * info.point]))
                print(f"{a.m1} M1 {cur:%Y-%m}: {0 if r is None else len(r)} bars", flush=True)
                cur = nxt
            if parts:
                arr = np.concatenate(parts)
                arr = arr[np.unique(arr[:, 0], return_index=True)[1]]
                np.save(out / f"{a.m1}_m1_full.npy", arr)          # t (server time), o, h, l, c, tick volume, spread (price)
    finally:
        mt5.shutdown()
    (out / "spec.json").write_text(json.dumps(spec, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
