"""Download 1h USDT-perp klines + funding for every perp from Binance's public data portal (data.binance.vision).

The portal is a static file host, so this does not use the shared API IP weight the other agents need.
Rows are stored in the same format as common.klines(): [open_ms, o, h, l, c, base_vol, quote_vol, trades, taker_buy_quote].
Output: data/monthly20_07_pump_scanner/{1h,funding}/SYMBOL.json
"""
import sys, io, json, zipfile, urllib.request, urllib.error, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
from common import perp_symbols, PROJECT, TEST_END, utc, _save

OUT = PROJECT / "data" / "monthly20_07_pump_scanner"
BASEURL = "https://data.binance.vision/data/futures/um"
FIRST = (2020, 12)


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
    return None


def list_delisted():
    import re
    p = OUT / "delisted.json"
    if p.exists():
        return json.loads(p.read_text())
    syms, marker = [], ""
    while True:
        u = ("https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/futures/um/monthly/klines/"
             + ("&marker=data/futures/um/monthly/klines/" + marker + "/" if marker else ""))
        x = urllib.request.urlopen(u, timeout=60).read().decode()
        part = re.findall(r"<Prefix>data/futures/um/monthly/klines/([^/<]+)/</Prefix>", x)
        syms += part
        if "<IsTruncated>true" in x and part:
            marker = part[-1]
        else:
            break
    cur = {s["symbol"] for s in perp_symbols()}
    out = [s for s in syms if s.endswith("USDT") and s not in cur]
    _save(p, out)
    return out


def months(onboard):
    y, m = FIRST
    if onboard:
        d = datetime.fromtimestamp(onboard / 1000, timezone.utc)
        if (d.year, d.month) > (y, m):
            y, m = d.year, d.month
    out = []
    while (y, m) <= (2026, 9):
        out.append((y, m)); m += 1
        if m == 13: y, m = y + 1, 1
    return out


def one(s):
    sym = s["symbol"]
    kp, fp = OUT / "1h" / f"{sym}.json", OUT / "funding" / f"{sym}.json"
    if kp.exists() and fp.exists():
        return sym, "cached"
    rows, frows, miss = [], [], 0
    for y, m in months(s["onboard"]):
        L = get_csv(f"{BASEURL}/monthly/klines/{sym}/1h/{sym}-1h-{y}-{m:02d}.zip")
        if L is None: miss += 1; L = []
        for line in L:
            p = line.split(",")
            if not p[0].isdigit(): continue
            rows.append([int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5]), float(p[7]), int(p[8]), float(p[10])])
        F = get_csv(f"{BASEURL}/monthly/fundingRate/{sym}/{sym}-fundingRate-{y}-{m:02d}.zip") or []
        for line in F:
            p = line.split(",")
            if p[0].isdigit(): frows.append([int(p[0]), float(p[2])])
    d = datetime(2026, 10, 1, tzinfo=timezone.utc)
    while int(d.timestamp() * 1000) < TEST_END:
        L = get_csv(f"{BASEURL}/daily/klines/{sym}/1h/{sym}-1h-{d:%Y-%m-%d}.zip") or []
        for line in L:
            p = line.split(",")
            if not p[0].isdigit(): continue
            rows.append([int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5]), float(p[7]), int(p[8]), float(p[10])])
        d += timedelta(days=1)
    rows = sorted({r[0]: r for r in rows if r[0] + 3600_000 <= TEST_END}.values())
    _save(kp, rows); _save(fp, sorted(frows))
    return sym, f"{len(rows)} bars {len(frows)} funding miss={miss}"


if __name__ == "__main__":
    S = perp_symbols()
    if len(sys.argv) > 1 and sys.argv[1] == "reverse":
        S = S[::-1]
    if len(sys.argv) > 1 and sys.argv[1].startswith("delisted"):
        # survivorship fix: every USDT perp that ever had a monthly kline file but is not trading today
        # (list from the portal's public S3 index, saved by list_delisted() below)
        S = [{"symbol": x, "onboard": 0} for x in list_delisted()]
        if sys.argv[1] == "delisted_rev":
            S = S[::-1]
    with ThreadPoolExecutor(32) as ex:
        for n, (sym, msg) in enumerate(ex.map(one, S)):
            if n % 25 == 0: print(n, sym, msg, flush=True)
    print("DONE")
