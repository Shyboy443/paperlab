"""Shared: load events, map ticker -> Binance USDT perp in the survivorship-free 1h cache (agent 07, incl. delisted),
and measure post-announcement returns. Entry = open of the first full hour after the announcement time
(floor(ts/1h)+1), i.e. 0-60 min latency."""
import json, sys
from functools import lru_cache
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import FEE, SLIP, DEV_START, DEV_END, TEST_START, TEST_END, HOUR, DAY, DATA_FROM
V07 = Path(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_07_pump_scanner")
EV = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r3_exchange_events/events.json")
ALIAS = {"RNDR": "RENDER", "MATIC": "POL", "LUNA": "LUNA2"}


@lru_cache(None)
def bars(sym):
    f = V07 / "1h" / f"{sym}.json"
    if not f.exists():
        return None
    a = np.array([r[:7] for r in json.loads(f.read_text())], dtype=float)
    return a  # t,o,h,l,c,v,qv


@lru_cache(None)
def fund(sym):
    f = V07 / "funding" / f"{sym}.json"
    if f.exists():
        x = json.loads(f.read_text())
        if x:
            return np.array(x, dtype=float)
    return np.zeros((0, 2))


def perp_for(tk, ts):
    """the USDT perp of ticker tk that has a bar at the entry hour; None if none."""
    eb = (ts // HOUR + 1) * HOUR
    for s in (f"{tk}USDT", f"1000{tk}USDT", f"1000000{tk}USDT", f"{ALIAS.get(tk, tk)}USDT"):
        a = bars(s)
        if a is None:
            continue
        i = np.searchsorted(a[:, 0], eb)
        if i < len(a) and a[i, 0] == eb:
            return s, a, i
    return None


def load_events():
    return [tuple(e) for e in json.loads(EV.read_text(encoding="utf-8"))]


def measure(typ_set, hours=(1, 4, 12, 24, 48, 72, 168, 336), t0=DEV_START, t1=DEV_END, min_age_h=24, min_qv=0.0):
    """one row per (type, ts, ticker) event with a tradable perp. Raw and BTC-relative log... simple returns from entry open."""
    btc = bars("BTCUSDT")
    rows, seen = [], set()
    for typ, ts, tk, title in load_events():
        if typ not in typ_set or not (t0 <= ts < t1):
            continue
        key = (typ, ts // DAY, tk)
        if key in seen:
            continue
        seen.add(key)
        p = perp_for(tk, ts)
        if p is None:
            continue
        s, a, i = p
        if i < min_age_h:
            continue
        qv24 = np.nansum(a[max(0, i - 24):i, 6])
        if qv24 < min_qv:
            continue
        bi = np.searchsorted(btc[:, 0], a[i, 0])
        r = {"typ": typ, "ts": ts, "tk": tk, "sym": s, "i": i, "qv24": qv24,
             "pre24": a[i, 1] / a[max(0, i - 24), 1] - 1}
        for h in hours:
            j = min(i + h, len(a) - 1)
            px = a[j, 1] if i + h < len(a) else a[-1, 4]
            bj = min(bi + h, len(btc) - 1)
            r[f"r{h}"] = px / a[i, 1] - 1
            r[f"x{h}"] = r[f"r{h}"] - (btc[bj, 1] / btc[bi, 1] - 1)
            r[f"cut{h}"] = i + h >= len(a)
        rows.append(r)
    return rows
