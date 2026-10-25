"""Probe how far back the OKX history-mark endpoint actually goes.

Walks back ~50 pages and prints the (earliest ts) and the (gap between
last page's earliest and second-to-last page's earliest). If the gap
stops growing we have hit the endpoint's retention wall.
"""
import time
from research.round9_data import _http_get_json

inst = "BTC-USDT-SWAP"
after = None
for i in range(50):
    url = f"https://www.okx.com/api/v5/market/history-mark-price-candles?instId={inst}&bar=1m&limit=100"
    if after:
        url += f"&after={after}"
    data = _http_get_json(url)
    rows = data.get("data", [])
    if not rows:
        print(f"page {i}: empty -> stop")
        break
    ts_first = int(rows[0][0])
    ts_last = int(rows[-1][0])
    print(f"page {i}: rows={len(rows)} earliest_ts={ts_first} ({time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(ts_first/1000))}) latest_ts={ts_last} ({time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(ts_last/1000))})")
    after = ts_first
    if len(rows) < 100:
        print("last page partial -> stop")
        break
    time.sleep(0.2)
