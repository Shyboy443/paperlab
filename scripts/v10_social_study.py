"""V10 Reddit study (pre-registered in docs/V10_PROTOCOL.md): run once scout.db holds >= 14 days.

    python scripts/v10_social_study.py [--days 14]       (on the server: scout.db + the Alpaca paper keys for stock bars)

An EVENT is a Reddit attention spike the scout saw live: in a 5-minute bucket, the symbol's posts + comments over the
last hour are >= 3x its normal hour (trailing 7 days, at least 3 days of history) and >= 10; one event per symbol per
2 hours. The trade enters at the open of the first 5m bar >= the bucket's end + 60 s and is measured 60 minutes later
(stocks: same regular session). Verdicts use the pass rules frozen in the protocol.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

sys.path.insert(0, str(PROJECT / "scripts"))
from v10_news_study import COST, M5, H, DAY, crypto_bars, forward, stock_bars, tstat  # noqa: E402

from app.exchange.alpaca_client import AlpacaClient  # noqa: E402
from app.live.alpaca_market import Sessions, iso  # noqa: E402
from app.live.v9_worker import alpaca_keys  # noqa: E402
from app.scout import text  # noqa: E402
from app.scout.store import ScoutStore  # noqa: E402

SPIKE, MIN_1H, GAP_MS, HIST_MS = 3.0, 10, 2 * H, 3 * DAY
PASS = {"vol_ratio": 1.3, "min_events_half": 50, "min_toned_half": 40, "t_stat": 2.5, "tone_min": 0.3}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    store = ScoutStore(os.path.join(os.environ.get("DATA_DIR") or "data", "scout.db"))
    end = int(time.time() * 1000) // M5 * M5 - H
    start = end - args.days * DAY
    rows = store.features(start - 7 * DAY)
    first = min((r["bucket_ms"] for r in rows), default=end)
    if end - first < args.days * DAY:
        raise SystemExit(f"only {(end - first) / DAY:.1f} days collected; the study needs {args.days}")
    keys = alpaca_keys()
    client = AlpacaClient("testnet", *keys) if keys else None
    cal = asyncio.run(client.request("GET", f"/v2/calendar?start={iso(start)[:10]}&end={iso(end)[:10]}")) if client else []
    sessions = Sessions.from_calendar(cal)
    mid = (start + end) // 2
    by: dict[str, dict[int, dict]] = {}
    for r in rows:
        by.setdefault(r["symbol"], {})[r["bucket_ms"]] = r
    report = {"window": {"from": iso(start), "to": iso(end), "days": args.days}, "rules": {"spike": SPIKE, "min_1h": MIN_1H},
              "pass_rules": PASS, "markets": {}}
    for market in ("crypto", "stocks"):
        if market == "stocks" and client is None:
            continue
        ev = {"A": {0: [], 1: []}, "B": {0: [], 1: []}}
        base = {0: [], 1: []}
        n_events = 0
        for s, (mk, _n, _t) in text.UNIVERSE.items():
            if mk != market:
                continue
            bars = crypto_bars(s, start - DAY, end + DAY) if market == "crypto" else asyncio.run(stock_bars(client, s, start - DAY, end + DAY))
            starts = [b[0] for b in bars]
            sess = sessions if market == "stocks" else None
            for k in range(0, len(bars), 3):
                if start <= bars[k][0] < end:
                    r = forward(bars, starts, bars[k][0], H, sess)
                    if r is not None:
                        base[int(bars[k][0] >= mid)].append(abs(r))
            f = by.get(s, {})
            last_ev = -GAP_MS
            for b in range(start, end, M5):
                win = [f[x] for x in range(b - H + M5, b + M5, M5) if x in f]
                att = sum(x["posts"] + x["comments"] for x in win)
                hist = [f[x]["posts"] + f[x]["comments"] for x in f if b - 7 * DAY <= x < b - H + M5]
                if b - first < HIST_MS or not hist:
                    continue
                normal = sum(hist) / ((b - H + M5 - max(first, b - 7 * DAY)) / H)
                if att < MIN_1H or not normal or att / normal < SPIKE or b - last_ev < GAP_MS:
                    continue
                last_ev = b
                seen_at = b + M5 + 60_000                       # the bucket closes, then the bots' 60 s delay
                r = forward(bars, starts, (seen_at + M5 - 1) // M5 * M5, H, sess)
                if r is None:
                    continue
                n_events += 1
                half = int(b >= mid)
                ev["A"][half].append(abs(r))
                tones = [x["tone_reddit"] for x in win if x["tone_reddit"] is not None]
                tone = statistics.fmean(tones) if tones else None
                if tone is not None and abs(tone) >= PASS["tone_min"]:
                    ev["B"][half].append((1 if tone > 0 else -1) * r - COST[market])
        halves = [{"events": len(ev["A"][h]), "vol_ratio": round(statistics.fmean(ev["A"][h]) / statistics.fmean(base[h]), 3)
                   if ev["A"][h] and base[h] else None, "toned": len(ev["B"][h]),
                   "net_mean_bps": round(statistics.fmean(ev["B"][h]) * 1e4, 2) if ev["B"][h] else None} for h in (0, 1)]
        both = ev["B"][0] + ev["B"][1]
        report["markets"][market] = {
            "events": n_events, "halves": halves, "t_all": round(tstat(both), 2) if tstat(both) is not None else None,
            "verdict": {"attention_predicts_volatility": all(x["events"] >= PASS["min_events_half"] and (x["vol_ratio"] or 0) >= PASS["vol_ratio"] for x in halves),
                        "tone_predicts_direction": all(x["toned"] >= PASS["min_toned_half"] and (x["net_mean_bps"] or 0) > 0 for x in halves)
                        and (tstat(both) or 0) >= PASS["t_stat"]}}
    out = PROJECT / "docs" / "V10_SOCIAL_STUDY.json"
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
