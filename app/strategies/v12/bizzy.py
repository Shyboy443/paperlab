"""V12 BIZZY: "Bizzy Bee" from github.com/imikerussell/beebots (src/bees/bizzy.ts, strategies/BIZZY_BEE.md, her live
rules since 2026-09-24), ported to PaperLab's Bybit paper engine. A Larry Williams volatility breakout, LONG only, at
most ONE trade per UTC day:

    trigger   today's UTC open + 0.5 x yesterday's high-low range                 (beebots breakoutLevels, k = 0.5)
    entry     the first coin trading above its trigger -- a 1m close above it -- in beebots' preference order
              (BTC, ETH, SOL, HYPE); the order fills at the next minute's open, PaperLab's 60 s decision window
    size      full size: the whole book as margin at 2x leverage = 2x the equity in notional   (MAX_LEVERAGE 2, sizeFrac 1)
    stop      today's open ("failed breakout: back below today's open")
    exit      the UTC day close (1 minute before midnight) unless the stop fills first
    +JEV twin Jev may decline the breakout (beebots' WAIT): the bot keeps watching and asks again after a 5-minute
              cooldown (beebots' BIZZY cooldown) while the coin is still above its trigger. Jev does not manage the
              open position (beebots' CUT_LOSS is not ported)
    once      a day: the day counts as traded once a position is OPEN (the engine's manage() call shows it), so a
              declined or refused candidate does not use up the day, and a stopped-out trade is not re-entered

BTC is beebots' first choice but it cannot be bought here: a 20 USDT book at 2x is 40 USDT of notional and Bybit's
smallest BTC order (0.001 BTC) is worth ~110 USDT. BTC stays the feed's ANCHOR (the bot decides once per minute, on
BTC's candle, when every coin's candle of that minute is in) and the bot trades ETH, SOL and HYPE.

Expectation (scripts/v12_bizzy_study.py, docs/V12_BIZZY_STUDY.json): after Bybit costs the rule LOST over the last 95
days at 1m precision (-24% without BTC) and over ~2.5 years on 1h bars (-99%); no edge is claimed. It runs forward on
live data because the operator asked for it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.types import Candle, Signal
from app.strategies.v11.scan import ANCHOR, Params, ScanV11
from app.strategies.v6.base import scale

DAY = 86_400_000
MINUTE = 60_000
# beebots BIZZY_BREAKOUT_COINS, in their preference order; BTC (the anchor) is not tradable with a 20 USDT book
PREFERENCE: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT")
TRADE_COINS: tuple[str, ...] = ("ETHUSDT", "SOLUSDT", "HYPEUSDT")


@dataclass
class BizzyParams(Params):
    k: float = 0.5                      # Larry Williams: today's open + k x yesterday's range
    margin_frac: float = 1.0            # full size: the whole book as margin (x the 2x leverage ceiling)
    last_entry_min: int = 1435          # no entry in the day's last 5 minutes (it would close at once)
    retry_min: int = 5                  # a candidate not taken (Jev's WAIT, a refused order) is re-proposed after this
    exit_before_close_ms: int = MINUTE  # ride to 1 minute before the UTC midnight
    max_hold_min: float = 24 * 60.0     # (informational: the real limit is the day close)
    max_stop_pct: float = 0.5           # a sanity bound only; Bizzy has no stop cap


class BizzyV12(ScanV11):
    id = "V12.1"
    name = "Bizzy Bee: Larry Williams day breakout"
    family = "DAY_BREAKOUT"
    thesis = ("a coin trading above today's open + half of yesterday's range keeps rising to the day's close "
              "(Larry Williams volatility breakout, beebots' Bizzy Bee)")
    fails_when = "the breakout fails and price falls back to the day's open; costs; no tested edge (docs/V12_BIZZY_STUDY.json)"
    expected_hold = "until the UTC day close"
    expected_frequency = "at most one trade a day"
    Params = BizzyParams
    signal_tf, ctx_fast, ctx_slow = "1m", "1h", "1h"
    max_positions = 1
    trade_coins: tuple[str, ...] = TRADE_COINS

    def __init__(self, params: Any = None):
        super().__init__(params)
        self.traded_day: int | None = None              # one trade per UTC day: set once a position is open
        self.retry_at = 0                                # no new candidate before this instant

    # -- the day's levels, as beebots computes them (src/market/data.ts breakoutLevels) --------------------------------
    def levels(self, ctx: Any, sym: str, t: int) -> dict[str, float] | None:
        """Today's UTC open and yesterday's high-low range from the coin's 1h candles (today's first 1m candle while
        today's first hour is still open); None until a full previous day (>= 20 hourly candles) is available."""
        d0 = (t // DAY) * DAY
        h1 = list(ctx.candles(sym, "1h"))
        prev = [c for c in h1 if d0 - DAY <= c.open_time < d0]
        if len(prev) < 20:
            return None
        first = next((c for c in h1 if c.open_time == d0), None)
        if first is None:
            first = next((c for c in ctx.candles(sym, "1m") if c.open_time == d0), None)
        if first is None:
            return None
        rng = max(c.high for c in prev) - min(c.low for c in prev)
        if rng <= 0:
            return None
        return {"open": first.open, "prev_range": rng, "trigger": first.open + self.params.k * rng}

    # -- one decision per minute, on the anchor's candle -------------------------------------------------------------
    def on_candle(self, c: Candle, ctx: Any) -> list[Signal]:
        if c.tf != "1m" or c.symbol != self.anchor:
            return []
        t, day = c.close_time, c.open_time // DAY
        if self.traded_day == day or t < self.retry_at or (c.open_time - day * DAY) // MINUTE >= self.params.last_entry_min:
            return []
        for sym in PREFERENCE:
            if sym not in self.trade_coins or sym not in self.universe:
                continue
            lv = self.levels(ctx, sym, t)
            cs = ctx.candles(sym, "1m")
            if lv is None or not cs or cs[-1].close_time != t:          # no levels yet, or a data gap this minute
                continue
            last = cs[-1]
            if last.close <= lv["trigger"]:
                continue
            sig = self.breakout(ctx, last, lv, t, day)
            if sig is not None:
                self.retry_at = t + self.params.retry_min * MINUTE
                return [sig]
        return []

    def opened(self, ts: int) -> None:
        """An entry FILLED (BizzyEngine tells the strategy): today is used up -- no re-entry after the stop or the close."""
        self.traded_day = int(ts) // DAY

    def manage(self, pos: Any, c: Any, ctx: Any) -> None:
        self.opened(pos.entry_ts)                    # the same, from the open position (a backstop)
        return None


    def breakout(self, ctx: Any, c: Candle, lv: dict[str, float], t: int, day: int) -> Signal | None:
        p = self.params
        stop_pct = (c.close - lv["open"]) / c.close
        if stop_pct <= 0 or stop_pct > p.max_stop_pct:
            return None
        through = (c.close - lv["trigger"]) / c.close
        f = {"through_trigger": scale(through, 0.0, 0.01), "range": scale(lv["prev_range"] / lv["open"], 0.01, 0.08)}
        sig = self.entry(c=c, ctx=ctx, side="long", price=c.close, stop_pct=stop_pct, tps_r=(), trail_atr=None,
                         be_at_r=None, expected_move_pct=0.5 * lv["prev_range"] / c.close,
                         expected_move_source="day breakout ridden to the UTC close", factors=f,
                         reason=f"through today's trigger by {through * 100:.2f}%",
                         extra={"setup": "DAY_BREAKOUT", "horizon": "DAY", "margin_pct": p.margin_frac,
                                "day_open": round(lv["open"], 8), "trigger": round(lv["trigger"], 8),
                                "prev_range_pct": round(lv["prev_range"] / lv["open"] * 100, 3),
                                "day_move_pct": round((c.close / lv["open"] - 1) * 100, 3),
                                "through_trigger_pct": round(through * 100, 3), "stop_pct": round(stop_pct, 5),
                                "exit": "UTC day close (23:59) or back below today's open"})
        # ride to 23:59: the fill lands ~60 s after the decision, the deadline is counted from the fill
        close_at = (day + 1) * DAY - p.exit_before_close_ms
        sig.max_hold_s = max(60, int((close_at - (t + MINUTE)) / 1000))
        sig.id = f"{self.id}:DAY:{c.symbol}:{t}:long"
        return sig


def bizzy_engine():
    """The V11 scan engine (level fills, the pre-trade gap check) that also tells Bizzy when an entry has filled."""
    from app.live.scan_engine import ScanReplayEngine

    class BizzyEngine(ScanReplayEngine):
        def _signals(self, strat: Any, closed: Any, meta: Any, res: Any, cooldown_ms: int) -> None:
            self._strat = strat
            return super()._signals(strat, closed, meta, res, cooldown_ms)

        def _execute_pending(self, bar: Any, meta: Any, res: Any) -> None:
            before = len(res.fills)
            super()._execute_pending(bar, meta, res)
            strat = getattr(self, "_strat", None)
            if strat is not None and hasattr(strat, "opened"):
                for f in res.fills[before:]:
                    if f.kind == "entry":
                        strat.opened(f.ts)
    return BizzyEngine


def load_v12() -> dict[str, type[BizzyV12]]:
    return {BizzyV12.id: BizzyV12}


__all__ = ["ANCHOR", "BizzyV12", "BizzyParams", "PREFERENCE", "TRADE_COINS", "bizzy_engine", "load_v12"]
