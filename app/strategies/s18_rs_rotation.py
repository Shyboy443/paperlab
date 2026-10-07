"""S18 Relative Strength Rotation - "ride the leader, dump the laggard"

Idea:       Rank the alts by their 4h return relative to BTC; hold the single strongest name when BTC trends up,
            short the single weakest when BTC trends down, rotating whenever the leader changes.
Timeframe:  15m.
Symbols:    all configured symbols except BTCUSDT, which is the benchmark (idle without it).
Entry:      once per 15m bar, when every symbol has closed that bar: relative return = 16-bar log return of the alt
            minus BTC's; z = z-score of the latest relative return over the last 24 values. Long the max-z alt when
            z > 0.8 and 15m EMA20 > EMA50 on BTC; short the min-z alt when z < -0.8 and EMA20 < EMA50.
Stop:       2 x ATR(15m, 14) from the entry.
Targets:    one target at 5R closes 100%; a new leader on another symbol/side rotates: exit "rotate" + new entry.
Size:       2% of the strategy wallet at risk per trade, 15x virtual leverage.
Why aggressive: the shorter 24-bar z window and the 0.8 z trigger mean it takes a leader early instead of waiting
            for a 1.2-sigma move that a 6h testnet session rarely produces, then holds it all the way to 5R;
            concentrated single-name exposure with no hedge, flipped whenever the ranking changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.core.indicators import last, zscore
from app.core.types import Candle, Signal
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc

_BENCHMARK = "BTCUSDT"


@dataclass
class Params:
    lookback_bars: int = P(16, min=4, max=96, step=1, label="return lookback (bars)", help="16 x 15m = 4h")
    z_lookback: int = P(24, min=10, max=200, step=1, label="z-score window")
    z_entry: float = P(0.8, min=0.3, max=3.0, step=0.1, label="z entry")
    ema_fast: int = P(20, min=5, max=100, step=1, label="BTC fast EMA")
    ema_slow: int = P(50, min=10, max=200, step=1, label="BTC slow EMA")
    stop_atr_mult: float = P(2.0, min=0.5, max=5.0, step=0.1, label="stop ATR mult")
    tp_r: float = P(5.0, min=1.0, max=8.0, step=0.1, label="target (R)")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")


class RelativeStrengthRotation(Strategy):
    id = "S18"
    name = "Relative Strength Rotation"
    Params = Params
    doc = StrategyDoc(
        idea="Concentrate in the alt with the strongest (or weakest) 4h return vs BTC, in BTC's trend direction.",
        timeframe="15m",
        symbols="all configured except BTCUSDT (the benchmark)",
        entry="z-score of the alt-minus-BTC 16-bar log return over 24 values: long the max z > 0.8 when BTC "
              "EMA20 > EMA50, short the min z < -0.8 when EMA20 < EMA50; one position, rotated on a new leader",
        stop="2 x ATR(15m, 14)",
        targets="5R closes 100%; exit 'rotate' when the pick moves to another symbol/side",
        sizing="2% wallet risk per trade at 15x virtual leverage",
        why_aggressive="24-bar z window and a 0.8 z trigger take the leader early, then hold to 5R; concentrated "
                       "single-name, no hedge",
    )
    timeframes = ("15m",)
    max_positions = 1
    min_rr = 2.5
    contributes_votes = False
    warmup_bars = 80

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._last_open: dict[str, int] = {}
        self._last_eval: int | None = None
        self._z: dict[str, float | None] = {}
        self._btc_trend: str | None = None
        self._leader: dict[str, Any] | None = None
        self._notice: str | None = None

    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "15m":
            return []
        self._last_open[c.symbol] = c.open_time
        if _BENCHMARK not in ctx.symbols:
            self._notice = f"{_BENCHMARK} not configured: no benchmark, rotation idle"
            return []
        alts = [s for s in ctx.symbols if s != _BENCHMARK]
        if not alts:
            self._notice = "no alt symbols besides the benchmark"
            return []
        pending = [s for s in ctx.symbols if self._latest_open(s, ctx) != c.open_time]
        if pending:
            self._notice = "waiting for the 15m close on " + ", ".join(pending)
            return []
        if self._last_eval == c.open_time:
            return []
        self._last_eval = c.open_time
        self._notice = None
        p = self.params
        ef = ctx.ind_last(_BENCHMARK, "15m", "ema", n=int(p.ema_fast))
        es = ctx.ind_last(_BENCHMARK, "15m", "ema", n=int(p.ema_slow))
        trend = None if ef is None or es is None else "up" if ef > es else "down" if ef < es else "flat"
        self._btc_trend = trend
        btc_lr = ctx.ind(_BENCHMARK, "15m", "log_returns", n=int(p.lookback_bars))
        zs = {alt: self._relative_z(ctx.ind(alt, "15m", "log_returns", n=int(p.lookback_bars)), btc_lr,
                                    int(p.z_lookback)) for alt in alts}
        self._z = {s: (None if z is None else round(z, 3)) for s, z in zs.items()}
        valid = {s: z for s, z in zs.items() if z is not None}
        held = ctx.positions_of(self.id)
        pick: tuple[str, str, float] | None = None
        if valid and trend == "up":
            s = max(valid, key=valid.__getitem__)
            pick = (s, "long", valid[s]) if valid[s] > p.z_entry else None
            self._leader = {"symbol": s, "side": "long", "z": round(valid[s], 3), "qualifies": pick is not None}
        elif valid and trend == "down":
            s = min(valid, key=valid.__getitem__)
            pick = (s, "short", valid[s]) if valid[s] < -p.z_entry else None
            self._leader = {"symbol": s, "side": "short", "z": round(valid[s], 3), "qualifies": pick is not None}
        else:
            self._leader = None
        if pick is None:
            return []  # nothing qualifies: keep whatever is open, its stop/target manage it
        sym, side, z = pick
        if any(pos.symbol == sym and pos.side == side for pos in held):
            return []  # already positioned in the leader
        atr = ctx.ind_last(sym, "15m", "atr", n=int(p.atr_period))
        alt_c = ctx.candles(sym, "15m")
        if atr is None or atr <= 0 or not alt_c:
            return []
        price = ctx.last_price(sym) or alt_c[-1].close
        stop = price - p.stop_atr_mult * atr if side == "long" else price + p.stop_atr_mult * atr
        exits: list[Signal] = []
        for pos in held:
            if any(x.symbol == pos.symbol for x in exits):
                continue
            exits.append(self.make_exit(symbol=pos.symbol, side=pos.side, ts=c.close_time, tf="15m", reason="rotate",
                                        meta={"to": sym, "to_side": side, "z": round(z, 3)}))
        entry = self.make_entry(
            symbol=sym, side=side, ts=c.close_time, tf="15m", price=price, stop=stop, tps_r=[(p.tp_r, 1.0)],
            valid_bars=1, reason=f"RS leader z={z:+.2f}, BTC trend {trend} -> {side}",
            meta={"z": round(z, 3), "btc_trend": trend, "atr": atr, "rotated_from": [x.symbol for x in exits]})
        return exits + [entry]

    def _latest_open(self, symbol: str, ctx: MarketContext) -> int | None:
        seen = self._last_open.get(symbol)
        if seen is not None:
            return seen
        c15 = ctx.candles(symbol, "15m")
        return c15[-1].open_time if c15 else None

    @staticmethod
    def _relative_z(alt_lr: Sequence[float | None], btc_lr: Sequence[float | None], n: int) -> float | None:
        """z-score of the newest (alt - BTC) log return over the last n values, series aligned from the end."""
        m = min(len(alt_lr), len(btc_lr))
        if m < n:
            return None
        rel = [None if (a is None or b is None) else a - b for a, b in zip(alt_lr[-m:], btc_lr[-m:])]
        return last(zscore(rel, n))

    def state(self) -> dict[str, Any]:
        return {"benchmark": _BENCHMARK, "z": self._z, "btc_trend": self._btc_trend, "leader": self._leader,
                "notice": self._notice, "last_eval_open_time": self._last_eval}

    def reset(self) -> None:
        self._last_open.clear()
        self._last_eval = None
        self._z = {}
        self._btc_trend = None
        self._leader = None
        self._notice = None
