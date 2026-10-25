"""Shared helpers for the monthly-20% research round. Public Binance market data only.

Every agent imports this so the 10 agents share one download cache and one set of rules for
the windows, costs and the monthly report. Nothing here can place an order or read a key.

    import sys; sys.path.insert(0, r"D:/Projects/BinanceBOt/paperlab/research/monthly20")
    from common import *
"""
from __future__ import annotations
import json, math, os, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT))
from app.autoresearch.client import Client, RateLimit  # noqa: E402

MIN = 60_000
HOUR = 60 * MIN
DAY = 24 * HOUR
INTERVAL_MS = {"1m": MIN, "5m": 5 * MIN, "15m": 15 * MIN, "30m": 30 * MIN, "1h": HOUR, "2h": 2 * HOUR,
               "4h": 4 * HOUR, "6h": 6 * HOUR, "12h": 12 * HOUR, "1d": DAY}


def utc(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


# The windows every agent uses. Design and tune ONLY on DEV. TEST is touched once, after PREREG.md is written.
DATA_FROM = utc(2020, 1, 1)
DEV_START, DEV_END = utc(2021, 1, 1), utc(2024, 11, 1)
TEST_START, TEST_END = utc(2024, 11, 1), utc(2026, 10, 22)
# Baseline costs per side; report every result again with both doubled (the "stress" run).
FEE, SLIP = 0.0005, 0.0002
MAINT_MARGIN = 0.005  # a leveraged position is liquidated when its loss reaches 1/leverage minus this

CACHE = PROJECT / "data" / "monthly20_cache"
_client, _lock = None, threading.Lock()


def client():
    global _client
    with _lock:
        if _client is None:
            _client = Client(threading.Event())
        return _client


def _get(venue, endpoint, params, weight=5):
    for attempt in range(6):
        try:
            return client().get(venue, endpoint, params, weight=weight)
        except RateLimit as e:
            time.sleep(min(e.seconds, 600))
        except RuntimeError:
            if attempt == 5:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("market data unavailable")


def _save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    os.replace(tmp, path)  # atomic, so agents reading the same cache never see half a file


def klines(symbol, interval="1h", venue="perp", start=DATA_FROM, end=TEST_END):
    """Closed OHLCV bars [open_ms, o, h, l, c, base_vol, quote_vol, trades, taker_buy_quote] from the shared cache.

    The cache holds DATA_FROM..TEST_END per symbol and interval; slices are cut from it.
    A symbol that did not exist yet simply starts later. Delisted symbols are not on Binance any more
    (survivorship bias: say so in the report)."""
    step = INTERVAL_MS[interval]
    path = CACHE / venue / interval / f"{symbol}.json"
    if path.exists():
        rows = json.loads(path.read_text())
    else:
        rows, cur = [], DATA_FROM
        while cur < TEST_END:
            part = _get(venue, "klines", {"symbol": symbol, "interval": interval, "startTime": cur,
                                          "endTime": TEST_END - 1, "limit": 1000 if venue == "spot" else 1500},
                        weight=10)
            if not part:
                break
            rows += [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[7]),
                      int(r[8]), float(r[10])] for r in part if int(r[0]) + step <= TEST_END]
            nxt = int(part[-1][0]) + step
            if nxt <= cur:
                raise ValueError("kline pagination made no progress")
            cur = nxt
            if len(part) < (1000 if venue == "spot" else 1500):
                break
        _save(path, rows)
    return [r for r in rows if start <= r[0] < end]


def funding(symbol, start=DATA_FROM, end=TEST_END):
    """[(funding_ms, rate)] for a USDT perp. A long pays rate x notional at each stamp (a short receives it)."""
    path = CACHE / "funding" / f"{symbol}.json"
    if path.exists():
        rows = json.loads(path.read_text())
    else:
        rows = [[t, r] for t, r, _ in client().funding(symbol, DATA_FROM, TEST_END)]
        _save(path, rows)
    return [(t, r) for t, r in rows if start <= t < end]


def perp_symbols():
    """USDT perpetuals trading today with their listing time (onboardDate). Today's list only: no delisted coins."""
    path = CACHE / "perp_symbols.json"
    if path.exists():
        return json.loads(path.read_text())
    info = _get("perp", "exchangeInfo", None, weight=20)
    out = [{"symbol": s["symbol"], "base": s["baseAsset"], "onboard": int(s.get("onboardDate", 0))}
           for s in info["symbols"] if s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL"
           and s.get("status") == "TRADING"]
    _save(path, out)
    return out


def liquid_universe(n=40, at=DEV_START, lookback_days=30, interval="1d"):
    """The n perps with the highest quote volume over the lookback before `at`, using only data known at `at`.

    Re-pick the universe at each rebalance with the rebalance time as `at` to avoid look-ahead."""
    ranked = []
    for s in perp_symbols():
        if s["onboard"] and s["onboard"] > at - lookback_days * DAY:
            continue
        bars = klines(s["symbol"], interval, start=at - lookback_days * DAY, end=at)
        if len(bars) >= lookback_days * 0.8:
            ranked.append((sum(b[6] for b in bars), s["symbol"]))
    return [sym for _, sym in sorted(ranked, reverse=True)[:n]]


def monthly_returns(curve):
    """curve = [(ms, equity)] sorted. Returns {"YYYY-MM": % return} by calendar month (UTC)."""
    out, start_eq, cur_key, last = {}, None, None, None
    for t, e in curve:
        key = datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m")
        if key != cur_key:
            if cur_key is not None:
                out[cur_key] = (last / start_eq - 1) * 100
            cur_key, start_eq = key, (last if last is not None else e)
        last = e
    if cur_key is not None:
        out[cur_key] = (last / start_eq - 1) * 100
    return out


def summary(curve, trades_pnl=None):
    """The numbers every report must show for a window. trades_pnl = list of per-trade P&L (any unit)."""
    months = monthly_returns(curve)
    vals = list(months.values())
    peak, dd = curve[0][1], 0.0
    for _, e in curve:
        peak = max(peak, e)
        dd = max(dd, 1 - e / peak if peak > 0 else 1)
    n = len(vals)
    geo = ((curve[-1][1] / curve[0][1]) ** (1 / n) - 1) * 100 if n and curve[0][1] > 0 and curve[-1][1] > 0 else -100.0
    srt = sorted(vals)
    out = {"months": {k: round(v, 2) for k, v in months.items()}, "n_months": n,
           "mean_month": round(sum(vals) / n, 2) if n else None,
           "median_month": round(srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2, 2) if n else None,
           "geo_month": round(geo, 2), "worst_month": round(min(vals), 2) if n else None,
           "best_month": round(max(vals), 2) if n else None, "months_up": sum(v > 0 for v in vals),
           "net_pct": round((curve[-1][1] / curve[0][1] - 1) * 100, 2), "max_dd_pct": round(dd * 100, 2)}
    if trades_pnl is not None:
        p = sorted(trades_pnl, reverse=True)
        total = sum(p)
        out.update({"trades": len(p), "win_rate": round(sum(x > 0 for x in p) / len(p) * 100, 1) if p else 0,
                    "top3_share_of_profit": round(sum(p[:3]) / total * 100, 1) if total > 0 else None})
    return out
