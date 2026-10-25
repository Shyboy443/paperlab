"""Download Binance SPOT klines (1d for every *USDT pair, 1h for pairs that are ever in the top-40) for 2017-08..2021-06
from the public archive data.binance.vision. Symbols come from the archive's directory listing (includes delisted pairs).
Cache: data/w2_backfill/{list.json, 1d/SYM.json, 1h/SYM.json}. Rows: [open_ms, o, h, l, c, base_vol, quote_vol]."""
import io, json, re, sys, time, zipfile, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w2_backfill")
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
BASE = "https://data.binance.vision/data/spot/monthly/klines"
FIRST, LAST = (2017, 8), (2021, 6)


def _get(url):
    for a in range(5):
        try:
            return urllib.request.urlopen(url, timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            time.sleep(2 * (a + 1))
        except Exception:
            time.sleep(2 * (a + 1))
    return None


def s3_list(prefix, what="Prefix"):
    out, marker = [], ""
    while True:
        u = f"{S3}?delimiter=/&prefix={prefix}" + (f"&marker={marker}" if marker else "")
        x = _get(u).decode()
        if what == "Prefix":
            part = re.findall(rf"<Prefix>{re.escape(prefix)}([^/<]+)/</Prefix>", x)
            last = prefix + part[-1] + "/" if part else ""
        else:
            part = re.findall(rf"<Key>{re.escape(prefix)}([^<]+)</Key>", x)
            last = prefix + part[-1] if part else ""
        out += part
        if "<IsTruncated>true" in x and part:
            marker = last
        else:
            return out


def symbols():
    p = OUT / "list.json"
    if p.exists():
        return json.loads(p.read_text())
    s = [x for x in s3_list("data/spot/monthly/klines/") if x.endswith("USDT")]
    p.write_text(json.dumps(s))
    return s


def months():
    y, m = FIRST
    while (y, m) <= LAST:
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def fetch(sym, iv):
    path = OUT / iv / f"{sym}.json"
    if path.exists():
        return sym, "cached"
    files = set(s3_list(f"data/spot/monthly/klines/{sym}/{iv}/", what="Key"))
    rows, miss = [], 0
    for y, m in months():
        fn = f"{sym}-{iv}-{y}-{m:02d}.zip"
        if fn not in files:
            continue
        b = _get(f"{BASE}/{sym}/{iv}/{fn}")
        if not b:
            miss += 1; continue
        z = zipfile.ZipFile(io.BytesIO(b))
        for line in z.read(z.namelist()[0]).decode().splitlines():
            q = line.split(",")
            if not q[0].isdigit():
                continue
            t = int(q[0])
            if t > 10 ** 14:  # microsecond stamps (newer files)
                t //= 1000
            rows.append([t, float(q[1]), float(q[2]), float(q[3]), float(q[4]), float(q[5]), float(q[7])])
    if miss:
        return sym, f"MISSING {miss}"
    rows.sort()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    return sym, len(rows)


if __name__ == "__main__":
    iv = sys.argv[1]
    syms = symbols() if iv == "1d" else json.loads((OUT / "need_1h.json").read_text())
    print(len(syms), "symbols", flush=True)
    with ThreadPoolExecutor(int(sys.argv[2]) if len(sys.argv) > 2 else 4) as ex:
        for i, (s, r) in enumerate(ex.map(lambda s: fetch(s, iv), syms)):
            if i % 50 == 0 or (isinstance(r, str) and r.startswith("MISSING")):
                print(i, s, r, flush=True)
