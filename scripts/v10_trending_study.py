"""V10 CoinGecko-trending study (pre-registered in docs/V10_PROTOCOL.md): run once scout.db holds >= 14 days.

    python scripts/v10_trending_study.py [--days 14]        (on the server; Bybit prices are public)

An EVENT is a coin ENTERING CoinGecko's trending list: on the list in a 5-minute snapshot and absent from every
snapshot of the previous 6 hours. Every such coin that has a Bybit USDT perpetual counts (not only the bots' coins).
The trade enters at the open of the first 5m bar >= the snapshot + 60 s (the bots' delay), LONG (the hypothesis:
fresh attention brings buyers), and is measured 60 minutes later. Verdicts use the pass rules frozen in the protocol.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))
from v10_news_study import DAY, H, M5, crypto_bars, forward, tstat  # noqa: E402

from app.live.alpaca_market import iso  # noqa: E402
from app.scout.store import ScoutStore  # noqa: E402

FRESH_MS = 6 * H
COST = 0.0013                         # Bybit round trip: 2 x 0.055% taker + spread
PASS = {"vol_ratio": 1.3, "min_events_half": 30, "t_stat": 2.5}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    store = ScoutStore(os.path.join(os.environ.get("DATA_DIR") or "data", "scout.db"))
    end = int(time.time() * 1000) // M5 * M5 - H
    start = end - args.days * DAY
    rows = store.trending(start - FRESH_MS)
    if not rows or end - rows[0]["ts"] < args.days * DAY + FRESH_MS:
        have = (end - rows[0]["ts"]) / DAY if rows else 0
        raise SystemExit(f"only {have:.1f} days of trending snapshots; the study needs {args.days} (+6 h)")
    snaps: dict[int, set[str]] = {}
    for r in rows:
        snaps.setdefault(r["ts"], set()).add(r["symbol"])
    times = sorted(snaps)
    events = []
    for i, t in enumerate(times):
        if not start <= t < end:
            continue
        before = [snaps[x] for x in times[:i] if x >= t - FRESH_MS]
        for sym in snaps[t]:
            if not any(sym in b for b in before):
                events.append((t, sym))
    mid = (start + end) // 2
    bars: dict[str, list] = {}
    ev = {"A": {0: [], 1: []}, "B": {0: [], 1: []}}
    base = {0: [], 1: []}
    skipped = set()
    for t, sym in events:
        if sym not in bars:
            try:
                bars[sym] = crypto_bars(sym, start - DAY, end + DAY)
            except Exception:
                bars[sym] = []
            b, st = bars[sym], [x[0] for x in bars[sym]]
            for k in range(0, len(b), 3):
                if start <= b[k][0] < end:
                    r = forward(b, st, b[k][0], H, None)
                    if r is not None:
                        base[int(b[k][0] >= mid)].append(abs(r))
        b = bars[sym]
        if not b:
            skipped.add(sym)
            continue
        r = forward(b, [x[0] for x in b], (t + 60_000 + M5 - 1) // M5 * M5, H, None)
        if r is None:
            continue
        half = int(t >= mid)
        ev["A"][half].append(abs(r))
        ev["B"][half].append(r - COST)
    halves = [{"events": len(ev["A"][h]), "vol_ratio": round(statistics.fmean(ev["A"][h]) / statistics.fmean(base[h]), 3)
               if ev["A"][h] and base[h] else None,
               "net_mean_bps": round(statistics.fmean(ev["B"][h]) * 1e4, 2) if ev["B"][h] else None} for h in (0, 1)]
    both = ev["B"][0] + ev["B"][1]
    report = {"window": {"from": iso(start), "to": iso(end), "days": args.days}, "events": len(events),
              "not_on_bybit": sorted(skipped), "halves": halves, "pass_rules": PASS, "cost_bps": COST * 1e4,
              "t_all": round(tstat(both), 2) if tstat(both) is not None else None,
              "verdict": {"entering_trending_predicts_volatility": all(x["events"] >= PASS["min_events_half"] and (x["vol_ratio"] or 0) >= PASS["vol_ratio"] for x in halves),
                          "entering_trending_predicts_a_rise": all(x["events"] >= PASS["min_events_half"] and (x["net_mean_bps"] or 0) > 0 for x in halves)
                          and (tstat(both) or 0) >= PASS["t_stat"]}}
    out = PROJECT / "docs" / "V10_TRENDING_STUDY.json"
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
