"""V11 SCAN: paper bots that scan EVERY coin of a 30-coin Bybit universe and take the best setups (docs/V11_PROTOCOL.md).

A V8 bot trades one coin. A V11 bot holds one book for the whole universe: at each decision bar it evaluates every coin,
ranks what it finds by the setup's score and sends the best first; the RiskManager then fills the free slots, one
position per coin (a coin already held, or in its one-bar cooldown, is refused). A setup scanner holds up to 8 coins at
once (operator's request, 2026-09-29: a bot with 30 coins should take several trades together); the engine's gross
notional cap (8x equity) and the legal sizing tiers still bound the book.

The decision is taken ONCE per bar, on the ANCHOR coin's candle (BTCUSDT, which the V11 feed always delivers last in
each minute), so every coin's candle for that instant is already in the context -- a coin whose candle is missing at
that instant (a data gap) is simply not scanned. Nothing is read that closed after the decision instant.

    V11.1  RS_BREAKOUT     15m  a coin in the top 20% by 4h return (vs the universe) closes above its 4h high on
                                >= 1.5x volume in the 1h uptrend (mirror: bottom 20%, 4h low, 1h downtrend)
                                stop 1.2-3% (was 0.8%), 2R, 4 h; only scores >= the study's discovery 67th
                                percentile (TOP)
    V11.2  RS_PULLBACK     15m  a top-20% coin in a 15m AND 1h uptrend dips to its 15m EMA20 and resumes through the
                                previous bar (mirror for the weakest); stop 1.5-3% (was 0.8%), 2R, 4 h; TOP
    V11.3  CAPITULATION    5m   a 1 h fall of >= 4 ATR on climax volume, then a reversal bar (mirror: a blow-off top);
                                stop 1.0-3% (was 0.6%: docs/V11_ZAP_STUDY.json), 2R, 2 h
    V11.4  REVERSAL_1H     1h   every hour, short the 2 coins that rose most over 4 h and buy the 2 that fell most (the
                                one robust effect in docs/V11_FEATURE_STUDY.json); held 58 minutes, wide 2 ATR stop

No edge is claimed. The 90-day study (docs/V11_SCAN_STUDY.json, docs/V11_FEATURE_STUDY.json) found NO scanner rule that
beats taker costs in both halves; these are its least-bad variants, run forward so live data can judge them.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Sequence

from app.core.types import Candle, Signal
from app.strategies.v6.base import V6Strategy, close_location, scale

ANCHOR = "BTCUSDT"


@dataclass
class Params:
    target_r: float = 2.0
    min_stop_pct: float = 0.008
    max_stop_pct: float = 0.03
    stop_buffer_atr: float = 0.25
    max_hold_min: float = 240.0
    cooldown_bars: int = 1
    min_score: float = 0.0                       # TOP: the study's discovery 67th percentile of the family's score
    max_signals: int = 8                         # best first; the RiskManager fills the free slots


@dataclass
class Cand:
    symbol: str
    side: str
    extreme: float                               # the structural stop reference (before the ATR buffer)
    atr: float
    score: float
    factors: dict[str, float]
    note: str


def rank_pct(values: dict[str, float]) -> dict[str, float]:
    """Cross-sectional percentile rank (average rank / count, as pandas rank(pct=True))."""
    items = sorted(values.items(), key=lambda kv: kv[1])
    out: dict[str, float] = {}
    n = len(items)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[items[k][0]] = avg / n
        i = j + 1
    return out


class ScanV11(V6Strategy):
    version = "v11"
    Params = Params
    horizon = "SCAN"
    universe: tuple[str, ...] = ()
    anchor = ANCHOR
    max_positions = 8                            # up to 8 coins at once, one position per coin
    flat_extreme = False                         # REVERSAL_1H: the stop is measured from the close, all ATR buffer
    expected_hold = ""
    expected_frequency = ""
    fails_when = "no demonstrated edge after taker costs (docs/V11_SCAN_STUDY.json)"
    signal_tf = "15m"
    ctx_fast = "1h"
    ctx_slow = "1h"

    @classmethod
    def for_universe(cls, universe: Sequence[str], feed=None, market=None):
        return type(cls.__name__ + "_SCAN", (cls,), {
            "universe": tuple(universe), "native_timeframe": cls.signal_tf, "supported_timeframes": (cls.signal_tf,),
            "context_tf": cls.ctx_slow, "timeframes": tuple(dict.fromkeys((cls.signal_tf, cls.ctx_fast, cls.ctx_slow))),
            "feed": feed, "market": market, "time_stop_h": cls.Params.max_hold_min / 60.0, "tag": "SCAN",
            "day_bars": 288, "journal": {}, "__module__": cls.__module__})

    # -- one decision per bar, on the anchor's candle --------------------------------------------------------------
    def on_candle(self, c: Candle, ctx: Any) -> list[Signal]:
        if not self.bound(c) or c.symbol != self.anchor:
            return []
        found = [x for x in self.scan(ctx, c.close_time) if x.score >= self.params.min_score]
        found.sort(key=lambda x: (-x.score, x.symbol))
        out: list[Signal] = []
        for x in found[:self.params.max_signals]:
            sig = self._signal(ctx, x, c.close_time)
            if sig is not None:
                out.append(sig)
        return out

    def scan(self, ctx: Any, t: int) -> list[Cand]:
        raise NotImplementedError

    def current(self, ctx: Any, sym: str, tf: str, t: int, need: int) -> list[Candle] | None:
        """The coin's candles on `tf` if its latest one closed exactly at the decision instant (else: a data gap)."""
        cs = ctx.candles(sym, tf)
        if len(cs) < need or cs[-1].close_time != t:
            return None
        return list(cs)

    def _signal(self, ctx: Any, x: Cand, t: int) -> Signal | None:
        p = self.params
        cs = ctx.candles(x.symbol, self.signal_tf)
        c = cs[-1]
        dist = (c.close - x.extreme) if x.side == "long" else (x.extreme - c.close)
        raw = (dist + p.stop_buffer_atr * x.atr) / c.close
        if dist < 0 or (dist == 0 and not self.flat_extreme) or raw <= 0 or raw > p.max_stop_pct:
            return None
        stop_pct = max(raw, p.min_stop_pct)
        sig = self.entry(c=c, ctx=ctx, side=x.side, price=c.close, stop_pct=stop_pct, tps_r=[(p.target_r, 1.0)],
                         trail_atr=None, be_at_r=None, expected_move_pct=p.target_r * stop_pct,
                         expected_move_source=f"{p.target_r:g}R scanner target", factors=x.factors, reason=x.note,
                         extra={"setup": x.note, "horizon": "SCAN", "target_r": p.target_r, "score": round(x.score, 4),
                                "time_stop_h": p.max_hold_min / 60.0, "stop_pct": round(stop_pct, 5)})
        sig.max_hold_s = int(p.max_hold_min * 60)
        sig.id = f"{self.id}:SCAN:{x.symbol}:{t}:{x.side}"
        return sig

    # -- shared measures -------------------------------------------------------------------------------------------
    def rs_ranks(self, ctx: Any, t: int, tf: str, bars: int) -> dict[str, float]:
        """Every coin's return over the last `bars` candles of `tf`, as a percentile across the coins that have it."""
        rets: dict[str, float] = {}
        for sym in self.universe:
            cs = self.current(ctx, sym, tf, t, bars + 1)
            if cs is not None and cs[-1 - bars].close > 0:
                rets[sym] = cs[-1].close / cs[-1 - bars].close - 1.0
        return rank_pct(rets) if len(rets) >= 10 else {}

    @staticmethod
    def median_volume(cs: Sequence[Candle], n: int) -> float:
        return statistics.median(x.volume for x in cs[-1 - n:-1])


@dataclass
class RsbParams(Params):
    min_score: float = 0.6743                    # docs/V11_SCAN_STUDY.json families.RSB.top_cut (discovery half)
    min_stop_pct: float = 0.012                  # was 0.8%: better in both halves (docs/V11_SCANNER_COST_STUDY.json)


@dataclass
class RspParams(Params):
    min_score: float = 0.6111                    # docs/V11_SCAN_STUDY.json families.RSP.top_cut (discovery half)
    min_stop_pct: float = 0.015                  # was 0.8%: better in both halves (docs/V11_SCANNER_COST_STUDY.json)


class RsBreakoutV11(ScanV11):
    id = "V11.1"
    name = "Relative-strength breakout scanner"
    family = "SCAN_RS_BREAKOUT"
    thesis = "the universe's strongest (weakest) coin breaking its 4h high (low) on volume keeps going"
    expected_hold = "15 minutes to 4 hours"
    expected_frequency = "about 8 a day across the universe (study, TOP)"
    Params = RsbParams
    signal_tf, ctx_fast, ctx_slow = "15m", "1h", "1h"

    def scan(self, ctx: Any, t: int) -> list[Cand]:
        ranks = self.rs_ranks(ctx, t, "15m", 16)
        out = []
        for sym, rk in ranks.items():
            cs = self.current(ctx, sym, "15m", t, 60)
            atr = self.atr(ctx, sym, "15m") if cs else None
            if not cs or not atr:
                continue
            c = cs[-1]
            med = self.median_volume(cs, 20)
            if med <= 0 or c.volume < 1.5 * med:
                continue
            prior = cs[-17:-1]
            t1h = self.ctx_trend(ctx, sym, "1h")
            loc = close_location(c)
            if rk >= 0.8 and c.close > max(x.high for x in prior) and t1h == "up" and loc >= 0.6:
                side, ext = "long", min(x.low for x in cs[-3:])
            elif rk <= 0.2 and c.close < min(x.low for x in prior) and t1h == "down" and loc <= 0.4:
                side, ext = "short", max(x.high for x in cs[-3:])
            else:
                continue
            f = {"rs": scale(abs(rk - 0.5), 0.3, 0.5), "volume": scale(c.volume / med, 1.5, 4.0)}
            out.append(Cand(sym, side, ext, atr, sum(f.values()) / 2, f, f"RS {rk:.2f} breakout of the 4h range"))
        return out


class RsPullbackV11(ScanV11):
    id = "V11.2"
    name = "Relative-strength pullback scanner"
    family = "SCAN_RS_PULLBACK"
    thesis = "a leader (laggard) that dips to its 15m EMA20 inside a 15m + 1h trend resumes"
    expected_hold = "15 minutes to 4 hours"
    expected_frequency = "about 11 a day across the universe (study, TOP)"
    Params = RspParams
    signal_tf, ctx_fast, ctx_slow = "15m", "1h", "1h"

    def scan(self, ctx: Any, t: int) -> list[Cand]:
        ranks = self.rs_ranks(ctx, t, "15m", 16)
        out = []
        for sym, rk in ranks.items():
            cs = self.current(ctx, sym, "15m", t, 60)
            atr = self.atr(ctx, sym, "15m") if cs else None
            ema = ctx.ind(sym, "15m", "ema", n=20) if cs else None
            if not cs or not atr or not ema or ema[-1] is None:
                continue
            e, c, prev = ema[-1], cs[-1], cs[-2]
            t15, t1h = self.ctx_trend(ctx, sym, "15m"), self.ctx_trend(ctx, sym, "1h")
            loc = close_location(c)
            if abs(c.close - e) > 1.5 * atr:
                continue
            prior = cs[-4:-1]
            if (rk >= 0.8 and t15 == "up" and t1h == "up" and min(x.low for x in prior) <= e + 0.3 * atr
                    and c.close > prev.high and c.close > e and loc >= 0.55):
                side, ext, q = "long", min(x.low for x in cs[-4:]), loc
            elif (rk <= 0.2 and t15 == "down" and t1h == "down" and max(x.high for x in prior) >= e - 0.3 * atr
                    and c.close < prev.low and c.close < e and loc <= 0.45):
                side, ext, q = "short", max(x.high for x in cs[-4:]), 1 - loc
            else:
                continue
            f = {"rs": scale(abs(rk - 0.5), 0.3, 0.5), "close": scale(q, 0.55, 1.0)}
            out.append(Cand(sym, side, ext, atr, sum(f.values()) / 2, f, f"RS {rk:.2f} pullback to the 15m EMA20"))
        return out


@dataclass
class CapParams(Params):
    # 2.0% (operator 2026-10-03). History: 0.6% -> 1.0% on 2026-10-02 ("improve Zap", docs/V11_ZAP_STUDY.json): the tight
    # stops made the round-trip fee 0.15 R a trade. The follow-up rejected 1.5% / 2.0% as worse in the second half, but
    # that was one bogus ARB end-of-replay exit priced on BTC's bar (docs/V11_END_OF_RUN_RECHECK.json). Corrected, 2.0%
    # beats 1.0% in both halves: net R per trade -0.042 / -0.055 / -0.049 (half 1 / half 2 / all) vs -0.130 / -0.098 /
    # -0.114, with resting take-profits, 90 days. Still a loss: it cuts cost, it is not an edge.
    min_stop_pct: float = 0.020
    stop_buffer_atr: float = 0.2
    max_hold_min: float = 120.0


class CapitulationV11(ScanV11):
    id = "V11.3"
    name = "Capitulation snap-back scanner"
    family = "SCAN_CAPITULATION"
    thesis = "a coin that falls (rises) 4+ ATR in an hour on climax volume snaps back after a reversal bar"
    expected_hold = "5 minutes to 2 hours"
    expected_frequency = "about 20 a day across the universe (study)"
    Params = CapParams
    signal_tf, ctx_fast, ctx_slow = "5m", "1h", "1h"

    def scan(self, ctx: Any, t: int) -> list[Cand]:
        out = []
        for sym in self.universe:
            cs = self.current(ctx, sym, "5m", t, 290)
            atr = self.atr(ctx, sym, "5m") if cs else None
            if not cs or not atr:
                continue
            c, prev = cs[-1], cs[-2]
            med = self.median_volume(cs, 288)
            if med <= 0:
                continue
            window = cs[-13:-1]
            fall = (prev.close - max(x.high for x in window)) / atr
            rise = (prev.close - min(x.low for x in window)) / atr
            climax = sum(1 for x in cs[-5:-1] if x.volume >= 2.5 * med) >= 2
            loc = close_location(c)
            if not climax:
                continue
            if fall <= -4 and c.close > c.open and c.close > prev.close and loc >= 0.6:
                side, ext, size = "long", min(x.low for x in cs[-4:]), -fall
            elif rise >= 4 and c.close < c.open and c.close < prev.close and loc <= 0.4:
                side, ext, size = "short", max(x.high for x in cs[-4:]), rise
            else:
                continue
            f = {"stretch": scale(size, 4.0, 8.0)}
            out.append(Cand(sym, side, ext, atr, f["stretch"], f, f"{size:.1f} ATR 1h move on climax volume, reversal bar"))
        return out


@dataclass
class RevParams(Params):
    target_r: float = 3.0
    min_stop_pct: float = 0.015
    max_stop_pct: float = 0.08
    stop_buffer_atr: float = 2.0                 # the stop sits 2 ATR(1h) away: a tail guard, not the exit
    max_hold_min: float = 58.0                   # out before the next hourly decision
    cooldown_bars: int = 0
    per_side: int = 2
    max_signals: int = 4


class Reversal1hV11(ScanV11):
    id = "V11.4"
    name = "1-hour reversal ranker"
    family = "SCAN_REVERSAL_1H"
    thesis = "over the next hour, the coins that ran most over 4 h give some back and the ones that fell most bounce"
    expected_hold = "58 minutes"
    expected_frequency = "4 positions an hour"
    Params = RevParams
    max_positions = 4
    flat_extreme = True
    signal_tf, ctx_fast, ctx_slow = "1h", "1h", "1h"

    def scan(self, ctx: Any, t: int) -> list[Cand]:
        rets: dict[str, float] = {}
        atrs: dict[str, float] = {}
        for sym in self.universe:
            cs = self.current(ctx, sym, "1h", t, 20)
            atr = self.atr(ctx, sym, "1h") if cs else None
            if cs is None or not atr or cs[-5].close <= 0:
                continue
            rets[sym] = cs[-1].close / cs[-5].close - 1.0
            atrs[sym] = atr
        if len(rets) < 10:
            return []
        ranks = rank_pct(rets)
        order = sorted(rets, key=lambda s: (rets[s], s))
        k = self.params.per_side
        out = []
        for sym, side in [(s, "long") for s in order[:k]] + [(s, "short") for s in order[-k:]]:
            close = ctx.candles(sym, "1h")[-1].close
            f = {"extreme": scale(abs(ranks[sym] - 0.5), 0.3, 0.5)}
            out.append(Cand(sym, side, close, atrs[sym], f["extreme"], f,
                            f"4h return {rets[sym]:+.2%}: {'bottom' if side == 'long' else 'top'} {k} of the universe"))
        return out


def load_v11_scanners() -> dict[str, type[ScanV11]]:
    return {cls.id: cls for cls in (RsBreakoutV11, RsPullbackV11, CapitulationV11, Reversal1hV11)}

