"""Backtest ANY registered strategy on real USD-M futures 1m bars, through the LIVE engine.

This script owns no trading logic. It fetches bars, builds an app.backtest.ReplayEngine and prints
results. Sizing, risk gates, fills, fees, stops, take-profit ladders, trailing and paper liquidation
all come from app/core/{risk,portfolio,positions}.py -- the same code the running lab uses. When a
modelling choice changes there, this changes with it; there is nothing here to keep in sync.

Two consequences worth knowing before reading the numbers:

  * A stop is no longer exactly -1R. The live ExitEngine fills at the price the path reached, not at
    the stop level, so a bar that gaps through the stop costs more than R. Earlier revisions of this
    script filled at the stop and were optimistic by exactly that amount.
  * Books can blow up. Positions are sized from a real 100 USDT wallet at the strategy's virtual
    leverage, paper liquidation is checked, and a book that drops through its halt floor stops
    trading for the rest of the run (`HALT` in the output).

Only candle-driven strategies can be tested: there is no order book, tick tape or funding feed in
historical klines. `--list` prints which strategies qualify.

    python scripts/backtest.py --strategy S21
    python scripts/backtest.py --all --months 2026-06,2026-07,2026-08
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import statistics as st
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest import ReplayEngine  # noqa: E402
from app.backtest.replay import unsupported  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.core.types import Candle  # noqa: E402
from app.strategies.registry import load_all  # noqa: E402
from scripts.backtest_s15 import fetch, to_15m  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def settings_for(balance: float, **over: str):
    """Lab settings without reading paperlab/.env, so a backtest never depends on local keys."""
    env = {"MODE": "FUTURES_TESTNET", "DASHBOARD_PASSWORD": "backtest", "DRY_RUN": "true",
           "DATA_DIR": str(PROJECT / "data"), "SYMBOLS": ",".join(SYMBOLS),
           "STRATEGY_STARTING_BALANCE": str(balance), **over}
    return load_settings(env)


def tape(data: dict[str, list[Candle]]) -> list[Candle]:
    """One merged 1m stream across symbols, ordered the way the live feed would deliver it."""
    return sorted((c for cs in data.values() for c in cs), key=lambda c: (c.close_time, c.symbol))


def summarise(res) -> dict:
    rs = res.r_series
    if not rs:
        return {"n": 0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    kinds: dict[str, int] = {}
    for t in res.trades:
        kinds[t.exit_kind] = kinds.get(t.exit_kind, 0) + 1
    return {"n": len(rs), "win_rate": len(wins) / len(rs), "avg_r": st.fmean(rs), "total_r": sum(rs),
            "pf": (sum(wins) / -sum(losses)) if losses and sum(losses) else float("inf"),
            "equity": res.final_equity, "dd": res.max_drawdown(), "halted": res.halted, "kinds": kinds}


def boot_ci(rs, n: int = 2000):
    rng = random.Random(7)
    means = sorted(st.fmean(rng.choices(rs, k=len(rs))) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--months", default="2026-06,2026-07,2026-08")
    ap.add_argument("--since", default="2026-06-19")
    ap.add_argument("--seeds", type=int, default=5, help="slippage draws; results are pooled across them")
    ap.add_argument("--balance", type=float, default=100.0, help="per-strategy starting wallet (USDT)")
    ap.add_argument("--rejects", action="store_true", help="print why entries were refused")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    classes = load_all(strict=True)
    if args.list:
        for sid in sorted(classes):
            why = unsupported(sid, classes[sid])
            print(f"  {sid}  {classes[sid].name:<34} {'SKIP - needs ' + why if why else 'backtestable'}")
        return

    settings = settings_for(args.balance)
    months = args.months.split(",")
    days = [(dt.date(2026, 9, 1) + dt.timedelta(days=i)).isoformat() for i in range(19)]
    data = {s: fetch(s, months, days) for s in SYMBOLS}
    bars = tape(data)
    since = int(dt.datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)

    targets = sorted(classes) if args.all else [s.strip().upper() for s in args.strategy.split(",") if s.strip()]
    print("=" * 118)
    print(f"BACKTEST  {args.since} -> 2026-09-19   live engine (risk/portfolio/positions)   "
          f"taker {settings.taker_fee * 100:.3f}%  slippage {settings.slippage_bps_min}-"
          f"{settings.slippage_bps_max}bp  {args.balance:.0f} USDT/book  {args.seeds} seeds")
    print("=" * 118)
    print(f"{'id':<5}{'name':<32}{'n':>5}{'win%':>6}{'avgR':>8}{'95% CI':>19}{'PF':>6}"
          f"{'eq':>9}{'maxDD':>8}  exits")
    for sid in targets:
        cls = classes[sid]
        why = unsupported(sid, cls)
        if why:
            print(f"{sid:<5}{cls.name:<32}  skipped - needs {why}")
            continue
        runs = []
        for k in range(args.seeds):
            eng = ReplayEngine(settings, SYMBOLS, seed=4000 + k)
            runs.append(eng.run(cls, bars, since_ms=since))
        pooled = [r for run in runs for r in run.r_series]
        if not pooled:
            rej = max(runs[0].rejects.items(), key=lambda kv: kv[1], default=("", 0))
            print(f"{sid:<5}{cls.name:<32}   no trades"
                  f"{('  (top reject: ' + rej[0] + ' x' + str(rej[1]) + ')') if rej[1] else ''}")
            continue
        s = summarise(runs[0])
        lo, hi = boot_ci(pooled)
        eq = st.fmean(r.final_equity for r in runs)
        dd = st.fmean(r.max_drawdown() for r in runs)
        halts = sum(1 for r in runs if r.halted)
        exits = "/".join(f"{v}{k}" for k, v in sorted(s["kinds"].items()))
        verdict = "POSITIVE" if lo > 0 else "negative" if hi < 0 else "flat"
        print(f"{sid:<5}{cls.name:<32}{len(pooled) // args.seeds:>5}{s['win_rate'] * 100:>5.1f}%"
              f"{st.fmean(pooled):>+8.3f}{f'[{lo:+.3f},{hi:+.3f}]':>19}{s['pf']:>6.2f}"
              f"{eq:>9.2f}{dd * 100:>7.1f}%  {exits}  {verdict}"
              f"{f'  HALT x{halts}' if halts else ''}")
        if args.rejects:
            top = sorted(runs[0].rejects.items(), key=lambda kv: -kv[1])[:5]
            print(f"       rejects: {', '.join(f'{k} x{v}' for k, v in top) or 'none'}")


if __name__ == "__main__":
    main()
