"""V8 +LADDER exits (requested by the operator on 2026-09-28): the SAME V8 entries, with a take-profit ladder and
stop moves instead of one 1.5R target.

    TP1  +0.75R  closes 1/3 -> the stop moves to entry + 0.15% (the round-trip costs: a "break-even" that pays fees)
    TP2  +1.5R   closes 1/3 -> the stop moves to +0.75R (profit locked)
    TP3  +2.5R   closes the last 1/3
    the 45-minute time stop and the structural initial stop are unchanged

Every +LADDER bot runs next to its CONTROL on the same signals, so the forward results show whether the ladder's
higher win rate turns into more money. The 60-day replay before launch (docs/V8_EXIT_STUDY.json, LADDER3) found a
higher win rate (39.5% vs 34.6%) but a slightly worse result per trade (-0.290R vs -0.284R); live data decides.

Mechanics: the engine closes each TP as a fraction of the ORIGINAL quantity and moves the stop to entry + 6 bp the
instant TP1 prints (`be_at_r`); after every 1m bar `manage()` raises the stop to entry + 0.15% after TP1 and to
+0.75R after TP2. Stops only ever move in the trade's favour.
"""
from __future__ import annotations

from app.core.types import ExitUpdate, TakeProfit

LADDER: tuple[tuple[float, float], ...] = ((0.75, 1 / 3), (1.5, 1 / 3), (2.5, 1 / 3))
COST_LOCK = 0.0015                 # entry + 2 x 0.055% taker + spread
LOCK_AFTER_TP2_R = 0.75


def ladder_class(base_cls):
    """The +LADDER variant of a V8 family: same entries, ladder exits."""

    class Ladder(base_cls):
        exits = "LADDER"

        def on_candle(self, c, ctx):
            sigs = super().on_candle(c, ctx)
            for s in sigs:
                risk = abs(s.entry_price - s.stop)
                d = 1 if s.side == "long" else -1
                s.take_profits = [TakeProfit(s.entry_price + d * r * risk, f) for r, f in LADDER]
                s.be_at_r = LADDER[0][0]
                s.meta["exits"] = "LADDER"
            return sigs

        def manage(self, pos, c, ctx):
            if pos.qty_initial <= 0 or pos.initial_risk_usd <= 0:
                return None
            done = len(LADDER) - len(pos.take_profits)
            if done < 1:
                return None
            d = 1 if pos.side == "long" else -1
            lock = pos.entry_price * (1 + d * COST_LOCK)
            if done >= 2:
                lock = pos.entry_price + d * LOCK_AFTER_TP2_R * pos.initial_risk_usd / pos.qty_initial
            if (lock - pos.stop) * d > 0:
                return ExitUpdate(stop=lock, reason="be")
            return None

    Ladder.__name__ = base_cls.__name__ + "Ladder"
    Ladder.__qualname__ = Ladder.__name__
    return Ladder
