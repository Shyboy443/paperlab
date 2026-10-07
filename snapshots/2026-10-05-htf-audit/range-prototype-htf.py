"""Causal, UTC-aligned 4h/daily context for V14. See docs/V14_PROTOCOL.md.

Only complete groups of closed hourly bars become HTF candles. Each timeframe's own
close determines its trend; the entry price cannot rewrite a completed HTF trend.
Breakouts need 4h agreement; pullbacks can use a neutral 4h inside a daily trend;
reversion can trade ranges but cannot fade an opposing daily or strong 4h trend.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from app.core.types import Candle

HOUR, DAY = 3_600_000, 86_400_000
EMA_MID, SLOPE_MID = 21, 3             # actual 4h bars
EMA_SLOW, SLOPE_SLOW = 10, 1          # actual daily bars
MIN_H4, MIN_D1 = 42, 20              # at least two EMA periods
MIN_1H = MIN_D1 * 24
ATR_PERIOD = 14
PRICE_DEADBAND_ATR, SLOPE_DEADBAND_ATR = 0.10, 0.05
STRONG_SLOPE_ATR = 0.20


def rule_profile() -> dict[str, Any]:
    return {"source": "complete UTC 4h/1d OHLC candles assembled from closed 1h bars",
            "mid_ema": EMA_MID, "slow_ema": EMA_SLOW, "min_1h": MIN_1H,
            "min_4h": MIN_H4, "min_1d": MIN_D1, "slope_bars": [SLOPE_MID, SLOPE_SLOW],
            "atr_period": ATR_PERIOD, "price_deadband_atr": PRICE_DEADBAND_ATR,
            "slope_deadband_atr_per_bar": SLOPE_DEADBAND_ATR,
            "strong_slope_atr_per_bar": STRONG_SLOPE_ATR,
            "policies": {"V14.1": "reversion", "V14.2": "breakout", "V14.3": "pullback", "V14.4": "reversion"}}


def ema(values: Sequence[float], n: int) -> list[float]:
    a = 2.0 / (n + 1.0)
    out, e = [], None
    for v in values:
        e = v if e is None else e + a * (v - e)
        out.append(e)
    return out


def completed_bars(cs: Sequence[Candle], hours: int) -> list[Candle]:
    """Aggregate only exact, complete UTC buckets; never bridge gaps or fill them."""
    span = hours * HOUR
    groups: dict[int, list[Candle]] = {}
    for c in cs:
        groups.setdefault(c.open_time // span * span, []).append(c)
    out = []
    for start, group in groups.items():
        if len(group) != hours or any(c.open_time != start + i * HOUR for i, c in enumerate(group)):
            continue
        out.append(Candle(group[0].symbol, "4h" if hours == 4 else "1d", start, group[0].open,
                          max(c.high for c in group), min(c.low for c in group), group[-1].close,
                          sum(c.volume for c in group), start + span - 1, True,
                          sum(c.quote_volume for c in group)))
    return out


def _trend(cs: Sequence[Candle], n: int, k: int) -> tuple[int, float]:
    e = ema([c.close for c in cs], n)
    tr = [max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)) for p, c in zip(cs, cs[1:])]
    atr = sum(tr[-ATR_PERIOD:]) / ATR_PERIOD
    if atr <= 0:
        return 0, 0.0
    slope = (e[-1] - e[-1 - k]) / (k * atr)
    distance = (cs[-1].close - e[-1]) / atr
    direction = (1 if distance > PRICE_DEADBAND_ATR and slope > SLOPE_DEADBAND_ATR else
                 -1 if distance < -PRICE_DEADBAND_ATR and slope < -SLOPE_DEADBAND_ATR else 0)
    return direction, slope


def _continuous(cs: Sequence[Candle], need: int, span: int, t: int) -> bool:
    expected_close = (t + 1) // span * span - 1
    tail = cs[-need:]
    return (len(tail) == need and tail[-1].close_time == expected_close
            and all(c.open_time == tail[0].open_time + i * span for i, c in enumerate(tail)))


def htf_view(ctx: Any, symbol: str, price: float, asof: int | None = None) -> dict[str, Any] | None:
    """HTF context known at the decision time. Missing/stale/invalid history fails closed.

    `price` is checked for validity, but does not classify HTF candles. An explicit
    cutoff is required unless the context supplies its causal clock.
    """
    if asof is None:
        asof = ctx.now_ms() if hasattr(ctx, "now_ms") else 0
    if asof <= 0 or not math.isfinite(price) or price <= 0:
        return None
    raw = ctx.candles(symbol, "1h")
    if not raw:
        return None
    # Bounded to one cached result per coin. Refresh each hour and on a new bar.
    key = ((asof + 1) // HOUR, len(raw), id(raw[-1]))
    cache = getattr(ctx, "_v14_htf_cache", None)
    if cache is None:
        cache = ctx._v14_htf_cache = {}
    if symbol in cache and cache[symbol][0] == key:
        return cache[symbol][1]
    cs = [c for c in raw if c.closed and c.close_time <= asof]
    view = None
    valid = (len(cs) >= MIN_1H and _continuous(cs, MIN_1H, HOUR, asof)
             and all(c.symbol == symbol and c.tf == "1h" and c.open_time % HOUR == 0
                     and c.close_time == c.open_time + HOUR - 1
                     and all(math.isfinite(v) and v > 0 for v in (c.open, c.high, c.low, c.close))
                     and c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high for c in cs)
             and all(a.open_time < b.open_time for a, b in zip(cs, cs[1:])))
    if valid:
        h4, d1 = completed_bars(cs, 4), completed_bars(cs, 24)
        if _continuous(h4, MIN_H4, 4 * HOUR, asof) and _continuous(d1, MIN_D1, DAY, asof):
            mid, mid_slope = _trend(h4, EMA_MID, SLOPE_MID)
            slow, slow_slope = _trend(d1, EMA_SLOW, SLOPE_SLOW)
            view = {"h4": mid, "d1": slow, "bias": mid + slow,
                    "h4_slope_atr": mid_slope, "d1_slope_atr": slow_slope,
                    "h4_close_ms": h4[-1].close_time, "d1_close_ms": d1[-1].close_time,
                    "source": "closed_utc_4h_1d"}
    cache[symbol] = (key, view)
    return view


def allows(view: dict[str, Any] | None, side: str, policy: str = "breakout") -> bool:
    if view is None or side not in ("long", "short"):
        return False
    d = 1 if side == "long" else -1
    h4, d1 = d * view["h4"], d * view["d1"]
    slope = d * view["h4_slope_atr"]
    if d1 < 0:
        return False
    if policy == "breakout":
        return h4 > 0
    if policy == "pullback":
        return h4 > 0 or (h4 == 0 and d1 > 0 and slope >= -SLOPE_DEADBAND_ATR)
    if policy == "reversion":
        return h4 >= 0 and slope >= -STRONG_SLOPE_ATR
    return False


def _check(self, ctx, symbol, price, t, side, policy):
    view = htf_view(ctx, symbol, price, t)
    if allows(view, side, policy):
        return {**view, "policy": policy, "decision_ms": t}
    self.htf_skips = getattr(self, "htf_skips", 0) + 1
    reason = "missing_or_stale_htf" if view is None else "regime_veto"
    rejects = getattr(self, "htf_rejects", {})
    rejects[reason] = rejects.get(reason, 0) + 1
    self.htf_rejects = rejects
    return None


def _price(ctx: Any, symbol: str, tf: str, t: int) -> float:
    cs = ctx.candles(symbol, tf)
    return float(cs[-1].close) if cs and cs[-1].closed and cs[-1].close_time == t else 0.0


def htf_scanner(base: type, sid: str, name: str) -> type:
    """A V11 scanner (already built with ladder_class(...).for_universe(...)) that drops set-ups against the higher
    timeframes BEFORE ranking them, so a filtered coin never takes the slot of one that passes."""

    def scan(self, ctx, t, _base=base.scan):
        out = []
        for x in _base(self, ctx, t):
            policy = "pullback" if sid == "V14.3" else "breakout"
            v = _check(self, ctx, x.symbol, _price(ctx, x.symbol, self.signal_tf, t), t, x.side, policy)
            if v is not None:
                x.factors = {**x.factors, "htf": 1.0, "htf_h4": v["h4"], "htf_d1": v["d1"]}
                x.htf = v
                out.append(x)
        return out

    def _signal(self, ctx, x, t):
        sig = base._signal(self, ctx, x, t)
        if sig is not None:
            sig.meta["htf"] = x.htf
        return sig
    return _make(base, sid, name, {"scan": scan, "_signal": _signal})


def htf_single(base: type, sid: str, name: str) -> type:
    """A single-coin bot (V8.3, already built with for_class("SCALP")) whose entry is dropped against the higher
    timeframes."""

    def on_candle(self, c, ctx, _base=base.on_candle):
        out = []
        for s in _base(self, c, ctx):
            if getattr(s, "kind", "entry") != "entry":
                out.append(s)
                continue
            t = c.close_time
            v = _check(self, ctx, s.symbol, s.entry_price, t, s.side, "reversion")
            if v is not None:
                s.meta["htf"] = v
                out.append(s)
        return out
    return _make(base, sid, name, {"on_candle": on_candle})


def htf_snapback(base: type, sid: str, name: str) -> type:
    """V13 Snapback (already built with for_universe): the same scan, with the HTF check inside the coin loop (before
    the most-stretched-first ranking), so a filtered coin never uses up one of the 4 order slots."""
    from app.strategies.v13.snapback import DAY_BARS, stretch_atr

    def on_candle(self, c, ctx):
        if c.tf != self.signal_tf or c.symbol != self.anchor:
            return []
        t = c.close_time
        p = self.params
        found = []
        for sym in self.universe:
            if sym == self.anchor or self.resting.get(sym, 0) > t:
                continue
            cs = self.current(ctx, sym, "5m", t, DAY_BARS + 1)
            m = stretch_atr(cs) if cs is not None else None
            if m is None or abs(m[0]) < p.threshold or m[1] <= 0:
                continue
            v = _check(self, ctx, sym, cs[-1].close, t, "long" if m[0] < 0 else "short", "reversion")
            if v is None:
                continue
            found.append((abs(m[0]), sym, m, cs[-1], v))
        found.sort(key=lambda x: (-x[0], x[1]))
        out = []
        for _, sym, (st, atr, vwap), last, v in found[:p.max_signals]:
            sig = self.limit_signal(ctx, last, st, atr, vwap, t)
            if sig is not None:
                sig.meta["htf"] = v
                out.append(sig)
                self.resting[sym] = int(sig.meta.get("expire_ms") or t + 60_000)
        return out
    return _make(base, sid, name, {"on_candle": on_candle})


def _make(base: type, sid: str, name: str, body: dict[str, Any]) -> type:
    tfs = tuple(dict.fromkeys(tuple(base.timeframes) + ("1h",)))
    return type(base.__name__ + "HTF", (base,), {"id": sid, "name": name, "timeframes": tfs, "htf_rule": True,
                                                 "__module__": __name__, **body})


# -- the field: which strategy each V14 bot copies -------------------------------------------------------------------
SOURCES: dict[str, dict[str, str]] = {
    "V14.1": {"copies": "V8.3", "program": "v8", "name": "VWAP snap-back scalp + HTF", "kind": "single"},
    "V14.2": {"copies": "V11.1", "program": "v11", "name": "Relative-strength breakout scanner + HTF", "kind": "scanner"},
    "V14.3": {"copies": "V11.2", "program": "v11", "name": "Relative-strength pullback scanner + HTF", "kind": "scanner"},
    "V14.4": {"copies": "V13.1", "program": "v13", "name": "Snapback limit-order reversion + HTF", "kind": "snapback"},
}
SIGNAL_TF = {"V14.1": "5m", "V14.2": "15m", "V14.3": "15m", "V14.4": "5m"}


def base_class(sid: str) -> type:
    """The copied strategy's UNBUILT class (its own Params, setup and exits)."""
    src = SOURCES[sid]["copies"]
    if src == "V8.3":
        from app.strategies.v8.arena import load_v8_scalpers
        return load_v8_scalpers()["V8.3"]
    if src in ("V11.1", "V11.2"):
        from app.strategies.v11.scan import load_v11_scanners
        return load_v11_scanners()[src]
    from app.strategies.v13.snapback import load_v13
    return load_v13()["V13.1"]


def build(sid: str, universe: Sequence[str] = (), htf: bool = True) -> type:
    """The runnable class of a V14 bot (htf=False: the copied strategy alone, for the study's baseline)."""
    base = base_class(sid)
    kind = SOURCES[sid]["kind"]
    name = SOURCES[sid]["name"]
    if kind == "single":
        cls = base.for_class("SCALP")
        return htf_single(cls, sid, name) if htf else cls
    if kind == "scanner":
        from app.strategies.v11.ladder import ladder_class
        cls = ladder_class(base).for_universe(universe)
        return htf_scanner(cls, sid, name) if htf else cls
    cls = base.for_universe(universe)
    return htf_snapback(cls, sid, name) if htf else cls


__all__ = ["EMA_MID", "EMA_SLOW", "MIN_1H", "SIGNAL_TF", "SOURCES", "allows", "base_class", "build", "ema", "htf_view"]
