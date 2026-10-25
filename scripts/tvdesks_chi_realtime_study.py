"""How CHI trades when every 4h candle is decided AT ITS OWN CLOSE (real time), vs the historical backtest.

    python scripts/tvdesks_chi_realtime_study.py <path to the unpacked TradingView_Research folder>

The source substitutes close[1] for close on the LAST candle (barstate.islast). In a historical run only the final
candle of the dataset is "last", so every past signal used its real close. In real time, the candle being decided is
always the last one, so the substitution applies to EVERY decision -- the forward paper desk decides this way, and
so would any live trading. This study replays the package's recorded 2-year snapshot (84 books, same window, same cash broker, same costs)
with the real-time decisions and compares them with the backtest. Nothing is optimised; the rule is unchanged.

Decision-time values at candle i are computed in one pass from the full-history series (everything before i is
unchanged by the substitution; only candle i's EMAs, Bollinger basis / stdev and therefore the width change come from
close[i-1]). The pass is checked against the exact prefix computation (chi_requests(bars[:i+1])[-1]) on sampled candles.
Writes docs/tvdesks/chi_realtime_study.json and the compact app/tvdesks/history/chi_realtime.json.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.tvdesks import broker as B  # noqa: E402
from app.tvdesks.indicators import chi_requests, chi_series  # noqa: E402
from app.tvdesks.service import DESKS  # noqa: E402
from scripts.tvdesks_build_history import END, START, load_bars  # noqa: E402

OUT = PROJECT / "docs" / "tvdesks" / "chi_realtime_study.json"
APP_OUT = PROJECT / "app" / "tvdesks" / "history" / "chi_realtime.json"     # the compact summary the desk serves


def realtime_requests(bars: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """buy / sell requests as decided at each candle's own close (close[i] replaced by close[i-1] at candle i only)."""
    s = chi_series(bars, islast=False)                                # the real-close history
    close = np.asarray([b["c"] for b in bars], dtype=float)
    n = len(close)
    prev = np.r_[np.nan, close[:-1]]
    a20, a50 = 2 / 21, 2 / 51
    fast = np.r_[np.nan, s["fast"][:-1]] * (1 - a20) + prev * a20
    slow = np.r_[np.nan, s["slow"][:-1]] * (1 - a50) + prev * a50
    basis = np.full(n, np.nan)
    sd = np.full(n, np.nan)
    for i in range(40, n):
        w = np.r_[close[i - 39:i], close[i - 1]]
        basis[i] = w.mean()
        sd[i] = w.std()
    with np.errstate(divide="ignore", invalid="ignore"):
        width = 4 * sd / basis
        prev_w = np.r_[np.nan, s["width"][:-1]]
        roc = (width - prev_w) / prev_w * 100
    prev_roc = np.r_[np.nan, s["roc"][:-1]]
    cross = (roc > 9) & (prev_roc <= 9)
    t = np.asarray([b["t"] for b in bars], dtype=np.int64)
    win = (t >= START) & (t < END)
    buy = (fast > slow) & cross & win
    sell = (s["out"] & win) | ((fast < slow) & cross & win)
    return buy, sell


def main() -> int:
    pkg = Path(sys.argv[1])
    cov = {r["requested"]: r for r in json.loads((pkg / "data-coverage.json").read_text()) if r["status"] == "full_period"}
    rnd = random.Random(20261013)
    books, checked, mism = [], 0, []
    port = {"hist": None, "rt": None}
    for b in DESKS["chi"]["books"]:
        bars = load_bars(pkg, cov[b["asset"]])
        hb, hs, _ = chi_requests(bars, START, END, islast=True)        # the backtest's requests
        rb, rs = realtime_requests(bars)
        t = np.asarray([x["t"] for x in bars])
        idx = np.flatnonzero((t >= START) & (t < END))
        sample = set(rnd.sample(list(idx), 6)) | set(np.flatnonzero(hb | rb)[:4].tolist())
        for i in sorted(sample):                                       # exact prefix decision vs the one-pass value
            pb, ps, _ = chi_requests(bars[:i + 1], START, END, islast=True)
            checked += 1
            if (bool(pb[-1]), bool(ps[-1])) != (bool(rb[i]), bool(rs[i])):
                mism.append({"asset": b["asset"], "index": int(i), "prefix": [bool(pb[-1]), bool(ps[-1])], "one_pass": [bool(rb[i]), bool(rs[i])]})
        h = B.replay(bars, hb, hs, START, END)
        r = B.replay(bars, rb, rs, START, END)
        books.append({"asset": b["asset"], "venue": b["venue"],
                      "buy_requests": {"backtest": int(hb.sum()), "realtime": int(rb.sum()), "both": int((hb & rb).sum())},
                      "sell_requests": {"backtest": int(hs.sum()), "realtime": int(rs.sum())},
                      "backtest": {k: h["metrics"][k] for k in ("net_return_pct", "max_drawdown_pct", "trades")},
                      "realtime": {k: r["metrics"][k] for k in ("net_return_pct", "max_drawdown_pct", "trades")}})
        for k, res in (("hist", h), ("rt", r)):
            if port[k] is None:
                port[k] = {"equity": list(res["equity"]), "high": list(res["high_equity"]), "low": list(res["low_equity"]), "trades": list(res["trades"])}
            else:
                for f, src in (("equity", "equity"), ("high", "high_equity"), ("low", "low_equity")):
                    port[k][f] = [x + y for x, y in zip(port[k][f], res[src])]
                port[k]["trades"] += res["trades"]
        print(f"{b['asset']:>7} buys {int(hb.sum()):>3} -> {int(rb.sum()):>3}  trades {h['metrics']['trades']:>3} -> {r['metrics']['trades']:>3}  "
              f"return {h['metrics']['net_return_pct']:+8.2f}% -> {r['metrics']['net_return_pct']:+8.2f}%", flush=True)
    cap = 10000.0 * len(books)
    pm = {k: B.metrics(port[k]["equity"], port[k]["high"], port[k]["low"], cap, port[k]["trades"]) for k in port}
    out = {"window_utc": ["2024-10-11 00:00", "2026-10-11 00:00 (exclusive)"], "books": len(books), "capital_per_book": 10000.0,
           "costs": {"commission_per_side": B.FEE, "slippage_per_side": B.SLIP},
           "one_pass_check": {"decisions_checked": checked, "mismatches": mism[:20], "mismatch_count": len(mism)},
           "portfolio": {"backtest": pm["hist"], "realtime": pm["rt"]},
           "totals": {"buy_requests_backtest": sum(x["buy_requests"]["backtest"] for x in books),
                      "buy_requests_realtime": sum(x["buy_requests"]["realtime"] for x in books),
                      "buy_requests_both": sum(x["buy_requests"]["both"] for x in books),
                      "realtime_losing_books": sum(1 for x in books if x["realtime"]["net_return_pct"] < 0),
                      "realtime_winning_books": sum(1 for x in books if x["realtime"]["net_return_pct"] > 0),
                      "realtime_books_without_trades": sum(1 for x in books if x["realtime"]["trades"] == 0)},
           "per_book": books, "historical_only": True}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    keep = ("net_return_pct", "max_drawdown_pct", "trades", "win_rate_pct", "profit_factor")
    APP_OUT.write_text(json.dumps({"window_utc": out["window_utc"], "books": out["books"], "totals": out["totals"],
                                   "decisions_checked": checked, "mismatches": len(mism),
                                   "portfolio": {k: {m: v[m] for m in keep} for k, v in out["portfolio"].items()},
                                   "script": "scripts/tvdesks_chi_realtime_study.py", "historical_only": True}, indent=1))
    print(json.dumps({k: out[k] for k in ("one_pass_check", "portfolio", "totals")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
