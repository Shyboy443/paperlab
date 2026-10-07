"""Run a Bot Arena discovery competition: specialist bots, one coin and one timeframe each.

    python scripts/run_arena.py                                  # the default ten-coin arena
    python scripts/run_arena.py --plan-only                      # preflight and selection only
    python scripts/run_arena.py --coins SOLUSDT,BNBUSDT --max-bots 12

Each bot gets its own isolated 20 USDT wallet and may trade only the symbol in its identity, on its
strategy's native signal timeframe. Candidates are generated deterministically and trimmed by a
strategy x coin rotation -- never picked because they are already known to look good.

Discovery ADVANCES a bot to multi-year validation. It never qualifies one for live trading.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.backtest import load_rules  # noqa: E402
from app.backtest import brackets as bracket_mod  # noqa: E402
from app.competition import universe as universe_mod  # noqa: E402
from app.competition.arena import Arena, ArenaConfig  # noqa: E402
from app.competition.bots import ARENA_TIMEFRAMES  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.strategies.registry import load_all, load_v2  # noqa: E402
from scripts.backtest import settings_for  # noqa: E402

DEFAULT_COINS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                 "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT")


def pct(x, d=2):
    return "-" if x is None else f"{x * 100:.{d}f}%"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default="2026-07,2026-08")
    ap.add_argument("--coins", default=",".join(DEFAULT_COINS))
    ap.add_argument("--timeframes", default=",".join(ARENA_TIMEFRAMES))
    ap.add_argument("--leverage", type=int, default=20, help="leverage CEILING per bot")
    ap.add_argument("--strategies", default="", help="comma separated; blank = all")
    ap.add_argument("--balance", type=float, default=20.0)
    ap.add_argument("--profile", default="AGGRESSIVE")
    ap.add_argument("--max-bots", type=int, default=30)
    ap.add_argument("--min-bots", type=int, default=10)
    ap.add_argument("--min-volume", type=float, default=50e6, help="24h quote volume floor")
    ap.add_argument("--db", default=str(PROJECT / "data" / "paperlab.db"))
    ap.add_argument("--label", default="")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--version", default="v1", choices=("v1", "v2"), help="strategy behaviour version")
    ap.add_argument("--cost-gate", type=float, default=None, help="min edge-to-cost ratio; off if omitted")
    ap.add_argument("--dataset-role", default="", help="DEVELOPMENT / TEST (docs/DATASET_SPLIT_V2.md)")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--keys", default="", help="pre-registered field: comma separated bot keys, or @file")
    args = ap.parse_args(argv)

    settings = settings_for(args.balance)
    months = [m.strip() for m in args.months.split(",") if m.strip()]
    coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    tfs = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    # Production metadata, fetched now and recorded: the arena models real trading constraints.
    rules = load_rules(settings, coins, refresh=True, environment="production")
    uni = universe_mod.load(settings, universe_mod.UniverseFilters(
        wallet=args.balance, max_leverage=args.leverage, min_quote_volume_24h=args.min_volume),
        refresh=True)
    eligible = set(universe_mod.eligible(uni))
    uni_reason = {s.symbol: s.reason for s in uni}

    classes = load_v2() if args.version == "v2" else load_all(strict=True)
    wanted = [s.strip().upper() for s in args.strategies.split(",") if s.strip()]
    if wanted:
        classes = {k: v for k, v in classes.items() if k in wanted}

    only: tuple[str, ...] = ()
    if args.keys:
        raw = Path(args.keys[1:]).read_text(encoding="utf-8") if args.keys.startswith("@") else args.keys
        lines = [ln.split("#", 1)[0] for ln in raw.splitlines()]     # comments first, then commas
        only = tuple(k.strip() for ln in lines for k in ln.split(",") if k.strip())
    fingerprints: tuple[tuple[str, str], ...] = ()
    if args.version == "v2":
        from app.strategies.registry import v2_fingerprints
        fingerprints = tuple(sorted(v2_fingerprints().items()))
    cfg = ArenaConfig(min_active_bots=args.min_bots, max_bots=args.max_bots,
                      starting_balance=args.balance, profile=args.profile,
                      leverage_ceiling=args.leverage, params_version=args.version,
                      cost_gate_min_ratio=args.cost_gate, dataset_role=args.dataset_role.upper(),
                      only_keys=only, strategy_fingerprints=fingerprints)
    label = args.label or f"Specialist Arena - discovery {months[0]}..{months[-1]}"
    storage = None if args.plan_only else Storage(args.db)
    arena = Arena(settings, cfg, months, rules,
                  brackets=bracket_mod.load(coins, settings.data_dir), storage=storage,
                  label=label)
    arena.universe_reasons = uni_reason

    # ---------------- preflight ----------------
    t0 = time.time()
    run = arena.plan(classes, coins, sorted(eligible), timeframes=tfs)
    print("=" * 118)
    print(f"PREFLIGHT   {label}")
    print("=" * 118)
    print(f"risk {pct(cfg.risk.ordinary_risk_pct)} ordinary of {args.balance:.0f} USDT = "
          f"{cfg.risk.ordinary_risk_pct * args.balance:.2f} USDT; fee gate: round trip <= "
          f"{cfg.max_fee_share_of_r:.0%} of risk at taker {cfg.fees.taker_rate:.3%}; "
          f"min-notional safety x{cfg.min_notional_safety_multiplier:g}")
    print(f"{'symbol':<9}{'minNotional':>12}{'minQty':>9}{'ref price':>12}{'exch min':>10}"
          f"{'PaperLab min':>13}{'fee cap':>9}{'stop band':>17}{'risk needed':>12}  tradeable")
    for r in run.symbol_table:
        if "floor" not in r:
            print(f"{r['symbol']:<9}  NO  {r['reason']}")
            continue
        band = f"{pct(r['min_stop_pct'])}-{pct(r['max_stop_pct'])}" if r["max_stop_pct"] >= r["min_stop_pct"] else "empty"
        ok = "YES" if r["tradeable"] else f"NO  {r['reason']}"
        vol = "" if r["symbol"] in eligible else f"  [universe: {uni_reason.get(r['symbol']) or 'not listed'}]"
        print(f"{r['symbol']:<9}{r['min_notional']:>12g}{r['min_qty']:>9g}{r['reference_price']:>12.4f}"
              f"{r['exchange_min']:>10.2f}{r['floor']:>13.2f}{r['ceiling']:>9.2f}{band:>17}"
              f"{pct(r['min_risk_pct_needed']):>12}  {ok}{vol}")
    print()
    print(f"{'strategy':<9}{'native':>7}  {'supported':<11}{'context':<10}")
    for r in run.strategy_table:
        print(f"{r['strategy_id']:<9}{str(r['native_timeframe']):>7}  "
              f"{','.join(r['supported_timeframes']) or '-':<11}{','.join(r['context_timeframes']) or '-':<10}")
    reasons: dict[str, list[str]] = {}
    for b in run.not_entered:
        reasons.setdefault(b.reason, []).append(b.key)
    print(f"\nNOT ENTERED: {len(run.not_entered)} candidates")
    for reason, keys in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(keys):>3}  {reason}")
    print(f"\nELIGIBLE: {len(run.bots) + len(run.eligible_not_selected)}   SELECTED (active): "
          f"{len(run.bots)}   eligible but not selected (cap {cfg.max_bots}): "
          f"{len(run.eligible_not_selected)}")
    for b in run.bots:
        print(f"  {b.key}")
    if args.plan_only:
        return 0

    # ---------------- the competition ----------------
    def progress(br, n, total):
        m = br.metrics
        if m:
            print(f"  [{n:>2}/{total}] {br.key:<28}{br.state:<20} signals={br.extra.get('signals', 0):>4} "
                  f"trades={m.trades:>4} net={m.net_profit:>+7.2f} {br.elapsed_s:>5.1f}s", flush=True)
        else:
            print(f"  [{n:>2}/{total}] {br.key:<28}{br.state:<20} {br.reason}", flush=True)

    print(f"\nrunning {len(run.bots)} bots over {months[0]}..{months[-1]} ...", flush=True)
    run = arena.run(classes, coins, sorted(eligible), timeframes=tfs, on_done=progress,
                    workers=args.workers, version=args.version)
    s = run.summary()
    print("\n" + "=" * 118)
    print(f"BOT ARENA  {s['status']}   competition id {run.run_id}   {s['active_bots']} ACTIVE BOTS "
          f"(minimum {cfg.min_active_bots})   {time.time() - t0:.0f}s")
    print(f"coins {', '.join(s['coins'])}   timeframes {', '.join(s['timeframes'])}   "
          f"not entered {s['not_entered']}   config {cfg.fingerprint()}")
    print("=" * 118)
    hdr = (f"{'#':>3} {'bot':<27}{'coin':<5}{'tf':<4}{'lev':>4}{'eff':>6}{'n':>5}{'gross':>8}{'fees':>7}"
           f"{'slip':>7}{'fund':>7}{'net':>8}{'ret%':>7}{'expR':>7}{'PF':>6}{'DD':>6}{'liq':>4}  state")
    print(hdr)
    for b in run.ranked():
        m = b.metrics
        pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        print(f"{b.rank:>3} {b.key:<27}{b.spec.coin:<5}{b.spec.timeframe:<4}{b.spec.max_leverage:>3}x"
              f"{m.avg_effective_leverage:>5.2f}x{m.trades:>5}{m.gross_pnl:>+8.2f}{-m.fees_paid:>+7.2f}"
              f"{-m.slippage_cost:>+7.2f}{m.funding_paid:>+7.2f}{m.net_profit:>+8.2f}"
              f"{m.net_return_pct * 100:>6.1f}%{m.expectancy_r:>+7.2f}{pf:>6}"
              f"{m.max_drawdown_pct * 100:>5.1f}%{m.liquidation_count:>4}  {b.state}")
    for b in [b for b in run.bots if not b.metrics]:
        print(f"  - {b.key:<27}{b.state}  {b.reason}")
    print(f"\nprofitable after costs: {s['profitable_after_costs']}/{s['bots_with_results']}   "
          f"traded: {s['bots_that_traded']}   liquidated: {s['liquidated']}   "
          f"trades: {s['total_trades']}")
    print(f"gross {s['total_gross_pnl']:+.2f}   fees {-s['total_fees']:+.2f}   slippage "
          f"{-s['total_slippage']:+.2f}   funding {s['total_funding']:+.2f}   net {s['total_net_pnl']:+.2f}")
    print(f"best {s['best_bot']}   worst {s['worst_bot']}")
    adv = run.advanced()
    print(f"\nADVANCE TO MULTI-YEAR VALIDATION: {', '.join(b.key for b in adv) if adv else 'NONE'}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "summary": s, "config": cfg.to_dict(), "preflight": run.preflight_report(),
            "bots": [b.to_dict() for b in run.ranked()]}, indent=1, default=str))
    if storage is not None:
        storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
