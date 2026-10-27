"""Fetch Deribit DVOL daily candles (public endpoint, no key) for BTC and ETH into data/w3r1_options_iv/."""
import json, time, urllib.request
from pathlib import Path
OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r1_options_iv"); OUT.mkdir(parents=True, exist_ok=True)
URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data?currency={c}&start_timestamp={s}&end_timestamp={e}&resolution=1D"
for cur in ("BTC", "ETH"):
    rows, end = {}, 1793000000000
    while True:
        r = json.loads(urllib.request.urlopen(URL.format(c=cur, s=1577836800000, e=end), timeout=30).read())["result"]
        for x in r["data"]:
            rows[x[0]] = x
        cont = r.get("continuation")
        if not cont or not r["data"] or cont >= end:
            break
        end = cont; time.sleep(0.5)
    data = [rows[k] for k in sorted(rows)]
    (OUT / f"dvol_{cur}_1d.json").write_text(json.dumps(data))
    print(cur, len(data), data[0][0], data[-1][0])
