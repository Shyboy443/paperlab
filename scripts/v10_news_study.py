"""V10 news study (pre-registered in docs/V10_PROTOCOL.md): did Benzinga news predict our symbols' next hour?

    python scripts/v10_news_study.py [--days 180] [--jev]      (on the server: needs the Alpaca paper keys)

For every (headline, symbol) in the window the "trade" enters at the open of the first 5m bar starting at least 60 s
after publication (the bots' own delay) and is measured 60 minutes later. Stocks count only when both ends fall in
the same regular session. Baseline: every 5m bar start in the same window.

    A. attention -> volatility   mean |60m return| after news / baseline, per market, in both halves of the window
    B. tone -> direction         sign(tone) x 60m return for |tone| >= 0.3, net of the round-trip cost, t-stat, halves

Tone is Jev's (with --jev; cached in scout.db) or the word list. The verdicts use the pass rules frozen in the
protocol; nothing here is tuned.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import time
import urllib.parse
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest import bybit_archive as bb  # noqa: E402
from app.exchange.alpaca_client import AlpacaClient  # noqa: E402
from app.live.alpaca_market import Sessions, iso, parse_ts  # noqa: E402
from app.live.v9_worker import alpaca_keys  # noqa: E402
from app.scout import jev_tone, text  # noqa: E402
from app.scout.sources import fetch_news  # noqa: E402
from app.scout.store import ScoutStore  # noqa: E402

M5 = 300_000
H = 3_600_000
DAY = 24 * H
DELAY_MS = 60_000
COST = {"crypto": 0.0013, "stocks": 0.0002}       # round trip: Bybit taker 2 x 0.055% + spread; stocks: spread + fees
PASS = {"vol_ratio": 1.3, "min_events_half": 200, "min_toned_half": 100, "t_stat": 2.5, "tone_min": 0.3}


def crypto_bars(coin: str, start: int, end: int) -> list[tuple[int, float, float]]:
    rows = bb.klines(coin + "USDT", "5", start, end)
    return [(int(r[0]), float(r[1]), float(r[4])) for r in rows]


async def stock_bars(client: AlpacaClient, sym: str, start: int, end: int) -> list[tuple[int, float, float]]:
    out, token = [], None
    for _ in range(400):
        q = {"symbols": sym, "timeframe": "5Min", "start": iso(start), "end": iso(end), "limit": 10000, "feed": "iex",
             "adjustment": "raw", "sort": "asc"}
        if token:
            q["page_token"] = token
        page = await client.request("GET", "/v2/stocks/bars?" + urllib.parse.urlencode(q), data=True)
        out += [(parse_ts(b["t"]), float(b["o"]), float(b["c"])) for b in ((page or {}).get("bars") or {}).get(sym) or []]
        token = (page or {}).get("next_page_token")
        if not token:
            break
    return out


def forward(bars: list[tuple[int, float, float]], starts: list[int], t: int, horizon: int,
            sessions: Sessions | None) -> float | None:
    """Return from the open of the first bar at or after t to the close of the bar ending at entry + horizon."""
    import bisect
    i = bisect.bisect_left(starts, t)
    if i >= len(bars):
        return None
    e_ts, e_px = bars[i][0], bars[i][1]
    j = bisect.bisect_left(starts, e_ts + horizon - M5)
    if j >= len(bars) or bars[j][0] != e_ts + horizon - M5:
        return None
    if sessions is not None and (sessions.at(e_ts) is None or sessions.at(e_ts) != sessions.at(bars[j][0])):
        return None
    return bars[j][2] / e_px - 1.0 if e_px > 0 else None


def tstat(xs: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    sd = statistics.pstdev(xs)
    return statistics.fmean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--jev", action="store_true", help="score headline tone with Jev (cached in scout.db)")
    args = ap.parse_args()
    keys = alpaca_keys()
    if keys is None:
        raise SystemExit("no Alpaca paper keys")
    client = AlpacaClient("testnet", *keys)
    end = int(time.time() * 1000) // M5 * M5 - H
    start = end - args.days * DAY
    mid = (start + end) // 2
    news = asyncio.run(fetch_news(client, list(text.UNIVERSE), start, end, max_pages=2000))
    cal = asyncio.run(client.request("GET", f"/v2/calendar?start={iso(start)[:10]}&end={iso(end)[:10]}"))
    sessions = Sessions.from_calendar(cal)
    store = ScoutStore(os.path.join(os.environ.get("DATA_DIR") or "data", "scout.db"))
    pairs = [(n, s) for n in news for s in n["symbols"]]
    tones = store.tones([n["id"] for n in news])
    if args.jev:
        from app.ai.jev.client import JevClient
        from app.ai.jev.models import JevConfig
        jc = JevClient(JevConfig.from_env(), os.environ.get("OPENROUTER_API_KEY"))
        todo = [{"item_id": n["id"], "symbol": s, "headline": n["headline"], "summary": n["text"][len(n["headline"]):]}
                for n, s in pairs if (n["id"], s) not in tones]
        cost = 0.0
        for k in range(0, len(todo), jev_tone.BATCH):
            batch = todo[k:k + jev_tone.BATCH]
            res, c = jev_tone.score(jc, batch)
            cost += c
            rows = [(p["item_id"], p["symbol"], t) for p, t in zip(batch, res) if t]
            store.save_tones(rows, int(time.time() * 1000))
            tones.update({(i, s): t["tone"] for i, s, t in rows})
        print(f"Jev tone: {len(todo)} pairs scored, ${cost:.4f}")
    bars = {}
    for s, (market, _n, _t) in text.UNIVERSE.items():
        bars[s] = crypto_bars(s, start - DAY, end + DAY) if market == "crypto" else asyncio.run(stock_bars(client, s, start - DAY, end + DAY))
    starts = {s: [x[0] for x in b] for s, b in bars.items()}
    report = {"window": {"from": iso(start), "to": iso(end), "days": args.days}, "news_items": len(news),
              "pairs": len(pairs), "tone_source": "jev" if args.jev else "word list (Jev where cached)", "pass_rules": PASS,
              "markets": {}}
    for market in ("crypto", "stocks"):
        sess = sessions if market == "stocks" else None
        syms = [s for s, v in text.UNIVERSE.items() if v[0] == market]
        ev = {"A": {0: [], 1: []}, "B": {0: [], 1: []}}
        base = {0: [], 1: []}
        for s in syms:
            b, st = bars[s], starts[s]
            for k in range(0, len(b), 3):                       # baseline: every 15th minute, same horizon rule
                t = b[k][0]
                if start <= t < end:
                    r = forward(b, st, t, H, sess)
                    if r is not None:
                        base[int(t >= mid)].append(abs(r))
        for n, s in pairs:
            if text.UNIVERSE[s][0] != market:
                continue
            t = n["created_ms"] + DELAY_MS
            r = forward(bars[s], starts[s], (t + M5 - 1) // M5 * M5, H, sess)
            if r is None:
                continue
            half = int(n["created_ms"] >= mid)
            ev["A"][half].append(abs(r))
            tone = tones.get((n["id"], s), n.get("tone") if n.get("tone") is not None else text.tone(n["headline"]))
            if tone is not None and abs(tone) >= PASS["tone_min"]:
                ev["B"][half].append((1 if tone > 0 else -1) * r - COST[market])
        halves = []
        for hh in (0, 1):
            a, bse, dirn = ev["A"][hh], base[hh], ev["B"][hh]
            halves.append({"events": len(a), "vol_ratio": round(statistics.fmean(a) / statistics.fmean(bse), 3) if a and bse else None,
                           "toned": len(dirn), "net_mean_bps": round(statistics.fmean(dirn) * 1e4, 2) if dirn else None,
                           "t": round(tstat(dirn), 2) if tstat(dirn) is not None else None})
        both = ev["B"][0] + ev["B"][1]
        vol_pass = all(x["events"] >= PASS["min_events_half"] and (x["vol_ratio"] or 0) >= PASS["vol_ratio"] for x in halves)
        dir_pass = (all(x["toned"] >= PASS["min_toned_half"] and (x["net_mean_bps"] or 0) > 0 for x in halves)
                    and (tstat(both) or 0) >= PASS["t_stat"])
        report["markets"][market] = {"halves": halves, "net_mean_bps_all": round(statistics.fmean(both) * 1e4, 2) if both else None,
                                     "t_all": round(tstat(both), 2) if tstat(both) is not None else None,
                                     "cost_bps": COST[market] * 1e4,
                                     "verdict": {"attention_predicts_volatility": vol_pass, "tone_predicts_direction": dir_pass}}
    out = PROJECT / "docs" / "V10_NEWS_STUDY.json"
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
