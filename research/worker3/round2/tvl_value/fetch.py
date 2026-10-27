"""Download DefiLlama TVL / fees / revenue history for every protocol or chain whose token symbol matches a Binance
USDT perp in the survivorship-free panel, plus CoinMetrics community market cap / supply. Public, free, no keys.
Cache: data/w3r2_tvl_value/. Only trimmed series are stored (date, value)."""
import json, sys, time, concurrent.futures as cf, urllib.request
from pathlib import Path
import numpy as np
OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r2_tvl_value")
RAW = OUT / "raw"
L = lambda f: json.load(open(f, encoding="utf-8"))


def get(url, tries=4):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "paperlab-research"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            code = getattr(e, "code", None)
            if code in (400, 402, 404):
                return {"_error": code}
            time.sleep(3 * (a + 1))
    return {"_error": "fail"}


def bases():
    z = np.load(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r2_rotation/panel_1d.npz")
    syms = [str(s) for s in z["syms"]]
    b = {}
    for s in syms:
        if s.endswith("USDT"):
            k = s[:-4]
            b[k] = s
            if k.startswith("1000"):
                b[k[4:]] = s
            if k.startswith("1000000"):
                b[k[7:]] = s
    return b


def candidates():
    B = bases()
    pr = L(RAW / "protocols.json")
    groups = {}
    for p in pr:
        sym = (p.get("symbol") or "").upper()
        if sym not in B or p.get("category") == "CEX":
            continue
        slug = p["parentProtocol"].split("#", 1)[1] if p.get("parentProtocol") else p["slug"]
        groups.setdefault((sym, slug), 0)
        groups[(sym, slug)] += p.get("tvl") or 0
    chains = [(c["tokenSymbol"].upper(), c["name"], c.get("gecko_id")) for c in L(RAW / "chains.json")
              if (c.get("tokenSymbol") or "").upper() in B]
    return B, groups, chains


def fetch_protocol(slug):
    f = OUT / "protocol" / f"{slug}.json"
    if f.exists():
        return
    d = get(f"https://api.llama.fi/protocol/{slug}")
    if "_error" in d:
        rec = d
    else:
        rec = dict(slug=slug, name=d.get("name"), symbol=d.get("symbol"), gecko_id=d.get("gecko_id"),
                   category=d.get("category"), tvl=[[x["date"], x["totalLiquidityUSD"]] for x in d.get("tvl", [])])
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rec))


def fetch_fees(slug, dtype):
    f = OUT / dtype / f"{slug}.json"
    if f.exists():
        return
    d = get(f"https://api.llama.fi/summary/fees/{slug}?dataType={dtype}")
    rec = d if "_error" in d else dict(slug=slug, chart=d.get("totalDataChart", []))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rec))


def fetch_chain(name):
    f = OUT / "chain" / f"{name}.json"
    if f.exists():
        return
    d = get(f"https://api.llama.fi/v2/historicalChainTvl/{urllib.request.quote(name)}")
    rec = d if isinstance(d, dict) else [[x["date"], x["tvl"]] for x in d]
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rec))


if __name__ == "__main__":
    B, groups, chains = candidates()
    slugs = sorted({s for _, s in groups})
    print(len(slugs), "protocol slugs,", len(chains), "chains", flush=True)
    with cf.ThreadPoolExecutor(4) as ex:
        list(ex.map(fetch_protocol, slugs))
        print("protocols done", flush=True)
        list(ex.map(lambda s: fetch_fees(s, "dailyFees"), slugs))
        list(ex.map(lambda s: fetch_fees(s, "dailyRevenue"), slugs))
        print("fees done", flush=True)
        list(ex.map(fetch_chain, [c[1] for c in chains]))
        # chain-level fees (gas) are DefiLlama "protocols" of type chain; try the chain name slug
        list(ex.map(lambda c: fetch_fees(c[1].lower().replace(" ", "-"), "dailyFees"), chains))
    json.dump(dict(groups={f"{k[0]}|{k[1]}": v for k, v in groups.items()}, chains=chains),
              open(OUT / "candidates.json", "w", encoding="utf-8"))
    print("done")
