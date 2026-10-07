"""Run a Bot Trading Competition season and print the leaderboard.

    python scripts/competition.py --months 2026-07,2026-08 --balance 20 --leverage 20
    python scripts/competition.py --months 2026-08 --sort return --detail S04

Every competitor starts the season with the same balance, sees the same bars in the same order, and
keeps its own wallet. Ranking and qualification are printed as separate columns on purpose: the bot
at rank #1 is frequently not a bot you would promote.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest import load_rules  # noqa: E402
from app.backtest import funding as funding_mod  # noqa: E402
from app.competition import CompetitionEngine, Season  # noqa: E402
from app.backtest import brackets as bracket_mod  # noqa: E402
from app.competition.config import RiskConfig  # noqa: E402
from app.execution.config import ExecutionConfig  # noqa: E402
from app.strategies.registry import load_all  # noqa: E402
from scripts.backtest import settings_for, tape  # noqa: E402
from scripts.backtest_s15 import fetch  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def money(x: float) -> str:
    return f"{x:+.2f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default="2026-08")
    ap.add_argument("--balance", type=float, default=20.0)
    ap.add_argument("--leverage", type=int, default=20)
    ap.add_argument("--sort", default="score",
                    choices=["score", "return", "expectancy", "profit_factor", "drawdown", "trades"])
    ap.add_argument("--only", default="", help="comma-separated strategy ids")
    ap.add_argument("--detail", default="", help="print the full card for one strategy")
    ap.add_argument("--no-funding", action="store_true")
    ap.add_argument("--profile", default="new", choices=["new", "old"],
                    help="'old' reproduces the pre-ExecutionModel behaviour for A/B comparison: "
                         "flat 2bps slippage, zero latency, and the wrong flat 2.5%% maintenance "
                         "margin that exchangeInfo reports")
    args = ap.parse_args()

    months = [m.strip() for m in args.months.split(",") if m.strip()]
    settings = settings_for(args.balance)
    rules = load_rules(settings, SYMBOLS)
    data = {s: fetch(s, months, []) for s in SYMBOLS}
    bars = tuple(tape(data))
    if not bars:
        print("no bars for", months)
        return
    sched = None
    if not args.no_funding:
        sched = funding_mod.load(SYMBOLS, months, PROJECT / "data" / "funding")

    if args.profile == "old":
        execution = ExecutionConfig(level=3, base_slippage_bps=2.0, vol_component=0.0,
                                    signal_latency_ms=0, order_latency_ms=0)
        table = bracket_mod.BracketTable(
            {s: [bracket_mod.Bracket(float("inf"), 0.025, 0.0)] for s in SYMBOLS},
            source="legacy_exchangeInfo_default")
    else:
        execution = ExecutionConfig()
        table = bracket_mod.load(SYMBOLS, PROJECT / "data")

    season = Season(
        season_id="-".join(months), competition_id="paperlab-bake-off",
        start_ms=bars[0].close_time, end_ms=bars[-1].close_time, symbols=SYMBOLS,
        label=f"{months[0]}..{months[-1]}",
        execution=execution,
        risk=RiskConfig(starting_balance=args.balance, max_leverage=args.leverage))

    classes = load_all(strict=True)
    only = [s.strip().upper() for s in args.only.split(",") if s.strip()] or None
    run = CompetitionEngine(settings, season, rules=rules, funding=sched,
                        brackets=table).run(classes, bars, only=only)

    s = run.summary()
    fund_cov = sched.coverage() if sched else {}
    print("=" * 132)
    print(f"COMPETITION {s['season']}  {s['label']}   {s['bars']} bars   {s['competitors']} competitors "
          f"({s['skipped']} skipped)   {s['starting_balance']:.2f} USDT each   max {args.leverage}x")
    print(f"fees maker {season.fees.maker_rate * 100:.3f}% / taker {season.fees.taker_rate * 100:.3f}%   "
          f"funding events {fund_cov or 'OFF'}   fingerprint {run.fingerprint}")
    print(f"profile {args.profile}   execution L{season.execution.level} "
          f"latency {season.execution.total_latency_ms}ms   "
          f"mmr {table.source} {'/'.join(f'{k}={v}' for k, v in sorted(table.first_bracket_mmr().items()))}")
    print("=" * 132)
    print(f"{'#':>2} {'bot':<5}{'name':<28}{'equity':>8}{'gross':>9}{'net':>9}{'ret%':>8}{'maxDD':>7}"
          f"{'n':>5}{'win%':>6}{'expR':>7}{'PF':>6}{'fees':>7}{'slip':>7}{'fund':>7}{'lev':>5}"
          f"{'score':>7}  status")
    for i, c in enumerate(run.leaderboard(args.sort), 1):
        m = c.metrics
        pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        why = f"  <- {c.qualification.reasons[0]}" if c.qualification and c.qualification.reasons else ""
        print(f"{i:>2} {c.strategy_id:<5}{c.name[:27]:<28}{m.ending_equity:>8.2f}{money(m.gross_pnl):>9}"
              f"{money(m.net_profit):>9}{m.net_return_pct * 100:>7.1f}%{m.max_drawdown_pct * 100:>6.1f}%"
              f"{m.trades:>5}{m.win_rate * 100:>5.0f}%{m.expectancy_r:>+7.2f}{pf:>6}"
              f"{-m.fees_paid:>7.2f}{-m.slippage_cost:>7.2f}{m.funding_paid:>+7.2f}"
              f"{m.max_leverage_used:>4}x{c.score:>7.3f}  {c.state}{why}")

    skipped = [c for c in run.competitors if c.skipped]
    if skipped:
        print("\nskipped: " + ", ".join(f"{c.strategy_id}({c.skipped})" for c in skipped))
    q = run.qualified()
    print(f"\nQUALIFIED SET: {', '.join(c.strategy_id for c in q) if q else 'empty'}")
    print("states: " + ", ".join(f"{k}={v}" for k, v in sorted(s["states"].items())))

    if args.detail:
        card(run, args.detail.upper())


def card(run, sid: str) -> None:
    comp = next((c for c in run.competitors if c.strategy_id == sid and c.metrics), None)
    if comp is None:
        print(f"\n{sid}: no result")
        return
    m, b = comp.metrics, comp.breakdown
    print("\n" + "=" * 132)
    print(f"{sid} {comp.name}   version {comp.version}   state {comp.state}")
    print("=" * 132)
    print("  GROSS vs NET")
    print(f"    gross trading PnL        {money(m.gross_pnl):>10}")
    print(f"    commission               {-m.fees_paid:>10.2f}")
    print(f"    slippage (decision->fill){-m.slippage_cost:>10.2f}")
    print(f"        latency in flight    {-m.latency_cost:>10.2f}")
    print(f"        spread crossed       {-m.spread_cost:>10.2f}")
    print(f"        market impact        {-m.impact_cost:>10.2f}")
    print(f"    funding                  {m.funding_paid:>+10.2f}")
    print(f"    {'-' * 44}")
    print(f"    NET                      {money(m.net_profit):>10}   "
          f"equity {m.starting_equity:.2f} -> {m.ending_equity:.2f}")
    print(f"\n  trades {m.trades}  win {m.win_rate * 100:.0f}%  expR {m.expectancy_r:+.3f}  "
          f"medianR {m.median_r:+.3f}  best {m.best_trade:+.2f}  worst {m.worst_trade:+.2f}")
    print(f"  maxDD {m.max_drawdown_pct * 100:.1f}% ({m.max_drawdown_usdt:.2f} USDT)  "
          f"recovery {m.recovery_factor:.2f}  streaks +{m.longest_win_streak}/-{m.longest_loss_streak}  "
          f"turnover {m.turnover:.1f}x  maxlev {m.max_leverage_used}x")
    print(f"  fee/gross {m.fee_to_gross_profit_ratio * 100:.0f}%  "
          f"slip/gross {m.slippage_to_gross_profit_ratio * 100:.0f}%  "
          f"top-trade share {m.largest_trade_profit_contribution_pct * 100:.0f}%  "
          f"liquidations {m.liquidation_count}")
    print(f"  by symbol: {m.by_symbol}")
    print(f"  exits: {m.by_exit_kind}")
    top = sorted(m.rejects.items(), key=lambda kv: -kv[1])[:6]
    print(f"  top rejects: {', '.join(f'{k} x{v}' for k, v in top) or 'none'}")
    print(f"\n  SCORE {b.explain()}")
    print("\n  GATES")
    for g in comp.qualification.gates:
        mark = "PASS" if g.ok else ("soft" if g.soft else "FAIL")
        print(f"    [{mark:>4}] {g.describe():<52}{g.detail}")
    if comp.qualification.reasons:
        print("  not qualified because: " + "; ".join(comp.qualification.reasons))


if __name__ == "__main__":
    main()
