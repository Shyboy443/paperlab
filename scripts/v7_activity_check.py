"""Recent Bybit setup count only. No PnL optimization or live experiment mutation.

Uses settled historical positioning, not recorded arrival times. Counts raw setups,
not executed trades (overlapping positions and engine rejections can reduce activity).
"""
import datetime as dt
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.backtest import bybit_archive as bb
from app.backtest.replay import ReplayEngine
from app.competition import v6_config as base
from app.competition.v6_features import PositioningFeedV6
from app.core.types import Candle, MarketRules
from app.strategies.v7.active import load_v7

DAY = 86_400_000
HOUR = 3_600_000


def check_coin(coin, end):
    symbol = coin + "USDT"
    cache = Path("data/v7_activity") / f"{coin}-{end}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        data = json.loads(cache.read_text())
    else:
        data = {tf: bb.klines(symbol, interval, end - 35 * DAY, end)
                for tf, interval in (("15m", "15"), ("1h", "60"), ("4h", "240"))}
        data["funding"] = bb.funding(symbol, end - 95 * DAY, end)
        data["oi"] = bb.open_interest(symbol, end - 30 * DAY, end)
        cache.write_text(json.dumps(data))
    start = end - 7 * DAY
    caps = {t: {"oi": t, "funding": t} for t in range(start, end + HOUR, HOUR)}
    feed = PositioningFeedV6(symbol, funding=data["funding"], oi=data["oi"], caps=caps, funding_interval_h=8)
    rules = {symbol: MarketRules(**base.load_freeze()["rules"][symbol])}
    ctx = ReplayEngine(base.settings_v6(), [symbol], rules=rules).ctx
    candles = []
    for tf, step in (("15m", 900000), ("1h", HOUR), ("4h", 4 * HOUR)):
        for row in data[tf]:
            ts = int(row[0])
            candles.append(Candle(symbol, tf, ts, *map(float, row[1:6]), ts + step - 1, True))
    # Context closes at the same instant are delivered before the signal bar.
    candles.sort(key=lambda c: (c.close_time, {"4h": 0, "1h": 1, "15m": 2}[c.tf]))
    strategies = {k: cls.for_class("ACTIVE", feed)() for k, cls in load_v7().items()}
    counts = {k: 0 for k in strategies}
    legal = {k: 0 for k in strategies}
    sizing = base.SizingV6(rules, jev=False)
    for c in candles:
        ctx.push(c)
        ctx.set_price(symbol, c.close, c.close_time)
        if c.tf != "15m" or c.close_time + 1 < start:
            continue
        for k, st in strategies.items():
            for sig in st.on_candle(c, ctx):
                counts[k] += 1
                mult, _ = sizing(sig, {"equity": 20.0, "peak": 20.0, "r": []})
                legal[k] += int(mult > 0)
    return {"coin": coin, "setups": counts, "size_eligible_at_20_usdt": legal}


if __name__ == "__main__":
    end = int(dt.datetime(2026, 9, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
    coins = base.load_freeze()["universe"]["traded"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda c: check_coin(c, end), coins))
    report = {"window": "2026-09-18..2026-09-25 UTC", "days": 7, "results": results,
              "raw_setups": sum(sum(r["setups"].values()) for r in results),
              "note": "Setup counts only; NOT executed trades, returns or proof of improvement. Historical arrival times unavailable."}
    Path("docs/V7_ACTIVITY_CHECK.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
