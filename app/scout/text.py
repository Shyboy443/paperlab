"""Which coins / stocks a post or headline is about, and a transparent word-list tone.

Symbols are matched by cashtag ($SOL), by ticker in capitals (SOL), or by full name (solana). Tickers that are
ordinary words ("sol", "meta", "arb", "ena", "amd" in lower case) only count as a cashtag or in capitals, and
"META" only counts next to a stock word, so a Spanish "sol" or a "meta" joke is not a mention.

Tone is a word list with finance slang (moon / rekt / bagholder), scored -1 .. +1 with simple negation ("not
bullish"). It is deliberately simple and deterministic: the scout measures ATTENTION first; tone is a secondary
feature whose value the study decides.
"""
from __future__ import annotations

import re
from typing import Iterable

# symbol -> (market, names / aliases matched case-insensitively, tickers matched only in capitals or as a cashtag)
UNIVERSE: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "BTC": ("crypto", ("bitcoin", "btc"), ("BTC", "XBT")),
    "ETH": ("crypto", ("ethereum", "ether"), ("ETH",)),
    "SOL": ("crypto", ("solana",), ("SOL",)),
    "XRP": ("crypto", ("ripple", "xrp"), ("XRP",)),
    "DOGE": ("crypto", ("dogecoin", "doge"), ("DOGE",)),
    "ARB": ("crypto", ("arbitrum",), ("ARB",)),
    "ENA": ("crypto", ("ethena",), ("ENA",)),
    "SPY": ("stocks", ("s&p 500", "s&p500"), ("SPY", "SPX")),
    "QQQ": ("stocks", ("nasdaq 100", "nasdaq-100", "qqq"), ("QQQ", "NDX")),
    "AAPL": ("stocks", ("apple", "aapl"), ("AAPL",)),
    "NVDA": ("stocks", ("nvidia", "nvda"), ("NVDA",)),
    "TSLA": ("stocks", ("tesla", "tsla"), ("TSLA",)),
    "AMD": ("stocks", ("advanced micro devices",), ("AMD",)),
    "MSFT": ("stocks", ("microsoft", "msft"), ("MSFT",)),
    "META": ("stocks", ("facebook",), ()),
    "AMZN": ("stocks", ("amazon", "amzn"), ("AMZN",)),
    "GOOGL": ("stocks", ("alphabet", "googl", "goog"), ("GOOGL", "GOOG")),
}
_META_CONTEXT = re.compile(r"\b(stock|shares|earnings|zuckerberg|platforms|calls|puts|\$meta)\b", re.I)


def _rx(words: Iterable[str], flags: int) -> re.Pattern | None:
    words = [w for w in words if w]
    if not words:
        return None
    return re.compile(r"(?<![\w$])(" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")(?![\w])", flags)


_NAMES = {s: _rx(n, re.I) for s, (_, n, _t) in UNIVERSE.items()}
_TICKERS = {s: _rx(t, 0) for s, (_, _n, t) in UNIVERSE.items()}
_CASHTAG = re.compile(r"\$([A-Za-z]{2,6})\b")
_CASH_MAP = {**{s: s for s in UNIVERSE}, "XBT": "BTC", "SPX": "SPY", "NDX": "QQQ", "GOOG": "GOOGL"}


def symbols_in(text: str) -> list[str]:
    """The universe symbols a text is about (sorted, unique)."""
    if not text:
        return []
    found = {_CASH_MAP[m.upper()] for m in _CASHTAG.findall(text) if m.upper() in _CASH_MAP}
    for s in UNIVERSE:
        if s in found:
            continue
        if (_NAMES[s] and _NAMES[s].search(text)) or (_TICKERS[s] and _TICKERS[s].search(text)):
            found.add(s)
    if "META" not in found and re.search(r"(?<![\w$])META(?![\w])", text) and _META_CONTEXT.search(text):
        found.add("META")
    return sorted(found)


BULLISH = {
    "bull", "bullish", "moon", "mooning", "pump", "pumping", "rally", "rallies", "surge", "surges", "soar", "soars",
    "breakout", "ath", "buy", "buying", "calls", "rip", "ripping", "green", "gain", "gains", "higher",
    "beat", "beats", "upgrade", "upgraded", "approval", "approved", "adoption", "partnership", "record", "strong",
    "outperform", "accumulate", "undervalued", "rocket", "squeeze", "bounce", "recover", "recovery", "etf", "inflows",
}
BEARISH = {
    "bear", "bearish", "dump", "dumping", "crash", "crashing", "plunge", "plunges", "tank", "tanking", "sell", "selling",
    "puts", "red", "loss", "losses", "lower", "miss", "misses", "downgrade", "downgraded", "lawsuit",
    "sec", "hack", "hacked", "exploit", "rug", "rugpull", "scam", "rekt", "liquidated", "liquidation", "bagholder",
    "overvalued", "bubble", "weak", "fear", "outflows", "ban", "delist", "delisted", "investigation", "fraud", "fud",
}
NEGATORS = {"not", "no", "never", "isn't", "wasn't", "aren't", "don't", "doesn't", "won't", "without", "hardly"}
_WORD = re.compile(r"[a-z$']+")


def tone(text: str) -> float | None:
    """-1 (bearish) .. +1 (bullish); None when no tone word appears."""
    words = _WORD.findall((text or "").lower())
    score, hits = 0.0, 0
    for i, w in enumerate(words):
        w = w.strip("$'")
        v = 1.0 if w in BULLISH else (-1.0 if w in BEARISH else 0.0)
        if not v:
            continue
        if any(p in NEGATORS for p in words[max(0, i - 3):i]):
            v = -v
        score += v
        hits += 1
    return round(score / hits, 4) if hits else None
