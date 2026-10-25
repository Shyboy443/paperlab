"""Export candles of any timeframe from the operator's logged-in MetaTrader 5 terminal, month by month (read-only).

    python scripts/mt5_export_tf.py --tf M5 --symbols EURUSD,GBPUSD --start 2024-10-01 [--end 2026-10-20]

Writes data/mt5_cache/<SYM>_<tf>[tag].npy (t_ms in UTC, o, h, l, c, tick_volume, spread in price). Server time (Fusion Markets:
UTC+2, UTC+3 while US daylight saving is on) is converted to UTC. History depth is capped by MT5's "Max. bars in chart".
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
TERMINAL = r"C:\Program Files\MetaTrader 5\terminal64.exe"


def main() -> int:
    import MetaTrader5 as mt5
    from scripts.gold_scalp_study import us_dst
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", required=True)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--start", default="2024-10-01")
    ap.add_argument("--end", default="2026-10-21")
    ap.add_argument("--tag", default="", help="suffix for the file name, e.g. L -> <SYM>_<tf>L.npy")
    a = ap.parse_args()
    tfc = getattr(mt5, "TIMEFRAME_" + a.tf)
    utc = dt.timezone.utc
    t0, t1 = (dt.datetime.fromisoformat(x).replace(tzinfo=utc) for x in (a.start, a.end))
    out = PROJECT / "data" / "mt5_cache"
    out.mkdir(parents=True, exist_ok=True)
    if not mt5.initialize(path=TERMINAL):
        raise SystemExit(f"MT5 terminal not available: {mt5.last_error()}")
    try:
        for sym in a.symbols.split(","):
            if not mt5.symbol_select(sym, True):
                print("missing", sym)
                continue
            point = mt5.symbol_info(sym).point
            parts, cur = [], t0
            while cur < t1:
                nxt = min((cur.replace(day=1) + dt.timedelta(days=32)).replace(day=1), t1)
                r = mt5.copy_rates_range(sym, tfc, cur, nxt)
                if r is not None and len(r):
                    parts.append(np.column_stack([r["time"].astype(float) * 1000, r["open"], r["high"], r["low"], r["close"],
                                                  r["tick_volume"].astype(float), r["spread"].astype(float) * point]))
                cur = nxt
            if not parts:
                print(sym, a.tf, "no data")
                continue
            arr = np.concatenate(parts)
            arr = arr[np.unique(arr[:, 0], return_index=True)[1]]
            arr[:, 0] -= np.where(us_dst(arr[:, 0] - 3 * 3_600_000), 3, 2) * 3_600_000          # server time -> UTC
            np.save(out / f"{sym}_{a.tf}{a.tag}.npy", arr)
            f = lambda x: dt.datetime.fromtimestamp(x / 1000, utc).strftime("%Y-%m-%d")  # noqa: E731
            print(f"{sym} {a.tf}: {len(arr)} bars {f(arr[0, 0])} -> {f(arr[-1, 0])}, mean spread {arr[:, 6].mean():.5g}", flush=True)
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
