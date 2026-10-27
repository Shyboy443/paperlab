"""Upbit public notice list, 'trade' category (listings, warnings, delistings). No keys."""
import json, time, urllib.request
from pathlib import Path
OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r3_exchange_events/upbit_trade.json")
U = "https://api-manager.upbit.com/api/v1/announcements?os=web&page={p}&per_page=20&category=trade"
out, p = [], 1
while True:
    for k in range(8):
        try:
            req = urllib.request.Request(U.format(p=p), headers={"User-Agent": "Mozilla/5.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=30).read())["data"]; break
        except Exception as e:
            print("retry", p, e, flush=True); time.sleep(10 * (k + 1))
    if not d["notices"]: break
    out += d["notices"]; print(p, d["total_pages"], out[-1]["listed_at"], flush=True)
    if p >= d["total_pages"]: break
    p += 1; time.sleep(3.0)
OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
