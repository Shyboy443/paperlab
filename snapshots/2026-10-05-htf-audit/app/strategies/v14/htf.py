"""V14 HTF: copies of the best current strategies that check the HIGHER TIMEFRAMES before every entry (docs/V14_PROTOCOL.md).

Operator, 2026-10-04: "create another set of bots just same strategy of most winning and returning bots ... it should
analyze higher time frames as well before taking trades, that's what real humans do". The leaders on 2026-10-04 were
Jinx / Gamma (V8.3 VWAP snap-back), Juno (V11.1 RS breakout scanner), Kilo (V11.2 RS pullback scanner) and Bounce (V13
limit-order snapback). Sable (V7.1) needs V7's own positioning feed and already trades only with the 1h trend and never
against the 4h trend, so it is not copied.

THE HTF RULE (a human trader's "trade with the bigger trend"), fixed before any test, from the coin's 1-HOUR candles (a
4h / 1d candle built from 1m bars is dropped whole by the engine on any 1-minute gap, so the 1h series is the robust
source):
    "4h" trend    price vs EMA(84) of 1h closes (~ the 4h EMA 21), and that EMA vs 12 hours earlier
    "daily" trend price vs EMA(240) of 1h closes (~ the daily EMA 10), and that EMA vs 24 hours earlier
                  up   = price above a RISING average; down = price below a FALLING one; otherwise flat
    bias = (+1 up / -1 down / 0 flat) for each, summed:  LONG only if bias >= +1, SHORT only if bias <= -1
    (at least one higher timeframe agrees and neither disagrees). Fewer than 300 hourly candles: no trade.
Everything else -- the setup, the stop, the targets, the time limit, the engine -- is the copied strategy's own, unchanged.
"""
from __future__ import annotations

from typing import Any, Sequence

EMA_MID, SLOPE_MID = 84, 12
EMA_SLOW, SLOPE_SLOW = 240, 24
MIN_1H = 300


def ema(values: Sequence[float], n: int) -> list[float]:
    a = 2.0 / (n + 1.0)
    out, e = [], None
    for v in values:
        e = v if e is None else e + a * (v - e)
        out.append(e)
    return out


def htf_view(ctx: Any, symbol: str, price: float) -> dict[str, Any] | None:
    """The coin's higher-timeframe trends at this instant, from its closed 1h candles (None: not enough history)."""
    cs = list(ctx.candles(symbol, "1h"))
    if len(cs) < MIN_1H or price <= 0:
        return None
    closes = [c.close for c in cs]

    def trend(n: int, k: int) -> int:
        e = ema(closes, n)
        if price > e[-1] and e[-1] > e[-1 - k]:
            return 1
        if price < e[-1] and e[-1] < e[-1 - k]:
            return -1
        return 0
    mid, slow = trend(EMA_MID, SLOPE_MID), trend(EMA_SLOW, SLOPE_SLOW)
    return {"h4": mid, "d1": slow, "bias": mid + slow}


def allows(view: dict[str, Any] | None, side: str) -> bool:
    if view is None:
        return False
    return view["bias"] >= 1 if side == "long" else view["bias"] <= -1


def _price(ctx: Any, symbol: str, tf: str) -> float:
    cs = ctx.candles(symbol, tf)
    return float(cs[-1].close) if cs else 0.0


def htf_scanner(base: type, sid: str, name: str) -> type:
    """A V11 scanner (already built with ladder_class(...).for_universe(...)) that drops set-ups against the higher
    timeframes BEFORE ranking them, so a filtered coin never takes the slot of one that passes."""

    def scan(self, ctx, t, _base=base.scan):
        out = []
        for x in _base(self, ctx, t):
            v = htf_view(ctx, x.symbol, _price(ctx, x.symbol, self.signal_tf))
            if allows(v, x.side):
                x.factors = {**x.factors, "htf": 1.0}
                out.append(x)
            else:
                self.htf_skips = getattr(self, "htf_skips", 0) + 1
        return out
    return _make(base, sid, name, {"scan": scan})


def htf_single(base: type, sid: str, name: str) -> type:
    """A single-coin bot (V8.3, already built with for_class("SCALP")) whose entry is dropped against the higher
    timeframes."""

    def on_candle(self, c, ctx, _base=base.on_candle):
        out = []
        for s in _base(self, c, ctx):
            if getattr(s, "kind", "entry") != "entry":
                out.append(s)
                continue
            v = htf_view(ctx, s.symbol, _price(ctx, s.symbol, self.signal_tf))
            if allows(v, s.side):
                s.meta["htf"] = v
                out.append(s)
            else:
                self.htf_skips = getattr(self, "htf_skips", 0) + 1
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
            if not allows(htf_view(ctx, sym, cs[-1].close), "long" if m[0] < 0 else "short"):
                self.htf_skips = getattr(self, "htf_skips", 0) + 1
                continue
            found.append((abs(m[0]), sym, m, cs[-1]))
        found.sort(key=lambda x: (-x[0], x[1]))
        out = []
        for _, sym, (st, atr, vwap), last in found[:p.max_signals]:
            sig = self.limit_signal(ctx, last, st, atr, vwap, t)
            if sig is not None:
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
