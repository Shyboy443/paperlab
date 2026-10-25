"""Fetch Binance SPOT ZECUSDT 1h klines 2024-09-01 .. 2026-10-11 (public market data) into a gz CSV."""
import csv, gzip, hashlib, json, sys, time, urllib.request
from datetime import datetime, timezone
out = sys.argv[1]
ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)
start, end, rows = ms("2024-09-01T00:00"), ms("2026-10-11T00:00"), []
cur = start
while cur < end:
    url = f"https://api.binance.com/api/v3/klines?symbol=ZECUSDT&interval=1h&startTime={cur}&endTime={end - 1}&limit=1000"
    part = json.load(urllib.request.urlopen(url, timeout=30))
    if not part: break
    rows += [r[:6] for r in part]
    cur = int(part[-1][0]) + 3600_000
    time.sleep(0.15)
with gzip.open(out, "wt", newline="") as f:
    w = csv.writer(f); w.writerow(["open_time", "open", "high", "low", "close", "volume"]); w.writerows(rows)
print(len(rows), hashlib.sha256(open(out, "rb").read()).hexdigest())
