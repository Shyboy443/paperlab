"""Data for r4_10_cascade: Binance USD-M 5m klines + 5m open-interest `metrics`, from data.binance.vision (keyless).

Universe (point-in-time, survivorship-free): at the start of every calendar quarter, the 20 USDT perps with the highest
quote volume over the trailing 90 days (>= 90% of hourly bars present), ranked from agent 04's archive 1h cache, which
INCLUDES delisted coins. TradFi perps (gold, stocks, oil...) are excluded. A coin trades only in quarters it was picked.

Open interest: the archive's daily `metrics` files start 2021-12-01 for almost every coin (BTC from 2020-09). So the
strategy cannot trade before 2021-12: 2021-01..2021-11 are flat months (0%) in every report.

Cache: data/monthly20_r4_cascade/{5m,metrics}/SYM.npz
"""
import io, sys, zipfile, urllib.request, urllib.error, time, json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np

PROJ = Path(__file__).resolve().parents[3]
ROOT = PROJ / "data" / "monthly20_r4_cascade"
H1 = PROJ / "data" / "monthly20_04_crash_reversal" / "1h"
FUND = PROJ / "data" / "monthly20_04_crash_reversal" / "funding"
CDN = "https://data.binance.vision/"
TRADFI = {"XAUUSDT", "XAGUSDT", "PAXGUSDT", "CLUSDT", "BZUSDT", "MRVLUSDT", "SKHYNIXUSDT", "SKHYUSDT", "SNDKUSDT",
          "SOXLUSDT", "SOXSUSDT", "SPCXUSDT", "EWYUSDT", "QQQUSDT", "SAMSUNGUSDT", "INTCUSDT", "MUUSDT", "DRAMUSDT",
          "USDCUSDT", "BTCDOMUSDT", "AAPLUSDT", "TSLAUSDT", "NVDAUSDT", "COINUSDT", "MSTRUSDT", "HOODUSDT", "AMZNUSDT",
          "GOOGLUSDT", "METAUSDT", "CRCLUSDT", "NATGASUSDT", "XPTUSDT", "XPDUSDT", "COPPERUSDT"}
TOPN, LOOKBACK_D = 20, 90
LAST_DAY = datetime(2026, 10, 21, tzinfo=timezone.utc)


def ms(d):
    return int(d.timestamp() * 1000)


def quarters():
    out = []
    for y in range(2021, 2027):
        for m in (1, 4, 7, 10):
            if (2021, 10) <= (y, m) <= (2026, 10):
                out.append(datetime(y, m, 1, tzinfo=timezone.utc))
    return out


def universe():
    """{quarter_start_iso: [symbols]} picked from data known before the quarter starts."""
    p = ROOT / "universe.json"
    if p.exists():
        return json.loads(p.read_text())
    data = {}
    for f in H1.glob("*.npz"):
        if f.stem in TRADFI:
            continue
        a = np.load(f)["a"]
        if len(a):
            data[f.stem] = a
    out = {}
    for q in quarters():
        t = ms(q)
        r = []
        for s, a in data.items():
            i0, i1 = np.searchsorted(a[:, 0], t - LOOKBACK_D * 86400000), np.searchsorted(a[:, 0], t)
            if i1 - i0 < 0.9 * LOOKBACK_D * 24:
                continue
            r.append((float(a[i0:i1, 5].sum()), s))
        r.sort(reverse=True)
        out[q.date().isoformat()] = [s for _, s in r[:TOPN]]
    ROOT.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1))
    return out


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


def _rows(blob):
    if blob is None:
        return []
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return []
    return [l.split(",") for l in z.read(z.namelist()[0]).decode().splitlines() if l and l[0].isdigit()]


def spans():
    """{sym: sorted list of 'YYYY-MM' months needed (quarter months + 1 warm-up month before)}."""
    u = universe()
    need = {}
    for q, syms in u.items():
        d = datetime.fromisoformat(q)
        months = []
        for k in (-1, 0, 1, 2):
            y, m = d.year, d.month + k
            if m < 1:
                y, m = y - 1, m + 12
            months.append(f"{y}-{m:02d}")
        for s in syms:
            need.setdefault(s, set()).update(mm for mm in months if "2021-11" <= mm <= "2026-10")
    return {s: sorted(v) for s, v in need.items()}


def _days(month):
    y, m = map(int, month.split("-"))
    d = datetime(y, m, 1, tzinfo=timezone.utc)
    while d.month == m and d <= LAST_DAY:
        yield d.date().isoformat()
        d += timedelta(days=1)


def fetch_5m(sym, months):
    path = ROOT / "5m" / f"{sym}.npz"
    if path.exists():
        return
    rows = []
    for mo in months:
        if mo == "2026-10":
            for d in _days(mo):
                rows += _rows(_get(CDN + f"data/futures/um/daily/klines/{sym}/5m/{sym}-5m-{d}.zip"))
        else:
            got = _rows(_get(CDN + f"data/futures/um/monthly/klines/{sym}/5m/{sym}-5m-{mo}.zip"))
            if not got:  # month zip missing (e.g. delisted mid-month): try the daily files
                for d in _days(mo):
                    got += _rows(_get(CDN + f"data/futures/um/daily/klines/{sym}/5m/{sym}-5m-{d}.zip"))
            rows += got
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        np.savez_compressed(path, a=np.zeros((0, 7)))
        return
    a = np.array([[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7]), float(r[10])]
                  for r in rows])
    a[a[:, 0] > 1e14, 0] //= 1000
    a = a[np.argsort(a[:, 0], kind="stable")]
    a = a[np.concatenate([[True], np.diff(a[:, 0]) > 0])]
    np.savez_compressed(path, a=a)  # cols: open_ms o h l c quote_vol taker_buy_quote


def _metric_day(sym, d):
    out = []
    for r in _rows(_get(CDN + f"data/futures/um/daily/metrics/{sym}/{sym}-metrics-{d}.zip")):
        try:
            t = ms(datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc))
            out.append([t, float(r[2]), float(r[3])])
        except (ValueError, IndexError):
            pass
    return out


def fetch_metrics(sym, months, workers=24):
    path = ROOT / "metrics" / f"{sym}.npz"
    if path.exists():
        return
    days = [d for mo in months if mo >= "2021-12" for d in _days(mo)]
    out = []
    with ThreadPoolExecutor(workers) as ex:
        for part in ex.map(lambda d: _metric_day(sym, d), days):
            out += part
    path.parent.mkdir(parents=True, exist_ok=True)
    a = np.array(out) if out else np.zeros((0, 3))
    if len(a):
        a = a[np.argsort(a[:, 0], kind="stable")]
        a = a[np.concatenate([[True], np.diff(a[:, 0]) > 0])]
    tmp = path.with_name(path.stem + ".tmp.npz")
    np.savez_compressed(tmp, a=a)  # cols: stamp_ms open_interest(base) open_interest_value(usd)
    tmp.replace(path)


def fetch_all(workers=12):
    sp = spans()
    jobs = sorted(sp.items())
    print("coins", len(jobs), "coin-months", sum(len(v) for _, v in jobs), flush=True)
    for name, fn, wk in (("5m", fetch_5m, workers), ("metrics", fetch_metrics, 2)):
        done = 0
        with ThreadPoolExecutor(wk) as ex:
            for _ in ex.map(lambda kv: fn(*kv), jobs):
                done += 1
                if done % 10 == 0:
                    print(name, done, "/", len(jobs), flush=True)


def load(sym, kind):
    p = ROOT / kind / f"{sym}.npz"
    return np.load(p)["a"] if p.exists() else None


def load_funding(sym):
    p = FUND / f"{sym}.npz"
    return np.load(p)["a"] if p.exists() else np.zeros((0, 2))


if __name__ == "__main__":
    fetch_all()
