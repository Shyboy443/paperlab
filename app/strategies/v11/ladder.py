"""The operator's take-profit ladder for the V11 scanners (requested 2026-09-30, TP1 re-tuned 2026-10-01):

    TP1  closes 25% of the position -> the stop moves past entry far enough to pay the fees (or further)
    TP2  closes 50% of the position -> the stop moves to TP1
    TP3  closes the last 25%        -> the trade is fully closed

The percentages are how much of the position (the ORIGINAL quantity) each TP closes. The operator asked (2026-10-01)
whether a bigger TP1, or a stop locked beyond entry after TP1, would stop trades that hit TP1 from ending in a loss.
scripts/v11_tp1_study.py tested TP1 = 25 / 30 / 35 / 40 / 50% x stop after TP1 = entry + fees / a third / half of the way
to TP1 on the 90-day replay, on the engine that fills exits at their level; the pre-registered rule (best discovery R
among variants where <= 2% of TP1-hit trades end negative) kept 25 / 50 / 25 and chose LOCK_AFTER_TP1 per scanner
(docs/V11_TP1_LEVEL_STUDY.json). The first run, on path-point fills, had favoured 35% (docs/V11_TP1_STUDY.json): the
"TP1 hit, still a loss" trades it fixed were mostly stops filled at the bar's extreme. The three TPs are EQUALLY spaced
(operator, 2026-09-30): TP1 = k R, TP2 = 2k R, TP3 = 3k R of the planned risk. The spacing k per scanner is chosen on
the DISCOVERY half of the 90-day replay (scripts/v11_ladder_study.py --equal, docs/V11_EQUAL_LADDER.json). A +JEV twin
stretches k by Jev's confidence (app/ai/jev/v11.py tp_scale); the ladder stays equally spaced.

Mechanics: app/live/scan_engine.py moves the stop the moment a take-profit fills -- after TP1 to tp1_lock (entry + 0.15%,
the round-trip fees, or LOCK_AFTER_TP1 of the way to TP1 when that is further), after TP2 onto TP1. manage() applies the
same locks at the scanner's decision bar as a backstop: after TP2 the stop goes to entry + (TP3 - entry) / 3, which is TP1
for an equally spaced ladder, stretched or not. Stops only ever move in the trade's favour.
"""
from __future__ import annotations

from typing import Any

from app.core.types import ExitUpdate, TakeProfit

N_TPS = 3
BE_COVER = 0.0015          # after TP1 the stop goes at least to entry + the round-trip fees (scan_engine.BE_COVER_BPS)
# How much of the position each TP closes, per scanner. Re-chosen 2026-10-01 on the engine that fills exits AT THEIR LEVEL
# (docs/V11_TP1_LEVEL_STUDY.json, 90 days; TP1 25 / 30 / 35 / 40 / 50% x three locks): once stops fill at the stop and
# TPs at the TP, a bigger TP1 no longer helps -- all shares are within noise and 25% is best on the discovery half for
# every scanner. All trades, R per trade and TP1-hit trades still ending negative, path-point fills -> level fills:
#   V11.1 -0.079 R, 0.7% -> -0.080 R, 0.0%     V11.2 -0.101 R, 2.7% -> -0.096 R, 0.4%
#   V11.3 -0.261 R, 15.9% -> -0.183 R, 4.9%    V11.4 -0.061 R, 0.3% -> -0.060 R, 0.0%
SHARES: dict[str, tuple[float, float, float]] = {f: (0.25, 0.50, 0.25) for f in ("V11.1", "V11.2", "V11.3", "V11.4")}
# Where the stop goes once TP1 has filled, as a fraction of the way from entry to TP1 (never less than BE_COVER)
LOCK_AFTER_TP1: dict[str, float] = {"V11.1": 0.0, "V11.2": 0.5, "V11.3": 1 / 3, "V11.4": 0.5}
# TP1 spacing k in R per scanner (TP2 = 2k, TP3 = 3k): docs/V11_EQUAL_LADDER.json, the best on the DISCOVERY half
# (90 days, gap check on: V11.1 0.67 -> -0.075 R/trade, V11.2 0.5 -> -0.108, V11.3 0.5 -> -0.247, V11.4 0.5 -> -0.061;
#  each at least as good as the unequal 0.5 / 1 / 2 ladder it replaces; still no scanner is profitable)
SPACING: dict[str, float] = {"V11.1": 2 / 3, "V11.2": 0.5, "V11.3": 0.5, "V11.4": 0.5}


def rungs_for(family: str, scale: float = 1.0) -> tuple[float, float, float]:
    k = SPACING[family] * float(scale)
    return (k, 2 * k, 3 * k)


RUNGS: dict[str, tuple[float, float, float]] = {f: rungs_for(f) for f in SPACING}


def tp1_lock(entry: float, side: str, tp1: float | None, frac: float) -> float:
    """The stop once TP1 has filled: `frac` of the way from entry to TP1, and never short of entry + the fees."""
    d = 1 if side == "long" else -1
    dist = entry * BE_COVER
    if tp1:
        dist = max(dist, float(frac) * abs(float(tp1) - entry))
    return entry + d * dist


def apply_ladder(sig: Any, rungs: tuple[float, ...], family: str) -> Any:
    """Set a signal's take-profits to `family`'s ladder (SHARES) at `rungs` R of its planned risk."""
    risk = abs(sig.entry_price - sig.stop)
    d = 1 if sig.side == "long" else -1
    sig.take_profits = [TakeProfit(sig.entry_price + d * r * risk, f) for r, f in zip(rungs, SHARES[family])]
    sig.be_at_r = rungs[0]
    sig.meta.update(exits="LADDER", ladder_r=[round(r, 4) for r in rungs], target_r=round(rungs[-1], 4),
                    shares=list(SHARES[family]), tp1_lock=LOCK_AFTER_TP1[family])
    return sig


def ladder_class(base_cls, rungs: tuple[float, float, float] | None = None):
    """The scanner family with its take-profit ladder instead of its single target."""

    class Ladder(base_cls):
        exits = "LADDER"
        ladder_rungs = tuple(rungs or RUNGS[base_cls.id])

        def _signal(self, ctx, x, t):
            s = super()._signal(ctx, x, t)
            return apply_ladder(s, self.ladder_rungs, base_cls.id) if s is not None else None

        def manage(self, pos, c, ctx):
            done = N_TPS - len(pos.take_profits)
            if done < 1 or pos.qty_initial <= 0:
                return None
            d = 1 if pos.side == "long" else -1
            # after TP1: past entry + the fees (or further, LOCK_AFTER_TP1), so the trade cannot end in a loss any more
            lock = tp1_lock(pos.entry_price, pos.side, pos.meta.get("tp1"),
                            pos.meta.get("tp1_lock", LOCK_AFTER_TP1[base_cls.id]))
            if done >= 2 and pos.take_profits:          # after TP2: the stop sits on TP1, a third of the way to TP3
                lock = pos.entry_price + (pos.take_profits[-1].price - pos.entry_price) / 3.0
            if (lock - pos.stop) * d > 0:
                return ExitUpdate(stop=lock, reason="be")
            return None

    Ladder.__name__ = base_cls.__name__ + "Ladder"
    Ladder.__qualname__ = Ladder.__name__
    return Ladder
