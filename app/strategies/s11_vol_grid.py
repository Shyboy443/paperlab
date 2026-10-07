"""S11 Volatility Grid - "dense two-sided grid that only runs while the 5m ATR% is high"

Idea:       While the 5m ATR% (ATR/close x 100) is above 1.0 lay a symmetric 8-level grid around the last 5m
            close (4 buy levels below, 4 sell levels above, 0.25% apart); every level filled is a mean-reversion
            leg targeting the grid centre. Below ATR% 0.5 everything is flattened and the grid switches off.
Timeframe:  5m (grid and regime); 15m (optional EMA50 directional bias).
Symbols:    all configured symbols (one grid per symbol, one shared inventory cap per strategy wallet).
Entry:      the first 5m close at/below a buy level fills that long leg (L1..L4), the first close at/above a sell
            level fills that short leg (L5..L8); once per level per grid centre; re-centred only when flat.
Stop:       far shared stop at mid -/+ (levels_per_side + 1) x step for long / short legs.
Targets:    back to mid, full leg size.
Size:       each leg puts 10% of strategy equity up as margin (signal meta margin_pct); the sum of open-leg margin
            is capped at 50% of the allocation, new legs beyond the cap are skipped and counted.
Why aggressive: 8 concurrent legs, 10% equity of margin each, and the ATR% gate is set where this tape actually
            lives (1.0) so the grid runs instead of sleeping.

Interpretation: each level = 10% of strategy equity as margin (notional = margin × leverage); inventory cap = Σ leg margin ≤ 50% of allocation. Maker fees are paper-only; the exchange net-delta legs are taker orders.

Flatten rules (exit signal per open leg): ATR% < atr_pct_off -> reason "regime"; inventory cap hit AND price
1.5% beyond mid against the net inventory -> reason "regime"; 15m bias flip with directional_bias -> "bias_flip".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.types import Candle, Signal, VirtualPosition
from app.strategies.base import MarketContext, P, Strategy, StrategyDoc


@dataclass
class Params:
    atr_pct_on: float = P(1.0, min=0.05, max=3.0, step=0.01, label="ATR% on",
                          help="5m ATR / close x 100 above which the grid runs")
    atr_pct_off: float = P(0.5, min=0.01, max=2.0, step=0.01, label="ATR% off",
                           help="flatten every leg and switch the grid off below this")
    step_pct: float = P(0.25, min=0.05, max=2.0, step=0.05, label="level step %")
    levels_per_side: int = P(4, min=1, max=6, step=1, label="levels per side")
    level_margin_pct: float = P(0.10, min=0.01, max=0.25, step=0.01, label="margin per level",
                                help="fraction of strategy equity used as margin per leg")
    inventory_cap_pct: float = P(0.50, min=0.05, max=1.0, step=0.05, label="inventory cap",
                                 help="sum of open-leg margin must stay <= this fraction of the allocation")
    adverse_flatten_pct: float = P(1.5, min=0.2, max=10.0, step=0.1, label="adverse flatten %",
                                   help="when capped, flatten if price is this far from mid against the net inventory")
    directional_bias: bool = P(False, label="15m EMA bias",
                               help="only buy levels while the 15m close is above its EMA, only sell levels below")
    assume_maker: bool = P(False, label="assume maker fills", help="paper-only: charge the maker fee on leg entries")
    atr_period: int = P(14, min=5, max=50, step=1, label="ATR period")
    bias_ema: int = P(50, min=10, max=200, step=1, label="15m bias EMA")


class VolatilityGrid(Strategy):
    id = "S11"
    name = "Volatility Grid"
    Params = Params
    doc = StrategyDoc(
        idea="Symmetric 8-level 0.25% grid around the last 5m close, only while the 5m ATR% is above 1.0.",
        timeframe="5m grid and regime, 15m optional EMA50 bias",
        symbols="all configured (one grid per symbol)",
        entry="first 5m close at/below a buy level -> long leg L1..L4; at/above a sell level -> short leg L5..L8; "
              "once per level per centre, re-centred only when flat",
        stop="mid -/+ (levels_per_side + 1) x step, shared by all legs of a side",
        targets="back to mid, full leg size",
        sizing="10% of strategy equity as margin per leg (notional = margin x leverage); open-leg margin <= 50% "
               "of the allocation",
        why_aggressive="8 concurrent legs at 10% equity margin each, with the ATR% gate at 1.0 where this tape "
                       "actually trades",
    )
    timeframes = ("5m", "15m")
    max_positions = 8
    min_rr = 0.0
    contributes_votes = False
    badges = ("GRID",)
    warmup_bars = 60

    def __init__(self, params: Any = None):
        super().__init__(params)
        self._grids: dict[str, dict[str, Any]] = {}
        self._inventory: dict[str, Any] = {}
        self._capped: int = 0
        self._flattens: dict[str, int] = {}

    # -- grid bookkeeping -----------------------------------------------------------------
    @staticmethod
    def _new_grid() -> dict[str, Any]:
        return {"mid": None, "levels": [], "levels_bias": None, "regime_on": False, "atr_pct": None, "bias": None,
                "had_positions": False, "flattened": None, "capped": 0, "skipped_wrong_side": 0}

    def _htf_bias(self, symbol: str, ctx: MarketContext) -> str | None:
        p = self.params
        htf = ctx.candles(symbol, "15m")
        if len(htf) < p.bias_ema + 2:
            return None
        ema = ctx.ind_last(symbol, "15m", "ema", n=p.bias_ema)
        if ema is None:
            return None
        close = htf[-1].close
        return "long" if close > ema else "short" if close < ema else None

    def _centre(self, g: dict[str, Any], mid: float, bias: str | None) -> None:
        p = self.params
        n = int(p.levels_per_side)
        step = p.step_pct / 100.0
        use_long = not p.directional_bias or bias == "long"
        use_short = not p.directional_bias or bias == "short"
        levels: list[dict[str, Any]] = []
        for i in range(1, n + 1):
            if use_long:
                levels.append({"leg": f"L{i}", "side": "long", "price": mid * (1 - i * step), "filled": False})
        for i in range(1, n + 1):
            if use_short:
                levels.append({"leg": f"L{n + i}", "side": "short", "price": mid * (1 + i * step), "filled": False})
        g.update({"mid": mid, "levels": levels, "levels_bias": bias, "had_positions": False})

    def _flatten(self, g: dict[str, Any], own: list[VirtualPosition], c: Candle, reason: str, why: str) -> list[Signal]:
        g.update({"mid": None, "levels": [], "levels_bias": None, "had_positions": False, "flattened": why})
        self._flattens[why] = self._flattens.get(why, 0) + 1
        return [self.make_exit(symbol=c.symbol, side=x.side, ts=c.close_time, tf="5m", reason=reason, leg=x.leg,
                               meta={"why": why, "atr_pct": g["atr_pct"]}) for x in own]

    def _fill_legs(self, g: dict[str, Any], c: Candle, ctx: MarketContext, open_legs: set[str | None],
                   margin_used: float, leg_margin: float, cap: float) -> list[Signal]:
        p = self.params
        n = int(p.levels_per_side)
        step = p.step_pct / 100.0
        mid = float(g["mid"])
        price = ctx.last_price(c.symbol) or c.close
        out: list[Signal] = []
        placed = 0
        for lvl in g["levels"]:
            if lvl["filled"] or lvl["leg"] in open_legs:
                continue
            is_long = lvl["side"] == "long"
            if (is_long and c.close > lvl["price"]) or (not is_long and c.close < lvl["price"]):
                continue
            if margin_used + (placed + 1) * leg_margin > cap + 1e-9:
                g["capped"] += 1
                self._capped += 1
                continue
            stop = mid * (1 - (n + 1) * step) if is_long else mid * (1 + (n + 1) * step)
            if (is_long and not stop < price < mid) or (not is_long and not mid < price < stop):
                lvl["filled"] = True
                g["skipped_wrong_side"] += 1
                continue
            lvl["filled"] = True
            placed += 1
            meta: dict[str, Any] = {"margin_pct": p.level_margin_pct, "mid": mid, "level": lvl["price"],
                                    "atr_pct": g["atr_pct"]}
            if p.assume_maker:
                meta["maker"] = True
            out.append(self.make_entry(
                symbol=c.symbol, side=lvl["side"], ts=c.close_time, tf="5m", price=price, stop=stop,
                tps_price=[(mid, 1.0)], valid_bars=2, leg=lvl["leg"],
                reason=f"grid {lvl['leg']} {lvl['side']} at {lvl['price']:.6g} (mid {mid:.6g})", meta=meta))
        return out

    # -- hook -------------------------------------------------------------------------------
    def on_candle(self, c: Candle, ctx: MarketContext) -> list[Signal]:
        if c.tf != "5m":
            return []
        p = self.params
        sym = c.symbol
        g = self._grids.get(sym)
        if g is None:
            g = self._grids[sym] = self._new_grid()
        atr_pct = ctx.ind_last(sym, "5m", "atr_pct", n=p.atr_period)
        if atr_pct is None:
            return []
        g["atr_pct"] = atr_pct
        if atr_pct > p.atr_pct_on:
            g["regime_on"] = True
        elif atr_pct < p.atr_pct_off:
            g["regime_on"] = False
        own = [x for x in ctx.positions_of(self.id) if x.symbol == sym]
        wallet = ctx.wallet_info(self.id)
        allocation = float(wallet.get("allocation", 0.0))
        equity = float(wallet.get("equity", allocation))
        margin_used = float(wallet.get("margin_used", 0.0))
        cap = p.inventory_cap_pct * allocation
        leg_margin = p.level_margin_pct * equity
        capped = margin_used + leg_margin > cap + 1e-9
        self._inventory = {"margin_used": margin_used, "cap": cap, "leg_margin": leg_margin, "capped": capped}
        bias = self._htf_bias(sym, ctx) if p.directional_bias else None
        prev_bias, g["bias"] = g["bias"], bias
        if own:
            g["had_positions"] = True
            if atr_pct < p.atr_pct_off:
                return self._flatten(g, own, c, "regime", "atr_pct_off")
            if p.directional_bias and prev_bias is not None and bias != prev_bias:
                return self._flatten(g, own, c, "bias_flip", "bias_flip")
            if g["mid"] is None and own[0].take_profits:
                self._centre(g, own[0].take_profits[0].price, bias)  # restart: the leg TP is the grid centre
                g["had_positions"] = True
            if capped and g["mid"]:
                net = sum(x.qty if x.side == "long" else -x.qty for x in own)
                adverse = p.adverse_flatten_pct / 100.0
                if (net > 0 and c.close <= g["mid"] * (1 - adverse)) or (net < 0 and c.close >= g["mid"] * (1 + adverse)):
                    return self._flatten(g, own, c, "regime", "adverse_move")
        if not g["regime_on"]:
            if not own:
                g.update({"mid": None, "levels": [], "levels_bias": None})
            return []
        if not own:
            stale = (g["mid"] is None or g["had_positions"] or g["levels_bias"] != bias
                     or all(lvl["filled"] for lvl in g["levels"]))
            if stale:
                self._centre(g, c.close, bias)
                return []
        if g["mid"] is None:
            return []
        open_legs = {x.leg for x in own}
        return self._fill_legs(g, c, ctx, open_legs, margin_used, leg_margin, cap)

    def state(self) -> dict[str, Any]:
        return {"grids": {s: {k: v for k, v in g.items() if k != "levels_bias"} for s, g in self._grids.items()},
                "inventory": dict(self._inventory), "inventory_capped": self._capped, "flattens": dict(self._flattens)}

    def reset(self) -> None:
        self._grids.clear()
        self._inventory = {}
        self._capped = 0
        self._flattens.clear()
