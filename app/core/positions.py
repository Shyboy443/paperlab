"""ExitEngine: soft stops / take-profits / time exits / break-even / trailing for virtual positions.

Order of checks on every price update (last trade price, mark price where available):
paper liquidation -> stop -> time -> take-profits (fractions of ORIGINAL qty) -> break-even -> trailing.
The engine fills exits at the CURRENT price (gap-through-stop realistic), never at the stop level.
"""
from __future__ import annotations

from typing import Callable

from app.core.types import ExitIntent, ExitUpdate, MarketRules, VirtualPosition

AtrProvider = Callable[[str, str, int], float | None]          # (symbol, tf, period) -> ATR
LineProvider = Callable[[str, str], float | None]             # (symbol, tf) -> supertrend line


class ExitEngine:
    def __init__(self, rules: dict[str, MarketRules], mmr_by_symbol: dict[str, float] | None = None,
                 default_mmr: float = 0.025, fee_buffer_bps: float = 6.0,
                 atr_provider: AtrProvider | None = None, line_provider: LineProvider | None = None):
        self.rules = rules
        self.mmr_by_symbol = dict(mmr_by_symbol or {})
        self.default_mmr = default_mmr
        self.fee_buffer_bps = fee_buffer_bps
        self.atr_provider = atr_provider
        self.line_provider = line_provider

    def mmr(self, symbol: str) -> float:
        return self.mmr_by_symbol.get(symbol, self.rules[symbol].maint_margin_rate if symbol in self.rules
                                      else self.default_mmr)

    @staticmethod
    def _better(pos: VirtualPosition, candidate: float) -> bool:
        return candidate > pos.stop if pos.side == "long" else candidate < pos.stop

    def on_price(self, pos: VirtualPosition, last: float, mark: float | None, now_ms: int) -> list[ExitIntent]:
        if pos.qty <= 0 or last <= 0:
            return []
        long = pos.side == "long"
        m = mark if mark else last
        # 1. paper liquidation at the virtual leverage (labelled PAPER in the GUI)
        liq = pos.liq_price(self.mmr(pos.symbol))
        if liq and ((long and m <= liq) or (not long and m >= liq)):
            return [ExitIntent(pos.id, "liq", 1.0, liq, "paper liquidation at virtual leverage")]
        # 2. stop (initial / break-even / trailing)
        if pos.stop > 0 and ((long and last <= pos.stop) or (not long and last >= pos.stop)):
            kind = str(pos.meta.get("stop_kind") or "stop")
            return [ExitIntent(pos.id, kind, 1.0, last, f"{kind} hit at {pos.stop:.6g}")]
        # 3. time
        if pos.max_hold_deadline and now_ms >= pos.max_hold_deadline:
            return [ExitIntent(pos.id, "time", 1.0, last, "max hold reached")]
        intents: list[ExitIntent] = []
        # 4. take-profits
        hit_fraction = 0.0
        while pos.take_profits:
            tp = pos.take_profits[0]
            if (long and last >= tp.price) or (not long and last <= tp.price):
                hit_fraction += tp.fraction
                pos.take_profits.pop(0)
                pos.tp1_done = True
            else:
                break
        if hit_fraction > 0:
            frac_current = min(1.0, hit_fraction * pos.qty_initial / pos.qty) if pos.qty else 1.0
            if not pos.take_profits and frac_current < 1.0 and pos.trail is None:
                frac_current = 1.0  # last TP with no runner -> close everything
            intents.append(ExitIntent(pos.id, "tp", frac_current, last, "take-profit"))
        # 5. break-even
        if pos.be_at_r and not pos.be_done and pos.r_multiple(last) >= pos.be_at_r:
            buffer = pos.entry_price * self.fee_buffer_bps / 1e4
            be = pos.entry_price + buffer if long else pos.entry_price - buffer
            if self._better(pos, be):
                pos.stop = be
                pos.meta["stop_kind"] = "be"
            pos.be_done = True
        # 6. trailing
        self._trail(pos, last)
        return intents

    def _trail(self, pos: VirtualPosition, last: float) -> None:
        trail = pos.trail
        if trail is None or trail.kind == "none":
            return
        if trail.activate_after_tp1 and not pos.tp1_done:
            return
        long = pos.side == "long"
        if (long and last > pos.extreme_price) or (not long and last < pos.extreme_price):
            pos.extreme_price = last
        candidate: float | None = None
        if trail.kind == "atr" and self.atr_provider is not None:
            atr = self.atr_provider(pos.symbol, trail.tf, trail.atr_period)
            if atr:
                candidate = pos.extreme_price - trail.mult * atr if long else pos.extreme_price + trail.mult * atr
        elif trail.kind == "pct":
            candidate = pos.extreme_price * (1 - trail.mult / 100) if long else pos.extreme_price * (1 + trail.mult / 100)
        elif trail.kind == "supertrend" and self.line_provider is not None:
            candidate = self.line_provider(pos.symbol, trail.tf)
        if candidate is not None and candidate > 0 and self._better(pos, candidate):
            pos.stop = candidate
            pos.meta["stop_kind"] = "trail"

    def apply_update(self, pos: VirtualPosition, upd: ExitUpdate, last: float) -> list[ExitIntent]:
        """Apply a strategy's manage() result. Stops move only in the favourable direction unless allow_worse."""
        if upd.close:
            return [ExitIntent(pos.id, "manual", 1.0, last, upd.reason or "strategy close")]
        if upd.stop is not None and upd.stop > 0:
            if upd.allow_worse or self._better(pos, upd.stop):
                pos.stop = upd.stop
                pos.meta["stop_kind"] = "trail" if pos.meta.get("stop_kind") != "stop" or pos.tp1_done else "stop"
                if upd.reason:
                    pos.meta["stop_kind"] = upd.reason if upd.reason in ("stop", "be", "trail") else pos.meta["stop_kind"]
        if upd.take_profits is not None:
            pos.take_profits = list(upd.take_profits)
        return []
