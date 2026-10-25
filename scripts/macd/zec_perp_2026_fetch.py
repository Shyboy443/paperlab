"""Binance USD-M public data for ZECUSDT: 4h and 1h klines (completed only) and funding settlements with mark price."""
import json, sys, time, urllib.request
from datetime import datetime, timezone
out = sys.argv[1]
ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)
now = int(time.time() * 1000)
def get(url):
    for k in range(5):
        try: return json.load(urllib.request.urlopen(url, timeout=30))
        except Exception:
            time.sleep(2 + 2 * k)
    raise RuntimeError(url)
def klines(interval, step, start):
    rows, cur = [], start
    while cur < now:
        part = get(f"https://fapi.binance.com/fapi/v1/klines?symbol=ZECUSDT&interval={interval}&startTime={cur}&limit=1500")
        if not part: break
        rows += [[int(r[0]), r[1], r[2], r[3], r[4], r[5]] for r in part if int(r[0]) + step <= now]   # completed only
        cur = int(part[-1][0]) + step; time.sleep(0.2)
        if len(part) < 1500: break
    return rows
def funding(start):
    rows, cur = [], start
    while cur < now:
        part = get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol=ZECUSDT&startTime={cur}&limit=1000")
        if not part: break
        rows += [[int(r["fundingTime"]), r["fundingRate"], r.get("markPrice")] for r in part]
        cur = int(part[-1]["fundingTime"]) + 1; time.sleep(0.2)
        if len(part) < 1000: break
    return rows
data = {"fetched_utc": datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(),
        "k4h": klines("4h", 14_400_000, ms("2025-09-01T00:00")), "k1h": klines("1h", 3_600_000, ms("2025-12-31T00:00")),
        "funding": funding(ms("2025-12-31T00:00"))}
json.dump(data, open(out, "w"))
print("fetched", data["fetched_utc"], {k: len(v) for k, v in data.items() if isinstance(v, list)})
