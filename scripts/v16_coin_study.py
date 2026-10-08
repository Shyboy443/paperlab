"""V16 coin by coin: for each of the 30 Bybit coins, is there a strategy -- and a market condition to run it in -- that
makes money on data it was not chosen on? (operator, 2026-10-08: "find strategy that makes positive returns daily for
each coin ... 30 coins each 1 by 1"; "have temporary bots which give higher returns in specific conditions".)

    BT_START=2024-10-01 BT_END=2026-10-01 BT_MID=2025-10-01 \
        python scripts/v16_coin_study.py run --data-dir /data/bt2y --workers 16
    BT_START=2023-10-01 BT_END=2024-10-01 BT_MID=2024-04-01 \
        python scripts/backtest_2y.py prep --data-dir /data/bt_hold_crypto          (1h closes for the context, once)
    BT_START=2023-10-01 BT_END=2024-10-01 BT_MID=2024-04-01 \
        python scripts/v16_coin_study.py run --data-dir /data/bt_hold_crypto --workers 16
    python scripts/v16_coin_study.py analyze --main /data/bt2y --hold /data/bt_hold_crypto --out docs/V16_COIN_STUDY.json

No strategy makes money every day; the question is a positive AVERAGE that holds out of sample.

PRE-REGISTERED (2026-10-08, before any of these runs)

Menu (fixed; no parameter is changed): the six frozen V6 HOURLY families V6.1 .. V6.6 (app/strategies/v6), with V6's
execution, fees, funding, positioning feeds and 16-coin breadth context exactly as frozen, 1% risk sizing, halts off.
One continuous 1000 USDT book per coin and family (20 USDT books are vetoed by exchange minimums on ETH, SOL, BNB...).
Instrument rules and funding intervals for all 30 coins come from docs/V11_FREEZE.json.

Market condition at a trade's entry hour H, from BTC's daily closes (00:00 UTC) known at H:
- BULL: BTC above its 50-day average and the average higher than 5 days earlier;
- BEAR: BTC below its 50-day average and the average lower than 5 days earlier;
- CHOP: anything else.
A "conditional bot" is a (family, condition) pair: it only takes the trades its family takes in that condition. The
condition ALL means always on.

Windows: DEV 2024-10-01 .. 2025-10-01, TEST 2025-10-01 .. 2026-10-01 (same continuous book), HOLDOUT 2023-10-01 ..
2024-10-01 (a separate book on data fetched for this study and never used before).

Per coin:
- SELECT on DEV only: among the 24 (family, condition) pairs with at least 8 DEV trades and DEV net > 0, the one with
  the highest DEV net. None: the coin gets no bot.
- PASS only if that pair is also profitable in TEST and in HOLDOUT, each with at least 5 trades and a profit factor of
  at least 1.10. A coin without enough HOLDOUT history (listed later) is NOT TESTABLE and does not pass.
Luck baseline: the same rule with the pair chosen at random among the 24 (2000 draws per coin), giving the number of
coins expected to pass by chance. The selection shows skill only if real passes clearly exceed that.
Passing coins become candidates for conditional paper bots; nothing goes live from this study alone.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import random
import sys
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

FAMILIES = ("V6.1", "V6.2", "V6.3", "V6.4", "V6.5", "V6.6")
CONDITIONS = ("ALL", "BULL", "BEAR", "CHOP")
BOOK = 1000.0
DAY = 86_400_000


def coins() -> list[str]:
    return sorted(json.loads((PROJECT / "docs" / "V11_FREEZE.json").read_text(encoding="utf-8"))["rules"])


def run_one(job: tuple[str, str], data_dir: str) -> dict[str, Any]:
    try:
        os.nice(19)
    except Exception:
        pass
    sid, sym = job
    import scripts.backtest_2y as bt
    from app.backtest.funding import FundingSchedule
    from app.backtest.replay import ReplayEngine
    from app.competition.v6_config import AGGRESSIVE_V6, EXECUTION_V6, FEES_V6, settings_v6
    from app.core.types import MarketRules
    from app.strategies.registry import load_v6
    root = Path(data_dir)
    v11 = json.loads((PROJECT / "docs" / "V11_FREEZE.json").read_text(encoding="utf-8"))
    v6 = bt.freeze("v6")
    rules = {s: MarketRules(**r) for s, r in v11["rules"].items()}
    intervals = {s: int(v) for s, v in v11["funding_interval_min"].items()}
    t0 = time.time()
    feeds, ctx = bt.positioning(root, [sym], list(v6["breadth_set"]), intervals, bt.START - bt.WARM_SINGLE, bt.END)
    cls = load_v6()[sid].for_class("HOURLY", feeds[sym], ctx)
    settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0,
                                   strategy_starting_balance=BOOK)
    eng = bt.thin(ReplayEngine)(settings, [sym], rules={sym: rules[sym]},
                                funding=FundingSchedule({sym: bt.funding_rows(root, sym, bt.START - bt.WARM_SINGLE, bt.END)}),
                                execution=EXECUTION_V6, sizing=bt.sizing_v6(rules), max_risk_pct=AGGRESSIVE_V6.max_risk_pct,
                                fees=FEES_V6, fee_source="schedule", leverage_policy="needed", cost_gate=None, seed=7)
    eng.portfolio.closed_trades = deque(maxlen=None)
    res = eng.run(cls, bt.tape(root, [sym], None, bt.START - bt.WARM_SINGLE, bt.END), since_ms=bt.START, leverage=20,
                  signal_tf="1h", only_symbol=sym)
    paid: dict[str, float] = {}
    for f in res.fills:
        if f.kind == "funding":
            paid[f.position_id] = paid.get(f.position_id, 0.0) + f.realized_pnl
    trades = [(t.entry_ts, t.exit_ts, round(t.net + paid.get(t.position_id, 0.0), 6), round(t.r_multiple, 4), t.side)
              for t in res.trades if t.entry_ts >= bt.START]
    first_bar = next((b for b in bt.tape(root, [sym], None, bt.START, bt.END)), None)
    out = {"family": sid, "symbol": sym, "start": bt.START, "end": bt.END, "mid": bt.MID, "trades": trades,
           "has_data_from": first_bar.open_time if first_bar else None, "signals": res.signals,
           "elapsed_s": round(time.time() - t0, 1)}
    p = root / "v16_results" / f"{sid}__{sym}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out), encoding="utf-8")
    return {"family": sid, "symbol": sym, "trades": len(trades), "net": round(sum(t[2] for t in trades), 2),
            "elapsed": out["elapsed_s"]}


def run(args: argparse.Namespace) -> int:
    root = Path(args.data_dir)
    todo = [(sid, sym) for sym in coins() for sid in FAMILIES
            if args.force or not (root / "v16_results" / f"{sid}__{sym}.json").exists()]
    print(f"{len(todo)} runs, {args.workers} workers", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(args.workers) as ex:
        futs = {ex.submit(run_one, j, str(root)): j for j in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                r = f.result()
                print(f"[{i}/{len(todo)}] {r['family']} {r['symbol']}: {r['trades']} trades net {r['net']:+.2f} "
                      f"({r['elapsed']:.0f}s, total {time.time() - t0:.0f}s)", flush=True)
            except Exception as exc:
                print(f"[{i}/{len(todo)}] {futs[f]} FAILED {type(exc).__name__}: {exc}", flush=True)
    return 0


# -- analysis ---------------------------------------------------------------------------------------------------------
def regimes(root: Path):
    """condition(H) for an entry hour H, from BTC's daily closes known at H."""
    from app.backtest.bybit_archive import read_series
    closes = sorted(read_series(root / "positioning", "BTCUSDT", "close"))          # (hour open ms, close)
    daily: dict[int, float] = {}
    for t, c in closes:                                                             # the last hourly close of each day
        daily[t // DAY] = c
    days = sorted(daily)
    sma: dict[int, float] = {}
    for i in range(49, len(days)):
        sma[days[i]] = sum(daily[d] for d in days[i - 49:i + 1]) / 50

    def at(h: int) -> str:
        d = h // DAY - 1                                                            # the last COMPLETED day before H
        if d not in sma or d - 5 not in sma:
            return "CHOP"
        up, rising = daily[d] > sma[d], sma[d] > sma[d - 5]
        return "BULL" if up and rising else "BEAR" if (not up and not rising) else "CHOP"
    return at


def stats(trades: list[tuple]) -> dict[str, Any]:
    wins = sum(t[2] for t in trades if t[2] > 0)
    losses = -sum(t[2] for t in trades if t[2] < 0)
    return {"trades": len(trades), "net": round(sum(t[2] for t in trades), 2),
            "pf": round(wins / losses, 3) if losses else (None if not wins else 99.0)}


def passes(s: dict[str, Any]) -> bool:
    return s["trades"] >= 5 and s["net"] > 0 and (s["pf"] or 0) >= 1.10


def analyze(args: argparse.Namespace) -> int:
    main, hold = Path(args.main), Path(args.hold)
    at_main, at_hold = regimes(main), regimes(hold)

    def load(root: Path, at) -> dict[tuple[str, str], dict[str, Any]]:
        out = {}
        for p in (root / "v16_results").glob("*.json"):
            d = json.loads(p.read_text(encoding="utf-8"))
            d["trades"] = [tuple(t) + (at(t[0] // 3_600_000 * 3_600_000),) for t in d["trades"]]
            out[(d["family"], d["symbol"])] = d
        return out
    M, H = load(main, at_main), load(hold, at_hold)
    pick = lambda trades, cond: [t for t in trades if cond == "ALL" or t[5] == cond]      # noqa: E731
    report, real_pass, rng = {}, 0, random.Random(7)
    null_counts = [0] * 2000
    portfolio: list[tuple] = []
    for sym in coins():
        pairs = {}
        for sid in FAMILIES:
            m, h = M.get((sid, sym)), H.get((sid, sym))
            if m is None:
                continue
            for cond in CONDITIONS:
                tr = pick(m["trades"], cond)
                pairs[(sid, cond)] = {
                    "dev": stats([t for t in tr if t[0] < m["mid"]]), "test": stats([t for t in tr if t[0] >= m["mid"]]),
                    "hold": stats(pick(h["trades"], cond)) if h else {"trades": 0, "net": 0.0, "pf": None},
                    "_test": [t for t in tr if t[0] >= m["mid"]], "_hold": pick(h["trades"], cond) if h else []}
        eligible = [(k, v) for k, v in pairs.items() if v["dev"]["trades"] >= 8 and v["dev"]["net"] > 0]
        hold_ok = any(v["hold"]["trades"] >= 5 for v in pairs.values())
        chosen = max(eligible, key=lambda kv: kv[1]["dev"]["net"]) if eligible else None
        verdict = ("NO DEV CANDIDATE" if chosen is None else "NOT TESTABLE" if not hold_ok else
                   "PASS" if passes(chosen[1]["test"]) and passes(chosen[1]["hold"]) else "FAIL")
        if verdict == "PASS":
            real_pass += 1
            portfolio += chosen[1]["_test"] + chosen[1]["_hold"]
        if hold_ok and pairs:
            keys = list(pairs)
            for i in range(len(null_counts)):
                v = pairs[rng.choice(keys)]
                null_counts[i] += int(passes(v["test"]) and passes(v["hold"]))
        report[sym] = {"verdict": verdict, "chosen": list(chosen[0]) if chosen else None,
                       "dev": chosen[1]["dev"] if chosen else None, "test": chosen[1]["test"] if chosen else None,
                       "hold": chosen[1]["hold"] if chosen else None,
                       "all_pairs": {f"{k[0]}|{k[1]}": {x: v[x] for x in ("dev", "test", "hold")} for k, v in pairs.items()}}
        c = report[sym]
        print(f"{sym:14} {verdict:16} {c['chosen']}  dev {c['dev']}  test {c['test']}  hold {c['hold']}", flush=True)
    null_counts.sort()
    null = {"mean": round(sum(null_counts) / len(null_counts), 2), "p95": null_counts[int(0.95 * len(null_counts))]}
    port = {"coins": real_pass, "trades": len(portfolio), "net_usdt": round(sum(t[2] for t in portfolio), 2),
            "avg_daily_pct_per_coin": round(100 * sum(t[2] for t in portfolio) / (BOOK * max(1, real_pass)) / (365 + 366), 4)}
    print(f"PASS {real_pass} coins; by luck expected {null['mean']} (95th pct {null['p95']}); out-of-sample portfolio {port}")
    Path(args.out).write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "rules": __doc__,
                                          "passed": real_pass, "luck": null, "portfolio": port, "coins": report},
                                         indent=1), encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--data-dir", required=True)
    r.add_argument("--workers", type=int, default=16)
    r.add_argument("--force", action="store_true")
    a = sub.add_parser("analyze")
    a.add_argument("--main", required=True)
    a.add_argument("--hold", required=True)
    a.add_argument("--out", default=str(PROJECT / "docs" / "V16_COIN_STUDY.json"))
    args = ap.parse_args()
    return {"run": run, "analyze": analyze}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
