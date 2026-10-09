"""Trading Desk news: the last 24 h of headlines from four crypto outlets' public RSS feeds, de-duplicated and tagged by
coin. Only titles, a short summary, the outlet, the time and the link are kept."""
from __future__ import annotations

import email.utils
import html
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Sequence

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
}
# Words that tag a headline to a coin. Upper-case tickers must match as written (so "ton" in a sentence is not TON).
COIN_WORDS = {
    "BTC": (r"\bbitcoin\b", r"\bBTC\b"), "ETH": (r"\bethereum\b", r"\bether\b", r"\bETH\b"),
    "SOL": (r"\bsolana\b", r"\bSOL\b"), "BNB": (r"\bBNB\b", r"\bbnb chain\b", r"\bbinance coin\b"),
    "XRP": (r"\bXRP\b", r"\bripple\b"), "DOGE": (r"\bdogecoin\b", r"\bDOGE\b"),
    "ADA": (r"\bcardano\b", r"\bADA\b"), "AVAX": (r"\bavalanche\b", r"\bAVAX\b"),
    "LINK": (r"\bchainlink\b", r"\bLINK\b"), "TON": (r"\btoncoin\b", r"\bTON\b", r"\bthe open network\b"),
}
DAY_MS = 86_400_000
UA = {"User-Agent": "PaperLab-TradingDesk/1.0 (paper trading research; RSS reader)"}


def http_text(url: str, timeout: float = 12.0) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _strip(text: str | None, n: int = 280) -> str:
    t = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", t).strip()[:n]


def _time_ms(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(email.utils.parsedate_to_datetime(text.strip()).timestamp() * 1000)
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(text.strip().replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def parse_feed(outlet: str, xml_text: str) -> list[dict[str, Any]]:
    """RSS 2.0 items or Atom entries -> {outlet, title, summary, url, ts}."""
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        get = {c.tag.rsplit("}", 1)[-1]: c for c in item}
        title = _strip(get["title"].text if "title" in get else "", 200)
        link = get.get("link")
        url = (link.text or link.get("href") or "") if link is not None else ""
        when = None
        for k in ("pubDate", "published", "updated", "date"):
            if k in get:
                when = _time_ms(get[k].text)
                if when:
                    break
        body = get.get("description", get.get("summary"))
        summary = _strip(body.text if body is not None else "")
        if title:
            out.append({"outlet": outlet, "title": title, "summary": summary, "url": url.strip(), "ts": when})
    return out


def tag_coins(text: str, universe: Sequence[str]) -> list[str]:
    tags = []
    for coin in universe:
        pats = COIN_WORDS.get(coin, (rf"\b{re.escape(coin)}\b",))
        if any(re.search(p, text, 0 if any(ch.isupper() for ch in p) else re.I) for p in pats):
            tags.append(coin)
    return tags


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()[:90]


def news(universe: Sequence[str], fetch: Callable[[str], str] | None = None, now_ms: int | None = None,
         hours: int = 24) -> dict[str, Any]:
    """Headlines of the last `hours`, newest first, de-duplicated by title, each tagged with the coins it names."""
    fetch = fetch or http_text
    now_ms = now_ms or int(time.time() * 1000)
    status: dict[str, str] = {}

    def one(item: tuple[str, str]) -> list[dict[str, Any]]:
        outlet, url = item
        try:
            rows = parse_feed(outlet, fetch(url))
            status[outlet] = f"ok ({len(rows)})"
            return rows
        except Exception as exc:
            status[outlet] = f"error: {type(exc).__name__}"
            return []
    with ThreadPoolExecutor(4) as ex:
        rows = [r for part in ex.map(one, FEEDS.items()) for r in part]
    seen, out = set(), []
    for r in sorted(rows, key=lambda x: -(x["ts"] or 0)):
        if not r["ts"] or r["ts"] < now_ms - hours * 3_600_000 or r["ts"] > now_ms + 600_000:
            continue
        k = _key(r["title"])
        if k in seen:
            continue
        seen.add(k)
        out.append({**r, "coins": tag_coins(r["title"] + " " + r["summary"], universe)})
    return {"items": out, "sources": status, "as_of_ms": now_ms}


def for_coins(feed: dict[str, Any], coins: Sequence[str], per_coin: int = 6, general: int = 5) -> dict[str, Any]:
    """The headlines that name each coin, plus the newest market-wide ones (no coin tag)."""
    items = feed.get("items") or []
    return {"by_coin": {c: [i for i in items if c in i["coins"]][:per_coin] for c in coins},
            "market": [i for i in items if not i["coins"]][:general], "sources": feed.get("sources")}
