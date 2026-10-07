"""V8's engine (operator 2026-10-02, "improve the other bots"; scripts/v8_cost_study.py, docs/V8_COST_STUDY.json): V11's
scan engine -- exits filled AT THEIR LEVEL, the ladder's stop moved the moment a take-profit fills, take-profits resting
as LIMIT orders (maker fee, filled on a 0.5 bp trade-through) -- without V11's pre-trade gap check, which V8 never had.

Over the cached 60 days (CONTROL bots, every family x coin) the level fills and resting take-profits improved every
family's net R per trade in both halves, together with each family's wider minimum stop
(app/strategies/v8/arena.py). The shared engine (app/backtest/replay.py) is unchanged; this subclass is pinned by V8's
freeze.
"""
from __future__ import annotations

from typing import Any

from app.backtest.replay import ReplayEngine
from app.live.scan_engine import MAKER_TP_TRADE_THROUGH, ScanReplayEngine


class LevelMakerEngineV8(ScanReplayEngine):
    def _execute_pending(self, bar: Any, meta: Any, res: Any) -> None:
        before = len(res.fills)
        ReplayEngine._execute_pending(self, bar, meta, res)  # shared fill-time risk and invalidated-level checks
        for f in res.fills[before:]:
            pos = self.portfolio.positions.get(f.position_id) if f.kind == "entry" else None
            if pos is None or not pos.take_profits or "tp1" in pos.meta:
                continue
            pos.meta.update(tp1=pos.take_profits[0].price, n_tps=len(pos.take_profits), tp1_lock=0.0)
            if self.maker_tp:                                         # a resting limit fills on a trade-THROUGH
                d = 1 if pos.side == "long" else -1
                for tp in pos.take_profits:
                    tp.price *= 1 + d * MAKER_TP_TRADE_THROUGH
