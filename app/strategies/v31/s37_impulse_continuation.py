"""S37 - Impulse Continuation (new family).

Hypothesis: a large intraday impulse -- a setup-timeframe bar that closes far beyond its EMA with a
strong close and an extreme fast RSI, not against the 4h trend -- is the START of a structural move
that continues for hours, far larger than a round trip.

Why it should overcome the V3 failure: V3's S36 FADED exactly this kind of stretch (> 2.5 ATR from
the EMA, extreme RSI) and was the most wrong-way family on 15m/30m: after its entries price kept
going the other way by 19-54 bps (15m) and 17-101 bps (30m) over 15 minutes to 12 hours
(DEVELOPMENT). S37 trades the continuation instead: it needs a STRONG close (V3's S36 needed an
exhausted one, so this is a new condition tested here, not a replay of V3's trades), only takes a FRESH
impulse (the bar that first crosses the stretch -- the start of a move, not its tenth bar), takes the
3m/5m trigger only as timing after a 15m impulse, and holds the runner for hours on the 1h ATR.

Expected holding period: 2-12 hours. Expected move: several R when it works; many small stops when
it does not. Expected frequency (raw setups per coin-day, DEVELOPMENT counts): 15m ~1.6, 30m ~0.8,
3m ~1.2, 5m ~0.9 (3m/5m are bounded by the 15m impulses they time).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, StrategyDoc
from app.strategies.v31.base import V31Strategy, close_location, scale


@dataclass
class Params:
    ema_n: int = P(20, min=5, max=100, step=1, label="impulse EMA")
    stretch_atr: float = P(2.0, min=1.0, max=5.0, step=0.1, label="impulse stretch from the EMA (ATR)")
    strong_close: float = P(0.7, min=0.5, max=1.0, step=0.05, label="impulse close location")
    rsi_min: float = P(70.0, min=50.0, max=95.0, step=1.0, label="fast RSI (longs)")
    trigger_window_min: int = P(30, min=5, max=240, step=5, label="3m/5m trigger window after the impulse (min)")
    stop_atr: float = P(1.5, min=0.5, max=4.0, step=0.1, label="stop (setup ATR)")
    tp_r: float = P(2.5, min=1.0, max=8.0, step=0.1, label="partial target (R)")
    tp_frac: float = P(0.25, min=0.0, max=1.0, step=0.05, label="partial size")
    be_at_r: float = P(2.0, min=0.0, max=5.0, step=0.1, label="break-even at (R)")
    trail_mult: float = P(2.5, min=0.5, max=6.0, step=0.1, label="runner trail (x ATR 1h)")
    cooldown_bars: int = P(6, min=0, max=100, step=1, label="cooldown (bars)")


class ImpulseContinuationV31(V31Strategy):
    id = "S37"
    name = "Impulse Continuation v3.1"
    family = "IMPULSE_CONTINUATION"
    hypothesis = "a large impulse bar with a strong close, not against the 4h trend, starts a multi-hour continuation"
    thesis = "trade the continuation of large intraday impulses (V3 faded them and was wrong-way), long hold"
    why_v31 = "V3 S36 fades of >2.5 ATR stretches were strongly wrong-way on 15m/30m (price kept going 17-101 bps); S37 trades the continuation"
    expected_hold = "2-12 hours"
    expected_frequency = "raw per coin-day: 15m ~1.6, 30m ~0.8, 3m ~1.2, 5m ~0.9"
    max_hold_hours = 12.0
    Params = Params
    doc = StrategyDoc(
        idea="Join a large intraday impulse in its direction and hold the continuation for hours.",
        timeframe="15m / 30m on the impulse bar itself; 3m / 5m as timing after a 15m impulse; 1m execution",
        symbols="one coin per bot",
        entry="a FRESH impulse: the setup bar closes >= 2 ATR beyond EMA20 (the bar before it did not) with its "
              "close in the outer 30% and RSI(7) >= 70 (<= 30), 4h trend not opposite; 3m/5m enter on the first "
              "bar within 30 min that closes beyond the impulse close",
        stop="1.5 ATR of the setup timeframe, 0.6%-2.5%",
        targets="break-even at 2R, 25% at 2.5R, runner trailed 2.5 x ATR(1h), 12 h max",
        sizing="AGGRESSIVE_V31 behind the expected-net-edge gate; Jev V3 on the +JEV twin",
        why_aggressive="takes large directional moves early and holds them",
    )

    def setup_tf(self) -> str:
        return self.ctx_fast if self.signal_tf in ("3m", "5m") else self.signal_tf

    def impulse(self, ctx: MarketContext, symbol: str) -> dict | None:
        """The latest CLOSED setup-timeframe bar, if it is a FRESH impulse: it crosses the stretch that
        the bar before it had not reached (the start of a move, not the tenth bar of one)."""
        p, stf = self.params, self.setup_tf()
        cs = self.series(ctx, symbol, stf)
        if len(cs) < 60:
            return None
        bar = cs[-1]
        ema_s = ctx.ind(symbol, stf, "ema", n=int(p.ema_n))
        atr_s = ctx.ind(symbol, stf, "atr", n=14)
        rsi = ctx.ind(symbol, stf, "rsi", n=7)[-1]
        if len(ema_s) < 2 or len(atr_s) < 2:
            return None
        ema, ema_prev, atr, atr_prev = ema_s[-1], ema_s[-2], atr_s[-1], atr_s[-2]
        if None in (ema, ema_prev, rsi) or not atr or not atr_prev:
            return None
        z = (bar.close - ema) / atr
        z_prev = (cs[-2].close - ema_prev) / atr_prev
        cl = close_location(bar)
        if z >= p.stretch_atr and z_prev < p.stretch_atr and cl >= p.strong_close and rsi >= p.rsi_min:
            side = "long"
        elif z <= -p.stretch_atr and z_prev > -p.stretch_atr and cl <= 1.0 - p.strong_close and rsi <= 100.0 - p.rsi_min:
            side = "short"
        else:
            return None
        h4 = self.trend(ctx, symbol, "4h")
        if h4 is not None and h4["direction"] == ("down" if side == "long" else "up"):
            return None
        return {"side": side, "bar": bar, "z": z, "cl": cl, "rsi": rsi, "atr": atr,
                "h4": (h4 or {}).get("direction"), "tf": stf}

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if not self.bound(c):
            return []
        p = self.params
        imp = None
        if self.signal_tf in ("15m", "30m"):
            imp = self.impulse(ctx, c.symbol)
            if imp is None or imp["bar"].close_time != c.close_time:
                return []
        else:
            imp = self.impulse(ctx, c.symbol)
            if imp is None:
                return []
            age_min = (c.close_time - imp["bar"].close_time) / 60_000
            if not (0 < age_min <= p.trigger_window_min):
                return []
            beyond = c.close > imp["bar"].close if imp["side"] == "long" else c.close < imp["bar"].close
            if not beyond:
                return []
            prev = self.series(ctx, c.symbol, self.signal_tf)[-2]
            first = prev.close <= imp["bar"].close if imp["side"] == "long" else prev.close >= imp["bar"].close
            if not first:                                    # only the FIRST continuation bar
                return []
        side = imp["side"]
        price = ctx.last_price(c.symbol) or c.close
        sp = self.stop_pct(price, p.stop_atr * imp["atr"])
        if sp is None:
            return []
        vr = self.volume_ratio(self.series(ctx, c.symbol, imp["tf"])) or 0.0
        extremity = (imp["rsi"] - p.rsi_min) / (100.0 - p.rsi_min) if side == "long" else ((100.0 - p.rsi_min) - imp["rsi"]) / (100.0 - p.rsi_min)
        cl = imp["cl"] if side == "long" else 1.0 - imp["cl"]
        factors = {"stretch": scale(abs(imp["z"]), p.stretch_atr, 4.0), "close": scale(cl, p.strong_close, 1.0),
                   "rsi": scale(extremity, 0.0, 0.8), "volume": scale(vr, 1.0, 3.0),
                   "trend_4h": 1.0 if imp["h4"] == ("up" if side == "long" else "down") else 0.5}
        return [self.runner_entry(c=c, ctx=ctx, side=side, price=price, stop_pct=sp, tp_r=p.tp_r, tp_frac=p.tp_frac,
                                  be_at_r=p.be_at_r, trail_mult=p.trail_mult, factors=factors,
                                  reason=f"{self.signal_tf} continuation of a {abs(imp['z']):.1f} ATR {imp['tf']} impulse",
                                  extra={"context": {"slow": imp["h4"], "impulse_tf": imp["tf"]},
                                         "impulse_stretch_atr": round(imp["z"], 3)})]
