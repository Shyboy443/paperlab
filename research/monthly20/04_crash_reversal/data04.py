"""Data for agent 04: Binance USD-M perp 1h klines + funding from the public data.binance.vision CDN.

Why not common.klines(): the API path is rate-limited for 10 agents on one IP (30 kline pages/min per process),
and Binance's exchangeInfo lists only coins trading today. The public monthly zip archive has every USDT perp
that ever traded, INCLUDING DELISTED ones, so this universe has no survivorship bias, and it costs no API weight.
Cache: data/monthly20_04_crash_reversal/ (outside the research folder)."""
import io, os, re, sys, zipfile, urllib.request, urllib.error, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[3] / "data" / "monthly20_04_crash_reversal"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
CDN = "https://data.binance.vision/"
MONTHS = [f"{y}-{m:02d}" for y in range(2020, 2027) for m in range(1, 13) if "2020-09" <= f"{y}-{m:02d}" <= "2026-09"]
OCT_DAYS = [f"2026-10-{d:02d}" for d in range(1, 22)]


def _get(url, tries=4):
    for i in range(tries):
        try:
            return urllib.request.urlopen(url, timeout=30).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (i + 1))
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def list_prefixes(prefix):
    out, marker = [], ""
    while True:
        x = _get(f"{S3}?prefix={prefix}&delimiter=/&marker={marker}").decode()
        out += re.findall(r"<Prefix>([^<]+)</Prefix></CommonPrefixes>", x)
        if "<IsTruncated>true" not in x:
            return out
        marker = re.search(r"<NextMarker>([^<]+)</NextMarker>", x).group(1)


def list_keys(prefix):
    out, marker = [], ""
    while True:
        x = _get(f"{S3}?prefix={prefix}&marker={marker}").decode()
        keys = re.findall(r"<Key>([^<]+)</Key>", x)
        out += keys
        if "<IsTruncated>true" not in x:
            return out
        marker = keys[-1]


def all_usdt_perps():
    p = ROOT / "symbols.txt"
    if p.exists():
        return p.read_text().split()
    syms = [s.rstrip("/").split("/")[-1] for s in list_prefixes("data/futures/um/monthly/klines/")]
    syms = [s for s in syms if s.endswith("USDT") and "_" not in s and s.isascii() and s.isalnum()]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(syms))
    return syms


def _csv_rows(blob):
    if blob is None:
        return []
    z = zipfile.ZipFile(io.BytesIO(blob))
    rows = []
    for line in z.read(z.namelist()[0]).decode().splitlines():
        if not line or not line[0].isdigit():
            continue
        rows.append(line.split(","))
    return rows


def _fetch_symbol_1h(sym):
    path = ROOT / "1h" / f"{sym}.npz"
    if path.exists():
        return
    keys = set(list_keys(f"data/futures/um/monthly/klines/{sym}/1h/"))
    rows = []
    for m in MONTHS:
        k = f"data/futures/um/monthly/klines/{sym}/1h/{sym}-1h-{m}.zip"
        if k in keys:
            rows += _csv_rows(_get(CDN + k))
    if keys and any(m == "2026-09" for m in [k[-11:-4] for k in keys]):  # still trading: add October daily files
        for d in OCT_DAYS:
            rows += _csv_rows(_get(CDN + f"data/futures/um/daily/klines/{sym}/1h/{sym}-1h-{d}.zip"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        np.savez_compressed(path, a=np.zeros((0, 8)))
        return
    a = np.array([[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7]), float(r[10]), float(r[8])]
                  for r in rows])
    a[a[:, 0] > 1e14, 0] /= 1000  # microsecond stamps, if any
    a = a[np.argsort(a[:, 0])]
    a = a[np.concatenate([[True], np.diff(a[:, 0]) > 0])]
    np.savez_compressed(path, a=a)  # cols: open_ms o h l c quote_vol taker_buy_quote trades


def _fetch_symbol_funding(sym):
    path = ROOT / "funding" / f"{sym}.npz"
    if path.exists():
        return
    keys = set(list_keys(f"data/futures/um/monthly/fundingRate/{sym}/"))
    rows = []
    for k in sorted(keys):
        if k.endswith(".zip"):
            rows += _csv_rows(_get(CDN + k))
    path.parent.mkdir(parents=True, exist_ok=True)
    a = np.array([[float(r[0]), float(r[2])] for r in rows]) if rows else np.zeros((0, 2))
    if len(a):
        a = a[np.argsort(a[:, 0])]
    np.savez_compressed(path, a=a)  # cols: funding_ms rate (a long pays rate x notional)


def fetch_all(kind="1h", syms=None, workers=16):
    syms = syms or all_usdt_perps()
    fn = _fetch_symbol_1h if kind == "1h" else _fetch_symbol_funding
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for _ in ex.map(fn, syms):
            done += 1
            if done % 50 == 0:
                print(kind, done, "/", len(syms), flush=True)


def load(sym, kind="1h"):
    return np.load(ROOT / kind / f"{sym}.npz")["a"]


if __name__ == "__main__":
    fetch_all(sys.argv[1] if len(sys.argv) > 1 else "1h")
