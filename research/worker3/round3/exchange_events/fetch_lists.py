"""Fetch Binance announcement lists (public CMS API, no keys) for catalogs 161 Delisting, 48 New listing, 49 Latest news."""
import json, time, urllib.request
from pathlib import Path
OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r3_exchange_events")
U = "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query?type=1&catalogId={c}&pageNo={p}&pageSize=20"
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())
for cat in (161, 48, 49):
    f = OUT / f"list_{cat}.json"
    if f.exists(): continue
    arts, p = [], 1
    while True:
        d = get(U.format(c=cat, p=p))["data"]["catalogs"][0]
        a = d["articles"]
        if not a: break
        arts += a; print(cat, p, len(arts), d["total"], flush=True)
        if len(arts) >= d["total"]: break
        p += 1; time.sleep(2.0)
    f.write_text(json.dumps(arts))
