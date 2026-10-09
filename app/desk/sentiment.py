"""Trading Desk sentiment: what the crowd is doing and saying about a coin, from free public sources.

X/Twitter's search API is paid, so the desk reads the crowd from:
- derivatives positioning on Bybit's public API: the funding rate (who pays to hold), the share of accounts long vs
  short over the last 24 h, and the 24 h change in open interest (new money in or out);
- CoinGecko's trending list (what retail is searching for);
- the social posts and headlines PaperLab's scout already collects for the coin (Reddit when enabled), with the scout's
  own tone score per post. Posts from accounts that look like paid-signal channels are dropped before the agent sees
  them (link-shortener / "signals" / "pump" / "VIP" / referral patterns).
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Sequence

from app.desk.market import http_json

SPAM = re.compile(r"(t\.me/|telegram\.me|discord\.gg|\bvip\b|\bsignals?\b|\bpump\b|100x|\bgiveaway\b|airdrop claim|"
                  r"ref(erral)?[ _-]?code|join (my|our)|bit\.ly|tinyurl)", re.I)


def _f(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def positioning(coin: str, fetch: Callable[[str], Any] = http_json) -> dict[str, Any]:
    sym = f"{coin}USDT"
    base = "https://api.bybit.com/v5/market"
    out: dict[str, Any] = {"source": "bybit linear perpetual"}
    try:
        t = ((fetch(f"{base}/tickers?category=linear&symbol={sym}").get("result") or {}).get("list") or [{}])[0]
        # Bybit's sign: positive = longs pay shorts, negative = shorts pay longs (per funding interval)
        out["funding_pct_longs_pay"] = None if _f(t.get("fundingRate")) is None else 100 * _f(t.get("fundingRate"))
        out["open_interest_usd"] = _f(t.get("openInterestValue"))
    except Exception as exc:
        out["tickers_error"] = type(exc).__name__
    try:
        rows = (fetch(f"{base}/account-ratio?category=linear&symbol={sym}&period=1h&limit=24").get("result") or {}).get("list") or []
        if rows:
            out["accounts_long_pct_now"] = 100 * _f(rows[0].get("buyRatio"))
            out["accounts_long_pct_24h_ago"] = 100 * _f(rows[-1].get("buyRatio"))
    except Exception as exc:
        out["ratio_error"] = type(exc).__name__
    try:
        rows = (fetch(f"{base}/open-interest?category=linear&symbol={sym}&intervalTime=1h&limit=25").get("result") or {}).get("list") or []
        if len(rows) >= 2 and _f(rows[-1].get("openInterest")):
            out["open_interest_change_24h_pct"] = 100 * (_f(rows[0]["openInterest"]) / _f(rows[-1]["openInterest"]) - 1)
    except Exception as exc:
        out["oi_error"] = type(exc).__name__
    return out


def trending(fetch: Callable[[str], Any] = http_json) -> list[str]:
    try:
        coins = fetch("https://api.coingecko.com/api/v3/search/trending").get("coins") or []
        return [str((c.get("item") or {}).get("symbol") or "").upper() for c in coins][:15]
    except Exception:
        return []


def clean_posts(items: Sequence[dict[str, Any]], limit: int = 8) -> tuple[list[dict[str, Any]], int]:
    """Drop paid-signal / spam posts; keep the newest `limit`. Returns (posts, dropped)."""
    kept, dropped = [], 0
    for it in items:
        text = str(it.get("text") or "")
        if not text.strip() or SPAM.search(text):
            dropped += 1
            continue
        kept.append({"source": it.get("source"), "channel": it.get("channel"), "text": text[:280],
                     "tone": it.get("tone"), "ts": it.get("created_ms")})
    return kept[:limit], dropped


def sentiment(coins: Sequence[str], social: Callable[[str], Sequence[dict[str, Any]]] | None = None,
              fetch: Callable[[str], Any] = http_json) -> dict[str, Any]:
    trend = trending(fetch)
    out: dict[str, Any] = {"coingecko_trending": trend, "coins": {}, "as_of_ms": int(time.time() * 1000),
                           "note": "X/Twitter is not used (its search API is paid)."}
    for coin in coins:
        posts, dropped = clean_posts(social(coin) if social else [])
        tones = [p["tone"] for p in posts if isinstance(p.get("tone"), (int, float))]
        out["coins"][coin] = {"positioning": positioning(coin, fetch), "trending_rank": trend.index(coin) + 1 if coin in trend else None,
                              "posts": posts, "spam_dropped": dropped,
                              "avg_post_tone": sum(tones) / len(tones) if tones else None}
    return out
