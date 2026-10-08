"""V17 swing study: do long-hold crypto strategies make money after fees and funding on data they were not built on?
(operator, 2026-10-08: "build day trading / swing trading strategies that can hold trades for longer durations, because
after testing all the scalp strategies they gave no good results because of the fees".)

    BT_START=2024-10-01 BT_END=2026-10-01 BT_MID=2025-10-01 \
        python scripts/v17_swing_study.py run --data-dir /data/bt2y --workers 16
    BT_START=2023-10-01 BT_END=2024-10-01 BT_MID=2024-04-01 \
        python scripts/v17_swing_study.py run --data-dir /data/bt_hold_crypto --workers 16
    python scripts/v17_swing_study.py analyze --main /data/bt2y --hold /data/bt_hold_crypto --out docs/V17_SWING_STUDY.json

PRE-REGISTERED (2026-10-08, before any of these runs; one shot, nothing is re-tuned after the results)

Families: V17.1 Donchian 20/10, V17.2 daily trend MA + 5-day breakout, V17.3 7/3 breakout (long only), V17.4 weekly
28-day momentum, V17.5 24-hour day trend -- exactly as in app/strategies/v17/swing.py.

Coins (the 10 majors with data in every window): BTC ETH SOL XRP DOGE ADA LINK AVAX LTC BNB (Bybit perpetuals).

Book: one continuous 1000 USDT book per coin and family, 1% risk per trade (V6 sizing), V6 execution and taker fees,
real funding settlements charged on every open position, halts off, 92 days of warm-up.

Windows: DEV 2024-10-01 .. 2025-10-01 and TEST 2025-10-01 .. 2026-10-01 (one continuous book), HOLDOUT 2023-10-01 ..
2024-10-01 (a separate book; data fetched for these studies and used only by the V16 study's holdout so far).

Verdict per family, summed over its 10 coins:
- PASS only if net > 0 in DEV, in TEST and in HOLDOUT; profit factor >= 1.20 over the three years; at least 6 of the
  10 coins net positive over the three years; at least 100 trades; and still net positive over the three years with
  the fees paid twice (a cost-robustness check).
Passing families become paper swing bots on the 10 coins; failing ones do not run.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

FAMILIES = ("V17.1", "V17.2", "V17.3", "V17.4", "V17.5")
COINS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT", "BNBUSDT")
BOOK = 1000.0
DAY = 86_400_000


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
    from app.strategies.v17.swing import load_v17
    root = Path(data_dir)
    v11 = json.loads((PROJECT / "docs" / "V11_FREEZE.json").read_text(encoding="utf-8"))
    rules = {s: MarketRules(**r) for s, r in v11["rules"].items()}
    t0 = time.time()
    cls = load_v17()[sid].for_class("SWING")
    settings = dataclasses.replace(settings_v6(), strategy_halt_pct=1.0, daily_halt_pct=1.0,
                                   strategy_starting_balance=BOOK)
    eng = bt.thin(ReplayEngine)(settings, [sym], rules={sym: rules[sym]},
                                funding=FundingSchedule({sym: bt.funding_rows(root, sym, bt.START - bt.WARM_SINGLE, bt.END)}),
                                execution=EXECUTION_V6, sizing=bt.sizing_v6(rules), max_risk_pct=AGGRESSIVE_V6.max_risk_pct,
                                fees=FEES_V6, fee_source="schedule", leverage_policy="needed", cost_gate=None, seed=7)
    eng.portfolio.closed_trades = deque(maxlen=None)
    res = eng.run(cls, bt.tape(root, [sym], None, bt.START - bt.WARM_SINGLE, bt.END), since_ms=bt.START, leverage=20,
                  signal_tf="4h", only_symbol=sym)
    paid: dict[str, float] = {}
    for f in res.fills:
        if f.kind == "funding":
            paid[f.position_id] = paid.get(f.position_id, 0.0) + f.realized_pnl
    trades = [(t.entry_ts, t.exit_ts, round(t.net + paid.get(t.position_id, 0.0), 6), round(t.r_multiple, 4), t.side,
               round(t.fees, 6), t.exit_kind) for t in res.trades if t.entry_ts >= bt.START]
    eq = [(t, round(e, 4)) for t, e in res.equity if t >= bt.START]
    daily = [(t, e) for i, (t, e) in enumerate(eq) if i == len(eq) - 1 or eq[i + 1][0] // DAY != t // DAY]
    out = {"family": sid, "symbol": sym, "start": bt.START, "end": bt.END, "mid": bt.MID, "trades": trades,
           "equity_daily": daily, "rejects": dict(res.rejects), "elapsed_s": round(time.time() - t0, 1)}
    p = root / "v17_results" / f"{sid}__{sym}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out), encoding="utf-8")
    return {"family": sid, "symbol": sym, "trades": len(trades), "net": round(sum(t[2] for t in trades), 2),
            "elapsed": out["elapsed_s"]}


def run(args: argparse.Namespace) -> int:
    root = Path(args.data_dir)
    todo = [(sid, sym) for sym in COINS for sid in FAMILIES
            if args.force or not (root / "v17_results" / f"{sid}__{sym}.json").exists()]
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


def stats(trades: list) -> dict[str, Any]:
    wins = sum(t[2] for t in trades if t[2] > 0)
    losses = -sum(t[2] for t in trades if t[2] < 0)
    hold = [(t[1] - t[0]) / 3_600_000 for t in trades]
    return {"trades": len(trades), "net": round(sum(t[2] for t in trades), 2), "fees": round(sum(t[5] for t in trades), 2),
            "pf": round(wins / losses, 3) if losses else None,
            "win": round(sum(t[2] > 0 for t in trades) / len(trades), 3) if trades else None,
            "avg_hold_h": round(sum(hold) / len(hold), 1) if hold else None}


def max_dd(daily: list) -> float:
    peak, dd = None, 0.0
    for _, e in daily:
        peak = e if peak is None else max(peak, e)
        dd = max(dd, (peak - e) / peak if peak else 0.0)
    return round(100 * dd, 2)


def analyze(args: argparse.Namespace) -> int:
    def load(root: Path) -> dict:
        out = {}
        for p in (root / "v17_results").glob("*.json"):
            d = json.loads(p.read_text(encoding="utf-8"))
            out[(d["family"], d["symbol"])] = d
        return out
    M, H = load(Path(args.main)), load(Path(args.hold))
    report: dict[str, Any] = {}
    for sid in FAMILIES:
        dev, test, hold, coins = [], [], [], {}
        for sym in COINS:
            m, h = M.get((sid, sym)), H.get((sid, sym))
            if m is None or h is None:
                continue
            d = [t for t in m["trades"] if t[0] < m["mid"]]
            t2 = [t for t in m["trades"] if t[0] >= m["mid"]]
            dev += d; test += t2; hold += h["trades"]
            coins[sym] = {"dev": stats(d), "test": stats(t2), "hold": stats(h["trades"]),
                          "all": stats(d + t2 + h["trades"]), "max_dd_pct_main": max_dd(m["equity_daily"]),
                          "max_dd_pct_hold": max_dd(h["equity_daily"])}
        every = dev + test + hold
        s = {"dev": stats(dev), "test": stats(test), "hold": stats(hold), "all": stats(every)}
        positive = sum(1 for c in coins.values() if c["all"]["net"] > 0)
        double_cost = round(s["all"]["net"] - s["all"]["fees"], 2)
        checks = {"dev_net": s["dev"]["net"] > 0, "test_net": s["test"]["net"] > 0, "hold_net": s["hold"]["net"] > 0,
                  "pf_1_20": (s["all"]["pf"] or 0) >= 1.20, "six_coins": positive >= 6, "100_trades": s["all"]["trades"] >= 100,
                  "double_fees_positive": double_cost > 0}
        days = 3 * 365.25
        report[sid] = {"pass": all(checks.values()), "checks": checks, "coins_positive": positive, **s,
                       "net_with_double_fees": double_cost,
                       "avg_daily_pct_per_coin": round(100 * s["all"]["net"] / (BOOK * max(1, len(coins))) / days, 4),
                       "coins": coins}
        print(f"{sid} {'PASS' if report[sid]['pass'] else 'FAIL'}  dev {s['dev']['net']:+.0f} test {s['test']['net']:+.0f} "
              f"hold {s['hold']['net']:+.0f}  pf {s['all']['pf']}  trades {s['all']['trades']}  hold {s['all']['avg_hold_h']}h  "
              f"coins+ {positive}/10  2x fees {double_cost:+.0f}  {report[sid]['avg_daily_pct_per_coin']}%/day/coin", flush=True)
    Path(args.out).write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "rules": __doc__,
                                          "families": report}, indent=1), encoding="utf-8")
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
    a.add_argument("--out", default=str(PROJECT / "docs" / "V17_SWING_STUDY.json"))
    args = ap.parse_args()
    return {"run": run, "analyze": analyze}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
