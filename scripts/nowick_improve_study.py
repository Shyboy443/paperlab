"""No-Wick improvement study (pre-registered in docs/NOWICK_IMPROVE_STUDY.md).

    python scripts/nowick_improve_study.py

180 days of Binance USD-M 1-minute candles for the desk's 10 coins (public data, cached under data/nowick_study_cache),
6 variants (5m / 15m / 1h x cost floor off / on), realistic trade-through fills for every variant, DEV = first 90 days,
TEST = last 90 days. Writes docs/nowick_study/study.json.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.autoresearch.client import Client  # noqa: E402
from app.divergence.market import UNIVERSE  # noqa: E402
from app.nowick import model  # noqa: E402

ms = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
END = ms("2026-10-17T00:00")
DAY = 86_400_000
START, MID = END - 180 * DAY, END - 90 * DAY
WARM = START - 3 * DAY                       # EMAs / ATR / levels warm up before the window
MINUTE = 60_000
VARIANTS = [(tf, floor) for tf in ("5m", "15m", "1h") for floor in (0.0, 0.0035)]
TF_MS = {"5m": 5 * MINUTE, "15m": 15 * MINUTE, "1h": 60 * MINUTE}
CACHE = PROJECT / "data" / "nowick_study_cache"
OUT = PROJECT / "docs" / "nowick_study"


def minutes(c: Client, sym: str) -> list[dict]:
    f = CACHE / f"{sym}.json"
    if f.exists():
        return json.loads(f.read_text())
    out, cur = {}, WARM
    while cur < END:
        rows = c.get("perp", "klines", {"symbol": sym, "interval": "1m", "startTime": cur, "endTime": END - 1, "limit": 1500},
                     weight=2)
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = {"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
                              "v": float(r[5])}
        nxt = int(rows[-1][0]) + MINUTE
        if nxt <= cur:
            break
        cur = nxt
    bars = [out[t] for t in sorted(out)]
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(bars))
    return bars


def stats(trades: list[dict]) -> dict:
    s = model.summarize(trades)
    return {k: s[k] for k in ("trades", "closed", "win_rate", "total_r", "avg_r", "profit_factor", "max_drawdown_r")}


def main() -> int:
    c = Client(threading.Event())
    data = {}
    for sym in UNIVERSE:
        data[sym] = minutes(c, sym)
        print(f"{sym}: {len(data[sym])} minutes", flush=True)
    res = {}
    for tf, floor in VARIANTS:
        name = f"{tf}{' +floor' if floor else ''}"
        dev, test, per_coin_test = [], [], {}
        for sym, bars in data.items():
            r = model.simulate(bars, tf, sym, start_ms=START, fill_through=True, min_risk_pct=floor, tf_ms=TF_MS[tf])
            d = [t for t in r["trades"] if t["entry_t"] < MID and t.get("r") is not None]
            te = [t for t in r["trades"] if MID <= t["entry_t"] < END and t.get("r") is not None]
            dev += d
            test += te
            per_coin_test[sym] = sum(t["r"] for t in te)
        res[name] = {"tf": tf, "floor": floor, "dev": stats(dev), "test": stats(test),
                     "test_coins_positive": sum(1 for v in per_coin_test.values() if v > 0), "test_per_coin_r": per_coin_test}
        print(f"{name:>10}  DEV {res[name]['dev']['trades']:>5} trades {res[name]['dev']['total_r']:+8.1f}R PF "
              f"{res[name]['dev']['profit_factor'] or 0:.2f} | TEST {res[name]['test']['trades']:>5} trades "
              f"{res[name]['test']['total_r']:+8.1f}R PF {res[name]['test']['profit_factor'] or 0:.2f}  "
              f"coins+ {res[name]['test_coins_positive']}/10", flush=True)
    eligible = [n for n, v in res.items() if v["dev"]["trades"] >= 30]
    pick = max(eligible, key=lambda n: res[n]["dev"]["total_r"]) if eligible else None
    t = res[pick]["test"] if pick else None
    passed = bool(pick and t["total_r"] > 0 and (t["profit_factor"] or 0) >= 1.2 and t["trades"] >= 30
                  and res[pick]["test_coins_positive"] >= 5)
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "window": [START, MID, END],
           "variants": res, "selected_on_dev": pick, "passed_test": passed}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "study.json").write_text(json.dumps(out, indent=1, default=str))
    print("selected on DEV:", pick, "| TEST pass:", passed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
