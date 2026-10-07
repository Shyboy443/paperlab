"""V13 SNAPBACK: mean reversion with LIMIT (maker) entries over the V11 scan universe (docs/V13_PROTOCOL.md).

Why (docs/SYSTEM_REVIEW.md, docs/ALTDATA_STUDY.json): across 30 coins and 99 days the one robust effect is short-term
mean reversion -- a coin stretched far from its recent average tends to come back -- but it is worth only 0-5 bp a
trade, less than a market-order round trip (~11 bp). This bot attacks the COST side: it never pays the taker fee to
get in. Instead of buying the stretch, it rests a post-only buy limit a little BELOW it (sell limit above, for a short):

    every 5 minutes (on the anchor's candle), for every coin:
      stretch = ln(close / VWAP of the last 24 h) / (24 h volatility)          (in daily-volatility units)
    stretch <= -threshold  -> LONG  set-up        stretch >= +threshold  -> SHORT set-up
      (the most stretched coins first, at most `max_signals` per decision, one order or position per coin)
    entry   a POST-ONLY limit `offset_atr` x ATR(5m, 14) beyond the close, live for `expiry_min` minutes; it fills
            only when the price trades THROUGH it (the engine: app/live/v13_engine.py), at the MAKER fee
    stop    max(min_stop_pct, stop_atr x ATR / entry), at most max_stop_pct (a set-up needing more is skipped)
    target  `target_r` x the stop distance, resting as a limit (maker); a time limit of `max_hold_min` (market exit)

The price of a maker entry is ADVERSE SELECTION: the order fills exactly when the price keeps moving against the
idea, and misses the cleanest snaps. The pre-registered study (scripts/v13_snapback_study.py) prices that in by
replaying this exact class through the exact engine the live bot uses. Its verdict was FAIL (every one of the 54
variants lost on the TEST half): no edge is claimed. It runs forward on paper because the operator asked for the bot.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle, Signal
from app.strategies.v11.scan import ANCHOR, Params, ScanV11
from app.strategies.v6.base import scale

DAY_BARS = 288                                    # 24 h of 5m bars (the context keeps 600)
MINUTE = 60_000


def stretch_atr(cs: Sequence[Candle]) -> tuple[float, float, float] | None:
    """(stretch, ATR14, VWAP) from >= DAY_BARS + 1 five-minute candles, using only closed candles.

    stretch = ln(close / VWAP_24h) / (std of the 288 five-minute log returns x sqrt(288)); VWAP uses the typical price
    (high + low + close) / 3 weighted by volume. ATR is the simple mean true range of the last 14 bars."""
    if len(cs) < DAY_BARS + 1:
        return None
    win = cs[-DAY_BARS:]
    vol = sum(c.volume for c in win)
    if vol <= 0:
        return None
    vwap = sum((c.high + c.low + c.close) / 3.0 * c.volume for c in win) / vol
    rets = []
    for prev, cur in zip(cs[-DAY_BARS - 1:-1], win):
        if prev.close <= 0 or cur.close <= 0:
            return None
        rets.append(math.log(cur.close / prev.close))
    mu = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
    if sd <= 0 or vwap <= 0:
        return None
    trs = [max(c.high, p.close) - min(c.low, p.close) for p, c in zip(cs[-15:-1], cs[-14:])]
    atr = sum(trs) / len(trs)
    return math.log(cs[-1].close / vwap) / (sd * math.sqrt(DAY_BARS)), atr, vwap


@dataclass
class SnapParams(Params):
    # threshold / offset_atr / target_r / max_hold_min: the DEV-selected variant of the pre-registered study
    # (docs/V13_SNAPBACK_STUDY.json, 54 variants). Its TEST verdict was FAIL: DEV +0.022 R a trade, TEST -0.089 R.
    threshold: float = 1.10                      # |stretch| to act on (daily-vol units); ~ the top 1.5% of 5m bars
    offset_atr: float = 0.5                      # the limit sits this many ATR(5m) beyond the close
    expiry_min: float = 15.0                     # an unfilled limit is cancelled after this
    stop_atr: float = 2.5
    min_stop_pct: float = 0.010
    max_stop_pct: float = 0.03
    target_r: float = 0.75
    max_hold_min: float = 240.0
    max_signals: int = 4
    cooldown_bars: int = 0


class SnapbackV13(ScanV11):
    id = "V13.1"
    name = "Snapback: limit-order mean reversion"
    family = "MAKER_REVERSION"
    thesis = ("a coin stretched far from its 24 h VWAP tends to snap back part of the way; entering with a resting "
              "limit (maker) instead of a market order removes most of the cost that sank every taker version")
    fails_when = ("the stretch keeps going (adverse selection: the limit fills exactly then), trending markets, "
                  "a maker queue position worse than the trade-through model assumes")
    expected_hold = "minutes to 4 hours"
    expected_frequency = "several limit orders an hour across 29 coins; fewer fills"
    Params = SnapParams
    max_positions = 4
    signal_tf, ctx_fast, ctx_slow = "5m", "5m", "5m"

    def __init__(self, params: Any = None):
        super().__init__(params)
        self.resting: dict[str, int] = {}         # coin -> when its resting limit expires (no second order before)

    def on_candle(self, c: Candle, ctx: Any) -> list[Signal]:
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
            found.append((abs(m[0]), sym, m, cs[-1]))
        found.sort(key=lambda x: (-x[0], x[1]))
        out: list[Signal] = []
        for _, sym, (st, atr, vwap), last in found[:p.max_signals]:
            sig = self.limit_signal(ctx, last, st, atr, vwap, t)
            if sig is not None:
                out.append(sig)
                self.resting[sym] = int(sig.meta.get("expire_ms") or t + MINUTE)
        return out

    def limit_signal(self, ctx: Any, c: Candle, st: float, atr: float, vwap: float, t: int) -> Signal | None:
        p = self.params
        side = "long" if st < 0 else "short"
        limit = c.close - p.offset_atr * atr if side == "long" else c.close + p.offset_atr * atr
        if limit <= 0:
            return None
        stop_pct = max(p.min_stop_pct, p.stop_atr * atr / limit)
        if stop_pct > p.max_stop_pct:
            return None
        expire = t + MINUTE + int(p.expiry_min * MINUTE)
        f = {"stretch": scale(abs(st), p.threshold, p.threshold + 1.0)}
        sig = self.entry(c=c, ctx=ctx, side=side, price=limit, stop_pct=stop_pct, tps_r=[(p.target_r, 1.0)],
                         trail_atr=None, be_at_r=None, expected_move_pct=p.target_r * stop_pct,
                         expected_move_source=f"{p.target_r:g}R snap back toward the 24 h VWAP", factors=f,
                         reason=f"stretch {st:+.2f} daily vol from the 24 h VWAP: {side} limit "
                                f"{p.offset_atr:g} ATR beyond",
                         extra={"setup": "SNAPBACK", "horizon": "SCAN", "order": "POST_ONLY_LIMIT", "limit": limit,
                                "expire_ms": expire, "stretch": round(st, 4), "vwap_24h": round(vwap, 10),
                                "atr_5m": round(atr, 10), "close_at_signal": c.close, "target_r": p.target_r,
                                "time_stop_h": p.max_hold_min / 60.0, "stop_pct": round(stop_pct, 5)})
        sig.max_hold_s = int(p.max_hold_min * 60)
        sig.id = f"{self.id}:LMT:{c.symbol}:{t}:{side}"
        return sig


def load_v13() -> dict[str, type[SnapbackV13]]:
    return {SnapbackV13.id: SnapbackV13}


__all__ = ["ANCHOR", "DAY_BARS", "SnapParams", "SnapbackV13", "load_v13", "stretch_atr"]
