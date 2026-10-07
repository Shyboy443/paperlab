"""Historical V1 study definition and shared tape/statistics helpers.

The CLI now delegates to v14_htf_revision_study.py. The historical evaluator
below describes V1 and must not overwrite V14_HTF_STUDY.json with V2 results.

    python scripts/v14_htf_study.py            (needs the research store: research/ingest.py)

PRE-REGISTERED (2026-10-04, before the first run):
  Data: the research store's Bybit 1m bars (data/research/market.duckdb, 2026-06-26 .. 2026-10-02), 30 coins; the first
  14 days are warm-up (the HTF rule needs 300 hourly candles); the rest is split into a first and a second half.
  Pairs, each strategy exactly as it runs live, once WITHOUT and once WITH the HTF rule (app/strategies/v14/htf.py):
    V14.1  V8.3 VWAP snap-back on V8's 6 coins (LevelMakerEngineV8, resting targets, 3 h hold)
    V14.2  V11.1 relative-strength breakout scanner, 30 coins (ScanReplayEngine, 25/50/25 ladder, resting targets)
    V14.3  V11.2 relative-strength pullback scanner, 30 coins (same)
    V14.4  V13.1 Snapback, 29 coins (LimitEntryEngineV13, post-only limit entries)
  Book: 20 USDT, SizingV6 (1% risk, hard cap 2%), reset daily; fees by role, funding, 60 s latency.
  VERDICT per strategy: "HTF HELPS" only if the HTF version's net R per trade beats the copy without it in BOTH halves;
  otherwise "HTF DOES NOT HELP". Reported too: trades, win rate, profit factor, net USDT. The V14 bots run forward on
  paper whatever the verdict (the operator asked for them), each labelled with its strategy's verdict.
"""
from __future__ import annotations

import dataclasses
import json
import sys
import time
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
DB = PROJECT / "data" / "research" / "market.duckdb"
OUT = PROJECT / "docs" / "V14_HTF_STUDY.json"
DAY, MIN = 86_400_000, 60_000
WARMUP_DAYS = 14
V8_COINS = ("ARBUSDT", "DOGEUSDT", "ENAUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


def window(symbols) -> tuple[int, int]:
    import duckdb
    con = duckdb.connect(str(DB), read_only=True)
    lo, hi = con.execute("SELECT max(lo), min(hi) FROM (SELECT symbol, min(ts) lo, max(ts) hi FROM candles_1m "
                         f"WHERE symbol IN {tuple(symbols)} GROUP BY 1)").fetchone()
    con.close()
    return (int(lo) // DAY + 1) * DAY, (int(hi) // DAY) * DAY


def tape(symbols, anchor: str | None, start: int, end: int):
    """1m Candles minute by minute, every coin in a fixed order with the anchor last (the scan feed's order)."""
    import duckdb

    from app.core.types import Candle
    order = sorted(s for s in symbols if s != anchor) + ([anchor] if anchor else [])
    con = duckdb.connect(str(DB), read_only=True)
    arr = {s: con.execute("SELECT ts, open, high, low, close, volume, turnover FROM candles_1m WHERE symbol = ? AND "
                          "ts >= ? AND ts < ? ORDER BY ts", [s, start, end]).fetchnumpy() for s in order}
    con.close()
    idx = {s: 0 for s in order}
    for m in range(start, end, MIN):
        for s in order:
            a, i = arr[s], idx[s]
            if i < len(a["ts"]) and int(a["ts"][i]) == m:
                idx[s] = i + 1
                yield Candle(s, "1m", m, float(a["open"][i]), float(a["high"][i]), float(a["low"][i]), float(a["close"][i]),
                             float(a["volume"][i]), m + MIN - 1, True, float(a["turnover"][i] or 0.0), 0, "historical", 0.0)


def run(args: tuple) -> dict:
    sid, mode, coin = args
    from app.competition import v11_config as v11
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6
    from app.competition.v8_config import EXECUTION_V8
    from app.core.types import MarketRules
    from app.live.scan_engine import BE_COVER_BPS, ScanReplayEngine
    from app.live.v8_engine import LevelMakerEngineV8
    from app.live.v13_engine import LimitEntryEngineV13
    from app.strategies.v14.htf import SIGNAL_TF, SOURCES, build
    rules = {s: MarketRules(**r) for s, r in v11.load_freeze()["rules"].items()}
    universe = list(v11.UNIVERSE) if sid != "V14.1" else [coin]
    start, end = window(universe)
    since = start + WARMUP_DAYS * DAY
    settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
    kw = dict(rules=rules if sid != "V14.1" else {coin: rules[coin]}, seed=7, fees=FEES_V6, fee_source="schedule",
              sizing=SizingV6(rules, jev=False), leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct)
    kind = SOURCES[sid]["kind"]
    if kind == "single":
        eng = LevelMakerEngineV8(settings, universe, execution=EXECUTION_V8, maker_tp=True, **kw)
    elif kind == "scanner":
        eng = ScanReplayEngine(settings, universe, execution=v11.EXECUTION_V11, be_cover_bps=BE_COVER_BPS, maker_tp=True, **kw)
    else:
        eng = LimitEntryEngineV13(settings, universe, execution=v11.EXECUTION_V11, maker_tp=True, **kw)
    eng.portfolio.closed_trades = deque(maxlen=None)
    cls = build(sid, universe, htf=(mode == "HTF"))
    anchor = None if kind == "single" else "BTCUSDT"
    last = [0]

    def feed():
        for c in tape(universe, anchor, start, end):
            last[0] = c.open_time
            yield c
    res = eng.run(cls, feed(), since_ms=since, leverage=20, signal_tf=SIGNAL_TF[sid],
                  only_symbol=coin if kind == "single" else None, reset_at=list(range(since + DAY, end, DAY)))
    tr = [t for t in res.trades if t.entry_ts >= since and t.exit_kind not in ("reset", "end_of_run")
          and not (t.exit_kind == "time" and t.exit_ts >= last[0])]       # the end-of-replay close is not a trade
    return {"sid": sid, "mode": mode, "coin": coin, "since": since, "end": end,
            "trades": [{"ts": t.entry_ts, "r": t.r_multiple, "net": t.net, "exit": t.exit_kind} for t in tr]}


def summarize(trades: list[dict], mid: int, days: float) -> dict:
    if not trades:
        return {"trades": 0}
    r = np.array([t["r"] for t in trades])
    h = [np.array([t["r"] for t in trades if (t["ts"] >= mid) == bool(i)]) for i in (0, 1)]
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    return {"trades": len(trades), "per_day": round(len(trades) / days, 2),
            "win": round(float(np.mean([t["net"] > 0 for t in trades])), 3), "net_r": round(float(r.mean()), 4),
            "net_r_half1": round(float(h[0].mean()), 4) if len(h[0]) else None,
            "net_r_half2": round(float(h[1].mean()), 4) if len(h[1]) else None,
            "pf": round(float(pos / neg), 3) if neg else None, "net_usdt": round(float(sum(t["net"] for t in trades)), 2),
            "exits": dict(Counter(t["exit"] for t in trades))}


def main() -> None:
    t0 = time.time()
    jobs = [(sid, mode, None) for sid in ("V14.2", "V14.3", "V14.4") for mode in ("BASE", "HTF")]
    jobs += [("V14.1", mode, c) for c in V8_COINS for mode in ("BASE", "HTF")]
    with ProcessPoolExecutor(max_workers=7) as pool:
        rows = list(pool.map(run, jobs))
    out, verdicts = {}, {}
    for sid in ("V14.1", "V14.2", "V14.3", "V14.4"):
        part = [r for r in rows if r["sid"] == sid]
        since, end = min(r["since"] for r in part), max(r["end"] for r in part)
        mid, days = since + (end - since) // 2, (end - since) / DAY
        out[sid] = {m: summarize([t for r in part if r["mode"] == m for t in r["trades"]], mid, days) for m in ("BASE", "HTF")}
        b, h = out[sid]["BASE"], out[sid]["HTF"]
        ok = bool(h.get("trades")) and h["net_r_half1"] is not None and h["net_r_half2"] is not None \
            and h["net_r_half1"] > b["net_r_half1"] and h["net_r_half2"] > b["net_r_half2"]
        verdicts[sid] = "HTF HELPS" if ok else "HTF DOES NOT HELP"
        for m in ("BASE", "HTF"):
            s = out[sid][m]
            print(f"{sid} {m:4s} trades {s.get('trades', 0):5d} ({s.get('per_day')}/day) win {s.get('win')} net R "
                  f"{s.get('net_r')} (h1 {s.get('net_r_half1')} h2 {s.get('net_r_half2')}) pf {s.get('pf')} "
                  f"net {s.get('net_usdt')} USDT exits {s.get('exits')}", flush=True)
        print(f"{sid} -> {verdicts[sid]}", flush=True)
    OUT.write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                               "rules": __doc__.split("PRE-REGISTERED")[1], "results": out, "verdicts": verdicts}, indent=1))
    print(f"wrote {OUT.relative_to(PROJECT)} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    from scripts.v14_htf_revision_study import main as revision_main
    revision_main()
