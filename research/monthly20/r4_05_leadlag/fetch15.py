"""Download 15m USDT-perp klines from the public data.binance.vision archive for the 85-coin subset in subset15m.json
(union of the top-12 coins by trailing 30-day quote volume at every quarter start 2021-01..2026-10, point-in-time,
INCLUDING delisted coins such as LUNA, FTM, MATIC, WAVES, BLZ ...). Months covered = months the coin has 1h bars in
agent 07's survivorship-free cache. Output: data/monthly20_r4_leadlag/15m/SYM.npy  cols [open_ms,o,h,l,c,quote_vol]
"""
import sys, io, json, zipfile, urllib.request, urllib.error, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import PROJECT, TEST_END

V07 = PROJECT / "data" / "monthly20_07_pump_scanner" / "1h"
OUT = PROJECT / "data" / "monthly20_r4_leadlag" / "15m"
BASE = "https://data.binance.vision/data/futures/um"
HERE = Path(__file__).resolve().parent


def get_csv(url):
    for a in range(5):
        try:
            b = urllib.request.urlopen(url, timeout=60).read()
            z = zipfile.ZipFile(io.BytesIO(b))
            return z.read(z.namelist()[0]).decode().splitlines()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            time.sleep(2 * (a + 1))
        except Exception:
            time.sleep(2 * (a + 1))
    return None


def parse(L, rows):
    for line in L:
        p = line.split(",")
        if p[0].isdigit():
            rows.append([int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[7])])


def one(sym):
    out = OUT / f"{sym}.npy"
    if out.exists():
        return sym, "cached"
    h = json.loads((V07 / f"{sym}.json").read_text())
    d0 = datetime.fromtimestamp(max(h[0][0], 1606780800000) / 1000, timezone.utc)
    d1 = datetime.fromtimestamp(h[-1][0] / 1000, timezone.utc)
    rows, miss = [], 0
    y, m = d0.year, d0.month
    while (y, m) <= (min(d1.year, 2026), d1.month if d1.year < 2026 or (d1.year == 2026 and d1.month < 10) else 9):
        L = get_csv(f"{BASE}/monthly/klines/{sym}/15m/{sym}-15m-{y}-{m:02d}.zip")
        if L is None:
            miss += 1
        else:
            parse(L, rows)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    if d1 >= datetime(2026, 10, 1, tzinfo=timezone.utc):
        d = datetime(2026, 10, 1, tzinfo=timezone.utc)
        while int(d.timestamp() * 1000) < TEST_END:
            parse(get_csv(f"{BASE}/daily/klines/{sym}/15m/{sym}-15m-{d:%Y-%m-%d}.zip") or [], rows)
            d += timedelta(days=1)
    rows = sorted({r[0]: r for r in rows if r[0] + 900_000 <= TEST_END}.values())
    OUT.mkdir(parents=True, exist_ok=True)
    np.save(out, np.array(rows, dtype=np.float64))
    return sym, f"{len(rows)} bars miss={miss}"


if __name__ == "__main__":
    S = json.loads((HERE / "subset15m.json").read_text())
    with ThreadPoolExecutor(12) as ex:
        for n, (sym, msg) in enumerate(ex.map(one, S)):
            print(n, sym, msg, flush=True)
    print("DONE")
