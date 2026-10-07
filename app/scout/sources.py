"""The scout's sources: Reddit (official API, application-only OAuth with the operator's free app key) and
Benzinga news through Alpaca's news API (the Alpaca paper keys). Read-only; hosts come from app/config.py and any
other host is refused before a socket opens. Keys travel only in request headers and never appear in an error.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping, Sequence

from app.config import COINGECKO_TRENDING, REDDIT_API, REDDIT_TOKEN_URL, host_of

SUBREDDITS: tuple[str, ...] = ("CryptoCurrency", "CryptoMarkets", "Bitcoin", "ethtrader", "solana", "XRP", "dogecoin",
                               "wallstreetbets", "stocks", "StockMarket", "options", "investing")
# Alpaca news tickers for the universe (crypto news is tagged with the USD pair)
NEWS_SYMBOLS: dict[str, str] = {"BTC": "BTCUSD", "ETH": "ETHUSD", "SOL": "SOLUSD", "XRP": "XRPUSD", "DOGE": "DOGEUSD",
                                "ARB": "ARBUSD", "ENA": "ENAUSD", "SPY": "SPY", "QQQ": "QQQ", "AAPL": "AAPL",
                                "NVDA": "NVDA", "TSLA": "TSLA", "AMD": "AMD", "MSFT": "MSFT", "META": "META",
                                "AMZN": "AMZN", "GOOGL": "GOOGL"}
FROM_NEWS = {v: k for k, v in NEWS_SYMBOLS.items()} | {"GOOG": "GOOGL", "BTC": "BTC", "ETH": "ETH"}
USER_AGENT = "python:paperlab-scout:1.0 (read-only research)"


class SourceError(RuntimeError):
    def __init__(self, status: int | None, message: str):
        super().__init__(f"{status or 'network'} error: {message}")
        self.status = status


class RedditClient:
    """Application-only OAuth (client_credentials) for public, read-only listings."""

    def __init__(self, client_id: str, secret: str, opener: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.time):
        self._id, self._secret = client_id, secret
        self._open = opener or urllib.request.urlopen
        self.clock = clock
        self.allowed_hosts = frozenset({host_of(REDDIT_TOKEN_URL), host_of(REDDIT_API)})
        self._token: str | None = None
        self._token_exp = 0.0
        self.ratelimit_remaining: float | None = None

    def _call(self, req: urllib.request.Request) -> tuple[Any, Mapping[str, str]]:
        if host_of(req.full_url) not in self.allowed_hosts:
            raise SourceError(None, f"refusing host {host_of(req.full_url)!r}")
        try:
            with self._open(req, timeout=20) as r:
                return json.loads(r.read() or b"{}"), dict(getattr(r, "headers", {}) or {})
        except urllib.error.HTTPError as e:
            raise SourceError(e.code, str(e.reason)[:120]) from None
        except urllib.error.URLError as e:
            raise SourceError(None, str(e.reason)[:120]) from None

    def _bearer(self) -> str:
        if self._token and self.clock() < self._token_exp - 60:
            return self._token
        basic = base64.b64encode(f"{self._id}:{self._secret}".encode()).decode()
        req = urllib.request.Request(REDDIT_TOKEN_URL, data=b"grant_type=client_credentials", method="POST",
                                     headers={"Authorization": "Basic " + basic, "User-Agent": USER_AGENT,
                                              "Content-Type": "application/x-www-form-urlencoded"})
        body, _ = self._call(req)
        if not body.get("access_token"):
            raise SourceError(401, str(body.get("error") or "no token issued")[:80])
        self._token = str(body["access_token"])
        self._token_exp = self.clock() + float(body.get("expires_in") or 3600)
        return self._token

    def _get(self, path: str) -> Any:
        req = urllib.request.Request(REDDIT_API + path, headers={"Authorization": "bearer " + self._bearer(),
                                                                 "User-Agent": USER_AGENT})
        body, headers = self._call(req)
        try:
            self.ratelimit_remaining = float(headers.get("x-ratelimit-remaining") or headers.get("X-Ratelimit-Remaining"))
        except (TypeError, ValueError):
            pass
        return body

    async def listing(self, sub: str, kind: str = "new", limit: int = 100) -> list[dict[str, Any]]:
        """The newest posts ("new") or comments ("comments") of a subreddit, as plain items."""
        body = await asyncio.to_thread(self._get, f"/r/{urllib.parse.quote(sub)}/{kind}?limit={int(limit)}&raw_json=1")
        out = []
        for ch in ((body or {}).get("data") or {}).get("children") or []:
            d = ch.get("data") or {}
            text = (d.get("title") or "") + ("\n" + d["selftext"] if d.get("selftext") else "") if kind == "new" else (d.get("body") or "")
            out.append({"id": "reddit:" + str(d.get("name") or d.get("id")), "source": "reddit", "channel": "r/" + sub,
                        "kind": "post" if kind == "new" else "comment", "created_ms": int(float(d.get("created_utc") or 0) * 1000),
                        "text": text[:1200], "url": "https://www.reddit.com" + str(d.get("permalink") or ""),
                        "score": int(d.get("score") or 0), "comments": int(d.get("num_comments") or 0)})
        return out

    async def fetch_balance(self) -> dict[str, Any]:
        """Provider-card check: a token can be issued with these keys (nothing else is read)."""
        await asyncio.to_thread(self._bearer)
        return {"wallet": 0.0, "available": 0.0, "currency": "", "access": "read-only token issued"}

    async def close(self) -> None:
        return None


def _trending_get(opener: Callable[..., Any]) -> Any:
    req = urllib.request.Request(COINGECKO_TRENDING, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with opener(req, timeout=20) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SourceError(e.code, str(e.reason)[:120]) from None
    except urllib.error.URLError as e:
        raise SourceError(None, str(e.reason)[:120]) from None


async def fetch_trending(opener: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    """CoinGecko's trending coins (public, no key): rank 1 = most searched right now."""
    body = await asyncio.to_thread(_trending_get, opener or urllib.request.urlopen)
    out = []
    for c in (body or {}).get("coins") or []:
        i = c.get("item") or {}
        if not i.get("symbol"):
            continue
        out.append({"rank": int(i.get("score") or 0) + 1, "symbol": str(i["symbol"]).upper(), "name": str(i.get("name") or ""),
                    "cg_id": str(i.get("id") or ""), "mcap_rank": i.get("market_cap_rank")})
    return out


async def fetch_news(alpaca: Any, symbols: Sequence[str], start_ms: int, end_ms: int | None = None,
                     max_pages: int = 50) -> list[dict[str, Any]]:
    """Benzinga news (via Alpaca) for the universe symbols in [start, end), oldest first."""
    from app.live.alpaca_market import iso, parse_ts
    tickers = ",".join(NEWS_SYMBOLS[s] for s in symbols if s in NEWS_SYMBOLS)
    out: dict[str, dict[str, Any]] = {}
    token = None
    for _ in range(max_pages):
        q = {"symbols": tickers, "start": iso(start_ms), "limit": 50, "sort": "asc", "include_content": "false"}
        if end_ms:
            q["end"] = iso(end_ms)
        if token:
            q["page_token"] = token
        page = await alpaca.request("GET", "/v1beta1/news?" + urllib.parse.urlencode(q), data=True)
        for n in (page or {}).get("news") or []:
            syms = sorted({FROM_NEWS[t] for t in n.get("symbols") or [] if t in FROM_NEWS})
            text = (n.get("headline") or "") + ("\n" + n["summary"] if n.get("summary") else "")
            out[str(n.get("id"))] = {"id": f"news:{n.get('id')}", "source": "news", "channel": str(n.get("source") or "benzinga"),
                                     "kind": "headline", "created_ms": parse_ts(n["created_at"]), "text": text[:1200],
                                     "url": n.get("url") or "", "score": 0, "comments": 0, "symbols": syms,
                                     "headline": n.get("headline") or ""}
        token = (page or {}).get("next_page_token")
        if not token:
            break
    return sorted(out.values(), key=lambda x: x["created_ms"])
