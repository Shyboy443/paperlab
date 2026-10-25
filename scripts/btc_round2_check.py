"""Independent re-check of the user's "BTC multiple methods, round 2" report (2026-10-22).

Rebuilds the report's leading books from Binance BTCUSDT perpetual data with the
report's own rules: completed UTC daily close -> fill at the next daily open,
entry notional min(0.5, 0.20 / annualised 30-day vol), fixed units until the
direction changes, fee 0.05% + slippage 0.02% per side (doubled for stress),
funding charged before the daily fill, 10,000 USDT, 2021-01-01 .. 2026-10-21 UTC.

It adds one check the report does not have: the neighbouring lookbacks, to see
whether 60 days / EMA5-100 sit on a plateau or on a lucky peak. Nothing here
changes or arms a bot. Public market data only.

    python scripts/btc_round2_check.py
"""
from __future__ import annotations
import json, math, sys, threading
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from app.autoresearch.client import Client  # noqa: E402

HOUR = 3_600_000
DAY = 24 * HOUR
CACHE = PROJECT / "data" / "btc_round2_cache.json"
FETCH_FROM = int(datetime(2019, 9, 10, tzinfo=timezone.utc).timestamp() * 1000)
WARM_FROM = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
START = int(datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
END = int(datetime(2026, 10, 21, tzinfo=timezone.utc).timestamp() * 1000)
OCT = int(datetime(2026, 9, 30, 18, 30, tzinfo=timezone.utc).timestamp() * 1000)  # 1 Oct 00:00 Colombo


def load():
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    c = Client(threading.Event())
    bars = c.history("perp", "BTCUSDT", FETCH_FROM, END)
    funding = c.funding("BTCUSDT", FETCH_FROM, END)
    data = {"bars": [b[:5] for b in bars], "funding": funding}
    CACHE.write_text(json.dumps(data))
    return data


def daily(bars):
    days = {}
    for t, o, h, l, c in bars:
        d = t // DAY * DAY
        x = days.setdefault(d, [d, o, h, l, c, t])
        x[2] = max(x[2], h); x[3] = min(x[3], l)
        if t >= x[5]:
            x[4] = c; x[5] = t
        if t == d:
            x[1] = o
    return [x[:5] for x in sorted(days.values()) if x[5] == x[0] + 23 * HOUR]


def signals_momentum(days, n):
    closes = [d[4] for d in days]
    out = {}
    for i in range(n, len(days)):
        c, p = closes[i], closes[i - n]
        out[days[i][0]] = 1 if c > p else -1 if c < p else 0
    return out


def signals_ema(days, fast, slow):
    out, ef, es, prev = {}, None, None, 0
    for d in days:
        if d[0] < WARM_FROM:
            continue
        c = d[4]
        ef = c if ef is None else ef + (c - ef) * 2 / (fast + 1)
        es = c if es is None else es + (c - es) * 2 / (slow + 1)
        prev = 1 if ef > es else -1 if ef < es else prev
        out[d[0]] = prev
    return out


def vol_fraction(days):
    closes = [d[4] for d in days]
    out = {}
    for i in range(30, len(days)):
        r = [math.log(closes[j] / closes[j - 1]) for j in range(i - 29, i + 1)]
        m = sum(r) / 30
        sd = math.sqrt(sum((x - m) ** 2 for x in r) / 29) * math.sqrt(365)
        out[days[i][0]] = min(0.5, 0.20 / sd) if sd > 0 else 0.5
    return out


def run(bars, days, sig, frac, fee=0.0005, slip=0.0002):
    """Hourly mark-to-market book; daily fills at the 00:00 UTC open."""
    fund = {}
    for ft, rate, mark in DATA["funding"]:
        fund[ft // HOUR * HOUR] = (rate, mark)
    opens = {b[0]: b[1] for b in bars}
    cash, units, entry_px, side = 10_000.0, 0.0, 0.0, 0
    curve, trades, fees = [], [], 0.0
    entry_t, fee_in = None, 0.0
    for t, o, h, l, c in bars:
        if t < START or t >= END:
            continue
        if t in fund and units:  # funding settles before the daily fill
            rate, mark = fund[t]
            cash -= units * (mark or opens[t]) * rate
        if t % DAY == 0:
            want = sig.get(t - DAY, 0)
            if want != side:
                if units:
                    px = o * (1 - slip) if units > 0 else o * (1 + slip)
                    pnl = units * (px - entry_px)
                    f = abs(units) * px * fee
                    cash += pnl - f; fees += f
                    trades.append({"side": side, "in": entry_t, "out": t, "pnl": pnl - f - fee_in})
                    units, side = 0.0, 0
                if want:
                    px = o * (1 + slip) if want > 0 else o * (1 - slip)
                    notional = frac.get(t - DAY, 0.5) * cash
                    units = want * notional / px
                    f = notional * fee
                    cash -= f; fees += f
                    entry_px, side, entry_t, fee_in = px, want, t, f
        eq = cash + units * (c - entry_px) if units else cash
        curve.append((t, eq))
    return curve, trades, fees, side



def stats(curve, trades):
    peak, dd = curve[0][1], 0.0
    for _, e in curve:
        peak = max(peak, e); dd = max(dd, 1 - e / peak)
    final = curve[-1][1]
    def at(ts):
        prev = curve[0][1]
        for t, e in curve:
            if t >= ts:
                return prev
            prev = e
        return prev
    yearly = {}
    for y in range(2021, 2027):
        a = at(int(datetime(y, 1, 1, tzinfo=timezone.utc).timestamp() * 1000))
        b = at(int(datetime(y + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)) if y < 2026 else final
        yearly[y] = round((b / a - 1) * 100, 2)
    six = at(END - 182 * DAY)
    octo = at(OCT)
    wins = sorted((t["pnl"] for t in trades), reverse=True)
    return {"net": round((final / 10_000 - 1) * 100, 2), "dd": round(dd * 100, 2), "closed": len(trades),
            "longs": sum(t["side"] > 0 for t in trades), "shorts": sum(t["side"] < 0 for t in trades),
            "six_months": round((final / six - 1) * 100, 2), "october_colombo": round((final / octo - 1) * 100, 2),
            "best_trade_usdt": round(wins[0], 0) if wins else 0,
            "top3_share_of_profit": round(sum(wins[:3]) / max(1e-9, final - 10_000) * 100, 1),
            "yearly": yearly}


def main():
    global DATA
    DATA = load()
    bars = [b for b in DATA["bars"]]
    days = daily(bars)
    frac = vol_fraction(days)
    books = {"Momentum 60d": signals_momentum(days, 60), "EMA5/100": signals_ema(days, 5, 100),
             "EMA10/100": signals_ema(days, 10, 100), "EMA20/50 (old baseline)": signals_ema(days, 20, 50)}
    report = {"data": {"hourly_bars": len(bars), "first": bars[0][0], "last": bars[-1][0],
                       "funding_rows": len(DATA["funding"]),
                       "funding_without_mark": sum(1 for f in DATA["funding"] if not f[2])}, "books": {}}
    for name, sig in books.items():
        row = {}
        for label, k in (("base", 1), ("double_costs", 2)):
            curve, trades, fees, open_side = run(bars, days, sig, frac, 0.0005 * k, 0.0002 * k)
            row[label] = stats(curve, trades)
            row[label]["open_at_cutoff"] = open_side
        report["books"][name] = row
    hold = {d[0]: 1 for d in days}
    curve, trades, _, _ = run(bars, days, hold, {d[0]: 0.5 for d in days})
    report["books"]["Passive 50% long"] = {"base": stats(curve, trades)}
    plateau = {}
    for n in (20, 30, 40, 45, 50, 55, 60, 65, 70, 75, 80, 90, 120, 180):
        curve, trades, _, _ = run(bars, days, signals_momentum(days, n), frac)
        s = stats(curve, trades)
        plateau[f"momentum {n}d"] = {k: s[k] for k in ("net", "dd", "closed")}
    for f_, s_ in ((3, 100), (4, 100), (5, 80), (5, 90), (5, 100), (5, 110), (5, 120), (6, 100), (7, 100), (5, 150), (5, 200)):
        curve, trades, _, _ = run(bars, days, signals_ema(days, f_, s_), frac)
        s = stats(curve, trades)
        plateau[f"EMA{f_}/{s_}"] = {k: s[k] for k in ("net", "dd", "closed")}
    report["neighbours_base_costs"] = plateau
    out = PROJECT / "docs" / "BTC_ROUND2_CHECK.json"
    out.write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
