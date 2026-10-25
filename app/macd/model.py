"""MACD (LuxAlgo library indicator) on Binance SPOT ZEC/USDT 4h, the operator's audited settings (2026-10-12). Paper only.

MACD-2 replaces MACD-1 (10 perps + ZEC perp, taker 0.05%, funding) at the operator's request: "make the MACD desk use spot
ZEC with the same settings" as the audit spec in docs/MACD_ZEC_AUDIT.md:
- MACD = EMA(close,12) - EMA(close,26), signal = EMA(MACD,9) (Pine ta.ema, SMA-seeded), 4h candles of Binance SPOT.
- Buy when MACD crosses above the signal (MACD[t] > S[t] and MACD[t-1] <= S[t-1]) and the book is flat; sell everything when
  it crosses below. Long or cash only, 1x fully funded, fractional units, one position, no stop / target / filters.
- A signal on candle t fills at candle t+1's OPEN. Commission f = 0.10% and adverse slippage s = 0.02%, each side:
  buy_price = open*(1+s); units = cash / (buy_price*(1+f)); sell_price = open*(1-s); cash = units*sell_price*(1-f).
- Warm-up candles only feed the indicators; the book starts flat with 10,000 USDT.
- Equity is marked at every 4h close; max drawdown includes the starting cash in the running peak.
- The backtest is the audit's fixed window, 2024-10-11 00:00 -> 2026-10-11 00:00 UTC (indicators from 2024-09-01), with
  any position left open sold at the final close (exit costs applied). It reproduces the audit to the cent:
  161 trades, 308,490.275496 USDT, +2,984.902755 %, max drawdown 37.310030 %.
- The forward paper book starts at the desk's start and marks an open position at the last close.
"""
from __future__ import annotations

from app.donchian import model as engine

from datetime import datetime, timezone

RULE_VERSION = "MACD-2"
TIMEFRAME, TF_MS = engine.TIMEFRAME, engine.TF_MS
FAST, SLOW, SIGNAL = 12, 26, 9
FEE, SLIP, START_EQUITY = 0.001, 0.0002, 10_000.0
_ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)
WARMUP_FROM, BACKTEST_START, BACKTEST_END = _ms("2024-09-01T00:00"), _ms("2024-10-11T00:00"), _ms("2026-10-11T00:00")
RULES = {"version": RULE_VERSION, "source": "LuxAlgo library: MACD (defaults 12 / 26 / 9, close)",
         "market": "Binance SPOT ZECUSDT", "timeframe": TIMEFRAME, "fast": FAST, "slow": SLOW, "signal": SIGNAL,
         "direction": "long or cash (fully funded spot, 1x)",
         "entry": "MACD crosses above the signal at a 4h close -> buy with all cash at the next 4h open",
         "exit": "MACD crosses below the signal -> sell everything at the next 4h open",
         "costs": {"commission": FEE, "slippage": SLIP, "per": "side"}, "start_cash": START_EQUITY,
         "backtest_window": ["2024-10-11T00:00Z", "2026-10-11T00:00Z"], "paper_only": True}
aggregate = engine.aggregate


def ema(values, length):
    """Pine ta.ema: None until `length` values, seeded with their simple average. None inputs are skipped."""
    out, prev, seen, alpha = [], None, [], 2 / (length + 1)
    for v in values:
        if v is None:
            out.append(None)
            continue
        if prev is None:
            seen.append(v)
            if len(seen) == length:
                prev = sum(seen) / length
            out.append(prev)
            continue
        prev = alpha * v + (1 - alpha) * prev
        out.append(prev)
    return out


def channels(bars, fast=FAST, slow=SLOW, signal=SIGNAL):
    """Per bar: macd, signal, hist (None during warm-up)."""
    closes = [b["c"] for b in bars]
    f, s = ema(closes, fast), ema(closes, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    sig = ema(line, signal)
    return [dict(macd=m, signal=g, hist=m - g if m is not None and g is not None else None) for m, g in zip(line, sig)]


def simulate(bars, funding=(), symbol="", start_ms=0, start_equity=START_EQUITY, end_ms=None, close_at_end=False,
             fee=FEE, slip=SLIP):
    """Spot replay over contiguous 4h bars (funding is ignored: spot has none). Candles before start_ms are warm-up;
    candles at or after end_ms are dropped. close_at_end sells an open position at the final close (the audit's rule)."""
    if end_ms is not None:
        bars = [b for b in bars if b["t"] < end_ms]
    ch = channels(bars)
    window = [i for i, b in enumerate(bars) if b["t"] >= start_ms]
    cash, units, pos, trades, curve, pending = float(start_equity), 0.0, None, [], [], None
    peak, max_dd = float(start_equity), 0.0
    last = window[-1] if window else -1
    for i in window:
        b = bars[i]
        if pending == "buy" and units == 0:
            bp = b["o"] * (1 + slip)
            units = cash / (bp * (1 + fee))
            pos = dict(id=f"{RULE_VERSION}:{symbol}:{b['t']}", symbol=symbol, side="long", entry_t=b["t"], entry=bp, qty=units,
                       notional=cash, fees=units * bp * fee, funding=0.0, signal_t=bars[i - 1]["t"], exit_t=None, exit=None,
                       reason="open", pnl=None, ret=None)
            cash = 0.0
        elif pending == "sell" and units > 0:
            cash = _close(pos, trades, units, b["o"], b["t"], "exit", fee, slip)
            units, pos = 0.0, None
        pending = None
        mark = cash + units * b["c"]
        curve.append({"time": b["t"] + TF_MS, "value": mark})
        peak = max(peak, mark)
        max_dd = max(max_dd, 1 - mark / peak)
        h, hp = ch[i]["hist"], ch[i - 1]["hist"] if i else None
        if i < last and h is not None and hp is not None:            # a final-candle signal has no next open
            if units == 0 and h > 0 >= hp:
                pending = "buy"
            elif units > 0 and h < 0 <= hp:
                pending = "sell"
    open_trade = None
    if units > 0 and close_at_end:
        cash = _close(pos, trades, units, bars[last]["c"], bars[last]["t"] + TF_MS, "end of test (final close)", fee, slip)
        units, pos = 0.0, None
        curve[-1]["value"] = cash
    elif units > 0:
        open_trade = dict(pos, mark=bars[last]["c"], unrealized=units * bars[last]["c"] - pos["notional"])
    final = cash + units * (bars[last]["c"] if window else 0)
    rets = [t["ret"] for t in trades]
    gains, losses = sum(t["pnl"] for t in trades if t["pnl"] > 0), -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    return dict(version=RULE_VERSION, symbol=symbol, trades=trades, open_trade=open_trade, equity_curve=curve, channels=ch,
                summary=dict(start_equity=start_equity, end_equity=final, return_pct=100 * (final / start_equity - 1),
                             max_drawdown_pct=100 * max_dd, trades=len(trades), wins=sum(r > 0 for r in rets),
                             win_rate=sum(r > 0 for r in rets) / len(rets) if rets else None,
                             profit_factor=gains / losses if losses else None,
                             avg_trade_pct=100 * sum(rets) / len(rets) if rets else None,
                             fees=sum(t["fees"] for t in trades), funding=0.0))


def _close(pos, trades, units, raw_px, t, reason, fee, slip):
    sp = raw_px * (1 - slip)
    cash = units * sp * (1 - fee)
    pos.update(exit_t=t, exit=sp, reason=reason, fees=pos["fees"] + units * sp * fee, pnl=cash - pos["notional"],
               ret=cash / pos["notional"] - 1)
    trades.append(pos)
    return cash
