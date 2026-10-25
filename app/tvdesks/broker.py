"""Fully funded spot, long/cash broker for source market-order requests (the research package's cash broker).

Rules (the package's screen.signal_path + screen.replay):
- a request on candle i fills at the OPEN of candle i+1; a request on the last candle of the window has no fill;
- while held, a sell request closes; while flat, a buy request opens; a buy while held is ignored (no pyramiding);
  a sell while flat has nothing to close; simultaneous requests therefore buy when flat or close when held;
- entry units = cash / (open*(1+slip)*(1+fee)); exit cash = units*open*(1-slip)*(1-fee); all-in, compounding;
- a position still open at the end is sold at the final CLOSE with the same exit costs;
- equity at every candle close; drawdown includes the initial capital; the OHLC bound assumes a held candle's high came
  before its low (the exit candle's own high/low do not belong to a trade that exited at its open).

Two modes:
- RESEARCH (exact reproduction): fractional units, exactly the package.
- EXECUTION: the exchange's quantity step and minimum notional are enforced; an order that cannot be placed is skipped
  and recorded with its reason. Its results are a different experiment from the research reproduction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

FEE, SLIP, CAPITAL = 0.001, 0.0002, 10_000.0


@dataclass(frozen=True)
class Filters:
    """Exchange order filters for the execution simulation (None = research mode: fractional, no minimum)."""
    step: float = 0.0
    min_qty: float = 0.0
    min_notional: float = 0.0
    source: str = ""

    def floor(self, qty: float) -> float:
        if self.step <= 0:
            return qty
        return math.floor(qty / self.step + 1e-9) * self.step


def replay(bars: Sequence[dict], buy: Sequence[bool], sell: Sequence[bool], start_ms: int, end_ms: int,
           cost_multiplier: float = 1.0, capital: float = CAPITAL, filters: Filters | None = None,
           final_sale: bool = True) -> dict[str, Any]:
    """Replay requests over candles; returns trades, per-candle equity (close / high / low), skips and metrics.
    final_sale=False leaves an open position marked at the last close (the forward books use that)."""
    fee, slip = FEE * cost_multiplier, SLIP * cost_multiplier
    t = [b["t"] for b in bars]
    idx = [i for i, x in enumerate(t) if start_ms <= x < end_ms]
    if not idx:
        raise ValueError("no candles inside the window")
    first, last = idx[0], idx[-1]
    cash, units, entry, trades, skips, dust = float(capital), 0.0, None, [], [], 0.0
    exact = not (filters and filters.step > 0)       # research mode: all-in, exactly zero cash while held
    eq, hi, lo = [], [], []
    pending = None                                   # ("buy" | "sell", signal index) to fill at this candle's open
    for i in range(first, last + 1):
        b = bars[i]
        exited_here = False
        if pending is not None:
            kind, sig_i = pending
            pending = None
            if kind == "sell" and units > 0:
                px = b["o"] * (1 - slip)
                qty = units
                if filters and filters.step > 0:
                    qty = filters.floor(units)
                    if qty <= 0 or qty * px < filters.min_notional:
                        skips.append(_skip(b, "sell", "position below the exchange minimum; cannot be sold", qty, px))
                        qty = 0.0
                if qty > 0:
                    proceeds = qty * px
                    exit_fee = proceeds * fee
                    cash += proceeds - exit_fee
                    left = units - qty
                    trades.append(_close(entry, b, i, px, qty, exit_fee, cash, 0.0, forced=False))
                    dust += max(0.0, left)                 # below one quantity step: cannot be sold, kept as dust
                    units, entry, exited_here = 0.0, None, True
            elif kind == "buy" and units == 0:
                px = b["o"] * (1 + slip)
                qty = cash / (px * (1 + fee))
                if filters and filters.step > 0:
                    qty = filters.floor(qty)
                    if qty < filters.min_qty or qty * px < filters.min_notional or qty <= 0:
                        skips.append(_skip(b, "buy", f"order {qty * px:.4f} USDT is below the exchange minimum "
                                                     f"(min qty {filters.min_qty:g}, min notional {filters.min_notional:g})", qty, px))
                        qty = 0.0
                if qty > 0:
                    spend = qty * px
                    entry_fee = spend * fee
                    entry = {"entry_index": i, "entry_time": b["t"], "entry_price": px, "units": qty,
                             "entry_cash": cash, "entry_fee": entry_fee, "signal_index": sig_i}
                    cash = 0.0 if exact else cash - (spend + entry_fee)
                    units = qty
        held = units + dust
        if held > 0:
            eq.append(cash + held * b["c"])
            hi.append(cash + held * b["h"])
            lo.append(cash + held * b["l"])
        else:
            eq.append(cash)
            hi.append(cash)
            lo.append(cash)
        if exited_here and units == 0:              # the exit candle's high/low do not belong to the closed trade
            hi[-1] = lo[-1] = eq[-1]
        # requests at this candle's close fill at the next open; none on the last candle of the window
        if i < last:
            if units > 0 and sell[i]:
                pending = ("sell", i)
            elif units == 0 and buy[i]:
                pending = ("buy", i)
    open_trade = None
    if units > 0:
        b = bars[last]
        if final_sale:
            px = b["c"] * (1 - slip)
            proceeds = units * px
            exit_fee = proceeds * fee
            cash += proceeds - exit_fee
            trades.append(_close(entry, b, last, px, units, exit_fee, cash, 0.0, forced=True))
            units = 0.0
            eq[-1] = cash
            hi[-1], lo[-1] = max(hi[-1], cash), min(lo[-1], cash)
        else:
            open_trade = {**entry, "mark": b["c"], "value": units * b["c"]}
    times = [bars[i]["t"] for i in range(first, last + 1)]
    return {"trades": trades, "open": open_trade, "skips": skips, "times": times, "equity": eq, "high_equity": hi,
            "low_equity": lo, "cash": cash, "units": units, "dust": dust, "pending": pending,
            "metrics": metrics(eq, hi, lo, capital, trades)}


def _close(entry, b, i, px, qty, exit_fee, cash_after, left, forced):
    frac = qty / entry["units"] if entry["units"] else 1.0
    entry_cash = entry["entry_cash"] * frac
    return {"entry_time": entry["entry_time"], "exit_time": b["t"] + (14_400_000 if forced else 0), "entry_index": entry["entry_index"],
            "exit_index": i, "entry_price": entry["entry_price"], "exit_price": px, "units": qty, "entry_cash": entry_cash,
            "exit_cash": qty * px - exit_fee if left > 0 else cash_after, "entry_fee": entry["entry_fee"] * frac,
            "exit_fee": exit_fee, "pnl": (qty * px - exit_fee) - entry_cash, "forced_exit": forced}


def _skip(b, side, why, qty, px):
    return {"time": b["t"], "side": side, "reason": why, "qty": qty, "price": px}


def metrics(eq, hi, lo, capital, trades) -> dict[str, Any]:
    e, h, lw = np.asarray(eq), np.asarray(hi), np.asarray(lo)
    peak = np.maximum.accumulate(np.r_[capital, e])[1:]
    hpeak = np.maximum.accumulate(np.r_[capital, h])[1:]
    dd = float((1 - e / peak).max() * 100) if len(e) else 0.0
    bound = float((1 - lw / hpeak).max() * 100) if len(e) else 0.0
    gains = sum(max(0.0, t["pnl"]) for t in trades)
    losses = -sum(min(0.0, t["pnl"]) for t in trades)
    return {"net_return_pct": float((e[-1] / capital - 1) * 100) if len(e) else 0.0, "ending_capital": float(e[-1]) if len(e) else capital,
            "max_drawdown_pct": dd, "ohlc_drawdown_bound_pct": max(dd, bound), "trades": len(trades),
            "win_rate_pct": 100 * sum(t["pnl"] > 0 for t in trades) / len(trades) if trades else 0.0,
            "profit_factor": gains / losses if losses else None, "fees": sum(t["entry_fee"] + t["exit_fee"] for t in trades)}
