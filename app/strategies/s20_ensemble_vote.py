"""S20 Ensemble Vote - "trade the crowd of bots"

Idea:       Count the live entry votes of seven contributor strategies per symbol; act only when at least three
            agree and the 15m EMA50 agrees with them, sizing up when five or more line up.
Timeframe:  5m evaluation; 15m EMA50 direction filter.
Symbols:    all configured symbols, one position at a time.
Entry:      |net votes| >= 3 within a 30 min TTL (LIVE votes only - allow_shadow_votes stays off - and at least 3
            live contributors must have a current ENTRY vote on that symbol) and the latest closed 15m close on the
            EMA50 side of the trade; size_mult 1.0 at |net| 3-4 and 1.25 at |net| >= 5.
Stop:       the tightest contributing same-side stop still on the correct side of the entry; fallback
            1.2 x ATR(5m, 14).
Targets:    TP1 2R closes 40%; the remaining 60% rides a 1.2 x ATR(5m, 14) trail armed after TP1, hard-capped at
            6R. Break-even stop at 0.8R. Exit "vote_flip" when the net vote flips against the position OR when the
            live |net| drops below 2.
Size:       2% of the strategy wallet at risk per trade (x1.25 on strong agreement), 15x virtual leverage.
Why aggressive: sizes up when agreement is high, borrows the tightest stop of the crowd and lets the runner go to
            6R with break-even at 0.8R. 1.25 (not 1.4) is deliberate: 1.4 put 11.57 SOL on an $84 book.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.signals import Vote
from app.core.types import Candle, Signal, TrailSpec
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc

CONTRIBUTORS: tuple[str, ...] = ("S01", "S03", "S06", "S10", "S12", "S13", "S19")


@dataclass
class Params:
    min_votes: int = P(3, min=1, max=7, step=1, label="min |net| votes")
    big_votes: int = P(5, min=2, max=7, step=1, label="big |net| votes", help="|net| at which size_big applies")
    size_big: float = P(1.25, min=1.0, max=3.0, step=0.05, label="size mult (big)",
                        help="size multiplier at |net| >= big_votes; |net| 3-4 always sizes 1.0")
    exit_below_net: int = P(2, min=1, max=6, step=1, label="exit below |net|",
                            help="close the position when the live |net| vote drops below this")
    vote_ttl_min: int = P(30, min=10, max=120, step=5, label="vote TTL (min)")
    allow_shadow_votes: bool = P(False, label="allow shadow votes",
                                 help="count votes of disabled contributors and drop the live-contributor gate")
    ema_period: int = P(50, min=10, max=200, step=1, label="15m EMA period")
    fallback_atr_mult: float = P(1.2, min=0.5, max=4.0, step=0.1, label="fallback stop ATR mult")
    tp1_r: float = P(2.0, min=0.5, max=6.0, step=0.1, label="TP1 (R)")
    tp1_fraction: float = P(0.40, min=0.1, max=0.9, step=0.05, label="TP1 fraction")
    runner_r: float = P(6.0, min=1.0, max=12.0, step=0.1, label="runner cap (R)",
                        help="hard cap on the runner: closes the remainder if the trail has not already")
    trail_atr_mult: float = P(1.2, min=0.5, max=5.0, step=0.1, label="runner trail ATR mult")
    be_at_r: float = P(0.8, min=0.0, max=3.0, step=0.1, label="break-even at (R)",
                       help="move the stop to entry once the trade is this many R in profit")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")


class EnsembleVote(Strategy):
    id = "S20"
    name = "Ensemble Vote"
    Params = Params
    doc = StrategyDoc(
        idea="Meta strategy: trade when at least three contributor strategies vote the same way.",
        timeframe="5m evaluation, 15m EMA50 filter",
        symbols="all configured, one position at a time",
        entry="|net live votes| >= 3 within 30 min (>= 3 live contributors with a current ENTRY vote on that "
              "symbol) and the latest closed 15m close on the EMA50 side; size_mult 1.0 at |net| 3-4, 1.25 at >= 5",
        stop="tightest same-side contributor stop on the correct side of the entry; fallback 1.2 x ATR(5m, 14)",
        targets="TP1 2R closes 40%, 1.2 x ATR trail on the rest after TP1, hard cap 6R; break-even at 0.8R; "
                "exit 'vote_flip' when the net vote turns against the position or the live |net| drops below 2",
        sizing="2% wallet risk per trade (x1.25 on strong agreement) at 15x virtual leverage",
        why_aggressive="size-up when agreement is high, runner to 6R with break-even at 0.8R",
    )
    timeframes = ("5m", "15m")
    max_positions = 1
    min_rr = 2.5
    contributes_votes = False
    warmup_bars = 60
    badges = ("META",)

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._symbols: dict[str, dict[str, Any]] = {}

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        sym = c.symbol
        now = ctx.now_ms()
        votes: list[Vote] = list(ctx.board.votes(sym, CONTRIBUTORS, ttl_ms=int(p.vote_ttl_min) * 60_000, now_ms=now))
        live_contributors = sum(1 for sid in CONTRIBUTORS if ctx.is_enabled(sid))
        counted = [v for v in votes if p.allow_shadow_votes or v.source == "live"]
        # The gate is on live contributors that actually have a current ENTRY vote on THIS symbol, not merely
        # on how many contributor strategies happen to be enabled.
        live_voters = sum(1 for v in votes if v.source == "live")
        longs = sum(1 for v in counted if v.side == "long")
        shorts = len(counted) - longs
        net = longs - shorts
        want = "long" if net > 0 else "short" if net < 0 else None
        ema = ctx.ind_last(sym, "15m", "ema", n=int(p.ema_period))
        c15 = ctx.candles(sym, "15m")
        ema_ok: bool | None = None
        if want is not None and ema is not None and c15:
            ema_ok = c15[-1].close > ema if want == "long" else c15[-1].close < ema
        gated = live_voters < int(p.min_votes) and not p.allow_shadow_votes
        notice = (f"needs >= {int(p.min_votes)} live contributors with a current entry vote on {sym} "
                  f"(have {live_voters})") if gated else None
        self._symbols[sym] = {"net": net, "live_contributors": live_contributors, "live_voters": live_voters,
                              "counted": len(counted), "votes": [v.to_dict(now) for v in votes], "ema_ok": ema_ok,
                              "notice": notice, "last_eval_ts": now}
        held = ctx.positions_of(self.id)
        mine = [pos for pos in held if pos.symbol == sym]
        if mine:
            pos = mine[0]
            flipped = (net < 0) if pos.side == "long" else (net > 0)
            faded = abs(net) < int(p.exit_below_net)
            if flipped or faded:
                why = "flip" if flipped else f"|net| {abs(net)} < {int(p.exit_below_net)}"
                return [self.make_exit(symbol=sym, side=pos.side, ts=c.close_time, tf="5m", reason="vote_flip",
                                       meta={"net": net, "longs": longs, "shorts": shorts, "why": why})]
            return []
        if held or gated or want is None or abs(net) < int(p.min_votes) or not ema_ok:
            return []
        price = ctx.last_price(sym) or c.close
        stop = self._tightest_stop(counted, want, price)
        stop_source = "contributor"
        if stop is None:
            atr = ctx.ind_last(sym, "5m", "atr", n=int(p.atr_period))
            if atr is None or atr <= 0:
                return []
            stop = price - p.fallback_atr_mult * atr if want == "long" else price + p.fallback_atr_mult * atr
            stop_source = "atr"
        size_mult = p.size_big if abs(net) >= int(p.big_votes) else 1.0
        return [self.make_entry(
            symbol=sym, side=want, ts=c.close_time, tf="5m", price=price, stop=stop,
            tps_r=[(p.tp1_r, p.tp1_fraction), (p.runner_r, 1.0 - p.tp1_fraction)],
            trail=TrailSpec("atr", "5m", p.trail_atr_mult, int(p.atr_period), activate_after_tp1=True),
            be_at_r=p.be_at_r, valid_bars=1, size_mult=size_mult,
            reason=f"ensemble net {net:+d} of {len(counted)} counted votes -> {want}",
            meta={"net": net, "longs": longs, "shorts": shorts, "stop_source": stop_source,
                  "voters": [v.strategy_id for v in counted if v.side == want],
                  "live_contributors": live_contributors, "live_voters": live_voters})]

    @staticmethod
    def _tightest_stop(votes: Sequence[Vote], side: str, price: float) -> float | None:
        """Tightest same-side contributor stop still on the correct side of `price`, else None."""
        if side == "long":
            cands = [v.stop for v in votes if v.side == "long" and 0 < v.stop < price]
            return max(cands) if cands else None
        cands = [v.stop for v in votes if v.side == "short" and v.stop > price]
        return min(cands) if cands else None

    def state(self) -> dict[str, Any]:
        return {"symbols": self._symbols, "contributors": list(CONTRIBUTORS)}

    def reset(self) -> None:
        self._symbols.clear()
