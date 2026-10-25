"""ZEC perpetual MACD account, 2026 by calendar month (UTC): the operator's monthly study, recomputed live. Paper only.

The same rule as the desk (MACD 12/26/9 crosses on 4h closes, Pine-style SMA-seeded EMAs) on Binance USD-M ZECUSDT
perpetual candles:
- one account, flat with START_CASH at 2026-01-01 00:00 UTC (earlier signals ignored), long or cash at 1x;
- fills at the next 4h open; commission FEE and adverse slippage SLIP per side:
  buy units = cash / (open(1+s)(1+f)); sell cash = units * open(1-s)(1-f) + accumulated funding;
- funding on units x Binance's recorded SETTLEMENT MARK PRICE x rate at every settlement while long; a fill at T is
  exposed to the settlement stamped at T (settlements land a few ms after T), an exit at T is not;
- equity = units x hourly close + accumulated funding, marked at every completed hour. Monthly drawdown is on those
  hourly closes from the month's opening equity; intrabar extremes are not included.
The current month is month-to-date with an open position marked, not sold. Verified against the operator's table
(docs/zec_perp_2026/README.md): Jan-Sep +505.64 % vs their +506.31 %, 54 trades each, max DD 36.00 % each.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .model import channels

FEE, SLIP, START_CASH = 0.0005, 0.0002, 10.0
H1, H4 = 3_600_000, 14_400_000
START = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
WARMUP_FROM = int(datetime(2025, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
SETTINGS = {"market": "Binance USD-M ZECUSDT perpetual", "timeframe": "4h", "rule": "MACD 12/26/9 crosses, long or cash, 1x",
            "start_utc": "2026-01-01T00:00Z", "start_cash_usd": START_CASH, "commission": FEE, "slippage": SLIP,
            "funding": "every settlement while long, at Binance's recorded settlement mark price",
            "drawdown": "hourly closing equity (intrabar extremes excluded)", "paper_only": True}


def _month(t):
    return datetime.fromtimestamp((t - 1) / 1000, timezone.utc).strftime("%Y-%m")


def compute(b4, h1, funding, start_ms=START, end_ms=None, start_cash=START_CASH, fee=FEE, slip=SLIP):
    """b4 / h1: completed 4h / 1h candles (dicts t,o,h,l,c), contiguous; funding: (t, rate, mark)."""
    end_ms = end_ms or (h1[-1]["t"] + H1 if h1 else start_ms)
    ch = channels(b4)
    fills, held = [], False
    for i in range(1, len(b4) - 1):
        if b4[i]["t"] < start_ms or b4[i + 1]["t"] >= end_ms:
            continue
        h, hp = ch[i]["hist"], ch[i - 1]["hist"]
        if h is None or hp is None:
            continue
        if not held and hp <= 0 < h:
            fills.append(("buy", b4[i + 1]["t"], b4[i + 1]["o"]))
            held = True
        elif held and hp >= 0 > h:
            fills.append(("sell", b4[i + 1]["t"], b4[i + 1]["o"]))
            held = False
    cash, units, fund_acc, entry, k, fi = start_cash, 0.0, 0.0, None, 0, 0
    trades, curve, fund_total = [], [], 0.0
    fund = sorted((int(t), float(r), float(m)) for t, r, m in funding)
    for hb in (x for x in h1 if start_ms <= x["t"] and x["t"] + H1 <= end_ms):
        t0, t1 = hb["t"], hb["t"] + H1
        while k < len(fills) and fills[k][1] <= t0:
            kind, ft, px = fills[k]
            k += 1
            if kind == "buy":
                bp = px * (1 + slip)
                units = cash / (bp * (1 + fee))
                entry = dict(entry_t=ft, entry=bp, cash_in=cash)
                cash = 0.0
            else:
                sp = px * (1 - slip)
                cash = units * sp * (1 - fee) + fund_acc
                trades.append(dict(entry, exit_t=ft, exit=sp, units=units, funding=fund_acc, cash_out=cash,
                                   pnl=cash - entry["cash_in"], ret=cash / entry["cash_in"] - 1))
                units, fund_acc, entry = 0.0, 0.0, None
        while fi < len(fund) and fund[fi][0] < t1:
            ft, rate, mark = fund[fi]
            fi += 1
            if units > 0 and ft >= t0:
                pay = -units * (mark or hb["c"]) * rate
                fund_acc += pay
                fund_total += pay
        curve.append((t1, units * hb["c"] + fund_acc if units > 0 else cash))
    months, prev, peak_all, dd_all = [], start_cash, start_cash, 0.0
    for m in sorted({_month(t) for t, _ in curve}):
        pts = [v for t, v in curve if _month(t) == m]
        peak, dd = prev, 0.0
        for v in pts:
            peak, dd = max(peak, v), max(dd, 1 - v / max(peak, v))
            peak_all = max(peak_all, v)
            dd_all = max(dd_all, 1 - v / peak_all)
        closed_in = [t for t in trades if _month(t["exit_t"] + 1) == m]
        months.append(dict(month=m, return_pct=100 * (pts[-1] / prev - 1), max_drawdown_pct=100 * dd, start_balance=prev,
                           end_balance=pts[-1], closed_trades=len(closed_in)))
        prev = pts[-1]
    as_of = curve[-1][0] if curve else start_ms
    if months and _month(as_of) == months[-1]["month"]:
        nxt = datetime.fromtimestamp((as_of - 1) / 1000, timezone.utc)
        month_end = datetime(nxt.year + (nxt.month == 12), nxt.month % 12 + 1, 1, tzinfo=timezone.utc).timestamp() * 1000
        months[-1]["partial"] = as_of < month_end
    open_pos = None
    if units > 0:
        last = next(x for x in reversed(h1) if x["t"] + H1 <= end_ms)["c"]
        open_pos = dict(entry, units=units, mark=last, funding=fund_acc, value=units * last + fund_acc,
                        value_if_sold=units * last * (1 - slip) * (1 - fee) + fund_acc)
    end_bal = curve[-1][1] if curve else start_cash
    return dict(settings=SETTINGS, as_of=as_of, months=months, trades=trades, open_position=open_pos,
                total=dict(return_pct=100 * (end_bal / start_cash - 1), max_drawdown_pct=100 * dd_all, end_balance=end_bal,
                           closed_trades=len(trades), funding_net=fund_total))
