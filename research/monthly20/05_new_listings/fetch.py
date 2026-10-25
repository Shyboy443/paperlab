"""Download the first months of 1h bars + funding for EVERY Binance USDT-M perp (and USDT spot pair) ever listed,
including delisted ones, from the public bulk archive data.binance.vision (not the rate-limited API).

Why the archive: perp_symbols() only knows coins that still trade. The archive keeps delisted symbols, so we can
measure the survivorship bias directly instead of guessing it.

Output (cache, outside the research folder): data/monthly20_05_new_listings/{perp,spot}/<SYM>.json
  {"symbol", "first_bar", "bars": [[open_ms,o,h,l,c,vol,quote_vol]...] (first ~4 months), "funding": [[ms,rate]...]}
"""
from __future__ import annotations
import csv, io, json, re, sys, time, urllib.parse, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_05_new_listings")
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DL = "https://data.binance.vision/"
MONTHS_KEPT = 4
MIN_MONTH = "2020-11"  # listings from 2021 on matter; keep a little earlier for context


def http(url, tries=5):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PaperLab-research/1"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(3 * (i + 1))
        except Exception:
            time.sleep(3 * (i + 1))
    return None


def s3_list(prefix, delimiter=True):
    out, marker = [], ""
    while True:
        url = f"{S3}?prefix={urllib.parse.quote(prefix)}&marker={urllib.parse.quote(marker)}" + ("&delimiter=/" if delimiter else "")
        x = (http(url, tries=10) or b"").decode()
        if not x:
            raise RuntimeError("archive listing unavailable: " + prefix)
        keys = re.findall(r"<Prefix>(.*?)</Prefix>", x)[1:] if delimiter else re.findall(r"<Key>(.*?)</Key>", x)
        out += keys
        if "<IsTruncated>true</IsTruncated>" not in x:
            return out
        nm = re.findall(r"<NextMarker>(.*?)</NextMarker>", x)
        marker = nm[0] if nm else (keys[-1] if keys else "")


def read_zip_csv(blob):
    z = zipfile.ZipFile(io.BytesIO(blob))
    rows = []
    for name in z.namelist():
        for r in csv.reader(io.TextIOWrapper(z.open(name), "utf-8")):
            if r and r[0][:1].isdigit():
                rows.append(r)
    return rows


def ms(t):
    t = int(t)
    return t // 1000 if t > 10**14 else t  # spot archive switched to microseconds in 2025


def one(venue, sym):
    path = OUT / venue / f"{sym}.json"
    if path.exists():
        return sym, "cached"
    root = "data/futures/um/monthly" if venue == "perp" else "data/spot/monthly"
    keys = [k for k in s3_list(f"{root}/klines/{sym}/1h/", delimiter=False) if k.endswith(".zip")]
    months = sorted(re.findall(r"-(\d{4}-\d{2})\.zip$", k)[0] for k in keys)
    if not months:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"symbol": sym, "first_bar": None, "bars": [], "funding": [], "months": []}))
        return sym, "empty"
    first = months[:MONTHS_KEPT]
    bars = []
    if months[0] >= MIN_MONTH:
        for m in first:
            blob = http(DL + urllib.parse.quote(f"{root}/klines/{sym}/1h/{sym}-1h-{m}.zip"))
            if blob:
                bars += [[ms(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[7])]
                         for r in read_zip_csv(blob)]
    fund = []
    if venue == "perp" and months[0] >= MIN_MONTH:
        for m in first:
            blob = http(DL + urllib.parse.quote(f"{root}/fundingRate/{sym}/{sym}-fundingRate-{m}.zip"))
            if blob:
                fund += [[ms(r[0]), float(r[2])] for r in read_zip_csv(blob)]
    bars.sort()
    fund.sort()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"symbol": sym, "first_bar": bars[0][0] if bars else None, "bars": bars,
                                "funding": fund, "months": months}, separators=(",", ":")))
    return sym, len(bars)


def main(venue):
    root = "data/futures/um/monthly" if venue == "perp" else "data/spot/monthly"
    syms = [p.rstrip("/").split("/")[-1] for p in s3_list(f"{root}/klines/")]
    syms = [s for s in syms if s.endswith("USDT") and "_" not in s]
    print(venue, len(syms), "USDT symbols in the archive", flush=True)
    (OUT / f"{venue}_archive_symbols.json").parent.mkdir(parents=True, exist_ok=True)
    (OUT / f"{venue}_archive_symbols.json").write_text(json.dumps(syms))
    done = 0
    with ThreadPoolExecutor(16) as ex:
        for sym, n in ex.map(lambda s: one(venue, s), syms):
            done += 1
            if done % 50 == 0:
                print(venue, done, sym, n, flush=True)


if __name__ == "__main__":
    for v in sys.argv[1:] or ["perp", "spot"]:
        main(v)
