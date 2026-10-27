"""Download Binance SPOT 1d klines (data.binance.vision monthly zips, public, no keys) for every perp that was ever in the
point-in-time top-60 perp universe, for spot quotes USDT, BUSD, FDUSD, USDC, TUSD (all USD-stable quotes, so the
spot volume of e.g. BTC/FDUSD zero-fee eras is counted). Months 2020-11..2026-09 from monthly zips, 2026-10 from
daily zips. Output per pair: data/w3r3_spec_ratio/spot/{PAIR}.json = [[open_ms, open, high, low, close, quote_vol], ...]"""
import io, json, re, sys, zipfile, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r3_spec_ratio")
(OUT / "spot").mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
spot = set(json.loads((OUT / "spot_symbols.json").read_text()))
perps = (HERE / "u60_syms.txt").read_text().split()
QUOTES = ("USDT", "BUSD", "FDUSD", "USDC", "TUSD")
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix="
DL = "https://data.binance.vision/"


def bases(p):
    b = p[:-4]
    out = [b]
    for pre in ("1000000", "1000", "1M"):
        if b.startswith(pre) and len(b) > len(pre):
            out.append(b[len(pre):])
    if b == "LUNA2":
        out.append("LUNA")
    return out


mapping = {}
for p in perps:
    for b in bases(p):
        prs = [b + q for q in QUOTES if b + q in spot]
        if prs:
            mapping[p] = dict(base=b, pairs=prs, mult=1000 if b != p[:-4] and p.startswith("1000") and not p.startswith("1000000") else
                              (1e6 if p.startswith("1000000") else (1e6 if p.startswith("1M") else 1)))
            break
print(len(perps), "perps;", len(mapping), "with spot;", sum(len(v["pairs"]) for v in mapping.values()), "pairs", flush=True)
(OUT / "mapping.json").write_text(json.dumps(mapping, indent=0))


def get(url):
    for _ in range(3):
        try:
            return urllib.request.urlopen(url, timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:
            pass
    return None


def rows_from_zip(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    out = []
    for line in z.read(z.namelist()[0]).decode().splitlines():
        f = line.split(",")
        if not f[0].isdigit():
            continue
        t = int(f[0])
        if t > 10**14:
            t //= 1000
        out.append([t, float(f[1]), float(f[2]), float(f[3]), float(f[4]), float(f[7])])
    return out


def list_pair(pair):
    x = get(S3 + f"data/spot/monthly/klines/{pair}/1d/")
    return pair, [k for k in (re.findall(r"<Key>([^<]+\.zip)</Key>", x.decode()) if x else []) if k[-11:-4] >= "2020-11"]


def oct_api(pair):
    """2026-10 daily bars (no monthly zip yet) from the public spot REST klines endpoint (no key), one call per pair."""
    b = get(f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1d&startTime=1790812800000&limit=40")
    if not b:
        return []
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])] for r in json.loads(b)]


pairs = sorted({pr for v in mapping.values() for pr in v["pairs"]})
todo = [p for p in pairs if not (OUT / "spot" / f"{p}.json").exists()]
print("todo", len(todo), flush=True)
with ThreadPoolExecutor(48) as ex:
    keys = dict(ex.map(list_pair, todo))
files = [k for v in keys.values() for k in v]
print("files", len(files), flush=True)
with ThreadPoolExecutor(64) as ex:
    blobs = dict(zip(files, ex.map(lambda k: get(DL + k), files)))
live = [p for p in todo if keys[p] and max(k[-11:-4] for k in keys[p]) >= "2026-09"]
with ThreadPoolExecutor(8) as ex:
    octs = dict(zip(live, ex.map(oct_api, live)))
for p in todo:
    rows = []
    for k in keys[p]:
        if blobs.get(k):
            rows += rows_from_zip(blobs[k])
    rows += octs.get(p, [])
    rows = sorted({r[0]: r for r in rows}.values())
    (OUT / "spot" / f"{p}.json").write_text(json.dumps(rows))
print("done", len(todo))
