"""Download CoinGecko public category members (no key) -> data/w3r2_sector_rotation/cg/<cat>_p<n>.json (cached)."""
import json, time, urllib.request
from pathlib import Path
OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r2_sector_rotation/cg")
CATS = {"layer-1": 3, "smart-contract-platform": 2, "layer-2": 2, "decentralized-finance-defi": 3, "artificial-intelligence": 3,
        "meme-token": 4, "gaming": 2, "metaverse": 2, "exchange-based-tokens": 1, "real-world-assets-rwa": 2,
        "privacy-coins": 1, "oracle": 1, "storage": 1, "depin": 2, "zero-knowledge-zk": 1, "liquid-staking-governance-tokens": 1,
        "lending-borrowing": 1, "decentralized-exchange": 2, "infrastructure": 2, "payment-solutions": 1}
for cat, pages in CATS.items():
    for p in range(1, pages + 1):
        f = OUT / f"{cat}_p{p}.json"
        if f.exists():
            continue
        url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&category={cat}&per_page=250&page={p}&order=market_cap_desc"
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "research"})
                data = json.loads(urllib.request.urlopen(req, timeout=60).read())
                f.write_text(json.dumps([{k: x.get(k) for k in ("id", "symbol", "name", "market_cap_rank", "market_cap")} for x in data]))
                print(cat, p, len(data), flush=True)
                break
            except Exception as e:
                print("err", cat, p, e, flush=True); time.sleep(30)
        time.sleep(8)
