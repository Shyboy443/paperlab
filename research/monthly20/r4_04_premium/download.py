"""Download UM premiumIndexKlines 1h from data.binance.vision (public archive, no keys).

Coins: every perp that was ever in the point-in-time top-40 liquid universe (daily re-pick, delisted coins included,
same panel as r2_rotation). Months: every month the coin was in the universe plus one month before (warm-up).
Monthly zips up to 2026-09, daily zips for 2026-10-01..2026-10-21.
Cache: data/monthly20_r4_premium/1h/SYM.json  rows [open_ms, o, h, l, c] (premium as a fraction)
"""
import io, json, sys, time, zipfile, csv, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
import engine_r2 as E
from data import build
from common import PROJECT

OUT = PROJECT / "data" / "monthly20_r4_premium"
RAW = OUT / "raw"
BASE = "https://data.binance.vision/data/futures/um"


def fetch(url, path):
    if path.exists():
        return True
    for k in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                b = r.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.with_suffix(".404").write_text("")
                return False
            time.sleep(2 * (k + 1))
        except Exception:
            time.sleep(2 * (k + 1))
    return False


def jobs():
    D = build(); P = E.Panel(D); U = E.universe_mask(P, 40)
    out = []
    for j in np.where(U.any(0))[0]:
        s = P.syms[j]
        days = np.where(U[:, j])[0]
        months = set()
        for d in days:
            dt = datetime.fromtimestamp(P.t[d] / 1000, timezone.utc)
            months.add((dt.year, dt.month))
            pm = (dt.year, dt.month - 1) if dt.month > 1 else (dt.year - 1, 12)
            months.add(pm)
        for (y, m) in sorted(months):
            if (y, m) >= (2026, 10):
                for dd in range(1, 22):
                    fn = f"{s}-1h-{y}-{m:02d}-{dd:02d}.zip"
                    out.append((f"{BASE}/daily/premiumIndexKlines/{s}/1h/{fn}", RAW / s / fn))
            elif (y, m) >= (2020, 12):
                fn = f"{s}-1h-{y}-{m:02d}.zip"
                out.append((f"{BASE}/monthly/premiumIndexKlines/{s}/1h/{fn}", RAW / s / fn))
    return out


def parse(sym):
    rows = []
    for z in sorted((RAW / sym).glob("*.zip")):
        try:
            with zipfile.ZipFile(z) as zf:
                txt = zf.read(zf.namelist()[0]).decode()
        except Exception:
            continue
        for r in csv.reader(io.StringIO(txt)):
            if not r or not r[0][:1].isdigit():
                continue
            t = int(r[0])
            if t > 1e14:  # microseconds in newer files
                t //= 1000
            rows.append([t, float(r[1]), float(r[2]), float(r[3]), float(r[4])])
    rows.sort()
    ded = []
    for r in rows:
        if not ded or r[0] != ded[-1][0]:
            ded.append(r)
    (OUT / "1h").mkdir(parents=True, exist_ok=True)
    (OUT / "1h" / f"{sym}.json").write_text(json.dumps(ded, separators=(",", ":")))
    return len(ded)


if __name__ == "__main__":
    J = [j for j in jobs() if not j[1].exists() and not j[1].with_suffix(".404").exists()]
    print("files to fetch", len(J), flush=True)
    done = 0
    with ThreadPoolExecutor(8) as ex:
        for ok in ex.map(lambda a: fetch(*a), J):
            done += 1
            if done % 500 == 0:
                print(done, flush=True)
    syms = sorted(p.name for p in RAW.iterdir() if p.is_dir())
    for s in syms:
        parse(s)
    print("parsed", len(syms))
