"""S16 Funding Extreme Fade - "fade the crowded side"

Idea:       When funding (or the mark/index basis) says leverage is crowded on one side and the 5m RSI confirms
            the stretch, fade the crowd: short a squeezed-up market paying rich positive funding, long a flushed
            market paying negative funding.
Timeframe:  funding polled every 60 s; 5m RSI(14) for the trigger, 5m ATR(14) for the stop.
Symbols:    all configured symbols on a futures venue (spot has no funding -> marked unsupported by the engine).
Entry:      short when funding >= +0.03% (or basis >= +8 bps) and 5m RSI(14) > 72;
            long when funding <= -0.03% (or basis <= -8 bps) and 5m RSI(14) < 28.
Stop:       1.4 x ATR(5m, 14) from the entry.
Targets:    one target at 3R closes 100%; a FUNDING-triggered trade goes flat as soon as |funding| normalises
            below 0.01%.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: testnet funding is close to flat, so the trigger drops to 0.03% funding / 8 bps basis to make the
            book trade at all; it fades crowded leverage against the prevailing trend, one shot per funding regime
            (30 min re-entry lock per symbol, also enforced by the engine as cooldown_ms()).
            funding_exit_abs is 0.0001 (1/3 of the entry trigger) so a trade entered at the 0.0003 trigger is not
            exited by the very next funding poll; the funding-normalised exit only applies to entries that funding
            actually triggered - a basis-triggered entry is left to its stop / target.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.types import FundingInfo, Signal
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    funding_threshold: float = P(0.0003, min=0.0001, max=0.005, step=0.0001, label="funding threshold",
                                 help="8h funding rate as a fraction (0.0003 = 0.03%)")
    basis_bps: float = P(8.0, min=2.0, max=200.0, step=1.0, label="basis (bps)",
                         help="mark vs index premium that counts as crowded even when funding is not")
    rsi_high: float = P(72.0, min=55.0, max=95.0, step=1.0, label="RSI high", help="5m RSI above this confirms a short")
    rsi_low: float = P(28.0, min=5.0, max=45.0, step=1.0, label="RSI low", help="5m RSI below this confirms a long")
    rsi_period: int = P(14, min=5, max=50, step=1, label="RSI period")
    stop_atr_mult: float = P(1.4, min=0.3, max=4.0, step=0.1, label="stop ATR mult")
    tp_r: float = P(3.0, min=1.0, max=8.0, step=0.1, label="target (R)")
    funding_exit_abs: float = P(0.0001, min=0.0, max=0.002, step=0.00005, label="funding exit |rate|",
                                help="a funding-triggered trade goes flat when |funding| drops below this; keep it "
                                     "well under funding_threshold or the entry exits itself on the next poll")
    reentry_minutes: int = P(30, min=5, max=480, step=5, label="re-entry lock (min)")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")


class FundingExtremeFade(Strategy):
    id = "S16"
    name = "Funding Extreme Fade"
    Params = Params
    doc = StrategyDoc(
        idea="Fade crowded leverage: short rich positive funding / premium into an overbought 5m, long the mirror.",
        timeframe="funding every 60 s; 5m RSI(14) and ATR(14)",
        symbols="all configured (futures venues only)",
        entry="funding >= +0.03% or basis >= +8 bps with 5m RSI > 72 -> short; "
              "funding <= -0.03% or basis <= -8 bps with RSI < 28 -> long",
        stop="1.4 x ATR(5m, 14)",
        targets="3R closes 100%; funding-triggered entries exit 'funding_normalised' when |funding| < 0.01%",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="0.03% funding / 8 bps basis trigger so it trades on flat testnet funding; fades crowded "
                       "leverage, not the trend; one entry per funding regime (30 min lock)",
    )
    timeframes = ("5m",)
    needs_funding = True
    supported_venues = frozenset({"futures"})
    min_rr = 2.5
    contributes_votes = False
    warmup_bars = 60
    badges = ("FUNDING",)

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last_funding: dict[str, dict[str, Any]] = {}
        self._last_trigger: dict[str, Any] | None = None
        self._entry_ts: dict[str, int] = {}
        self._entry_trigger: dict[str, str] = {}  # symbol -> "funding" | "basis" (what opened the position)

    def cooldown_ms(self) -> int:
        return int(self.params.reentry_minutes) * 60_000

    def on_funding(self, f: FundingInfo, ctx: MarketContext) -> list[Signal]:
        p = self.params
        sym = f.symbol
        now = ctx.now_ms()
        basis = f.basis_bps()
        self._last_funding[sym] = {"rate": f.rate, "basis_bps": round(basis, 2), "mark": f.mark, "ts": f.ts}
        held = [pos for pos in ctx.positions_of(self.id) if pos.symbol == sym]
        if held:
            # Only a funding-triggered fade is closed by funding normalising; a basis-triggered one would be
            # exited by the very next poll (its funding was near zero to begin with) for a guaranteed scratch.
            if self._entry_trigger.get(sym, "funding") == "funding" and abs(f.rate) < p.funding_exit_abs:
                return [self.make_exit(symbol=sym, side=held[0].side, ts=now, tf="5m", reason="funding_normalised",
                                       meta={"rate": f.rate, "threshold": p.funding_exit_abs})]
            return []
        crowded_long = f.rate >= p.funding_threshold or basis >= p.basis_bps
        crowded_short = f.rate <= -p.funding_threshold or basis <= -p.basis_bps
        if not (crowded_long or crowded_short):
            return []
        lock = self._entry_ts.get(sym)
        if lock is not None and now - lock < self.cooldown_ms():
            return []
        candles = ctx.candles(sym, "5m")
        if len(candles) < max(int(p.rsi_period), int(p.atr_period)) + 2:
            return []
        rsi = ctx.ind_last(sym, "5m", "rsi", n=int(p.rsi_period))
        atr = ctx.ind_last(sym, "5m", "atr", n=int(p.atr_period))
        if rsi is None or atr is None or atr <= 0:
            return []
        side: str | None = None
        if crowded_long and rsi > p.rsi_high:
            side = "short"
        elif crowded_short and rsi < p.rsi_low:
            side = "long"
        if side is None:
            return []
        price = ctx.last_price(sym) or candles[-1].close
        stop = price - p.stop_atr_mult * atr if side == "long" else price + p.stop_atr_mult * atr
        self._entry_ts[sym] = now
        trigger = "funding" if abs(f.rate) >= p.funding_threshold else "basis"
        self._entry_trigger[sym] = trigger
        self._last_trigger = {"symbol": sym, "side": side, "ts": now, "rate": f.rate, "basis_bps": round(basis, 2),
                              "rsi": round(rsi, 1), "trigger": trigger}
        return [self.make_entry(
            symbol=sym, side=side, ts=now, tf="5m", price=price, stop=stop, tps_r=[(p.tp_r, 1.0)], valid_bars=2,
            reason=f"funding {f.rate * 100:+.3f}% / basis {basis:+.0f} bps, RSI {rsi:.0f} -> {side} ({trigger})",
            meta={"rate": f.rate, "basis_bps": round(basis, 2), "rsi": rsi, "atr": atr, "trigger": trigger})]

    def state(self) -> dict[str, Any]:
        return {"last_funding": self._last_funding, "last_trigger": self._last_trigger,
                "reentry_lock_ts": dict(self._entry_ts), "entry_trigger": dict(self._entry_trigger)}

    def reset(self) -> None:
        self._last_funding.clear()
        self._last_trigger = None
        self._entry_ts.clear()
        self._entry_trigger.clear()
