"""V8.3 (VWAP snap-back) hold-time study: is the 45-minute time limit hurting it? (operator, 2026-10-03: "fix the V8.3
time limit issue")

    python scripts/v8_snap_hold_study.py           (needs the research store: research/ingest.py)

Why now: live, 33 of V8.3's first 40 control trades ended at the 45-minute limit. The 2026-10-02 cost study tested "no
time limit" only on the OLD settings (market targets, 0.4% stop: worse) and then adopted resting take-profits + a 1.2%
minimum stop for V8.3 WITHOUT re-testing the hold -- with a 1.2% stop and a 1.8% target, 45 minutes is short (84% of its
study trades ended on the clock).

PRE-REGISTERED (2026-10-03, before the first run):
  Data: the research store's Bybit 1m bars (data/research/market.duckdb, 2026-06-26 .. 2026-10-02) for V8's six coins
  (ARB DOGE ENA ETH SOL XRP), the first 3 days warm-up; first / second half of the rest.
  Bot: V8.3 exactly as it runs live (app/strategies/v8/arena.py: VwapSnapV8 with SnapParams; the live engine
  app/live/v8_engine.LevelMakerEngineV8 with resting take-profits; SizingV6 1% risk; 20 USDT book reset daily).
  Variants (only the hold changes): BASE 45 min | H90 | H180 | H360 | DAYCLOSE (no time limit, out at the stop, the
  target or 1 minute before the UTC midnight -- what V8.1 / V8.2 use since 2026-10-02).
  ADOPT a variant only if its net R per trade beats BASE in BOTH halves; of several, the best whole-window net R per
  trade. Otherwise V8.3 keeps 45 minutes. Reported too (no selection): net USDT, trades per coin-day, win rate, exits.
"""
from __future__ import annotations

import dataclasses
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
DB = PROJECT / "data" / "research" / "market.duckdb"
OUT = PROJECT / "docs" / "V8_SNAP_HOLD_STUDY.json"
DAY = 86_400_000
COINS = ("ARBUSDT", "DOGEUSDT", "ENAUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
VARIANTS = {"BASE": {"max_hold_min": 45.0}, "H90": {"max_hold_min": 90.0}, "H180": {"max_hold_min": 180.0},
            "H360": {"max_hold_min": 360.0}, "DAYCLOSE": {"hold_to_day_close": True}}


def window() -> tuple[int, int]:
    import duckdb
    con = duckdb.connect(str(DB), read_only=True)
    lo, hi = con.execute("SELECT max(lo), min(hi) FROM (SELECT symbol, min(ts) lo, max(ts) hi FROM candles_1m "
                         f"WHERE symbol IN {COINS} GROUP BY 1)").fetchone()
    con.close()
    start = (int(lo) // DAY + 1) * DAY
    end = (int(hi) // DAY) * DAY                     # whole UTC days only
    return start, end


def run(args: tuple) -> dict:
    coin, start, end = args
    import duckdb

    from app.competition import v8_config as v8
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6
    from app.core.types import Candle, MarketRules
    from app.live.v8_engine import LevelMakerEngineV8
    from app.strategies.v8.arena import load_v8_scalpers
    con = duckdb.connect(str(DB), read_only=True)
    rows = con.execute("SELECT ts, open, high, low, close, volume, turnover FROM candles_1m WHERE symbol = ? AND ts >= ? "
                       "AND ts < ? ORDER BY ts", [coin, start, end]).fetchall()
    con.close()
    rules = {s: MarketRules(**r) for s, r in v8.load_freeze()["rules"].items()}
    base_cls = load_v8_scalpers()["V8.3"]
    since = start + 3 * DAY
    mid = since + (end - since) // 2
    out = []
    for name, over in VARIANTS.items():
        params = dataclasses.replace(base_cls.Params(), **over)

        class Variant(base_cls):
            def __init__(self, p=None, _params=params):
                super().__init__(p or _params)
        settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
        eng = LevelMakerEngineV8(settings, [coin], rules={coin: rules[coin]}, seed=7, execution=v8.EXECUTION_V8,
                                 fees=FEES_V6, fee_source="schedule", sizing=SizingV6(rules, jev=False),
                                 leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct, maker_tp=True)
        tape = (Candle(coin, "1m", int(t), o, h, lo_, c, v, int(t) + 59_999, True, tv or 0.0, 0, "historical", 0.0)
                for t, o, h, lo_, c, v, tv in rows)
        res = eng.run(Variant.for_class("SCALP"), tape, since_ms=since, leverage=20, signal_tf="5m", only_symbol=coin,
                      reset_at=[since + k * DAY for k in range(1, (end - since) // DAY)])
        last = int(rows[-1][0]) if rows else end
        tr = [t for t in res.trades if t.entry_ts >= since and t.exit_kind != "reset"
              and not (t.exit_kind == "time" and t.exit_ts >= last)]           # the end-of-replay close is not a trade
        out.append({"coin": coin, "variant": name, "mid": mid, "since": since, "end": end,
                    "trades": [{"ts": t.entry_ts, "r": t.r_multiple, "net": t.net, "fees": t.fees, "exit": t.exit_kind,
                                "hold_min": (t.exit_ts - t.entry_ts) / 60_000} for t in tr]})
    return out


def summarize(rows: list[dict], mid: int, days: float) -> dict:
    tr = [t for r in rows for t in r["trades"]]
    if not tr:
        return {"trades": 0}
    h = [[t["r"] for t in tr if (t["ts"] >= mid) == bool(i)] for i in (0, 1)]
    r = np.array([t["r"] for t in tr])
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    return {"trades": len(tr), "per_coin_day": round(len(tr) / days / len(COINS), 2),
            "win": round(float(np.mean([t["net"] > 0 for t in tr])), 3), "net_r": round(float(r.mean()), 4),
            "net_r_half1": round(float(np.mean(h[0])), 4) if h[0] else None,
            "net_r_half2": round(float(np.mean(h[1])), 4) if h[1] else None, "pf": round(float(pos / neg), 3) if neg else None,
            "net_usdt": round(float(sum(t["net"] for t in tr)), 2), "fees_usdt": round(float(sum(t["fees"] for t in tr)), 2),
            "median_hold_min": round(float(np.median([t["hold_min"] for t in tr])), 1),
            "exits": dict(Counter(t["exit"] for t in tr))}


def main() -> None:
    t0 = time.time()
    start, end = window()
    with ProcessPoolExecutor(max_workers=len(COINS)) as pool:
        rows = [x for part in pool.map(run, [(c, start, end) for c in COINS]) for x in part]
    mid, since = rows[0]["mid"], rows[0]["since"]
    days = (end - since) / DAY
    summary = {n: summarize([r for r in rows if r["variant"] == n], mid, days) for n in VARIANTS}
    base = summary["BASE"]
    passing = [n for n in VARIANTS if n != "BASE" and summary[n]["trades"]
               and summary[n]["net_r_half1"] > base["net_r_half1"] and summary[n]["net_r_half2"] > base["net_r_half2"]]
    adopt = max(passing, key=lambda n: summary[n]["net_r"]) if passing else "BASE"
    for n, s in summary.items():
        print(f"{n:9s} trades {s['trades']:5d} ({s['per_coin_day']}/coin-day) win {s['win']} net R {s['net_r']:+.4f} "
              f"(h1 {s['net_r_half1']:+.4f} h2 {s['net_r_half2']:+.4f}) pf {s['pf']} net {s['net_usdt']:+.2f} USDT "
              f"fees {s['fees_usdt']} hold {s['median_hold_min']}m exits {s['exits']}"
              f"{'  PASSES' if n in passing else ''}{'  <= ADOPT' if n == adopt and n != 'BASE' else ''}", flush=True)
    print(f"verdict: {'ADOPT ' + adopt if adopt != 'BASE' else 'KEEP 45 min (no variant beat it in both halves)'}")
    OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                               "rules": __doc__.split("PRE-REGISTERED")[1], "window": [since, end], "days": round(days, 1),
                               "coins": COINS, "summary": summary, "passing": passing, "adopt": adopt}, indent=1))
    print(f"wrote {OUT.relative_to(PROJECT)} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
