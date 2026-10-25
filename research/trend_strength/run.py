"""Trend-strength filter study. Pre-registered 2026-10-22, before any result was seen.

Question (operator): do the two BTC round-2 leaders do better when they only trade
while the market is moving strongly one way, and does that hold on other coins?

Fixed before running (do not change after seeing results):
- Coins: BTC ETH SOL XRP BNB DOGE ADA LINK AVAX LTC, Binance USDT perpetuals listed before 2021.
- Strategies, with the report's exact rules: 60-day momentum and EMA5/100. A completed UTC
  daily close decides, and the fill is at the next daily open. Entry notional is
  min(0.5, 0.20 / annualised 30-day vol). Units stay fixed until the target changes.
- Filters, judged on the completed daily candle and applied from the next open:
    none: the unfiltered strategy
    adx:  in the market only while Wilder ADX(14) >= 25
    er:   in the market only while the 20-day efficiency ratio >= 0.30
  When a filter switches off, the book goes flat. It re-enters in the signal's direction when
  the filter switches back on.
- Test: 2021-01-01 00:00 UTC to 2026-10-22 00:00 UTC. 10,000 USDT per coin book. Fee 0.05% plus
  slippage 0.02% per side, both doubled for the stress run. Funding is charged at each
  settlement on the settlement mark price, or on the 4h open when the mark is missing.
- Verdict: a filter helps a strategy only if both of these hold at doubled costs:
    1. the equal-weight 10-coin portfolio has a higher return / max drawdown than unfiltered;
    2. return / max drawdown improves on at least 6 of the 10 coins.

Public market data only. Nothing here places orders or touches a bot.

    python research/trend_strength/run.py      # then open research/trend_strength/viewer.html
"""
from __future__ import annotations
import json, math, sys, threading
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT))
from app.autoresearch.client import Client  # noqa: E402

HOUR = 3_600_000
BAR = 4 * HOUR
DAY = 24 * HOUR
COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "LINK", "AVAX", "LTC"]
FILTERS = ["none", "adx", "er"]
STRATS = ["mom60", "ema5_100"]
CACHE = PROJECT / "data" / "trend_strength_cache"
OUT = HERE / "output"


def utc(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


FETCH_FROM = utc(2019, 9, 1)
WARM_FROM = utc(2020, 1, 1)
START = utc(2021, 1, 1)
END = utc(2026, 10, 22)


def fetch(client, coin):
    path = CACHE / f"{coin}.json"
    if path.exists():
        return json.loads(path.read_text())
    sym, bars, cur = coin + "USDT", [], FETCH_FROM
    while cur < END:
        part = client.get("perp", "klines", {"symbol": sym, "interval": "4h", "startTime": cur,
                                             "endTime": END - 1, "limit": 1000})
        if not part:
            break
        bars += [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])]
                 for r in part if int(r[0]) + BAR <= END]
        nxt = int(part[-1][0]) + BAR
        if nxt <= cur:
            raise ValueError("kline pagination made no progress")
        cur = nxt
        if len(part) < 1000:
            break
    data = {"bars": bars, "funding": client.funding(sym, FETCH_FROM, END)}
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def daily(bars):
    days = {}
    for t, o, h, l, c in bars:
        d = t // DAY * DAY
        x = days.setdefault(d, {"t": d, "o": None, "h": h, "l": l, "c": c, "n": 0, "last": t})
        x["h"] = max(x["h"], h); x["l"] = min(x["l"], l); x["n"] += 1
        if t == d:
            x["o"] = o
        if t >= x["last"]:
            x["c"] = c; x["last"] = t
    return [[x["t"], x["o"], x["h"], x["l"], x["c"]] for x in sorted(days.values(), key=lambda x: x["t"])
            if x["n"] == 6 and x["o"] is not None]


def momentum(days, n=60):
    out = {}
    for i in range(n, len(days)):
        c, p = days[i][4], days[i - n][4]
        out[days[i][0]] = 1 if c > p else -1 if c < p else 0
    return out


def ema_cross(days, fast=5, slow=100):
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


def adx(days, n=14):
    out, tr_s, p_s, m_s, value, dxs = {}, 0.0, 0.0, 0.0, None, []
    for i in range(1, len(days)):
        _, _, h, l, c = days[i]
        ph, pl, pc = days[i - 1][2], days[i - 1][3], days[i - 1][4]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        up, dn = h - ph, pl - l
        pdm = up if up > dn and up > 0 else 0.0
        mdm = dn if dn > up and dn > 0 else 0.0
        if i <= n:
            tr_s += tr; p_s += pdm; m_s += mdm
            if i < n:
                continue
        else:
            tr_s += tr - tr_s / n; p_s += pdm - p_s / n; m_s += mdm - m_s / n
        pdi = 100 * p_s / tr_s if tr_s else 0.0
        mdi = 100 * m_s / tr_s if tr_s else 0.0
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0
        if value is None:
            dxs.append(dx)
            if len(dxs) == n:
                value = sum(dxs) / n
        else:
            value = (value * (n - 1) + dx) / n
        if value is not None:
            out[days[i][0]] = value
    return out


def efficiency(days, n=20):
    out = {}
    for i in range(n, len(days)):
        path = sum(abs(days[j][4] - days[j - 1][4]) for j in range(i - n + 1, i + 1))
        out[days[i][0]] = abs(days[i][4] - days[i - n][4]) / path if path else 0.0
    return out


def vol_fraction(days):
    out = {}
    for i in range(30, len(days)):
        r = [math.log(days[j][4] / days[j - 1][4]) for j in range(i - 29, i + 1)]
        m = sum(r) / 30
        sd = math.sqrt(sum((x - m) ** 2 for x in r) / 29) * math.sqrt(365)
        out[days[i][0]] = min(0.5, 0.20 / sd) if sd > 0 else 0.5
    return out


def run(bars, funding, sig, frac, fee, slip):
    fund = {ft // HOUR * HOUR: (rate, mark) for ft, rate, mark in funding}
    cash, units, entry_px, side, entry_t, fee_in = 10_000.0, 0.0, 0.0, 0, None, 0.0
    curve, trades, bars_in = [], [], 0
    for t, o, h, l, c in bars:
        if t < START or t >= END:
            continue
        if t in fund and units:  # funding settles before the daily fill
            rate, mark = fund[t]
            cash -= units * (mark or o) * rate
        if t % DAY == 0:
            want = sig.get(t - DAY, 0)
            if want != side:
                if units:
                    px = o * (1 - slip) if units > 0 else o * (1 + slip)
                    f = abs(units) * px * fee
                    pnl = units * (px - entry_px)
                    cash += pnl - f
                    trades.append({"side": side, "in": entry_t, "out": t, "entry": entry_px, "exit": px,
                                   "pnl": round(pnl - f - fee_in, 2)})
                    units, side = 0.0, 0
                if want:
                    px = o * (1 + slip) if want > 0 else o * (1 - slip)
                    notional = frac.get(t - DAY, 0.5) * cash
                    units = want * notional / px
                    fee_in = notional * fee
                    cash -= fee_in
                    entry_px, side, entry_t = px, want, t
        bars_in += units != 0
        curve.append((t, cash + units * (c - entry_px) if units else cash))
    open_pos = {"side": side, "in": entry_t, "entry": entry_px} if units else None
    return curve, trades, bars_in / max(1, len(curve)), open_pos


def stats(curve, trades, exposure, start_eq=10_000.0):
    peak, dd = curve[0][1], 0.0
    for _, e in curve:
        peak = max(peak, e); dd = max(dd, 1 - e / peak)
    final = curve[-1][1]
    marks = {}
    for t, e in curve:
        marks.setdefault(datetime.fromtimestamp(t / 1000, timezone.utc).year, e)
    years = sorted(marks)
    yearly, prev = {}, start_eq
    for y in years:
        nxt = marks.get(y + 1, final)
        yearly[y] = round((nxt / prev - 1) * 100, 2)
        prev = nxt
    span = (curve[-1][0] - curve[0][0]) / (365.25 * DAY)
    net = final / start_eq - 1
    pnls = sorted((t["pnl"] for t in trades), reverse=True)
    return {"net": round(net * 100, 2), "cagr": round(((final / start_eq) ** (1 / span) - 1) * 100, 2),
            "dd": round(dd * 100, 2), "ret_dd": round(net * 100 / (dd * 100), 3) if dd else 0.0,
            "trades": len(trades), "win_rate": round(sum(p > 0 for p in pnls) / len(pnls) * 100, 1) if pnls else 0,
            "exposure": round(exposure * 100, 1), "yearly": yearly,
            "top3_share": round(sum(pnls[:3]) / (final - start_eq) * 100, 1) if final > start_eq and pnls else None}


def main():
    client = Client(threading.Event())
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {"protocol": __doc__.split("Public market data")[0].strip(), "coins": {}, "portfolio": {}}
    view = {"coins": {}, "portfolio": {}, "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "end": END}
    port = {}
    for coin in COINS:
        data = fetch(client, coin)
        bars, funding = data["bars"], data["funding"]
        days = daily(bars)
        frac = vol_fraction(days)
        strong = {"adx": {t: v >= 25 for t, v in adx(days).items()},
                  "er": {t: v >= 0.30 for t, v in efficiency(days).items()}}
        base = {"mom60": momentum(days), "ema5_100": ema_cross(days)}
        vc = {"candles": [d for d in days if d[0] >= START], "books": {}}
        summary["coins"][coin] = {}
        for s in STRATS:
            for f in FILTERS:
                sig = base[s] if f == "none" else {t: (v if strong[f].get(t) else 0) for t, v in base[s].items()}
                key = f"{s}|{f}"
                row = {}
                for cost, k in (("base", 1), ("double", 2)):
                    curve, trades, exposure, open_pos = run(bars, funding, sig, frac, 0.0005 * k, 0.0002 * k)
                    row[cost] = stats(curve, trades, exposure)
                    port.setdefault(f"{key}|{cost}", {})[coin] = curve
                # the viewer shows the doubled-cost run (the last one above), the run the verdict is judged on
                vc["books"][key] = {"stats": row, "trades": trades, "open": open_pos,
                                    "equity": [[t, round(e, 2)] for t, e in curve if t % DAY == 0] +
                                              [[curve[-1][0], round(curve[-1][1], 2)]]}
                summary["coins"][coin][key] = row
        view["coins"][coin] = vc
        print(coin, {k: (v["double"]["net"], v["double"]["dd"]) for k, v in summary["coins"][coin].items()}, flush=True)
    for key, per_coin in port.items():
        stamps = sorted({t for cv in per_coin.values() for t, _ in cv})
        last = {c: 10_000.0 for c in per_coin}
        lookup = {c: dict(cv) for c, cv in per_coin.items()}
        curve = []
        for t in stamps:  # equal weight; a coin missing a bar carries its last equity
            for c in per_coin:
                last[c] = lookup[c].get(t, last[c])
            curve.append((t, sum(last.values()) / len(per_coin)))
        st = stats(curve, [], 0.0)
        st.pop("trades"); st.pop("win_rate"); st.pop("exposure"); st.pop("top3_share")
        s, f, cost = key.split("|")
        summary["portfolio"].setdefault(f"{s}|{f}", {})[cost] = st
        if cost == "double":
            view["portfolio"].setdefault(f"{s}|{f}", {})["equity"] = [[t, round(e, 2)] for t, e in curve if t % DAY == 0] + \
                                                                     [[curve[-1][0], round(curve[-1][1], 2)]]
        view["portfolio"].setdefault(f"{s}|{f}", {})[cost] = st
    verdict = {}
    for s in STRATS:
        base_p = summary["portfolio"][f"{s}|none"]["double"]["ret_dd"]
        for f in ("adx", "er"):
            better = sum(summary["coins"][c][f"{s}|{f}"]["double"]["ret_dd"] > summary["coins"][c][f"{s}|none"]["double"]["ret_dd"]
                         for c in COINS)
            filt_p = summary["portfolio"][f"{s}|{f}"]["double"]["ret_dd"]
            verdict[f"{s}|{f}"] = {"portfolio_ret_dd": filt_p, "unfiltered_ret_dd": base_p, "coins_improved": better,
                                   "helps": filt_p > base_p and better >= 6}
    summary["verdict"] = verdict
    view["verdict"] = verdict
    (OUT / "results.json").write_text(json.dumps(summary, indent=1))
    (OUT / "viewer_data.js").write_text("window.TS_DATA=" + json.dumps(view, separators=(",", ":")) + ";\n")
    print(json.dumps({"portfolio": summary["portfolio"], "verdict": verdict}, indent=1))


if __name__ == "__main__":
    main()
