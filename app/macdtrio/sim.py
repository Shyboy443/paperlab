"""Long/short MACD on perpetual 4h candles: one account, 1x, always in the market after the first cross.

Execution model (docs/MACD_TRIO_STUDY.md), identical for the study and the paper books:
- signal at a 4h close: MACD histogram crosses above 0 -> target long, below 0 -> target short (flat if shorts are off);
- fills at the next 4h open; a reversal closes, then opens the other side; per side commission FEE + adverse slippage SLIP;
- funding at every settlement while open, at the recorded rate and mark price: longs pay a positive rate, shorts receive
  it. A fill at T is exposed to the settlement stamped T, an exit at T is not;
- disaster stop DISASTER against the entry fill, checked on the candle's high / low (as the live executor's exchange-side
  STOP_MARKET); a stop leaves the account flat until the next cross;
- equity marked at every 4h close; drawdown on close equity.
"""
from __future__ import annotations

from typing import Any, Iterable

import math

from app.macd.model import channels, ema

FEE, SLIP, DISASTER, H4 = 0.0005, 0.0002, 0.30, 14_400_000


def _daily_vol(closes: list[float], n: int) -> list[float | None]:
    """Daily volatility from the last n 4h log returns (std x sqrt(6)); None until n returns exist."""
    rets = [math.log(closes[i] / closes[i - 1]) if i and closes[i - 1] > 0 and closes[i] > 0 else 0.0 for i in range(len(closes))]
    out: list[float | None] = [None] * len(closes)
    s = s2 = 0.0
    for i, r in enumerate(rets):
        s, s2 = s + r, s2 + r * r
        if i >= n:
            s, s2 = s - rets[i - n], s2 - rets[i - n] ** 2
        if i >= n:
            m = s / n
            out[i] = math.sqrt(max(s2 / n - m * m, 0.0)) * math.sqrt(6)
    return out


def choppiness(bars: list[dict], n: int = 14) -> list[float | None]:
    """Choppiness Index: 100 * log10(sum of true range over n / (highest high - lowest low over n)) / log10(n).
    About 100 = sideways chop, about 0 = a clean trend; None until n candles exist."""
    out: list[float | None] = [None] * len(bars)
    tr = []
    for i, b in enumerate(bars):
        prev = bars[i - 1]["c"] if i else None
        tr.append(b["h"] - b["l"] if prev is None else max(b["h"] - b["l"], abs(b["h"] - prev), abs(b["l"] - prev)))
        if i >= n - 1:
            w = bars[i - n + 1:i + 1]
            rng = max(x["h"] for x in w) - min(x["l"] for x in w)
            s = sum(tr[i - n + 1:i + 1])
            out[i] = 100 * math.log10(s / rng) / math.log10(n) if rng > 0 and s > 0 else None
    return out


def signals(bars: list[dict]) -> list[int]:
    """Per candle: +1 bullish cross at its close, -1 bearish cross, 0 none."""
    ch = channels(bars)
    out = [0] * len(bars)
    for i in range(1, len(bars)):
        h, hp = ch[i]["hist"], ch[i - 1]["hist"]
        if h is None or hp is None:
            continue
        if hp <= 0 < h:
            out[i] = 1
        elif hp >= 0 > h:
            out[i] = -1
    return out


def simulate(bars: list[dict], funding: Iterable[tuple[int, float, float]] = (), start_ms: int = 0,
             end_ms: int | None = None, capital: float = 10_000.0, shorts: bool = True, fee: float = FEE,
             slip: float = SLIP, disaster: float = DISASTER, close_at_end: bool = True, size: float = 1.0,
             trend_len: int | None = None, vol_target: float | None = None, vol_len: int = 180,
             chop_len: int | None = None, chop_max: float | None = None, sig: list[int] | None = None,
             tf: int = H4) -> dict[str, Any]:
    """bars: completed, contiguous 4h candles (t,o,h,l,c) including warm-up before start_ms. size: the share of the
    equity each position uses (the rest stays as cash), 1.0 = all of it.
    trend_len: only take a long above / a short below the EMA of that many 4h closes (a cross against it goes flat).
    vol_target: daily volatility target; a position uses min(size, vol_target / the coin's daily volatility over the
    last vol_len 4h candles).
    chop_len / chop_max: at a cross, if the Choppiness Index (chop_len) of the 4h candles is above chop_max the book goes
    flat instead of taking the new side (docs/MACD_CHOP_TEST.md).
    sig: another strategy's +1 / -1 / 0 per candle; tf: the candle length in ms (4h by default, 1h for the hourly bots)."""
    end_ms = end_ms or (bars[-1]["t"] + tf if bars else start_ms)
    sig = sig if sig is not None else signals(bars)       # another strategy's +1 / -1 / 0 per candle (default: MACD)
    closes = [b["c"] for b in bars]
    trend = ema(closes, trend_len) if trend_len else None
    vol = _daily_vol(closes, vol_len) if vol_target else None
    chop = choppiness(bars, chop_len) if chop_len and chop_max is not None else None
    cur_size = size
    fund = sorted((int(t), float(r), float(m)) for t, r, m in funding if start_ms <= int(t) < end_ms)
    fi = 0
    eq, side, qty, entry, entry_t, margin, reserve = capital, 0, 0.0, 0.0, 0, 0.0, 0.0
    target = 0
    trades: list[dict] = []
    times, equity = [], []
    fees = funding_total = 0.0
    peak, max_dd = capital, 0.0

    def close(px_raw: float, t: int, reason: str) -> None:
        nonlocal eq, side, qty, entry, margin, fees, reserve
        px = px_raw * (1 - slip) if side > 0 else px_raw * (1 + slip)
        pnl = side * qty * (px - entry)
        f = qty * px * fee
        eq = reserve + margin + pnl - f
        reserve = 0.0
        fees += f
        trades.append({"side": "long" if side > 0 else "short", "entry_t": entry_t, "entry": entry, "exit_t": t, "exit": px,
                       "qty": qty, "pnl": eq - trades_open_equity[0], "reason": reason})
        side, qty, margin = 0, 0.0, 0.0

    def open_(d: int, px_raw: float, t: int) -> None:
        nonlocal eq, side, qty, entry, entry_t, margin, fees, reserve
        px = px_raw * (1 + slip) if d > 0 else px_raw * (1 - slip)
        trades_open_equity[0] = eq
        reserve = eq * (1 - cur_size)
        qty = eq * cur_size / (px * (1 + fee))
        f = qty * px * fee
        margin = eq * cur_size - f
        fees += f
        side, entry, entry_t = d, px, t

    trades_open_equity = [eq]
    pending = [False]                          # a cross at the last close owes an order at this open
    for i, b in enumerate(bars):
        t0 = b["t"]
        if t0 < start_ms or t0 >= end_ms:
            if t0 < start_ms and sig[i]:
                target = 0                      # warm-up crosses are ignored: start flat, wait for a cross in the window
            continue
        # 1. orders decided at the previous close fill at this open
        want = target if (shorts or target > 0) else 0
        if want != side and (want != 0 or side != 0) and pending[0]:
            if side != 0:
                close(b["o"], t0, "reversal" if want else "exit")
            if want != 0 and eq > 0:
                open_(want, b["o"], t0)
        pending[0] = False
        # 2. funding settlements inside this candle while open (a settlement exactly at the fill time counts)
        while fi < len(fund) and fund[fi][0] < t0 + tf:
            ft, rate, mark = fund[fi]
            fi += 1
            if side != 0 and ft >= entry_t:
                cost = side * qty * mark * rate
                margin -= cost
                funding_total -= cost
        # 3. disaster stop on the candle's extreme
        if side != 0:
            raw_entry = entry / (1 + slip) if side > 0 else entry / (1 - slip)
            stop = raw_entry * (1 - side * disaster)
            if (side > 0 and b["l"] <= stop) or (side < 0 and b["h"] >= stop):
                fill = min(b["o"], stop) if side > 0 else max(b["o"], stop)
                close(fill, t0, "disaster stop")
        # 4. mark at the close, decide for the next open
        mark_eq = eq if side == 0 else reserve + margin + side * qty * (b["c"] - entry)
        times.append(t0 + tf)
        equity.append(mark_eq)
        peak = max(peak, mark_eq)
        max_dd = max(max_dd, (peak - mark_eq) / peak if peak > 0 else 0.0)
        if sig[i]:
            target = sig[i]
            if trend is not None and trend[i] is not None and ((target > 0 and b["c"] < trend[i]) or (target < 0 and b["c"] > trend[i])):
                target = 0                      # against the trend: close and stay flat
            if chop is not None and chop[i] is not None and chop[i] > chop_max:
                target = 0                      # a choppy market: close and stay flat until a cross in a calmer one
            cur_size = size if vol is None or not vol[i] else min(size, vol_target / vol[i])
            pending[0] = True
    if close_at_end and side != 0 and bars:
        last = [b for b in bars if b["t"] < end_ms][-1]
        close(last["c"], last["t"] + tf, "end of test")
        if equity:
            equity[-1] = eq
    final = equity[-1] if equity else capital
    wins = [x["pnl"] for x in trades if x["pnl"] > 0]
    losses = [-x["pnl"] for x in trades if x["pnl"] <= 0]
    return {"trades": trades, "times": times, "equity": equity, "open": None if side == 0 else
            {"side": "long" if side > 0 else "short", "entry": entry, "entry_t": entry_t, "qty": qty},
            "metrics": {"net_return_pct": (final / capital - 1) * 100, "ending": final, "max_drawdown_pct": max_dd * 100,
                        "trades": len(trades), "win_rate_pct": (len(wins) / len(trades) * 100) if trades else None,
                        "profit_factor": (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (None if not wins else 999.0),
                        "fees": fees, "funding": funding_total,
                        "stops": sum(1 for x in trades if x["reason"] == "disaster stop")}}

