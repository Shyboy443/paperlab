"""Gold Scalping Strategy (1-Min TF): the operator's PDF (2026-10-20), written down as exact rules BEFORE any result
(docs/GOLD_SCALP_STUDY.md). The PDF names the asset (gold), so ONLY XAUUSD is tested.

The PDF, mechanised:
- sessions (Pakistan time, UTC+5, no DST): London open 12:00-15:00 -> 07:00-10:00 UTC; New York open 18:00-21:00 ->
  13:00-16:00 UTC. A signal candle must CLOSE inside a session;
- buy: the 9 EMA crossed above the 21 EMA within the last CROSS_WINDOW candles and is still above; RSI(14) > 50; the
  close breaks recent resistance = above the highest high of the previous LOOKBACK candles; a strong bullish
  confirmation candle = close > open with a body of at least BODY_MIN of the candle's range. Sell: the mirror image;
- take profit 10-20 pips, stop loss 10-15 pips (gold: 1 pip = 0.10 USD, MT5's 10 points): every pair of TP in
  (10, 15, 20) and SL in (10, 12, 15) is reported; "or close on the opposite signal": an opposite signal closes at
  the next open;
- "3 to 5 quality setups per day": at most MAX_PER_DAY entries per UTC day; one position at a time;
- not mechanisable and left out: the optional "Mayfair FX Scalper" filter, news avoidance, discretionary S/R zones.

Fills: the next 1-minute open; a buy pays the spread (fills at the ask); every exit pays commission + slippage; a
1-minute candle touching both the stop and the target counts as the stop (no finer data).
"""
from __future__ import annotations

from typing import Any

import numpy as np

from . import ind as I

PIP = 0.10                                      # USD per pip on gold (MT5: 10 points of 0.01)
SESSIONS_UTC = ((7 * 60, 10 * 60), (13 * 60, 16 * 60))   # minutes of the day, [start, end)
CROSS_WINDOW, LOOKBACK, BODY_MIN, MAX_PER_DAY = 5, 10, 0.6, 5
TPS, SLS = (10, 15, 20), (10, 12, 15)
# Costs in USD per ounce, from the operator's broker (Fusion Markets raw account, MT5 M1 Jul-Oct 2026):
SPREAD = 0.057                                  # mean recorded spread
COMMISSION = 0.0225                             # 2.25 USD per 100 oz lot per side
SLIPPAGE = 0.02                                 # per side


def signals(t, o, h, l, c) -> np.ndarray:
    """+1 buy / -1 sell / 0 per 1-minute candle, at its close."""
    e9, e21 = I.ema(c, 9), I.ema(c, 21)
    r = I.rsi(c, 14)
    with np.errstate(invalid="ignore"):
        above, below = e9 > e21, e9 < e21
        up_x, dn_x = I.cross_up(e9, e21), I.cross_down(e9, e21)
    recent_up = I.rolling(up_x.astype(float), CROSS_WINDOW, np.max) > 0
    recent_dn = I.rolling(dn_x.astype(float), CROSS_WINDOW, np.max) > 0
    res, sup = I.shift(I.highest(h, LOOKBACK), 1), I.shift(I.lowest(l, LOOKBACK), 1)
    rng = h - l
    body = np.abs(c - o)
    with np.errstate(invalid="ignore", divide="ignore"):
        strong = (rng > 0) & (body >= BODY_MIN * rng)
        minute = ((t // 60_000) + 1) % 1440                 # the minute the candle CLOSES at
        in_session = np.zeros(len(t), bool)
        for a, b in SESSIONS_UTC:
            in_session |= (minute > a) & (minute <= b)
        buy = in_session & above & recent_up & (r > 50) & (c > res) & (c > o) & strong
        sell = in_session & below & recent_dn & (r < 50) & (c < sup) & (c < o) & strong
    return np.where(buy, 1, np.where(sell, -1, 0))


def replay(t, o, h, l, c, sig, tp_pips: float, sl_pips: float, start_ms: int, end_ms: int, pip: float = PIP,
           spread: float = SPREAD, commission: float = COMMISSION, slippage: float = SLIPPAGE) -> list[dict[str, Any]]:
    """BID candles. A buy fills at the next open + spread (ask) + slippage and exits on the bid; a sell fills at the
    next open (bid) - slippage and exits on the ask (bid + spread). Results in R = the stop distance."""
    tp, sl = tp_pips * pip, sl_pips * pip
    idx = np.flatnonzero((t >= start_ms) & (t < end_ms))
    trades: list[dict[str, Any]] = []
    if not len(idx):
        return trades
    first, last = idx[0], idx[-1]
    day_count: dict[int, int] = {}
    i = first
    while i < last:
        d = int(sig[i])
        day = int(t[i] // 86_400_000)
        if d == 0 or day_count.get(day, 0) >= MAX_PER_DAY:
            i += 1
            continue
        j = i + 1
        if t[j] - t[i] > 5 * 60_000:                        # the market was closed in between: no stale fill
            i += 1
            continue
        day_count[day] = day_count.get(day, 0) + 1
        # A long buys at the ask (bid open + spread) and is closed by selling at the bid: its stop / target trigger on
        # the BID candles. A short sells at the bid and is closed by buying at the ask: they trigger on the ASK
        # (bid + spread). Levels sit at fixed distances from the actual fill, as a trader's SL / TP would.
        fill = o[j] + spread + slippage if d > 0 else o[j] - slippage
        stop_px = fill - d * sl
        tgt_px = fill + d * tp
        k, result, exit_px = j, None, None
        while k <= last:
            add = 0.0 if d > 0 else spread                                     # the exit side's price frame
            lo, hi, op = l[k] + add, h[k] + add, o[k] + add
            hit_stop = lo <= stop_px if d > 0 else hi >= stop_px
            hit_tgt = hi >= tgt_px if d > 0 else lo <= tgt_px
            if hit_stop:                                                       # both in one candle -> the stop
                exit_px, result = (min(op, stop_px) if d > 0 else max(op, stop_px)), "stop (candle also hit target)" if hit_tgt else "stop"
                break
            if hit_tgt:
                exit_px, result = (max(op, tgt_px) if d > 0 else min(op, tgt_px)), "target"
                break
            if k > j and int(sig[k]) == -d and k + 1 <= last:                  # opposite signal: close at the next open
                k += 1
                exit_px, result = o[k] + add, "opposite signal"
                break
            k += 1
        if exit_px is None:
            k = last
            exit_px, result = c[last] + (0.0 if d > 0 else spread), "end"
        exit_px = exit_px - d * slippage
        net_usd = d * (exit_px - fill) - 2 * commission
        raw_usd = d * (exit_px - fill) + spread + 2 * slippage                 # the price move alone (what a video sees)
        trades.append({"t": int(t[j]), "exit_t": int(t[k]), "dir": d, "entry": float(fill), "exit": float(exit_px),
                       "gross_r": float(raw_usd / sl), "net_r": float(net_usd / sl), "net_usd": float(net_usd),
                       "cost_r": float((raw_usd - net_usd) / sl), "result": result})
        i = k + 1 if k > i else i + 1
    return trades
