import sys
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import *
import numpy as np, pandas as pd
from zoneinfo import ZoneInfo
from events import FOMC, CPI

NY = ZoneInfo("America/New_York")
SYMS_ALL = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT",
        "LTCUSDT", "DOTUSDT", "TRXUSDT"]
import os
SYMS = [s for s in SYMS_ALL if (CACHE / "perp" / "1h" / f"{s}.json").exists()] if os.environ.get("PRELIM") else SYMS_ALL
ALTS = SYMS[2:]


def frame(sym):
    k = klines(sym, "1h")
    df = pd.DataFrame(k, columns=["t", "o", "h", "l", "c", "v", "qv", "n", "tbq"])
    df["ts"] = pd.to_datetime(df.t, unit="ms", utc=True)
    df = df.set_index("ts")
    df["r"] = df.o.shift(-1) / df.o - 1          # return earned holding bar t, filled at its open
    return df


def ny_time(utc_date_str, hh, mm):
    """UTC timestamp of hh:mm New York time on that date."""
    y, m, d = map(int, utc_date_str.split("-"))
    return pd.Timestamp(datetime(y, m, d, hh, mm, tzinfo=NY)).tz_convert("UTC")


def calendar(idx):
    """Per-hour calendar features for a UTC hourly index. All are known in advance (schedules)."""
    c = pd.DataFrame(index=idx)
    c["hour"] = idx.hour
    c["dow"] = idx.dayofweek
    c["dom"] = idx.day
    c["dim"] = idx.days_in_month
    ny = idx.tz_convert(NY)
    c["ny_hour"] = ny.hour
    c["ny_min_bar"] = ny.hour * 60 + ny.minute
    # US cash session 09:30-16:00 NY on weekdays (bar whose hour contains it)
    c["us_open_bar"] = (ny.hour == 9) & (ny.dayofweek < 5)
    c["us_session"] = (ny.hour >= 10) & (ny.hour < 16) & (ny.dayofweek < 5)
    # hours to next CPI / FOMC release
    for name, dates, hh, mm in [("cpi", CPI, 8, 30), ("fomc", FOMC, 14, 0)]:
        ev = pd.DatetimeIndex([ny_time(d, hh, mm) for d in dates]).floor("h")
        pos = np.searchsorted(ev.values, idx.values, side="left")
        nxt = np.where(pos < len(ev), (ev.values[np.minimum(pos, len(ev) - 1)] - idx.values) / np.timedelta64(1, "h"), 1e9)
        prv = np.where(pos > 0, (idx.values - ev.values[np.maximum(pos - 1, 0)]) / np.timedelta64(1, "h"), 1e9)
        # an event bar itself counts as "hours since = 0"
        same = nxt == 0
        c[f"{name}_to"] = np.where(same, 1e9, nxt)
        c[f"{name}_since"] = np.where(same, 0, prv)
    # Deribit expiry: Fridays 08:00 UTC; month-end = last Friday of month
    c["friday"] = idx.dayofweek == 4
    c["last_friday"] = c.friday & (idx.day + 7 > idx.days_in_month)
    return c
