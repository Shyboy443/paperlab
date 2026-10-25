"""Daily panel for the round-2 trend checks, built ONLY from caches that agents 04 / 07 already downloaded
from the public archive data.binance.vision (every USDT perp that ever traded, delisted ones included).

- 1h klines: data/monthly20_04_crash_reversal/1h/SYM.npz (cols open_ms o h l c quote_vol taker_buy_quote trades),
  2020-09..2026-10-21; agent 07's json (2020-12..) is the fallback for symbols 04 does not have.
- Warm-up before 2020-09 for coins still trading today: the shared common.py 1d cache (from 2020-01).
- Funding: data/monthly20_07_pump_scanner/funding/SYM.json ([ms, rate]), fallback agent 04's npz.
- Non-crypto perps are excluded: everything Binance tags as EQUITY / COMMODITY / INDEX / PREMARKET / FX,
  stable coins and gold tokens. Delisted symbols are not in exchangeInfo; they are all crypto (checked by name).

Output: data/monthly20_r2_trend/panel.npz with syms, day (ms), O H L C QV (NaN = no bar), FUND (sum of the
day's funding rates; a long pays rate x notional).
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np

PROJECT = Path(r"D:/Projects/BinanceBOt/paperlab")
D04 = PROJECT / "data" / "monthly20_04_crash_reversal"
D07 = PROJECT / "data" / "monthly20_07_pump_scanner"
D05 = PROJECT / "data" / "monthly20_05_new_listings"
CACHE1D = PROJECT / "data" / "monthly20_cache" / "perp" / "1d"
OUT = PROJECT / "data" / "monthly20_r2_trend"
DAY = 86_400_000
D0 = 1577836800000          # 2020-01-01
D1 = 1792540800000 + DAY    # exclusive: 2026-10-22 00:00 UTC (TEST_END)
EXCL_NAMES = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "FDUSDUSDT", "USDPUSDT", "USDEUSDT", "USD1USDT", "XUSDUSDT",
              "RLUSDUSDT", "EURUSDT", "PAXGUSDT", "XAUTUSDT", "DEFIUSDT", "BTCDOMUSDT", "FOOTBALLUSDT",
              "BLUEBIRDUSDT", "ALLUSDT"}


def excluded():
    info = json.loads((D05 / "exchangeinfo_all.json").read_text())
    return EXCL_NAMES | {e["symbol"] for e in info if e.get("underlyingType") != "COIN"}


def hourly(sym):
    p = D04 / "1h" / f"{sym}.npz"
    if p.exists():
        a = np.load(p)["a"]
        if len(a):
            return a[:, [0, 1, 2, 3, 4, 5]]
    p = D07 / "1h" / f"{sym}.json"
    if p.exists():
        r = json.loads(p.read_text())
        if r:
            a = np.array(r, dtype=float)
            return a[:, [0, 1, 2, 3, 4, 6]]
    return np.zeros((0, 6))


def to_daily(a, nd):
    O = np.full(nd, np.nan); H = O.copy(); L = O.copy(); C = O.copy(); Q = np.zeros(nd); N = np.zeros(nd)
    if not len(a):
        return O, H, L, C, Q
    a = a[(a[:, 0] >= D0) & (a[:, 0] < D1)]
    d = ((a[:, 0] - D0) // DAY).astype(int)
    for k in range(len(a)):  # rows are time sorted
        i = d[k]
        if N[i] == 0:
            O[i], H[i], L[i] = a[k, 1], a[k, 2], a[k, 3]
        else:
            H[i] = max(H[i], a[k, 2]); L[i] = min(L[i], a[k, 3])
        C[i] = a[k, 4]; Q[i] += a[k, 5]; N[i] += 1
    bad = N < 12  # a day needs at least 12 hourly bars (else it is treated as missing)
    for X in (O, H, L, C):
        X[bad] = np.nan
    Q[bad] = 0
    return O, H, L, C, Q


def funding_daily(sym, nd):
    F = np.zeros(nd)
    p = D07 / "funding" / f"{sym}.json"
    rows = json.loads(p.read_text()) if p.exists() else []
    if not rows:
        p4 = D04 / "funding" / f"{sym}.npz"
        if p4.exists():
            rows = np.load(p4)["a"].tolist()
    for t, r in rows:
        i = int((t - D0) // DAY)
        if 0 <= i < nd:
            F[i] += r
    return F


def build():
    syms4 = (D04 / "symbols.txt").read_text().split()
    syms7 = [p.stem for p in (D07 / "1h").glob("*.json")]
    ex = excluded()
    syms = sorted({s for s in syms4 + syms7 if s.isascii() and s.isalnum() and s.endswith("USDT")} - ex)
    nd = (D1 - D0) // DAY
    O = np.full((nd, len(syms)), np.nan); H = O.copy(); L = O.copy(); C = O.copy()
    Q = np.zeros((nd, len(syms))); F = np.zeros((nd, len(syms)))
    for j, s in enumerate(syms):
        o, h, l, c, q = to_daily(hourly(s), nd)
        p = CACHE1D / f"{s}.json"
        if p.exists():  # warm-up history before the archive cache starts (coins still trading)
            first = np.argmax(~np.isnan(o)) if (~np.isnan(o)).any() else nd
            for r in json.loads(p.read_text()):
                i = int((r[0] - D0) // DAY)
                if 0 <= i < first:
                    o[i], h[i], l[i], c[i], q[i] = r[1], r[2], r[3], r[4], r[6]
        O[:, j], H[:, j], L[:, j], C[:, j], Q[:, j] = o, h, l, c, q
        F[:, j] = funding_daily(s, nd)
        if j % 100 == 0:
            print(j, s, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT / "panel.npz", syms=np.array(syms), day=D0 + DAY * np.arange(nd), O=O, H=H, L=L, C=C,
                        QV=Q, FUND=F)
    print("saved", len(syms), "symbols x", nd, "days; excluded", len(ex & set(syms4 + syms7)))


def load():
    z = np.load(OUT / "panel.npz")
    return {k: z[k] for k in z.files}


if __name__ == "__main__":
    build()
