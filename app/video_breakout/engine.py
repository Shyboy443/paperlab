"""Long/cash breakout. Decisions use closed bars; executions use the next open.

This separate engine deliberately avoids the lab's leveraged sizing, ATR stops,
partial targets and daily halts, which would change the rules in the source video.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from app.core.types import Candle

STEP = 14_400_000
ENTRY_BARS = 42
EXIT_BARS = 18


@dataclass(frozen=True)
class Config:
    symbol: str = "BTCUSDT"
    initial_cash: float = 1000.0
    allocation: float = 1.0
    fee_bps: float = 10.0
    slippage_bps: float = 2.0

    def __post_init__(self):
        if not self.symbol.isalnum() or not self.symbol.endswith("USDT"):
            raise ValueError("A raw USDT symbol is required")
        for name in ("initial_cash", "allocation", "fee_bps", "slippage_bps"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        if self.initial_cash <= 0 or not 0 < self.allocation <= 1:
            raise ValueError("Positive cash and allocation in (0, 1] required")
        if not 0 <= self.fee_bps < 10_000 or not 0 <= self.slippage_bps < 10_000:
            raise ValueError("Costs must be in [0, 10000) basis points")


def validate_bar(c: Candle, symbol: str) -> None:
    if c.symbol != symbol or c.tf != "4h" or c.open_time % STEP:
        raise ValueError("Expected matching symbol and UTC-aligned 4h bars")
    values = (c.open, c.high, c.low, c.close, c.volume)
    if not all(math.isfinite(x) for x in values):
        raise ValueError("Non-finite OHLCV")
    if c.low <= 0 or c.volume < 0 or c.low > min(c.open, c.close) or c.high < max(c.open, c.close):
        raise ValueError("Invalid OHLCV")


class BreakoutBot:
    def __init__(self, config: Config = Config()):
        self.config = config
        self.cash = config.initial_cash
        self.qty = 0.0
        self.history: list[Candle] = []
        self.pending: dict[str, Any] | None = None
        self.entry: dict[str, Any] | None = None
        self.fills: list[dict[str, Any]] = []
        self.trades: list[dict[str, Any]] = []
        self.signals: list[dict[str, Any]] = []
        self.curve: list[dict[str, Any]] = []
        self.peak = config.initial_cash
        self.max_dd = 0.0

    def mark(self, ts: int, price: float) -> None:
        equity = self.cash + self.qty * price
        self.peak = max(self.peak, equity)
        dd = 1 - equity / self.peak
        self.max_dd = max(self.max_dd, dd)
        self.curve.append({"ts": ts, "price": price, "cash": self.cash,
                           "qty": self.qty, "equity": equity, "drawdown": dd})

    def _append(self, c: Candle) -> None:
        validate_bar(c, self.config.symbol)
        if not c.closed or c.close_time != c.open_time + STEP:
            raise ValueError("Only completed 4h bars with exclusive close timestamps are accepted")
        if self.history and c.open_time != self.history[-1].open_time + STEP:
            raise ValueError("Gap, duplicate or out-of-order bar; refusing to trade")
        self.history.append(c)
        self.history = self.history[-ENTRY_BARS:]

    def warmup(self, bars: list[Candle]) -> None:
        """Seed history without reconstructing hypothetical live trades."""
        if self.history or self.fills:
            raise ValueError("Warmup requires a new bot")
        for c in bars:
            self._append(c)

    def on_close(self, c: Candle) -> dict[str, Any] | None:
        validate_bar(c, self.config.symbol)
        if not c.closed or c.close_time != c.open_time + STEP:
            raise ValueError("A completed 4h bar is required")
        if self.history and c.open_time != self.history[-1].open_time + STEP:
            raise ValueError("Non-contiguous candles")
        if self.pending:
            raise ValueError("Pending next-open action must be handled before the next close")
        signal = None
        # Exclude the signal candle from both ranges. Equality does not trigger.
        if len(self.history) == ENTRY_BARS:
            upper = max(b.high for b in self.history)
            lower = min(b.low for b in self.history[-EXIT_BARS:])
            action = "BUY" if self.qty == 0 and c.close > upper else (
                "SELL" if self.qty > 0 and c.close < lower else None)
            if action:
                signal = {"side": action, "signal_time": c.close_time,
                          "fill_time": c.open_time + STEP, "signal_close": c.close,
                          "entry_high": upper, "exit_low": lower}
                self.pending = signal.copy()
                self.signals.append(signal.copy())
        self._append(c)
        self.mark(c.close_time, c.close)
        return signal

    def on_open(self, ts: int, price: float, *, observed_ms: int | None = None,
                max_lateness_ms: int = 30_000) -> dict[str, Any] | None:
        if not math.isfinite(price) or price <= 0 or ts % STEP:
            raise ValueError("Valid aligned open required")
        if not self.pending:
            return None
        p = self.pending
        if ts < p["fill_time"]:
            return None
        if ts != p["fill_time"]:
            raise ValueError("Cannot execute a signal at a later candle open")
        if observed_ms is not None and not ts <= observed_ms <= ts + max_lateness_ms:
            self.pending = None
            self.signals[-1]["expired"] = True
            return None
        return self._fill(p, ts, price)

    def manual_close(self, ts: int, price: float, reason: str = "manual") -> dict[str, Any] | None:
        """Operator-requested paper liquidation at the current observed quote."""
        if not math.isfinite(price) or price <= 0:
            raise ValueError("A valid current quote is required")
        self.pending = None
        if self.qty == 0:
            return None
        action = {"side": "SELL", "signal_time": ts, "fill_time": ts,
                  "signal_close": price, "reason": reason}
        self.signals.append(action.copy())
        return self._fill(action, ts, price)

    def _fill(self, p: dict[str, Any], ts: int, price: float) -> dict[str, Any]:
        slip, fee = self.config.slippage_bps / 10_000, self.config.fee_bps / 10_000
        buy = p["side"] == "BUY"
        fill_price = price * (1 + slip if buy else 1 - slip)
        qty = self.cash * self.config.allocation / (fill_price * (1 + fee)) if buy else self.qty
        notional = qty * fill_price
        cost = notional * fee
        fill = {**p, "price": fill_price, "reference_open": price, "qty": qty,
                "notional": notional, "fee": cost,
                "slippage_cost": qty * abs(fill_price - price)}
        if buy:
            self.cash -= notional + cost
            self.qty = qty
            self.entry = fill.copy()
        else:
            self.cash += notional - cost
            self.qty = 0.0
            assert self.entry is not None
            entry = self.entry
            pnl = notional - cost - entry["notional"] - entry["fee"]
            self.trades.append({"entry_time": entry["fill_time"], "exit_time": ts,
                                "entry_signal_time": entry["signal_time"],
                                "exit_signal_time": p["signal_time"], "qty": qty,
                                "entry_price": entry["price"], "exit_price": fill_price,
                                "fees": entry["fee"] + cost, "pnl": pnl,
                                "exit_reason": p.get("reason", "3-day breakdown"),
                                "return": pnl / (entry["notional"] + entry["fee"])})
            self.entry = None
        self.fills.append(fill)
        self.pending = None
        self.mark(ts, price)
        return fill

    def summary(self) -> dict[str, Any]:
        equity = self.curve[-1]["equity"] if self.curve else self.config.initial_cash
        reasons = []
        if self.max_dd > 0.40:
            reasons.append("Maximum drawdown exceeds the video's 40% rejection threshold")
        verdict = "FAIL" if reasons else "INCONCLUSIVE" if len(self.trades) < 50 else "KNOWN_GATES_PASS"
        return {"config": asdict(self.config), "final_equity": equity,
                "net_return_pct": (equity / self.config.initial_cash - 1) * 100,
                "max_drawdown_pct": self.max_dd * 100, "completed_trades": len(self.trades),
                "open_position": self.entry, "pending_signal": self.pending,
                "total_fees": sum(f["fee"] for f in self.fills),
                "total_slippage_cost": sum(f["slippage_cost"] for f in self.fills),
                "verdict": verdict, "rejection_reasons": reasons,
                "validation_scope": "Only the two gates disclosed in the video; other gates are unavailable",
                "drawdown_sampling": "4h closes and executed opens; intrabar drawdown unavailable"}

    def to_state(self) -> dict[str, Any]:
        return {"version": 1, "config": asdict(self.config),
                "history": [asdict(c) for c in self.history],
                **{key: getattr(self, key) for key in ("cash", "qty", "pending", "entry", "fills",
                    "trades", "signals", "curve", "peak", "max_dd")}}

    @classmethod
    def from_state(cls, state: dict[str, Any], config: Config) -> BreakoutBot:
        if state.get("version") != 1 or state.get("config") != asdict(config):
            raise ValueError("State version/config mismatch; use a separate state file")
        bot = cls(config)
        for c in state["history"]:
            bot._append(Candle(**c))
        for key in ("cash", "qty", "pending", "entry", "fills", "trades", "signals", "curve", "peak", "max_dd"):
            setattr(bot, key, state[key])
        return bot


def backtest(bars: list[Candle], config: Config, *, start_ms: int | None = None,
             end_ms: int | None = None) -> BreakoutBot:
    bot = BreakoutBot(config)
    if not bars:
        raise ValueError("No bars")
    # Validate the entire supplied tape before producing any results.
    for i, c in enumerate(bars):
        validate_bar(c, config.symbol)
        if not c.closed or c.close_time != c.open_time + STEP:
            raise ValueError("Incomplete or incorrectly timestamped bar")
        if i and c.open_time != bars[i - 1].open_time + STEP:
            raise ValueError("Non-contiguous input data")
    for c in bars:
        if end_ms is not None and c.close_time > end_ms:
            break
        if start_ms is not None and c.open_time < start_ms:
            bot._append(c)
            continue
        bot.on_open(c.open_time, c.open)
        bot.on_close(c)
    # Leave final positions marked to market and final signals unfilled.
    return bot
