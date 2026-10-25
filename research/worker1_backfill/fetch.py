"""Worker 1 backfill data: Binance SPOT 1d + 4h klines 2017-08..2021-06 from the public archive data.binance.vision,
every *USDT pair in the archive's symbol directory (delisted pairs included). Stablecoins, fiat, gold tokens and
leveraged tokens (*UP/*DOWN/*BULL/*BEAR) are excluded. Static file host only, no keys.
Cache: data/w1_backfill/raw/{1d,4h}/SYM.npy rows [open_ms, o, h, l, c, quote_vol, taker_buy_quote]."""
import io, json, re, sys, time, zipfile, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np

OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w1_backfill")
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
BASE = "https://data.binance.vision/data/spot/monthly/klines"
LAST = "2021-06"
STABLE = {"USDC", "TUSD", "PAX", "USDS", "USDSB", "BUSD", "DAI", "USDP", "FDUSD", "UST", "SUSD", "EUR", "GBP", "AUD",
          "PAXG", "XAUT", "USDE", "USD1", "XUSD", "RLUSD", "AEUR", "EURI", "BKRW", "BIDR", "IDRT", "BVND", "USTC", "TRY",
          "BRL", "RUB", "UAH", "NGN", "ZAR", "JPY", "MXN", "ARS", "COP", "PLN", "RON", "CZK"}


def get(url, tries=5):
    for a in range(tries):
        try:
            return urllib.request.urlopen(url, timeout=60).read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (a + 1))
        except Exception:
            time.sleep(2 * (a + 1))
    raise RuntimeError(url)


def s3_list(prefix, delim=True):
    out, marker = [], ""
    while True:
        x = get(f"{S3}?prefix={prefix}" + ("&delimiter=/" if delim else "") + f"&marker={marker}").decode()
        keys = re.findall(r"<Prefix>([^<]+)</Prefix>", x) if delim else re.findall(r"<Key>([^<]+)</Key>", x)
        keys = [k for k in keys if k != prefix]
        out += keys
        if "<IsTruncated>true</IsTruncated>" not in x:
            return out
        nm = re.findall(r"<NextMarker>([^<]+)</NextMarker>", x)
        marker = nm[0] if nm else keys[-1]


def keep(sym):
    if not sym.endswith("USDT") or not sym.isalnum() or not sym.isascii():
        return False
    base = sym[:-4]
    if base in STABLE or not base:
        return False
    if re.search(r"(UP|DOWN|BULL|BEAR)$", base) and len(base) > 4:
        return False
    return True


def one(sym):
    res = {}
    for iv in ("1d", "4h"):
        p = OUT / "raw" / iv / f"{sym}.npy"
        if p.exists():
            res[iv] = "cached"; continue
        keys = s3_list(f"data/spot/monthly/klines/{sym}/{iv}/", delim=False)
        months = sorted(set(re.findall(rf"{sym}-{iv}-(\d{{4}}-\d{{2}})\.zip$", " ".join(keys)) +
                            [m for k in keys for m in re.findall(rf"{sym}-{iv}-(\d{{4}}-\d{{2}})\.zip$", k)]))
        months = [m for m in months if m <= LAST]
        rows = []
        for m in months:
            b = get(f"{BASE}/{sym}/{iv}/{sym}-{iv}-{m}.zip")
            if not b:
                continue
            z = zipfile.ZipFile(io.BytesIO(b))
            for line in z.read(z.namelist()[0]).decode().splitlines():
                q = line.split(",")
                if q[0].isdigit():
                    t = int(q[0])
                    if t > 10 ** 14:
                        t //= 1000
                    rows.append([t, float(q[1]), float(q[2]), float(q[3]), float(q[4]), float(q[7]), float(q[10])])
        a = np.array(sorted({r[0]: r for r in rows}.values()), dtype=float).reshape(-1, 7)
        p.parent.mkdir(parents=True, exist_ok=True)
        np.save(p, a)
        res[iv] = len(a)
    return sym, res


if __name__ == "__main__":
    (OUT / "raw").mkdir(parents=True, exist_ok=True)
    sp = OUT / "symbols_all.json"
    if sp.exists():
        allsyms = json.loads(sp.read_text())
    else:
        allsyms = [k.rstrip("/").split("/")[-1] for k in s3_list("data/spot/monthly/klines/")]
        sp.write_text(json.dumps(allsyms))
    syms = sorted(s for s in allsyms if keep(s))
    excl = sorted(s for s in allsyms if s.endswith("USDT") and not keep(s))
    print("archive symbols", len(allsyms), "USDT kept", len(syms), "excluded", excl, flush=True)
    (OUT / "symbols_used_candidates.json").write_text(json.dumps({"kept": syms, "excluded": excl}))
    with ThreadPoolExecutor(16) as ex:
        for n, (s, r) in enumerate(ex.map(one, syms)):
            if r.get("1d") not in (0, "cached") or n % 50 == 0:
                print(n, s, r, flush=True)
