"""Download free public data for the cross-venue premium study (no keys). Cache: data/w3r2_venue_premium/.

- Upbit KRW daily candles (00:00 UTC day boundaries) for every current KRW market whose base also has a Binance USDT perp
  in the survivorship-free panel (Upbit's API returns 404 for delisted Upbit markets -> Upbit-side survivorship bias).
- Coinbase Exchange hourly candles BTC-USD, ETH-USD.
- USD/KRW daily from Yahoo (KRW=X) and FRED DEXKOUS (cross-check).
"""
import json, sys, time, urllib.request, csv, io
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np

OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r2_venue_premium")
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 research"}
START = datetime(2020, 12, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 22, tzinfo=timezone.utc)


def get(url, tries=6):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (k + 1))
        except Exception:
            time.sleep(2 * (k + 1))
    raise RuntimeError(url)


def upbit_days(market):
    p = OUT / "upbit" / f"{market}.json"
    if p.exists():
        return json.loads(p.read_text())
    rows, to = [], END
    while to > START:
        part = get(f"https://api.upbit.com/v1/candles/days?market={market}&count=200&to={to.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        time.sleep(0.13)
        if not part:
            break
        for x in part:
            rows.append([x["candle_date_time_utc"][:10], x["opening_price"], x["high_price"], x["low_price"], x["trade_price"],
                         x["candle_acc_trade_price"], x["candle_acc_trade_volume"]])
        to = datetime.fromisoformat(part[-1]["candle_date_time_utc"]).replace(tzinfo=timezone.utc)
        if len(part) < 200:
            break
    rows = sorted({r[0]: r for r in rows}.values())
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(rows))
    return rows


def coinbase_hours(prod):
    p = OUT / "coinbase" / f"{prod}_1h.json"
    if p.exists():
        return json.loads(p.read_text())
    rows, cur = {}, START
    while cur < END:
        nxt = min(cur + timedelta(hours=300), END)
        part = get(f"https://api.exchange.coinbase.com/products/{prod}/candles?granularity=3600&start={cur.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                   f"&end={(nxt - timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}")
        time.sleep(0.2)
        for x in part or []:
            rows[int(x[0])] = x  # [time, low, high, open, close, volume]
        cur = nxt
    out = [rows[k] for k in sorted(rows)]
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(out))
    return out


def fx():
    p = OUT / "fx_yahoo_krw.json"
    if not p.exists():
        a, b = int(START.timestamp()), int(END.timestamp())
        j = get(f"https://query1.finance.yahoo.com/v8/finance/chart/KRW=X?interval=1d&period1={a}&period2={b}")
        r = j["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        p.write_text(json.dumps([[t, c] for t, c in zip(r["timestamp"], q["close"]) if c]))
    p2 = OUT / "fx_fred_dexkous.csv"
    if not p2.exists():
      try:
        with urllib.request.urlopen(urllib.request.Request("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXKOUS", headers=UA), timeout=60) as r:
            p2.write_bytes(r.read())
      except Exception as e:
        print("FRED unavailable (cross-check skipped):", e)


if __name__ == "__main__":
    fx(); print("fx ok", flush=True)
    for prod in ("BTC-USD", "ETH-USD"):
        print(prod, len(coinbase_hours(prod)), flush=True)
    sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20/r2_rotation")
    sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
    from data import build
    D = build()
    bases = set()
    for s in D["syms"]:
        s = str(s)
        b = s[:-4]
        for pre in ("1000000", "10000", "1000", "1M"):
            if b.startswith(pre) and len(b) > len(pre) + 1:
                b = b[len(pre):]
        bases.add(b)
    mk = [m["market"] for m in get("https://api.upbit.com/v1/market/all") if m["market"].startswith("KRW-")]
    (OUT / "upbit_markets.json").write_text(json.dumps(mk))
    sel = [m for m in mk if m[4:] in bases]
    print(len(mk), "KRW markets,", len(sel), "with a Binance perp", flush=True)
    for i, m in enumerate(sel):
        r = upbit_days(m)
        if i % 20 == 0:
            print(i, m, len(r), flush=True)
    print("done")
