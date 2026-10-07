"""V6 LIVE FEATURES: per-coin positioning and the shared market context, read causally AND reproducibly
(docs/V6_PROTOCOL.md §3).

A V6 decision is taken at the instant H that closes its signal candle (H = candle close_time + 1 ms, an exact hour).
Everything it reads is stamped at or before H:

    funding   (settle_ts, rate)         a settled rate, visible from its settlement instant
    oi        (snapshot_ts, contracts)  Bybit 1h open-interest snapshots, visible from their stamp
    premium   (bar_open_ts, close)      1h premium-index bars, visible once CLOSED (open + 1h <= H)
    ratio     (snapshot_ts, buy ratio)  1h long / short ACCOUNT ratio, visible from its stamp
    close     (bar_open_ts, close)      1h klines of the market-context coins, visible once closed

Live, those points arrive a few seconds after H (measured 2026-09-24: basis ~6 s, OI and ratio ~11 s after the
stamp), so the live market holds each coin's hour-closing bar at the HOUR BARRIER until they are fetched (bounded),
then records a WATERMARK -- the newest point of each kind the decision could see. A re-derivation (a restarted
process rebuilding the book) caps visibility at the recorded watermark, so it reproduces the live decision exactly
even when a point came in late. An hour without a watermark (warm-up, or minutes no process was running) sees what
the stamps allow.
"""
from __future__ import annotations

import bisect
import math
import threading
from typing import Any, Iterable, Mapping, Sequence

HOUR = 3_600_000
DAY = 24 * HOUR
KINDS = ("funding", "oi", "premium", "ratio")


def _r(x: Any, nd: int = 6) -> Any:
    return round(float(x), nd) if isinstance(x, (int, float)) and math.isfinite(float(x)) else None


def _pct_rank(window: Sequence[float], value: float) -> float | None:
    if not window:
        return None
    return sum(1 for v in window if v <= value) / len(window)


class LiveSeries:
    """Sorted (ts, value) points that may grow while decisions read them. `lag_ms`: a point stamped ts becomes
    visible at ts + lag (a bar stamped by its open is visible once closed)."""

    def __init__(self, rows: Iterable[tuple[int, float]] = (), lag_ms: int = 0):
        self.t: list[int] = []
        self.v: list[float] = []
        self.lag = int(lag_ms)
        self._lock = threading.Lock()
        self.extend(rows)

    def add(self, ts: int, value: float) -> bool:
        """Insert or replace one point. Returns True when the point is new."""
        ts, value = int(ts), float(value)
        with self._lock:
            if not self.t or ts > self.t[-1]:
                self.t.append(ts)
                self.v.append(value)
                return True
            i = bisect.bisect_left(self.t, ts)
            if i < len(self.t) and self.t[i] == ts:
                self.v[i] = value
                return False
            self.t.insert(i, ts)
            self.v.insert(i, value)
            return True

    def extend(self, rows: Iterable[tuple[int, float]]) -> int:
        return sum(1 for ts, v in sorted((int(a), float(b)) for a, b in rows) if self.add(ts, v))

    def upto(self, t: int, cap: int | None = None) -> int:
        """How many points are visible at t (and at or before the watermark `cap`)."""
        limit = int(t) - self.lag
        if cap is not None:
            limit = min(limit, int(cap))
        return bisect.bisect_right(self.t, limit)

    def last(self, t: int, cap: int | None = None) -> tuple[int, float] | None:
        i = self.upto(t, cap)
        return (self.t[i - 1], self.v[i - 1]) if i else None

    def at_or_before(self, ts: int) -> float | None:
        """The value stamped at or before `ts` (a lookback point: older than anything a watermark hides)."""
        i = bisect.bisect_right(self.t, int(ts))
        return self.v[i - 1] if i else None

    def window(self, t: int, span_ms: int, cap: int | None = None) -> list[float]:
        """Visible values with stamps in (t - lag - span, t - lag]."""
        j = self.upto(t, cap)
        i = bisect.bisect_right(self.t, int(t) - self.lag - int(span_ms))
        return self.v[i:j] if j > i else []

    def latest_ts(self) -> int | None:
        return self.t[-1] if self.t else None

    def __len__(self) -> int:
        return len(self.t)


class PositioningFeedV6:
    """One coin's Bybit positioning, shared by every bot trading that coin. `caps[H]` is the watermark recorded
    for the decision at H: {"funding": ts, "oi": ts, "premium": ts, "ratio": ts} (None = unrecorded)."""

    def __init__(self, symbol: str, funding: Iterable[tuple[int, float]] = (), oi: Iterable[tuple[int, float]] = (),
                 premium: Iterable[tuple[int, float]] = (), ratio: Iterable[tuple[int, float]] = (),
                 caps: dict[int, dict[str, int]] | None = None, funding_interval_h: float | None = None):
        self.symbol = symbol
        self.funding = LiveSeries(funding)
        self.oi = LiveSeries(oi)
        self.premium = LiveSeries(premium, lag_ms=HOUR)
        self.ratio = LiveSeries(ratio)
        self.caps: dict[int, dict[str, int]] = caps if caps is not None else {}
        self.default_interval_h = funding_interval_h
        self._cache: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def series(self, kind: str) -> LiveSeries:
        return {"funding": self.funding, "oi": self.oi, "premium": self.premium, "ratio": self.ratio}[kind]

    def cap(self, t: int, kind: str) -> int | None:
        c = self.caps.get(int(t))
        return None if c is None else c.get(kind)

    def funding_interval_h(self, t: int) -> float | None:
        j = self.funding.upto(t, self.cap(t, "funding"))
        if j >= 2:
            return (self.funding.t[j - 1] - self.funding.t[j - 2]) / HOUR
        return self.default_interval_h

    def expected_funding(self, t: int, side: str, hold_h: float) -> float | None:
        """Funding a position would pay (+) or receive (-) over `hold_h`, as a fraction of notional, if the last
        settled rate persisted: longs pay a positive rate, shorts receive it."""
        last = self.funding.last(t, self.cap(t, "funding"))
        iv = self.funding_interval_h(t)
        if last is None or not iv:
            return None
        return (1.0 if side == "long" else -1.0) * last[1] * hold_h / iv

    def snapshot(self, t: int) -> dict[str, Any]:
        """Positioning at the decision instant t (an exact hour). Cached per t: every bot of the coin reads one."""
        t = int(t)
        with self._lock:
            hit = self._cache.get(t)
        if hit is not None:
            return hit
        out: dict[str, Any] = {"t": t}
        f = self.funding.last(t, self.cap(t, "funding"))
        if f is not None:
            cap = self.cap(t, "funding")
            iv = self.funding_interval_h(t) or 8.0
            hist = self.funding.window(t, 90 * DAY, cap)
            prev = self.funding.window(t - DAY, DAY, cap)
            recent = self.funding.window(t, DAY, cap)
            out.update({"funding_rate": _r(f[1], 8), "funding_interval_h": _r(iv, 2),
                        "funding_ann": _r(f[1] * (24.0 / iv) * 365.0, 4),
                        "funding_pct_90d": _r(_pct_rank(hist, f[1]), 4),
                        "funding_mean_24h": _r(sum(recent) / len(recent), 8) if recent else None,
                        "funding_chg_24h": _r(sum(recent) / len(recent) - sum(prev) / len(prev), 8)
                        if recent and prev else None,
                        "funding_age_h": _r((t - f[0]) / HOUR, 2)})
        o = self.oi.last(t, self.cap(t, "oi"))
        if o is not None and o[1] > 0:
            out["oi"] = o[1]
            for label, h in (("1h", 1), ("4h", 4), ("24h", 24), ("3d", 72)):
                past = self.oi.at_or_before(o[0] - h * HOUR)
                out[f"oi_chg_{label}"] = _r(o[1] / past - 1.0, 5) if past else None
            out["oi_pct_30d"] = _r(_pct_rank(self.oi.window(t, 30 * DAY, self.cap(t, "oi")), o[1]), 4)
            out["oi_age_h"] = _r((t - o[0]) / HOUR, 2)
        p = self.premium.last(t, self.cap(t, "premium"))
        if p is not None:
            out.update({"basis": _r(p[1], 8),
                        "basis_pct_30d": _r(_pct_rank(self.premium.window(t, 30 * DAY, self.cap(t, "premium")), p[1]), 4)})
        q = self.ratio.last(t, self.cap(t, "ratio"))
        if q is not None:
            past = self.ratio.at_or_before(q[0] - DAY)
            out.update({"long_ratio": _r(q[1], 4),
                        "long_ratio_chg_24h": _r(q[1] - past, 4) if past is not None else None})
        with self._lock:
            self._cache[t] = out
            if len(self._cache) > 400:
                for k in sorted(self._cache)[:200]:
                    self._cache.pop(k, None)
        return out

    def watermark(self) -> dict[str, int | None]:
        """What is visible NOW: the newest stamp of each kind (the live market records this at the barrier)."""
        return {k: self.series(k).latest_ts() for k in KINDS}


# ---- the market context (observed by every bot, traded by none) --------------------------------------------------

def ema(values: Sequence[float], n: int) -> list[float]:
    out: list[float] = []
    k = 2.0 / (n + 1.0)
    for x in values:
        out.append(x if not out else out[-1] + k * (x - out[-1]))
    return out


def trend_of(closes: Sequence[float], fast: int, slow: int, slope_bars: int = 3) -> str | None:
    """up / down / flat from an EMA pair and the slow EMA's slope; None without enough history."""
    if len(closes) < slow + slope_bars + 2:
        return None
    f, s = ema(closes, fast), ema(closes, slow)
    slope = s[-1] / s[-1 - slope_bars] - 1.0 if s[-1 - slope_bars] else 0.0
    c = closes[-1]
    if f[-1] > s[-1] and slope > 0 and c > s[-1]:
        return "up"
    if f[-1] < s[-1] and slope < 0 and c < s[-1]:
        return "down"
    return "flat"


def resample(opens: Sequence[int], closes: Sequence[float], hours: int) -> list[float]:
    """Closes of complete `hours`-hour bars (UTC-aligned) from 1h bars stamped by their open."""
    out: list[float] = []
    step = hours * HOUR
    for ts, c in zip(opens, closes):
        if (int(ts) + HOUR) % step == 0:
            out.append(c)
    return out


class MarketContextV6:
    """BTC, ETH and a fixed BREADTH SET of liquid coins, observed on 1h klines (plus open interest and settled
    funding): trends, breadth, aggregate funding and aggregate open interest at the decision instant t. Every bot
    may read it; none trades it. `caps[t]` holds the watermark per series key ("close:SYM", "oi:SYM",
    "funding:SYM")."""

    def __init__(self, symbols: Sequence[str], caps: dict[int, dict[str, int]] | None = None):
        self.symbols = list(dict.fromkeys(symbols))
        self.close = {s: LiveSeries(lag_ms=HOUR) for s in self.symbols}
        self.oi = {s: LiveSeries() for s in self.symbols}
        self.funding = {s: LiveSeries() for s in self.symbols}
        self.caps: dict[int, dict[str, int]] = caps if caps is not None else {}
        self._cache: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def series(self, key: str) -> LiveSeries:
        kind, _, sym = key.partition(":")
        return {"close": self.close, "oi": self.oi, "funding": self.funding}[kind][sym]

    def _cap(self, t: int, key: str) -> int | None:
        c = self.caps.get(int(t))
        return None if c is None else c.get(key)

    def closes(self, sym: str, t: int, span_h: int) -> tuple[list[int], list[float]]:
        s = self.close[sym]
        j = s.upto(t, self._cap(t, f"close:{sym}"))
        i = max(0, j - span_h)
        return s.t[i:j], s.v[i:j]

    def _coin(self, sym: str, t: int) -> dict[str, Any] | None:
        opens, closes = self.closes(sym, t, 24 * 45)
        if len(closes) < 60:
            return None
        c4 = resample(opens, closes, 4)
        c1d = resample(opens, closes, 24)
        ret24 = closes[-1] / closes[-25] - 1.0 if len(closes) > 25 and closes[-25] else None
        d_ema = ema(c1d, 20) if len(c1d) >= 20 else []
        return {"trend_1h": trend_of(closes, 20, 50), "trend_4h": trend_of(c4, 20, 50),
                "trend_1d": trend_of(c1d, 10, 30), "ret_24h": _r(ret24, 5),
                "above_1d_ema20": (closes[-1] > d_ema[-1]) if d_ema else None, "close": closes[-1]}

    def snapshot(self, t: int) -> dict[str, Any]:
        t = int(t)
        with self._lock:
            hit = self._cache.get(t)
        if hit is not None:
            return hit
        coins = {s: self._coin(s, t) for s in self.symbols}
        known = {s: c for s, c in coins.items() if c is not None}
        above = [c["above_1d_ema20"] for c in known.values() if c.get("above_1d_ema20") is not None]
        fund = []
        for s in self.symbols:
            f = self.funding[s].last(t, self._cap(t, f"funding:{s}"))
            if f is not None:
                fund.append(f[1])
        oi_now = oi_then = 0.0
        n_oi = 0
        for s, c in known.items():
            o = self.oi[s].last(t, self._cap(t, f"oi:{s}"))
            if o is None:
                continue
            past = self.oi[s].at_or_before(o[0] - DAY)
            px_then = self.close[s].at_or_before(o[0] - DAY - HOUR)
            if not past or not px_then:
                continue
            oi_now += o[1] * c["close"]
            oi_then += past * px_then
            n_oi += 1
        fund_sorted = sorted(fund)
        out = {"t": t, "btc": coins.get("BTCUSDT"), "eth": coins.get("ETHUSDT"), "coins_seen": len(known),
               "breadth_1d": _r(sum(1 for a in above if a) / len(above), 3) if above else None,
               "breadth_n": len(above),
               "agg_funding_median": _r(fund_sorted[len(fund_sorted) // 2], 8) if fund_sorted else None,
               "agg_oi_chg_24h": _r(oi_now / oi_then - 1.0, 5) if n_oi and oi_then > 0 else None, "agg_oi_n": n_oi}
        with self._lock:
            self._cache[t] = out
            if len(self._cache) > 400:
                for k in sorted(self._cache)[:200]:
                    self._cache.pop(k, None)
        return out

    def watermark(self) -> dict[str, int | None]:
        out: dict[str, int | None] = {}
        for s in self.symbols:
            out[f"close:{s}"] = self.close[s].latest_ts()
            out[f"oi:{s}"] = self.oi[s].latest_ts()
            out[f"funding:{s}"] = self.funding[s].latest_ts()
        return out


def regime(snapshot: Mapping[str, Any], side: str) -> str:
    """The market backdrop for a trade: WITH when BTC's 1D and 4h trends agree with its side, AGAINST when they
    both oppose it, MIXED otherwise, UNKNOWN without data."""
    b = snapshot.get("btc") or {}
    d, f = b.get("trend_1d"), b.get("trend_4h")
    if d is None or f is None:
        return "UNKNOWN"
    want = "up" if side == "long" else "down"
    anti = "down" if side == "long" else "up"
    if d == want and f == want:
        return "WITH"
    if d == anti and f == anti:
        return "AGAINST"
    return "MIXED"
