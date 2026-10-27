"""Build ONE coarse sector label per Binance USDT perp from CoinGecko public category members (cached JSON pages).

Matching: perp base symbol (prefixes 1000000/1000/1M stripped, a few rebrand aliases) -> CoinGecko symbol; when several
CoinGecko coins share a symbol, the one with the largest market cap across all fetched categories wins.
Label priority (first hit wins; structural, long-lived labels first to reduce relabelling look-ahead):
  meme > l2 > ai > gaming > defi > rwa > infra > l1 (exchange-token group dropped: after DEX tokens go to defi only BNB was left) (most specific first;
  CoinGecko tags most L2s and many app chains as 'smart-contract-platform', so L1 is the fallback label).  Unmatched coins get no label and are never traded.
Writes data/w3r2_sector_rotation/sector_map.json. Frozen before any backtest.
"""
import json, re
from pathlib import Path
import numpy as np

CG = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r2_sector_rotation/cg")
OUT = Path(r"D:/Projects/BinanceBOt/paperlab/data/w3r2_sector_rotation/sector_map.json")
PANEL = Path(r"D:/Projects/BinanceBOt/paperlab/data/monthly20_r2_rotation/panel_1d.npz")
GROUPS = [
    ("meme", ["meme-token"]),
    ("l2", ["layer-2", "zero-knowledge-zk"]),
    ("ai", ["artificial-intelligence"]),
    ("gaming", ["gaming", "metaverse"]),
    ("defi", ["decentralized-finance-defi", "decentralized-exchange", "lending-borrowing", "liquid-staking-governance-tokens"]),
    ("rwa", ["real-world-assets-rwa"]),
    ("infra", ["oracle", "storage", "depin", "infrastructure", "payment-solutions", "privacy-coins"]),
    ("l1", ["layer-1", "smart-contract-platform"]),
]
EXCLUDE = {"PAXGUSDT", "XAUTUSDT"}   # gold tokens, not crypto sectors
# perp base -> today's CoinGecko symbol (renames / migrations)
ALIAS = {"MATIC": "pol", "FTM": "s", "RNDR": "render", "AGIX": "fet", "OCEAN": "fet", "EOS": "a", "MKR": "sky",
         "LUNA": "lunc", "LUNA2": "luna", "MC": "beam", "TOMO": "vic", "WAVES": "waves", "KEEP": "t", "NU": "t",
         "LINA": "lina", "BTT": "btt", "BTTC": "btt", "LIT": "lit", "STPT": "stpt", "MITH": "mith", "ANT": "ant",
         "BLUR": "blur", "RAD": "rad", "AMB": "amb", "SC": "sc"}


def base(sym):
    b = sym[:-4] if sym.endswith("USDT") else sym
    for pre in ("1000000", "1000", "1M"):
        if b.startswith(pre) and len(b) > len(pre) and b[len(pre)].isalpha():
            return b[len(pre):]
    return b


def main():
    cats = {}
    best = {}   # symbol -> (mcap, id)
    for f in CG.glob("*.json"):
        cat = re.sub(r"_p\d+$", "", f.stem)
        for x in json.loads(f.read_text()):
            cats.setdefault(x["id"], set()).add(cat)
            s = (x["symbol"] or "").lower(); mc = x.get("market_cap") or 0
            if s and (s not in best or mc > best[s][0]):
                best[s] = (mc, x["id"])
    syms = [str(s) for s in np.load(PANEL)["syms"]]
    mp, how = {}, {}
    for s in syms:
        if s in EXCLUDE:
            continue
        b = base(s)
        key = ALIAS.get(b, b.lower())
        if key not in best:
            continue
        cid = best[key][1]
        for lab, cl in GROUPS:
            if cats.get(cid, set()) & set(cl):
                mp[s] = lab; how[s] = cid; break
    cnt = {}
    for v in mp.values():
        cnt[v] = cnt.get(v, 0) + 1
    OUT.write_text(json.dumps(dict(map=mp, cg_id=how, counts=cnt, n_perps=len(syms), n_labelled=len(mp),
                                   priority=[g[0] for g in GROUPS]), indent=1))
    print(len(syms), "perps;", len(mp), "labelled;", cnt)


if __name__ == "__main__":
    main()
