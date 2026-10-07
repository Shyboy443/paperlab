"""Bizzy Bee study (operator, 2026-10-02: "refer how the bizzy logic was built and add that bot").

Bizzy (github.com/imikerussell/beebots, src/bees/bizzy.ts + strategies/BIZZY_BEE.md, live rules since 2026-09-24) is a
Larry Williams volatility breakout, LONG ONLY, at most ONE trade per UTC day:

    trigger  = today's UTC open + 0.5 x yesterday's high-low range        (src/market/data.ts breakoutLevels, k = 0.5)
    entry    = the first coin of BTC, ETH, SOL, HYPE (that preference order) trading above its trigger
    size     = full size: 2x the book's equity in notional                 (MAX_LEVERAGE 2, sizeFrac 1)
    stop     = today's open ("failed breakout: back below today's open")
    exit     = the UTC day close (1 minute before midnight), unless stopped first
    (in beebots Jev may also decline the breakout, or cut it while it is losing; not modelled here: rules only)

Two views, Bybit linear perps, costs as our arenas pay them (0.055% taker each side + a spread, funding while held):

    A  1m precision, the cached 95 days of data/v11_scan: the signal is a 1m close above the trigger, the fill is the
       next minute's open (our 60 s decision window); stop fills at the level (or the open on a gap); exit at 23:59
    B  ~2.5 years on 1h bars (Bybit API, cached in data/v12_bizzy): entry at the trigger once an hour's high reaches it
       (or that hour's open if it opened above); an entry hour that also trades back to the open counts as stopped

Reported per variant: ALL4 (as beebots), NOBTC (a 20 USDT book at 2x cannot buy Bybit's 0.001 BTC minimum), and each
coin on its own. Informational: Bizzy is added either way (the operator asked); the numbers set the expectation.

    python scripts/v12_bizzy_study.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
DAY, HOUR, MIN = 86_400_000, 3_600_000, 60_000
COINS = ("BTC", "ETH", "SOL", "HYPE")
K = 0.5
LEV = 2.0
TAKER = 0.00055
HALF_SPREAD = {"BTC": 0.00005, "ETH": 0.00005, "SOL": 0.0001, "HYPE": 0.00015}
FUNDING_8H = 0.0001                     # a typical positive rate: a long pays it at 00 / 08 / 16 UTC while held


def cost(coin: str) -> float:
    """Round-trip cost as a fraction of notional: taker in and out + the half spread twice."""
    return 2 * TAKER + 2 * HALF_SPREAD[coin]


def fundings_crossed(t_in: int, t_out: int) -> int:
    return sum(1 for h in (8, 16) if t_in < (t_in // DAY) * DAY + h * HOUR <= t_out)


def trade_ret(coin: str, entry: float, exit_: float, t_in: int, t_out: int) -> float:
    """Return on the BOOK's equity: notional = 2x equity."""
    gross = exit_ / entry - 1.0
    return LEV * (gross - cost(coin) - FUNDING_8H * fundings_crossed(t_in, t_out))


# -- A: 1m precision on the cached 95 days ------------------------------------------------------------------------
def load_1m(coin: str) -> np.ndarray:
    return np.load(PROJECT / "data" / "v11_scan" / f"{coin}USDT.npy")


def day_levels_1m(a: np.ndarray, d0: int):
    prev = a[(a[:, 0] >= d0 - DAY) & (a[:, 0] < d0)]
    today = a[(a[:, 0] >= d0) & (a[:, 0] < d0 + DAY)]
    if len(prev) < 1200 or len(today) < 1200:
        return None
    opn = today[0, 1]
    return today, opn, opn + K * (prev[:, 2].max() - prev[:, 3].min())


def simulate_1m(coins: tuple[str, ...]) -> list[dict]:
    data = {c: load_1m(c) for c in coins}
    first = max(int(a[0, 0]) for a in data.values()) // DAY * DAY + DAY
    last = min(int(a[-1, 0]) for a in data.values()) // DAY * DAY
    out = []
    for d0 in range(first + DAY, last, DAY):
        lv = {c: day_levels_1m(data[c], d0) for c in coins}
        lv = {c: v for c, v in lv.items() if v is not None}
        if not lv:
            continue
        # the first minute (in coin preference order within a minute) whose close is above that coin's trigger
        hit = None
        for m in range(1439 - 2):
            for c in coins:
                if c in lv and m < len(lv[c][0]) - 2 and lv[c][0][m, 4] > lv[c][2]:
                    hit = (c, m)
                    break
            if hit:
                break
        if not hit:
            continue
        c, m = hit
        today, opn, trig = lv[c]
        entry = today[m + 1, 1] * (1 + HALF_SPREAD[c])
        t_in = int(today[m + 1, 0])
        exit_, t_out, kind = today[-1, 1], int(today[-1, 0]), "day_close"     # the 23:59 open
        for j in range(m + 1, len(today) - 1):
            lo = today[j, 3]
            if lo <= opn:
                exit_ = min(today[j, 1], opn)                 # at the stop, or the open on a gap through it
                t_out, kind = int(today[j, 0]), "stop"
                break
        out.append({"day": time.strftime("%Y-%m-%d", time.gmtime(d0 / 1000)), "coin": c, "entry": entry, "exit": exit_,
                    "kind": kind, "ret": trade_ret(c, entry, exit_, t_in, t_out),
                    "stop_pct": (entry - opn) / entry})
    return out


# -- B: ~2.5 years on 1h bars --------------------------------------------------------------------------------------
def load_1h(coin: str, start: int, end: int) -> np.ndarray:
    cache = PROJECT / "data" / "v12_bizzy"
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"{coin}USDT-60-{start}-{end}.json"
    if f.exists():
        raw = json.loads(f.read_text())
    else:
        from app.backtest import bybit_archive as bb
        raw = bb.klines(f"{coin}USDT", "60", start, end)
        f.write_text(json.dumps(raw))
    return np.array([[float(x) for x in r[:6]] for r in raw]) if raw else np.zeros((0, 6))


def simulate_1h(coins: tuple[str, ...], data: dict[str, np.ndarray]) -> list[dict]:
    present = [c for c in coins if len(data[c])]
    first = min(int(data[c][0, 0]) for c in present) // DAY * DAY + 2 * DAY
    last = max(int(data[c][-1, 0]) for c in present) // DAY * DAY
    out = []
    for d0 in range(first, last, DAY):
        lv = {}
        for c in present:
            a = data[c]
            prev = a[(a[:, 0] >= d0 - DAY) & (a[:, 0] < d0)]
            today = a[(a[:, 0] >= d0) & (a[:, 0] < d0 + DAY)]
            if len(prev) >= 20 and len(today) >= 20:
                opn = today[0, 1]
                lv[c] = (today, opn, opn + K * (prev[:, 2].max() - prev[:, 3].min()))
        hit = None
        for h in range(24):
            for c in coins:
                if c in lv and h < len(lv[c][0]) and lv[c][0][h, 2] >= lv[c][2]:
                    hit = (c, h)
                    break
            if hit:
                break
        if not hit:
            continue
        c, h = hit
        today, opn, trig = lv[c]
        bar = today[h]
        entry = max(trig, bar[1]) * (1 + HALF_SPREAD[c] + 0.0002)          # a stop-entry with a little slippage
        t_in = int(bar[0])
        exit_, t_out, kind = today[-1, 4], int(today[-1, 0]) + HOUR - MIN, "day_close"
        if bar[3] <= opn and bar[4] < entry:                              # the entry hour already failed
            exit_, t_out, kind = opn, t_in + HOUR // 2, "stop"
        else:
            for j in range(h + 1, len(today)):
                if today[j, 3] <= opn:
                    exit_ = min(today[j, 1], opn)
                    t_out, kind = int(today[j, 0]), "stop"
                    break
        out.append({"day": time.strftime("%Y-%m-%d", time.gmtime(d0 / 1000)), "coin": c, "entry": entry, "exit": exit_,
                    "kind": kind, "ret": trade_ret(c, entry, exit_, t_in, t_out), "stop_pct": (entry - opn) / entry})
    return out


def summary(tr: list[dict]) -> dict:
    if not tr:
        return {"trades": 0}
    r = np.array([t["ret"] for t in tr])
    eq = np.cumprod(1 + r)
    dd = float((1 - eq / np.maximum.accumulate(eq)).max())
    by_year: dict[str, list[float]] = {}
    for t in tr:
        by_year.setdefault(t["day"][:4], []).append(t["ret"])
    return {"trades": len(tr), "win": round(float((r > 0).mean()), 3), "avg_ret_pct": round(float(r.mean()) * 100, 3),
            "median_ret_pct": round(float(np.median(r)) * 100, 3), "total_ret_pct": round(float(eq[-1] - 1) * 100, 1),
            "max_dd_pct": round(dd * 100, 1), "stops": sum(1 for t in tr if t["kind"] == "stop"),
            "avg_stop_pct": round(float(np.mean([t["stop_pct"] for t in tr])) * 100, 2),
            "by_year_total_pct": {y: round(float(np.prod(1 + np.array(v)) - 1) * 100, 1) for y, v in sorted(by_year.items())},
            "coins": {c: sum(1 for t in tr if t["coin"] == c) for c in COINS}}


def main() -> None:
    report: dict = {"rules": "Larry Williams k=0.5, long only, 1 trade/UTC day, 2x notional, stop = day open, exit 23:59",
                    "costs": {"taker": TAKER, "half_spread": HALF_SPREAD, "funding_8h": FUNDING_8H}}
    a = {}
    for name, coins in (("ALL4", COINS), ("NOBTC", COINS[1:])) + tuple((c, (c,)) for c in COINS):
        a[name] = summary(simulate_1m(coins))
    report["A_1m_95d"] = a
    end = int(time.time() * 1000) // DAY * DAY
    start = end - 900 * DAY
    data = {c: load_1h(c, start, end) for c in COINS}
    b = {}
    for name, coins in (("ALL4", COINS), ("NOBTC", COINS[1:])) + tuple((c, (c,)) for c in COINS):
        b[name] = summary(simulate_1h(coins, data))
    report["B_1h_900d"] = b
    report["window_B"] = [time.strftime("%Y-%m-%d", time.gmtime(start / 1000)), time.strftime("%Y-%m-%d", time.gmtime(end / 1000))]
    (PROJECT / "docs" / "V12_BIZZY_STUDY.json").write_text(json.dumps(report, indent=1))
    for part in ("A_1m_95d", "B_1h_900d"):
        print(part)
        for name, s in report[part].items():
            print(f"  {name:6s} {json.dumps(s)}")


if __name__ == "__main__":
    main()
