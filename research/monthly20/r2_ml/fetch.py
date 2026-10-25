"""Backfill 2020-01..2020-08 1h USDT-perp klines from data.binance.vision for the symbols that already traded before
agent 04's archive cache starts (2020-09). Static file host only, no API weight. Output: data/monthly20_r2_ml/early1h/SYM.json
Rows: [open_ms, o, h, l, c, base_vol, quote_vol, trades, taker_buy_quote] (same as agent 07's cache)."""
import io, json, sys, time, zipfile, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np

DATA = Path(r"D:/Projects/BinanceBOt/paperlab/data")
OUT = DATA / "monthly20_r2_ml" / "early1h"
BASE = "https://data.binance.vision/data/futures/um/monthly/klines"


def get_csv(url):
    for a in range(4):
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
    return []


def one(sym):
    p = OUT / f"{sym}.json"
    if p.exists():
        return sym, "cached"
    rows = []
    for m in range(1, 9):
        for line in get_csv(f"{BASE}/{sym}/1h/{sym}-1h-2020-{m:02d}.zip"):
            q = line.split(",")
            if q[0].isdigit():
                rows.append([int(q[0]), float(q[1]), float(q[2]), float(q[3]), float(q[4]), float(q[5]), float(q[7]),
                             int(q[8]), float(q[10])])
    rows = sorted({r[0]: r for r in rows}.values())
    p.write_text(json.dumps(rows))
    return sym, len(rows)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    syms = []
    for f in sorted((DATA / "monthly20_04_crash_reversal" / "1h").glob("*.npz")):
        a = np.load(f)["a"]
        if len(a) and a[0, 0] < 1.6e12:
            syms.append(f.stem)
    with ThreadPoolExecutor(8) as ex:
        for s, n in ex.map(one, syms):
            print(s, n, flush=True)
