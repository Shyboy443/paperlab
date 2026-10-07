"""News -> structured, de-duplicated EVENTS -> does any event type move our coins? (research only; never trades)

    python research/news_events.py            # fetch, cluster, classify, store (news_items, news_events), study

Sources (legitimate, accessible):
  * Benzinga headlines via the Alpaca news API for the crypto symbols it tags (BTC ETH SOL XRP DOGE ARB ENA). Fetched
    ON THE SERVER with the operator's Alpaca paper keys from the encrypted vault (the key never leaves Railway).
  * Bybit's official announcements API (public): listings, delistings, product updates, news.

Each item becomes structured fields: symbols, event_type (rule taxonomy below), tone (-1..1, the scout's word list),
source_reliability (official exchange / Benzinga = high), btc_relevance. Items about the same coin(s) and event type
within EVENT_WINDOW_H whose titles share >= JACCARD of their words are ONE event (ten articles about one event are one
event, not ten signals). importance = 0.4 + 0.15 x extra articles + 0.1 x extra sources (capped at 1), +0.2 for
regulatory / hack / ETF / listing-delisting / macro types.

PRE-REGISTERED STUDY (2026-10-03): events tagged with one of our 30 coins (macro and regulatory events without a coin:
BTC). Entry 60 s after the FIRST article, returns market-adjusted (minus the 30-coin mean), horizons 15m / 1h / 4h,
statistics averaged per day, halves of the window. Per event type with >= 15 events:
  RISK FILTER   mean |adjusted 1h move| after the event >= 1.25 x the same coin's baseline |adjusted 1h move| in EACH half
                -> the risk engine may cut size / avoid entries around such events
  SIGNAL        tone-signed adjusted 1h return (|tone| >= 0.3) net of taker costs > 0 in EACH half, day t >= 2, >= 20 events
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "research"))
from study_altdata import TAKER_FEE_RT, tstat  # noqa: E402

DB = PROJECT / "data" / "research" / "market.duckdb"
OUT = PROJECT / "docs" / "NEWS_EVENT_STUDY.json"
DAY, H = 86_400_000, 3_600_000
EVENT_WINDOW_H = 6
JACCARD = 0.25

TAXONOMY = [   # first match wins; order = priority
    ("hack", r"\b(hack|hacked|exploit|exploited|drain(ed)?|stolen|breach|attack(er)?)\b"),
    ("delisting", r"\b(delist|delisting|delists|remove[sd]? (?:the )?trading)\b"),
    ("listing", r"\b(list(?:s|ed|ing)? (?:on|the)|new listing|will list|launches? .*perpetual|adds? .*trading)\b"),
    ("etf", r"\b(etf|etfs|exchange[- ]traded)\b"),
    ("regulatory", r"\b(sec|cftc|regulat\w*|lawsuit|court|judge|ban|bans|banned|sanction\w*|congress|senate|bill|legal\w*|license|compliance|doj|subpoena)\b"),
    ("macro", r"\b(fed|fomc|powell|cpi|inflation|rate cut|rate hike|interest rates?|jobs report|payrolls|tariff\w*|treasury|recession|gdp)\b"),
    ("whale", r"\b(whale|whales|transfer(?:red)?|moved|outflows?|inflows?|accumulat\w*|treasury (?:company|firm))\b"),
    ("partnership", r"\b(partner\w*|integrat\w*|collaborat\w*|adopt\w*|launch(?:es|ed)?)\b"),
    ("unlock", r"\b(unlock\w*|vesting|airdrop\w*|burn\w*|supply)\b"),
    ("price_action", r"\b(surge[sd]?|soar\w*|rall(?:y|ies|ied)|plunge[sd]?|crash\w*|tumble[sd]?|slump\w*|jump\w*|drop\w*|falls?|rises?|record high|all-time high)\b"),
]
HIGH_IMPACT = {"regulatory", "hack", "etf", "listing", "delisting", "macro"}
COIN_NAMES = {"BTC": "bitcoin|btc", "ETH": "ethereum|ether|eth", "SOL": "solana|sol", "XRP": "xrp|ripple",
              "DOGE": "dogecoin|doge", "HYPE": "hyperliquid|hype", "ZEC": "zcash|zec", "NEAR": "near protocol|near",
              "ENA": "ethena|ena", "SUI": "sui", "ADA": "cardano|ada", "ARB": "arbitrum|arb", "UNI": "uniswap|uni",
              "1000PEPE": "pepe", "PUMPFUN": "pump\\.fun|pumpfun", "TAO": "bittensor|tao", "LINK": "chainlink|link",
              "WLD": "worldcoin|wld", "BNB": "bnb|binance coin", "ONDO": "ondo", "AAVE": "aave", "TRUMP": "trump coin|\\$trump",
              "AVAX": "avalanche|avax", "XMR": "monero|xmr", "FARTCOIN": "fartcoin", "LTC": "litecoin|ltc", "XPL": "plasma|xpl"}

REMOTE = r'''
import asyncio, json, sys, time
sys.path.insert(0, "/srv/paperlab")
from app.exchange.alpaca_client import AlpacaClient
from app.live.v9_worker import alpaca_keys
from app.scout.sources import fetch_news
keys = alpaca_keys()
if keys is None:
    print("[]"); sys.exit(0)
client = AlpacaClient("testnet", *keys)
START = int(sys.argv[1])
news = asyncio.run(fetch_news(client, ["BTC", "ETH", "SOL", "XRP", "DOGE", "ARB", "ENA"], START, int(time.time() * 1000), max_pages=3000))
print(json.dumps([{"id": n["id"], "ts": n["created_ms"], "title": n["headline"], "channel": n["channel"],
                   "symbols": n["symbols"]} for n in news]))
'''


def fetch_benzinga(start: int) -> list[dict]:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    res = subprocess.run([shutil.which("railway") or "railway", "ssh", "--service", "paperlab", "--", "sh", "-c",
                          f"cat > /tmp/news_fetch.py && cd /srv/paperlab && python /tmp/news_fetch.py {start}"],
                         input=REMOTE.encode(), capture_output=True, env=env, cwd=str(PROJECT), timeout=1800)
    text = res.stdout.decode(errors="replace")
    return json.loads(text[text.index("["):])


def fetch_bybit(start: int) -> list[dict]:
    out, page = [], 1
    while True:
        url = f"https://api.bybit.com/v5/announcements/index?locale=en-US&limit=100&page={page}"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "paperlab-research"}), timeout=30) as r:
            rows = (json.loads(r.read()).get("result") or {}).get("list") or []
        if not rows:
            break
        for a in rows:
            ts = int(a.get("publishTime") or a.get("dateTimestamp") or 0)
            if ts >= start:
                out.append({"id": "bybit:" + a["url"].rstrip("/").rsplit("-", 1)[-1], "ts": ts, "title": a["title"],
                            "channel": "bybit:" + a["type"]["key"], "symbols": []})
        if min(int(a.get("publishTime") or a.get("dateTimestamp") or 0) for a in rows) < start:
            break
        page += 1
        time.sleep(0.2)
    return out


def classify(title: str) -> str:
    t = title.lower()
    for name, rx in TAXONOMY:
        if re.search(rx, t):
            return name
    return "other"


def tag_coins(title: str, given: list[str]) -> list[str]:
    t = " " + title.lower() + " "
    found = {c for c, rx in COIN_NAMES.items() if re.search(r"(?<![a-z0-9])(" + rx + r")(?![a-z0-9])", t)}
    return sorted(found | set(given))


def words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2}


def build_events(items: pd.DataFrame) -> pd.DataFrame:
    """Greedy clustering in time order: an item joins an open event of the same (coin key, type) within the window
    when the titles overlap enough; otherwise it opens a new event."""
    from app.scout.text import tone as word_tone
    events, open_ev = [], {}
    for it in items.sort_values("ts").itertuples():
        key = (",".join(it.coins) or "MARKET", it.event_type)
        w = words(it.title)
        ev = open_ev.get(key)
        if ev is not None and it.ts - ev["ts"] <= EVENT_WINDOW_H * H and \
                len(w & ev["words"]) / max(1, len(w | ev["words"])) >= JACCARD:
            ev["n_items"] += 1
            ev["sources"].add(it.channel)
            ev["words"] |= w
            ev["tones"].append(word_tone(it.title) or 0.0)
            continue
        ev = {"event_id": it.id, "ts": int(it.ts), "coins": it.coins, "event_type": it.event_type, "title": it.title,
              "n_items": 1, "sources": {it.channel}, "words": w, "tones": [word_tone(it.title) or 0.0],
              "reliability": "high" if it.channel.startswith("bybit") or it.channel.lower() in ("benzinga",) else "medium"}
        open_ev[key] = ev
        events.append(ev)
    rows = []
    for e in events:
        imp = min(1.0, 0.4 + 0.15 * (e["n_items"] - 1) + 0.1 * (len(e["sources"]) - 1) + (0.2 if e["event_type"] in HIGH_IMPACT else 0))
        btc_rel = 1.0 if "BTC" in e["coins"] else (0.8 if e["event_type"] in ("macro", "regulatory", "etf") and not e["coins"] else 0.3)
        rows.append({"event_id": e["event_id"], "ts": e["ts"], "coins": ",".join(e["coins"]), "event_type": e["event_type"],
                     "title": e["title"], "n_items": e["n_items"], "n_sources": len(e["sources"]),
                     "news_sentiment": round(float(np.mean(e["tones"])), 3), "news_importance": round(imp, 2),
                     "btc_relevance": btc_rel, "source_reliability": e["reliability"]})
    return pd.DataFrame(rows)


def study(con, ev: pd.DataFrame) -> dict:
    px = con.execute("SELECT symbol, ts, fwd_15m, fwd_1h, fwd_4h FROM features_5m").df()
    for h in ("15m", "1h", "4h"):
        px["adj_" + h] = px["fwd_" + h] - px.groupby("ts")["fwd_" + h].transform("mean")
    base = px.groupby("symbol")["adj_1h"].apply(lambda x: x.abs().mean()).to_dict()
    rows = []
    for e in ev.itertuples():
        coins = [c for c in (e.coins.split(",") if e.coins else []) if c]
        if not coins and e.event_type in ("macro", "regulatory", "etf"):
            coins = ["BTC"]
        d = (int(e.ts) + 60_000 + 299_999) // 300_000 * 300_000           # the first 5m decision >= 60 s after
        for c in coins:
            rows.append({"event_id": e.event_id, "event_type": e.event_type, "symbol": c + "USDT", "ts": d,
                         "tone": e.news_sentiment})
    m = pd.DataFrame(rows).merge(px, on=["symbol", "ts"], how="inner")
    if m.empty:
        return {}
    m["day"] = m["ts"] // DAY
    days = np.sort(px["ts"].unique() // DAY)
    split = int(days[len(days) // 2])
    m["base"] = m["symbol"].map(base)
    out = {}
    for typ, g in m.groupby("event_type"):
        if len(g) < 15:
            out[typ] = {"events": int(len(g)), "verdict": "TOO FEW"}
            continue
        ratio = {}
        for half, sub in (("h1", g[g["day"] < split]), ("h2", g[g["day"] >= split])):
            ratio[half] = round(float(sub["adj_1h"].abs().mean() / sub["base"].mean()), 3) if len(sub) else None
        s = g[g["tone"].abs() >= 0.3]
        net = (np.sign(s["tone"]) * s["adj_1h"] - TAKER_FEE_RT) * 1e4
        dn = net.groupby(s["day"]).mean()
        mm, t, n = tstat(dn.values)
        h1 = float(dn[dn.index < split].mean()) if (dn.index < split).any() else None
        h2 = float(dn[dn.index >= split].mean()) if (dn.index >= split).any() else None
        risk = all(v is not None and v >= 1.25 for v in ratio.values())
        sig = len(s) >= 20 and h1 is not None and h2 is not None and h1 > 0 and h2 > 0 and t >= 2
        out[typ] = {"events": int(len(g)), "vol_ratio_1h": ratio, "mean_abs_adj_1h_bp": round(float(g["adj_1h"].abs().mean() * 1e4), 1),
                    "toned_events": int(len(s)), "tone_net_bp": round(mm, 1) if s.size else None, "tone_t": round(t, 2) if s.size else None,
                    "tone_halves": [None if h1 is None else round(h1, 1), None if h2 is None else round(h2, 1)],
                    "verdict": ("SIGNAL" if sig else "") + (" RISK FILTER" if risk else "") or "NO EFFECT"}
    return out


def main() -> None:
    t0 = time.time()
    con = duckdb.connect(str(DB))
    start = int(con.execute("SELECT min(ts) FROM features_5m").fetchone()[0])
    bz = fetch_benzinga(start)
    by = fetch_bybit(start)
    print(f"fetched {len(bz)} Benzinga headlines, {len(by)} Bybit announcements ({time.time() - t0:.0f}s)", flush=True)
    items = pd.DataFrame(bz + by)
    items["event_type"] = items["title"].map(classify)
    items["coins"] = [tag_coins(t, s) for t, s in zip(items["title"], items["symbols"])]
    ev = build_events(items)
    items_db = items.assign(coins=items["coins"].map(",".join), symbols=items["symbols"].map(",".join))
    con.register("items_df", items_db)
    con.execute("CREATE OR REPLACE TABLE news_items AS SELECT * FROM items_df")
    con.register("ev_df", ev)
    con.execute("CREATE OR REPLACE TABLE news_events AS SELECT * FROM ev_df")
    print(f"{len(items)} items -> {len(ev)} events (dedup ratio {len(items) / max(1, len(ev)):.2f}); types: "
          + ", ".join(f"{k} {v}" for k, v in ev["event_type"].value_counts().items()), flush=True)
    res = study(con, ev)
    con.close()
    for k, v in sorted(res.items(), key=lambda kv: -kv[1]["events"]):
        print(f"  {k:13s} {json.dumps(v)}")
    OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "items": int(len(items)),
                               "events": int(len(ev)), "rules": __doc__.split("PRE-REGISTERED STUDY")[1], "by_type": res,
                               "examples": ev.sort_values("news_importance", ascending=False).head(15)[
                                   ["coins", "event_type", "title", "n_items", "n_sources", "news_sentiment",
                                    "news_importance", "btc_relevance", "source_reliability"]].to_dict("records")},
                              indent=1, default=str))
    print(f"wrote {OUT.relative_to(PROJECT)} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
