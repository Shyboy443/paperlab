"""Donchian Trend Rules (LuxAlgo library indicator, operator 2026-10-12) as a causal paper model.

The classic "four-week rule" breakout, with LuxAlgo's defaults:
    Entry Length 20, Exit Length 10, Breakout Trigger = Close, Trade Direction = Long & Short.
- Channels use PRIOR bars only: entry high/low = highest high / lowest low of the 20 bars before the current one, exit
  levels = the 10-bar lowest low (long) / highest high (short) before the current one.
- Long when a bar CLOSES above the prior 20-bar high; short when it closes below the prior 20-bar low. In Long & Short
  mode an opposite entry reverses the position directly.
- A long exits when a bar closes below the prior 10-bar low (a short above the prior 10-bar high): the trailing exit.
- No stop-loss or take-profit beyond that.
Execution (ours, the indicator draws signals only): a signal on a bar's close is filled at the NEXT bar's open, taker
0.05% + 0.02% slippage per side, the whole book equity as notional (1x, no leverage), real funding on every settlement
held. Return and drawdown are on that equity curve.
"""
from __future__ import annotations

RULE_VERSION = "DTR-1"
HOUR = 3_600_000
TIMEFRAME, TF_MS = "4h", 4 * HOUR
ENTRY_LEN, EXIT_LEN = 20, 10
TAKER, SLIP = 0.0005, 0.0002
START_EQUITY = 1000.0
RULES = {"version": RULE_VERSION, "source": "LuxAlgo library: Donchian Trend Rules (defaults)",
         "timeframe": TIMEFRAME, "entry_length": ENTRY_LEN, "exit_length": EXIT_LEN, "breakout_trigger": "close",
         "direction": "long & short (an opposite entry reverses)",
         "execution": "signal at the bar close, filled at the next bar open; whole equity as 1x notional",
         "costs": {"taker": TAKER, "slippage": SLIP}, "funding": "real Binance funding while a position is held",
         "paper_only": True}


def channels(bars, entry_len=ENTRY_LEN, exit_len=EXIT_LEN):
    """Per bar: prior entry high/low and prior exit high/low (None until enough bars)."""
    out = []
    for i in range(len(bars)):
        e = bars[i - entry_len:i] if i >= entry_len else None
        x = bars[i - exit_len:i] if i >= exit_len else None
        out.append(dict(up=max(b["h"] for b in e) if e else None, dn=min(b["l"] for b in e) if e else None,
                        xhi=max(b["h"] for b in x) if x else None, xlo=min(b["l"] for b in x) if x else None))
    return out


def rule(c, close, held):
    """The Donchian Trend Rules decision at one candle's close: c = that candle's channels (prior bars), held = +1 / -1 / 0.
    Returns ("enter", side), ("reverse", side), ("exit", 0) or None. Used by the backtest and the live switch alike."""
    if c["up"] is None or c["xlo"] is None:
        return None
    long_sig, short_sig = close > c["up"], close < c["dn"]
    if held == 0:
        return ("enter", 1) if long_sig else ("enter", -1) if short_sig else None
    if held == 1:
        return ("reverse", -1) if short_sig else ("exit", 0) if close < c["xlo"] else None
    return ("reverse", 1) if long_sig else ("exit", 0) if close > c["xhi"] else None


def simulate(bars, funding=(), symbol="", start_ms=0, start_equity=START_EQUITY):
    """Replay over contiguous 4h bars. funding: [(t, rate, mark)]. Trades opening at or after start_ms are counted."""
    ch = channels(bars)
    return run(bars, funding, symbol, start_ms, start_equity, RULE_VERSION, lambda i, b, held: rule(ch[i], b["c"], held), ch)


def run(bars, funding, symbol, start_ms, start_equity, version, decide, series):
    """Shared execution engine. decide(i, bar, held_dir) -> ("enter"|"reverse", side) | ("exit", 0) | None is evaluated at
    each bar's close from start_ms on and filled at the next bar's open; `series` (per-bar indicator values) is returned as
    `channels`. Bars before start_ms are warm-up only: no signals, no trades, so the book starts flat with start_equity
    (fixed 2026-10-12: warm-up trades used to compound into the reported return and the forward paper books)."""
    # (time, rate, mark): Binance settles funding on the position notional at the MARK price (fallback: the 4h close)
    fund = sorted((int(f[0]), float(f[1]), float(f[2]) if len(f) > 2 and f[2] else None) for f in funding)
    fi = 0
    equity, pos, trades, curve = start_equity, None, [], []
    peak, max_dd = start_equity, 0.0
    pending = None                                                        # (action, side) decided at the last close

    def open_pos(side, i):
        nonlocal equity
        b = bars[i]
        px = b["o"] * (1 + side * SLIP)
        fee = equity * TAKER
        equity -= fee
        return dict(id=f"{version}:{symbol}:{b['t']}", symbol=symbol, side="long" if side == 1 else "short", dir=side,
                    entry_t=b["t"], entry=px, notional=equity, qty=equity / px, fees=fee, funding=0.0,
                    signal_t=bars[i - 1]["t"] if i else b["t"], exit_t=None, exit=None, reason="open", pnl=None, ret=None)

    def close_pos(i, reason, at_open=True, px_raw=None):
        nonlocal equity, pos
        b = bars[i]
        raw = b["o"] if at_open else px_raw
        px = raw * (1 - pos["dir"] * SLIP)
        gross = pos["dir"] * pos["qty"] * (px - pos["entry"])
        fee = abs(pos["qty"] * px) * TAKER
        equity += gross - fee
        pos.update(exit_t=b["t"] if at_open else b["t"] + TF_MS, exit=px, reason=reason, fees=pos["fees"] + fee,
                   pnl=gross - pos["fees"] - fee + pos["funding"], ret=(gross - pos["fees"] - fee + pos["funding"]) / pos["notional"])
        pos.pop("dir", None)
        if pos["entry_t"] >= start_ms:
            trades.append(pos)
        pos = None

    for i, b in enumerate(bars):
        # 1) act at this bar's open on the decision made at the previous close
        if pending:
            action, side = pending
            if pos and action in ("exit", "reverse"):
                close_pos(i, "reverse" if action == "reverse" else "exit")
            if action in ("enter", "reverse"):
                pos = open_pos(side, i)
            pending = None
        # 2) funding settlements during this bar while holding
        while fi < len(fund) and fund[fi][0] < b["t"]:
            fi += 1
        while fi < len(fund) and fund[fi][0] < b["t"] + TF_MS:
            if pos:
                amt = -pos["dir"] * pos["qty"] * (fund[fi][2] or b["c"]) * fund[fi][1]
                pos["funding"] += amt
                equity += amt
            fi += 1
        # 3) decide at this bar's close; warm-up bars (before start_ms) only feed the indicators: the book starts flat
        pending = decide(i, b, pos["dir"] if pos else 0) if b["t"] >= start_ms else None
        mark = equity + (pos["dir"] * pos["qty"] * (b["c"] - pos["entry"]) if pos else 0.0)
        if b["t"] >= start_ms:
            curve.append({"time": b["t"] + TF_MS, "value": mark})
            peak = max(peak, mark)
            max_dd = max(max_dd, 1 - mark / peak if peak else 0)
    open_trade = None
    if pos:
        open_trade = dict(pos, mark=bars[-1]["c"], unrealized=pos["dir"] * pos["qty"] * (bars[-1]["c"] - pos["entry"]))
        open_trade.pop("dir", None)
    final = curve[-1]["value"] if curve else start_equity
    rets = [t["ret"] for t in trades]
    wins, losses = sum(t["pnl"] for t in trades if t["pnl"] > 0), -sum(t["pnl"] for t in trades if t["pnl"] < 0)
    return dict(version=version, symbol=symbol, trades=trades, open_trade=open_trade, equity_curve=curve,
                channels=series, summary=dict(start_equity=start_equity, end_equity=final, return_pct=100 * (final / start_equity - 1),
                                          max_drawdown_pct=100 * max_dd, trades=len(trades), wins=sum(r > 0 for r in rets),
                                          win_rate=sum(r > 0 for r in rets) / len(rets) if rets else None,
                                          profit_factor=wins / losses if losses else None,
                                          avg_trade_pct=100 * sum(rets) / len(rets) if rets else None,
                                          fees=sum(t["fees"] for t in trades), funding=sum(t["funding"] for t in trades)))


def aggregate(bars, tf_ms=TF_MS):
    """Complete UTC-aligned candles from finer bars (a bucket missing a part is skipped)."""
    if len(bars) < 2:
        return []
    step = bars[1]["t"] - bars[0]["t"]
    per = tf_ms // step
    buckets = {}
    for b in bars:
        buckets.setdefault(b["t"] // tf_ms * tf_ms, []).append(b)
    out = []
    for t, rows in sorted(buckets.items()):
        if len(rows) == per:
            out.append(dict(t=t, o=rows[0]["o"], h=max(r["h"] for r in rows), l=min(r["l"] for r in rows), c=rows[-1]["c"],
                            v=sum(r["v"] for r in rows)))
    return out
