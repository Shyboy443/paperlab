"""S24 Pivot Level Reversal - fade the first touch of a classic floor-trader pivot level.

Idea:       Classic pivots from the previous session's high/low/close:
                P  = (H + L + C) / 3
                R1 = 2P - L,  S1 = 2P - H
                R2 = P + (H - L),  S2 = P - (H - L)
            These are the most widely watched horizontal levels in the world, which is exactly why price
            reacts at them. We fade the FIRST touch of S1/R1 (or S2/R2) each session, and only with a
            rejection candle, so we are not standing in front of a level that is being cut through.
Timeframe:  5m trigger; levels recomputed from the previous 24h session at the UTC roll.
Symbols:    all configured symbols.
Entry:      price trades through a level intraday and the bar CLOSES back on the origin side with a
            rejection wick of at least `min_wick_frac` of the bar range -> fade back toward the pivot.
Stop:       `stop_buffer_atr` ATRs beyond the bar's extreme (past the level).
Targets:    the central pivot P closes 60%, the rest trails; time exit after `max_hold_s`.
Sizing:     standard per-trade risk, 10x virtual leverage.
Why aggressive: one shot per level per session at the moment of rejection, with the stop just past the
            wick - small R, and the target is the whole distance back to the central pivot.

Interpretation note: the "session" is the UTC day, computed from 15m candles (96 bars) because the lab
does not subscribe to a daily series. A level is armed only once per session per side.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.indicators import last
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    use_second_levels: bool = P(True, label="also trade S2/R2",
                                help="off = only the S1/R1 pair, which is the more reactive one")
    min_wick_frac: float = P(0.4, min=0.05, max=0.9, step=0.05, label="min rejection wick (fraction of range)")
    min_poke_pct: float = P(0.02, min=0.0, max=1.0, step=0.01, label="min poke through the level %",
                            help="the bar must actually trade through the level, not just touch it")
    stop_buffer_atr: float = P(0.6, min=0.1, max=3.0, step=0.1, label="stop beyond the wick (ATR)")
    atr_n: int = P(14, min=5, max=50, step=1, label="ATR length")
    tp_frac: float = P(0.6, min=0.1, max=1.0, step=0.05, label="fraction closed at the central pivot")
    atr_trail_mult: float = P(2.0, min=0.5, max=6.0, step=0.1, label="ATR trail multiple")
    max_hold_s: int = P(14400, min=300, max=28800, step=300, label="max hold (s)")
    cooldown_s: int = P(900, min=0, max=7200, step=60, label="cooldown after exit (s)")


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


class PivotLevelReversal(Strategy):
    id = "S24"
    name = "Pivot Level Reversal"
    Params = Params
    doc = StrategyDoc(
        idea="Fade the first rejection of a classic floor-trader pivot level back toward the central pivot.",
        timeframe="5m trigger, levels from the previous UTC session",
        symbols="all configured",
        entry="price pokes through S1/R1 (or S2/R2) and the 5m bar closes back inside with a >= 40% "
              "rejection wick; one shot per level per session",
        stop="0.6 ATR beyond the rejection wick",
        targets="the central pivot P closes 60%, ATR trail on the rest, time exit at 4h",
        sizing="standard risk per trade at 10x virtual leverage",
        why_aggressive="takes the level at the moment of rejection with a stop just past the wick, so R "
                       "is small next to the full distance back to P",
    )
    timeframes = ("5m", "15m")
    contributes_votes = False
    warmup_bars = 120
    max_positions = 1
    min_rr = 1.2
    default_leverage = 10

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._levels: dict[str, dict[str, Any]] = {}
        self._used: dict[str, set[str]] = {}
        self._stats = {"levels_built": 0, "pokes": 0, "no_wick": 0, "traded": 0}

    def cooldown_ms(self) -> int:
        return int(self.params.cooldown_s) * 1000

    def _session_levels(self, symbol: str, ctx: MarketContext, today: str) -> dict[str, float] | None:
        """Pivots from the previous complete UTC day, rebuilt once per session."""
        cached = self._levels.get(symbol)
        if cached and cached.get("day") == today:
            return cached.get("levels")
        c15 = ctx.candles(symbol, "15m")
        if len(c15) < 100:
            return None
        prev = [x for x in c15 if _day(x.open_time) == _prev_day(today)]
        if len(prev) < 80:                       # need most of a session; a partial one gives fake levels
            return None
        hi, lo, close = max(x.high for x in prev), min(x.low for x in prev), prev[-1].close
        pivot = (hi + lo + close) / 3.0
        levels = {"P": pivot, "R1": 2 * pivot - lo, "S1": 2 * pivot - hi,
                  "R2": pivot + (hi - lo), "S2": pivot - (hi - lo)}
        self._levels[symbol] = {"day": today, "levels": levels, "prev_high": hi, "prev_low": lo}
        self._used[symbol] = set()
        self._stats["levels_built"] += 1
        return levels

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        today = _day(c.open_time)
        levels = self._session_levels(c.symbol, ctx, today)
        if not levels:
            return []
        atr = last(ctx.ind(c.symbol, "5m", "atr", n=int(p.atr_n)))
        if atr is None or atr <= 0 or c.range <= 0:
            return []
        names = ["R1", "S1"] + (["R2", "S2"] if p.use_second_levels else [])
        used = self._used.setdefault(c.symbol, set())
        poke = p.min_poke_pct / 100.0
        for name in names:
            if name in used:
                continue
            lvl = levels[name]
            resistance = name.startswith("R")
            if resistance:
                # poked above and closed back below, with the rejection in the upper wick
                if not (c.high >= lvl * (1 + poke) and c.close < lvl):
                    continue
                wick = (c.high - max(c.open, c.close)) / c.range
                side, stop = "short", c.high + p.stop_buffer_atr * atr
            else:
                if not (c.low <= lvl * (1 - poke) and c.close > lvl):
                    continue
                wick = (min(c.open, c.close) - c.low) / c.range
                side, stop = "long", c.low - p.stop_buffer_atr * atr
            self._stats["pokes"] += 1
            if wick < p.min_wick_frac:
                self._stats["no_wick"] += 1
                continue
            price = ctx.last_price(c.symbol) or c.close
            target = levels["P"]
            risk = abs(price - stop)
            if risk <= 0 or abs(target - price) / risk < self.min_rr:
                continue
            if side == "long" and not (stop < price < target):
                continue
            if side == "short" and not (target < price < stop):
                continue
            used.add(name)                       # one shot per level per session
            self._stats["traded"] += 1
            return [self.make_entry(
                symbol=c.symbol, side=side, ts=c.close_time, tf="5m", price=price, stop=stop,
                tps_price=[(target, p.tp_frac)],
                trail=TrailSpec("atr", "5m", p.atr_trail_mult, int(p.atr_n), True),
                max_hold_s=int(p.max_hold_s), valid_bars=2,
                reason=f"{name} rejection, {wick * 100:.0f}% wick, back to P",
                meta={"level": name, "level_price": lvl, "pivot": target, "wick_frac": round(wick, 2)})]
        return []

    def state(self) -> dict[str, Any]:
        return {"levels": {s: v.get("levels") for s, v in self._levels.items()},
                "used_today": {s: sorted(v) for s, v in self._used.items() if v}, **self._stats}

    def reset(self) -> None:
        self._levels.clear()
        self._used.clear()
        for k in self._stats:
            self._stats[k] = 0


def _prev_day(day: str) -> str:
    from datetime import date, timedelta
    y, m, d = (int(x) for x in day.split("-"))
    return (date(y, m, d) - timedelta(days=1)).isoformat()
