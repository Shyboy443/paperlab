"""No-Wick Retest strategy (operator's Pine script "No-Wick Retest Levels", 2026-10-11), as a causal paper model.

Levels: a closed chart candle with NO wick on its open side becomes a level --
    bullish candle (close > open) with no lower wick  -> LONG level at its low
    bearish candle (close < open) with no upper wick  -> SHORT level at its high
Only the most recent MAX_LEVELS untapped levels are kept (older ones dropped first).

Trade: when a later candle TAPS a level (its range touches the price) the level is consumed. If no trade is open (and
none was open when the chart candle started) and the 1-minute trend agrees -- EMA20 above EMA50 and the 1-minute close
above EMA20 for a long, the mirror for a short -- a trade opens AT the level price with a take-profit TP_ATR x ATR(14)
and a stop SL_ATR x ATR(14) away (1:2). One trade at a time per symbol and timeframe.

Differences from the TradingView script, all to remove look-ahead:
- taps, entries and exits are timed on real 1-minute candles: the FIRST touched level in time is taken (the script
  picks the level nearest the bar open), the 1-minute trend is read from the minutes already closed at the tap (the
  script uses the 1-minute values at the chart bar's close), and exits are checked minute by minute from the minute after
  entry (a minute touching both = stop first, as the script does per bar);
- ATR is the chart timeframe ATR(14) of the candles already CLOSED at the tap (the script uses the tapping bar's own
  ATR, which includes its not-yet-finished range).
Costs: the entry and a take-profit are resting limit orders (maker 0.02%); a stop is a market order (taker 0.05% plus
0.02% slippage). A stop gapping beyond the level fills at that minute's open. R = net P&L / planned risk.
"""
from __future__ import annotations

RULE_VERSION = "NW-1"
MINUTE = 60_000
TIMEFRAMES = {"5m": 5 * MINUTE, "15m": 15 * MINUTE}
MAX_LEVELS = 3
FAST_EMA, SLOW_EMA = 20, 50
REQUIRE_PRICE_SIDE = True
ATR_LEN = 14
TP_ATR, SL_ATR = 2.0, 1.0
MAKER, TAKER, STOP_SLIP = 0.0002, 0.0005, 0.0002
RULES = {
    "version": RULE_VERSION, "source": "operator's Pine script 'No-Wick Retest Levels' (MPL-2.0)",
    "timeframes": list(TIMEFRAMES), "levels": "bullish candle with no lower wick -> long level at its low; bearish "
    "candle with no upper wick -> short level at its high; exact (0 tick tolerance)",
    "max_untapped_levels": MAX_LEVELS, "trend": f"1-minute EMA{FAST_EMA} vs EMA{SLOW_EMA}, close beyond EMA{FAST_EMA}",
    "entry": "limit at the level when first touched, if the 1m trend agrees and no trade is open",
    "take_profit_atr": TP_ATR, "stop_loss_atr": SL_ATR, "atr": f"ATR{ATR_LEN} (Wilder) of the closed chart candles",
    "costs": {"maker": MAKER, "taker": TAKER, "stop_slippage": STOP_SLIP},
    "one_trade_at_a_time": True, "paper_only": True,
}


def ema_series(values, n):
    """Pine ta.ema: seeded with the first value."""
    out, e, k = [], None, 2 / (n + 1)
    for v in values:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


def aggregate(bars, tf_ms):
    """Complete chart candles from 1-minute bars (a bucket missing any minute is skipped). Each candle keeps the index
    range of its minutes."""
    out, i, n, per = [], 0, len(bars), tf_ms // MINUTE
    while i < n:
        start = bars[i]["t"] // tf_ms * tf_ms
        j = i
        while j < n and bars[j]["t"] < start + tf_ms:
            j += 1
        seg = bars[i:j]
        if len(seg) == per and seg[0]["t"] == start:
            out.append(dict(t=start, o=seg[0]["o"], h=max(b["h"] for b in seg), l=min(b["l"] for b in seg),
                            c=seg[-1]["c"], v=sum(b["v"] for b in seg), i0=i, i1=j))
        i = j
    return out


def atr_series(cb, n=ATR_LEN):
    """Pine ta.atr: RMA of the true range, seeded with the SMA of the first n."""
    out, tr_hist, rma = [], [], None
    for k, b in enumerate(cb):
        prev = cb[k - 1]["c"] if k else None
        tr = b["h"] - b["l"] if prev is None else max(b["h"] - b["l"], abs(b["h"] - prev), abs(b["l"] - prev))
        if rma is None:
            tr_hist.append(tr)
            if len(tr_hist) == n:
                rma = sum(tr_hist) / n
        else:
            rma = (rma * (n - 1) + tr) / n
        out.append(rma)
    return out


def _r(side, entry, stop, exit_px, reason):
    risk = abs(entry - stop)
    if risk <= 0:
        return None, None
    gross = side * (exit_px - entry)
    fees = entry * MAKER + exit_px * (MAKER if reason == "target" else TAKER)
    return (gross - fees) / risk, gross / risk


def simulate(bars, tf, symbol, start_ms=0, fill_through=False, min_risk_pct=0.0, tf_ms=None):
    """Replay the strategy over contiguous 1-minute bars. Returns trades (entries at or after start_ms) and every level
    with what happened to it, for the chart.

    Research options (docs/NOWICK_IMPROVE_STUDY.md; the live desk uses the defaults):
    fill_through -- a resting limit (the entry at the level, the take-profit) fills only when the price trades THROUGH
                    it, not when it merely touches it;
    min_risk_pct -- skip a trade whose planned risk (SL_ATR x ATR) is below this share of the price (a cost floor);
    tf_ms        -- a chart timeframe not in TIMEFRAMES (e.g. 1 hour)."""
    tf_ms = tf_ms or TIMEFRAMES[tf]
    cb = aggregate(bars, tf_ms)
    atr = atr_series(cb)
    closes = [b["c"] for b in bars]
    fast, slow = ema_series(closes, FAST_EMA), ema_series(closes, SLOW_EMA)
    levels, history, trades = [], [], []
    trade = None
    warm = SLOW_EMA

    def close_trade(t_exit, px, reason):
        nonlocal trade
        r, rg = _r(trade["dir"], trade["entry"], trade["stop"], px, reason)
        trade.update(exit_t=t_exit, exit=px, reason=reason, r=r, r_gross=rg)
        trade = None

    for k, b in enumerate(cb):
        active_at_start = trade is not None
        opened_this_bar = False                                 # the script opens at most one trade per chart candle
        atr_prev = atr[k - 1] if k else None
        for mi in range(b["i0"], b["i1"]):
            m = bars[mi]
            if trade is not None and m["t"] > trade["entry_t"]:
                long = trade["dir"] == 1
                if (m["l"] <= trade["stop"]) if long else (m["h"] >= trade["stop"]):
                    gap = (m["o"] < trade["stop"]) if long else (m["o"] > trade["stop"])
                    px = m["o"] if gap else trade["stop"]
                    close_trade(m["t"] + MINUTE, px * (1 - trade["dir"] * STOP_SLIP), "stop")
                elif ((m["h"] > trade["target"]) if fill_through else (m["h"] >= trade["target"])) if long else \
                        ((m["l"] < trade["target"]) if fill_through else (m["l"] <= trade["target"])):
                    close_trade(m["t"] + MINUTE, trade["target"], "target")
            for lv in list(levels):
                if lv["origin_k"] >= k or not (m["l"] <= lv["price"] <= m["h"]):
                    continue
                levels.remove(lv)
                lv.update(end_t=m["t"])
                j = mi - 1                                      # the 1-minute trend from minutes already closed
                if j < warm:
                    lv.update(outcome="tapped (trend not warmed up)")
                    continue
                bull = fast[j] > slow[j] and (not REQUIRE_PRICE_SIDE or closes[j] > fast[j])
                bear = fast[j] < slow[j] and (not REQUIRE_PRICE_SIDE or closes[j] < fast[j])
                lv["trend"] = dict(ema_fast=fast[j], ema_slow=slow[j], close_1m=closes[j], bull=bull, bear=bear)
                ok = bull if lv["dir"] == 1 else bear
                if trade is not None or active_at_start or opened_this_bar:
                    lv.update(outcome="tapped while a trade was open")
                elif not ok:
                    lv.update(outcome="tapped against the 1m trend")
                elif not atr_prev:
                    lv.update(outcome="tapped (ATR not warmed up)")
                elif fill_through and not ((m["l"] < lv["price"]) if lv["dir"] == 1 else (m["h"] > lv["price"])):
                    lv.update(outcome="touched, not filled (no trade-through)")
                elif min_risk_pct and atr_prev * SL_ATR < min_risk_pct * lv["price"]:
                    lv.update(outcome="skipped (risk below the cost floor)")
                else:
                    risk, reward = atr_prev * SL_ATR, atr_prev * TP_ATR
                    trade = dict(id=f"{RULE_VERSION}:{symbol}:{tf}:{m['t']}", symbol=symbol, tf=tf, dir=lv["dir"],
                                 side="long" if lv["dir"] == 1 else "short", level=lv["price"], origin_t=lv["origin_t"],
                                 entry_t=m["t"], entry=lv["price"], stop=lv["price"] - lv["dir"] * risk,
                                 target=lv["price"] + lv["dir"] * reward, atr=atr_prev, trend=lv["trend"],
                                 exit_t=None, exit=None, reason="open", r=None, r_gross=None)
                    opened_this_bar = True
                    if m["t"] >= start_ms:
                        trades.append(trade)
                    lv.update(outcome="entry", trade_id=trade["id"])
        # the chart candle has closed: a new no-wick level
        up_wick, low_wick = b["h"] - max(b["o"], b["c"]), min(b["o"], b["c"]) - b["l"]
        new = None
        if b["c"] > b["o"] and low_wick <= 0:
            new = dict(dir=1, price=b["l"])
        elif b["c"] < b["o"] and up_wick <= 0:
            new = dict(dir=-1, price=b["h"])
        if new:
            new.update(origin_k=k, origin_t=b["t"], end_t=None, outcome="untapped")
            levels.append(new)
            history.append(new)
            while len(levels) > MAX_LEVELS:
                old = levels.pop(0)
                old.update(outcome="dropped (more than 3 untapped)", end_t=b["t"] + tf_ms)
    for t in trades:
        t.pop("dir", None)
    return dict(version=RULE_VERSION, symbol=symbol, tf=tf, trades=trades, levels=history, open_levels=levels,
                candles=len(cb))


def summarize(trades):
    rs = [t["r"] for t in trades if t.get("r") is not None]
    wins, losses = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    peak = cum = dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dict(trades=len(trades), closed=len(rs), open=len(trades) - len(rs), wins=sum(r > 0 for r in rs),
                win_rate=sum(r > 0 for r in rs) / len(rs) if rs else None, total_r=sum(rs),
                avg_r=sum(rs) / len(rs) if rs else None, profit_factor=wins / losses if losses else None,
                max_drawdown_r=dd)
