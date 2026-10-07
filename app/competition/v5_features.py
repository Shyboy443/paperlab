"""V5 POSITIONING FEATURES, read causally (docs/V5_PROTOCOL.md §2).

One `PositioningFeed` per symbol holds the Bybit-native hourly series (docs/V5_DATA_AUDIT.md):

    funding   (settle_ts, rate)             a rate is visible only once it has SETTLED (settle_ts <= t)
    oi        (snapshot_ts, open interest)  1h snapshots, visible when snapshot_ts <= t
    premium   (bar_open_ts, close)          1h premium-index bars, visible once CLOSED (open_ts + 1h <= t)
    ratio     (snapshot_ts, buy ratio)      1h long / short account ratio, visible when snapshot_ts <= t

`snapshot(t)` returns only numbers computed from what was visible at t -- a decision on the close of a 1h candle
(t = its close time) never sees the snapshot stamped at the next hour. Nothing here knows about trades or results.
"""
from __future__ import annotations

import bisect
import math
from typing import Any, Sequence

HOUR = 3_600_000
DAY = 24 * HOUR


def _r(x: Any, nd: int = 6) -> Any:
    return round(x, nd) if isinstance(x, (int, float)) and math.isfinite(x) else None


def _pct_rank(window: Sequence[float], value: float) -> float | None:
    if not window:
        return None
    return sum(1 for v in window if v <= value) / len(window)


class Series:
    def __init__(self, rows: Sequence[tuple[int, float]], visible_after_ms: int = 0):
        rows = sorted((int(t), float(v)) for t, v in rows)
        self.t = [t for t, _ in rows]
        self.v = [v for _, v in rows]
        self.lag = int(visible_after_ms)       # premium bars: visible one hour after their open

    def upto(self, t: int) -> int:
        """Number of points visible at t (index of the first invisible one)."""
        return bisect.bisect_right(self.t, int(t) - self.lag)

    def last(self, t: int) -> tuple[int, float] | None:
        i = self.upto(t)
        return (self.t[i - 1], self.v[i - 1]) if i else None

    def at_or_before(self, t: int) -> float | None:
        p = self.last(t)
        return p[1] if p else None

    def window(self, t: int, span_ms: int) -> list[float]:
        """Visible values with timestamp in (t - span, t]."""
        j = self.upto(t)
        i = bisect.bisect_right(self.t, int(t) - self.lag - span_ms)
        return self.v[i:j]

    def __len__(self) -> int:
        return len(self.t)


class PositioningFeed:
    def __init__(self, funding: Sequence[tuple[int, float]] = (), oi: Sequence[tuple[int, float]] = (),
                 premium: Sequence[tuple[int, float]] = (), ratio: Sequence[tuple[int, float]] = ()):
        self.funding = Series(funding)
        self.oi = Series(oi)
        self.premium = Series(premium, visible_after_ms=HOUR)
        self.ratio = Series(ratio)
        self._cache: tuple[int, dict[str, Any]] | None = None

    def funding_interval_h(self, t: int) -> float | None:
        j = self.funding.upto(t)
        if j < 2:
            return None
        return (self.funding.t[j - 1] - self.funding.t[j - 2]) / HOUR

    def expected_funding(self, t: int, side: str, hold_h: float) -> float | None:
        """Funding a position would pay (+) or receive (-) over `hold_h`, as a fraction of notional, assuming the
        last settled rate persists: longs pay a positive rate, shorts receive it."""
        last = self.funding.last(t)
        iv = self.funding_interval_h(t)
        if last is None or not iv:
            return None
        n = hold_h / iv
        sign = 1.0 if side == "long" else -1.0
        return sign * last[1] * n

    def snapshot(self, t: int) -> dict[str, Any]:
        t = int(t)
        if self._cache is not None and self._cache[0] == t:
            return self._cache[1]
        out: dict[str, Any] = {"t": t}
        f = self.funding.last(t)
        if f is not None:
            iv = self.funding_interval_h(t) or 8.0
            hist = self.funding.window(t, 90 * DAY)
            prev = self.funding.window(t - DAY, DAY)
            recent = self.funding.window(t, DAY)
            out.update({"funding_rate": _r(f[1], 8), "funding_interval_h": _r(iv, 2),
                        "funding_ann": _r(f[1] * (24.0 / iv) * 365.0, 4),
                        "funding_pct_90d": _r(_pct_rank(hist, f[1]), 4),
                        "funding_mean_24h": _r(sum(recent) / len(recent), 8) if recent else None,
                        "funding_chg_24h": _r((sum(recent) / len(recent)) - (sum(prev) / len(prev)), 8)
                        if recent and prev else None, "funding_n_90d": len(hist)})
        o = self.oi.last(t)
        if o is not None and o[1] > 0:
            cur = o[1]
            out["oi"] = cur
            for label, h in (("1h", 1), ("4h", 4), ("24h", 24), ("3d", 72)):
                past = self.oi.at_or_before(o[0] - h * HOUR)
                out[f"oi_chg_{label}"] = _r(cur / past - 1.0, 5) if past else None
            hist = self.oi.window(t, 30 * DAY)
            out["oi_pct_30d"] = _r(_pct_rank(hist, cur), 4)
        p = self.premium.last(t)
        if p is not None:
            hist = self.premium.window(t, 30 * DAY)
            out.update({"basis": _r(p[1], 8), "basis_pct_30d": _r(_pct_rank(hist, p[1]), 4)})
        q = self.ratio.last(t)
        if q is not None:
            past = self.ratio.at_or_before(q[0] - DAY)
            out.update({"long_ratio": _r(q[1], 4), "long_ratio_chg_24h": _r(q[1] - past, 4) if past is not None else None})
        self._cache = (t, out)
        return out
