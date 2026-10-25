"""MACD trio study (pre-registered in docs/MACD_TRIO_STUDY.md): pick 3 coins for the MACD rule, long and short.

    python scripts/macd_trio_study.py [--cache DIR]

Public Binance USD-M data only (klines, funding history, exchange info, 24h tickers). Writes docs/macd_trio/study.json
and the result table into docs/MACD_TRIO_STUDY.md is done by hand from that file.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.autoresearch.client import Client  # noqa: E402
from app.macdtrio import sim  # noqa: E402

ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
WARM, Y1, Y2, END = ms("2024-08-15T00:00"), ms("2024-10-11T00:00"), ms("2025-10-11T00:00"), ms("2026-10-11T00:00")
LISTED_BY = ms("2024-08-01T00:00")
UNIVERSE_N, CANDIDATES, PICK = 60, 10, 3
EXCLUDE = {"ZECUSDT", "USDCUSDT", "FDUSDUSDT", "BTCDOMUSDT", "DEFIUSDT", "BLUEBIRDUSDT", "TUSDUSDT", "USDPUSDT"}
H4 = sim.H4
OUT = PROJECT / "docs" / "macd_trio"


def universe(c: Client) -> list[str]:
    info = c.get("perp", "exchangeInfo", {}, weight=1)
    ok = {s["symbol"] for s in info["symbols"] if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING"
          and s.get("quoteAsset") == "USDT" and int(s.get("onboardDate") or 0) <= LISTED_BY and s["symbol"] not in EXCLUDE}
    vol = sorted(((float(t["quoteVolume"]), t["symbol"]) for t in c.get("perp", "ticker/24hr", {}, weight=40) if t["symbol"] in ok),
                 reverse=True)
    return [s for _, s in vol[:UNIVERSE_N]]


def klines(c: Client, sym: str) -> list[dict]:
    out, cur = {}, WARM
    while cur < END:
        rows = c.get("perp", "klines", {"symbol": sym, "interval": "4h", "startTime": cur, "endTime": END - 1, "limit": 1500}, weight=5)
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = {"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])}
        nxt = int(rows[-1][0]) + H4
        if nxt <= cur:
            break
        cur = nxt
    return [out[t] for t in sorted(out)]


def funding(c: Client, sym: str) -> list[tuple[int, float, float]]:
    out, cur = {}, Y1
    while cur < END:
        rows = c.get("perp", "fundingRate", {"symbol": sym, "startTime": cur, "endTime": END - 1, "limit": 1000}, weight=1)
        if not rows:
            break
        for r in rows:
            out[int(r["fundingTime"])] = (int(r["fundingTime"]), float(r["fundingRate"]), float(r.get("markPrice") or 0) or None)
        nxt = int(rows[-1]["fundingTime"]) + 1
        if nxt <= cur:
            break
        cur = nxt
    return [out[t] for t in sorted(out)]


def fill_marks(fund, bars):
    """Settlements without a recorded mark price use the 4h candle's open at or before them."""
    opens = {b["t"]: b["o"] for b in bars}
    res = []
    for t, r, m in fund:
        if not m:
            m = opens.get(t // H4 * H4)
        if m:
            res.append((t, r, m))
    return res


def contiguous(bars):
    return all(b["t"] - a["t"] == H4 for a, b in zip(bars, bars[1:]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(PROJECT / "data" / "macd_trio_cache"))
    a = ap.parse_args()
    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    c = Client(threading.Event())
    uni_file = cache / "universe.json"
    if uni_file.exists():
        uni = json.loads(uni_file.read_text())
    else:
        uni = universe(c)
        uni_file.write_text(json.dumps(uni))
    rows, skipped = [], []
    for sym in uni:
        f = cache / f"{sym}.json"
        if f.exists():
            d = json.loads(f.read_text())
        else:
            d = {"bars": klines(c, sym), "funding": funding(c, sym)}
            f.write_text(json.dumps(d))
        bars, fund = d["bars"], fill_marks([tuple(x) for x in d["funding"]], d["bars"])
        if not bars or bars[0]["t"] > Y1 - 60 * H4 or bars[-1]["t"] < END - H4 or not contiguous(bars):
            skipped.append(sym)
            continue
        r1 = sim.simulate(bars, fund, Y1, Y2)
        r2 = sim.simulate(bars, fund, Y2, END)
        bh2 = next(b["o"] for b in bars if b["t"] >= Y2)
        bh2 = (bars[-1]["c"] / bh2 - 1) * 100
        rows.append({"symbol": sym, "y1": r1["metrics"], "y2": r2["metrics"], "y2_buy_hold_pct": bh2})
        print(f"{sym:>14} Y1 {r1['metrics']['net_return_pct']:+9.1f}%  Y2 {r2['metrics']['net_return_pct']:+8.1f}%  "
              f"(Y2 trades {r2['metrics']['trades']}, PF {r2['metrics']['profit_factor'] or 0:.2f}, DD {r2['metrics']['max_drawdown_pct']:.1f}%)",
              flush=True)
    rows.sort(key=lambda r: r["y1"]["net_return_pct"], reverse=True)
    cands = rows[:CANDIDATES]

    def passes(m):
        return (m["net_return_pct"] > 0 and (m["profit_factor"] or 0) >= 1.2 and m["max_drawdown_pct"] <= 50
                and m["trades"] >= 8)
    for r in cands:
        r["passes_y2"] = passes(r["y2"])
    picked = [r["symbol"] for r in cands if r["passes_y2"]][:PICK]
    med_all = statistics.median(r["y2"]["net_return_pct"] for r in rows)
    med_c = statistics.median(r["y2"]["net_return_pct"] for r in cands)
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "universe": uni, "skipped": skipped,
           "coins_tested": len(rows), "candidates": [{"symbol": r["symbol"], "y1": r["y1"], "y2": r["y2"],
                                                     "y2_buy_hold_pct": r["y2_buy_hold_pct"], "passes_y2": r["passes_y2"]}
                                                    for r in cands],
           "picked": picked, "skill_check": {"y2_median_candidates_pct": med_c, "y2_median_all_pct": med_all,
                                             "candidates_better": med_c > med_all},
           "all": [{"symbol": r["symbol"], "y1_pct": r["y1"]["net_return_pct"], "y2_pct": r["y2"]["net_return_pct"],
                    "y2_pf": r["y2"]["profit_factor"], "y2_dd_pct": r["y2"]["max_drawdown_pct"], "y2_trades": r["y2"]["trades"]}
                   for r in rows]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "study.json").write_text(json.dumps(out, indent=1))
    print("\ncandidates (top 10 by Y1):")
    for r in cands:
        print(f"  {r['symbol']:>14} Y1 {r['y1']['net_return_pct']:+8.1f}%  Y2 {r['y2']['net_return_pct']:+8.1f}%  "
              f"PF {r['y2']['profit_factor'] or 0:.2f}  DD {r['y2']['max_drawdown_pct']:.1f}%  trades {r['y2']['trades']}  "
              f"B&H {r['y2_buy_hold_pct']:+.1f}%  {'PASS' if r['passes_y2'] else 'fail'}")
    print("picked:", picked)
    print(f"skill check: Y2 median candidates {med_c:+.1f}% vs all {med_all:+.1f}% -> {'better' if med_c > med_all else 'NOT better'}")
    print("skipped:", skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
