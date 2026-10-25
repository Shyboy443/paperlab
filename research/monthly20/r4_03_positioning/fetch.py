"""Download UM-futures daily `metrics` files from data.binance.vision (public archive, no keys) for every coin that was
ever in the point-in-time top-40 liquid universe (survivorship-free panel of r2_rotation, delisted coins included),
only for the days it was in the universe (+40-day lookback pad). Each day's 5-minute rows are reduced to one record:
  oi_last, oiv_last (last 5m snapshot of the day), top_acc (count_toptrader_long_short_ratio, day mean),
  top_pos (sum_toptrader_long_short_ratio, day mean), glob_acc (count_long_short_ratio, day mean),
  taker (sum_taker_long_short_vol_ratio, day mean), n (rows)
Cache: data/monthly20_r4_positioning/daily/SYM.json  {"YYYY-MM-DD": [oi, oiv, top_acc, top_pos, glob_acc, taker, n]}
       data/monthly20_r4_positioning/listing/SYM.json  (available archive days)
"""
import csv, io, json, re, sys, time, zipfile, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import engine_r2 as E
from data import build
from common import DEV_START, PROJECT

OUT = PROJECT / "data" / "monthly20_r4_positioning"
BASE = "https://data.binance.vision/data/futures/um/daily/metrics/"
LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/futures/um/daily/metrics/{s}/"


import threading, requests
_tl = threading.local()


def get(url, tries=5):
    if not hasattr(_tl, "s"):
        _tl.s = requests.Session()
    for a in range(tries):
        try:
            r = _tl.s.get(url, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                return r.content
        except Exception:
            pass
        time.sleep(2 * (a + 1))
    return None


def listing(s):
    p = OUT / "listing" / f"{s}.json"
    if p.exists():
        return json.loads(p.read_text())
    days, marker = [], ""
    while True:
        x = get(LIST.format(s=s) + (f"&marker={marker}" if marker else ""))
        if x is None:
            break
        t = x.decode()
        days += re.findall(r"<Key>[^<]*-metrics-(\d{4}-\d{2}-\d{2})\.zip</Key>", t)
        m = re.search(r"<NextMarker>([^<]*)</NextMarker>", t)
        if "<IsTruncated>true" not in t or not m:
            break
        marker = m.group(1)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(set(days))))
    return sorted(set(days))


def fnum(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else None
    except Exception:
        return None


def one_day(s, d):
    b = get(f"{BASE}{s}/{s}-metrics-{d}.zip")
    if b is None:
        return d, None
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
        rows = list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), "utf-8")))
    except Exception:
        return d, None
    rows = [r for r in rows[1:] if len(r) >= 8 and r[0][:4].isdigit()]
    if not rows:
        return d, None
    rows.sort(key=lambda r: r[0])
    last = rows[-1]
    def mean(k):
        v = [fnum(r[k]) for r in rows]; v = [x for x in v if x is not None and x > 0]
        return float(np.mean(v)) if v else None
    return d, [fnum(last[2]), fnum(last[3]), mean(4), mean(5), mean(6), mean(7), len(rows)]


def needed():
    P = E.Panel(build()); UM = E.universe_mask(P, 40)
    i0 = int(np.searchsorted(P.t, DEV_START)) - 45
    need = {}
    for j in np.where(UM[i0:].any(0))[0]:
        idx = np.where(UM[:, j])[0]; idx = idx[idx >= i0]
        days = set()
        for i in idx:
            for k in range(i - 40, i + 1):
                days.add(datetime.fromtimestamp(P.t[k] / 1000, timezone.utc).strftime("%Y-%m-%d"))
        need[P.syms[j]] = days
    return need


def main():
    need = needed()
    part, nparts = (int(x) for x in sys.argv[1].split("/")) if len(sys.argv) > 1 else (0, 1)
    need = {k: v for i, (k, v) in enumerate(sorted(need.items())) if i % nparts == part}
    print(len(need), "symbols,", sum(len(v) for v in need.values()), "symbol-days wanted", flush=True)
    with ThreadPoolExecutor(12) as ex:
        lists = dict(zip(need, ex.map(listing, need)))
    import threading
    pool = ThreadPoolExecutor(24); lock = threading.Lock(); tot = [0]

    def do_sym(s):
        p = OUT / "daily" / f"{s}.json"
        have = json.loads(p.read_text()) if p.exists() else {}
        todo = sorted((need[s] & set(lists[s])) - set(have))
        if todo:
            for d, rec in pool.map(lambda d: one_day(s, d), todo):
                have[d] = rec
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(have, separators=(",", ":")))
        with lock:
            tot[0] += len(todo)
            print(s, "archive from", lists[s][0] if lists[s] else None, "fetched", len(todo), "total", tot[0], flush=True)

    with ThreadPoolExecutor(3) as outer:
        list(outer.map(do_sym, sorted(need)))
    print("DONE", flush=True)

if __name__ == "__main__":
    main()
