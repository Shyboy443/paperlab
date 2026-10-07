"""Bounded stock repair experiment: frozen four-way comparison, no live-book changes.

Run through the bootstrap payload on Railway's existing /data/bt2y archive.
LEGACY is the saved stock logic. MECHANICS changes only execution/data mechanics.
SESSION adds session VWAP/target, genuine EMA-band contact and session-local setup
windows. SESSION_WIDE additionally raises only the minimum stop to 0.5%.

Training: 2024-10-01..2025-10-01. Validation: 2025-10-01..2026-04-01.
Final historical check: 2026-04-01..2026-10-01. One continuous $1,000 book
inside EACH split, no daily resets; 30% peak sizing stop retained. Rules are
ranked on training only. These dates were inspected in earlier studies and are
not an independent unseen test. Future paper evidence is still required.
"""
import dataclasses
import functools
import json
import multiprocessing as mp
import os
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from scripts import backtest_2y as bt
from app.backtest.replay import ReplayEngine
from app.competition import v9_config as v9
from app.competition.v6_config import AGGRESSIVE_V6, SizingV6
from app.core.types import MarketRules
from app.execution.config import FeeSchedule
from app.strategies.v9.stocks import load_v9_stock_scalpers as legacy_classes
from app.strategies.v9.fix_candidate import load_v9_stock_scalpers as candidate_classes
from app.live.stock_engine import StockReplayEngine
from app.live.fix_candidate import session_tape as corrected_tape

ROOT = Path('/data/bt2y')
OUT = Path('/data/stock-fix-audit-20261007')
SPLITS = {'train': ('2024-10-01', '2025-10-01'),
          'validation': ('2025-10-01', '2026-04-01'),
          'historical_check': ('2026-04-01', '2026-10-01')}


@functools.lru_cache(maxsize=2)
def tapes(symbol):
    legacy, sessions = bt.stock_tape(ROOT, symbol)
    corrected = corrected_tape(symbol, [b for b in legacy if b.source != 'filled'], sessions, until_ms=bt.END)
    return legacy, corrected, sessions


def run_job(job):
    os.nice(1) if os.getpriority(os.PRIO_PROCESS, 0) < 19 else None
    variant, sid, symbol = job
    legacy, corrected, sessions = tapes(symbol)
    base = (legacy_classes() if variant in ('LEGACY', 'MECHANICS') else candidate_classes())[sid]
    if variant == 'SESSION_WIDE':
        params = dataclasses.make_dataclass('WideParams', [('min_stop_pct', float, dataclasses.field(default=0.005))], bases=(base.Params,))
        base = type(base.__name__ + 'Wide', (base,), {'Params': params})
    cls = base.for_class('SCALP', session_at=sessions.at)
    rules = {symbol: MarketRules(**v9.RULES[symbol])}
    result = {'variant': variant, 'strategy_id': sid, 'symbol': symbol, 'splits': {}}
    for stage, (start_date, end_date) in SPLITS.items():
        start, end = bt.ms(start_date), bt.ms(end_date)
        engine_type = ReplayEngine if variant == 'LEGACY' else StockReplayEngine
        fees = v9.FEES_V9 if variant == 'LEGACY' else FeeSchedule(0.00002, 0.00002, 'alpaca_us_equity', '2026-10-07')
        engine = bt.thin(engine_type)(dataclasses.replace(v9.settings_v9(), daily_halt_pct=1, strategy_halt_pct=1),
                    [symbol], rules=rules, seed=7, funding=None, execution=v9.EXECUTION_V9,
                    fees=fees, fee_source='schedule', sizing=SizingV6(rules, jev=False),
                    leverage_policy='needed', max_risk_pct=AGGRESSIVE_V6.max_risk_pct, cost_gate=None)
        engine.portfolio.closed_trades = deque(maxlen=None)
        bars = legacy if variant == 'LEGACY' else corrected
        replay = engine.run(cls, (b for b in bars if start - bt.WARM_V9 <= b.open_time < end),
                            since_ms=start, leverage=v9.LEVERAGE_CAP, signal_tf='5m', only_symbol=symbol)
        trades = [t for t in replay.trades if t.exit_ts >= start]
        gains = sum(t.net for t in trades if t.net > 0)
        losses = -sum(t.net for t in trades if t.net < 0)
        equity = replay.equity[-1][1] if replay.equity else 1000
        peak, dd = 1000., 0.
        for ts, value in replay.equity:
            if ts >= start:
                peak = max(peak, value)
                dd = max(dd, (peak - value) / peak)
        result['splits'][stage] = {'trades': len(trades), 'net': round(equity - 1000, 6),
                    'profit_factor': round(gains / losses, 6) if losses else None,
                    'gross_profit': round(gains, 6), 'gross_loss': round(losses, 6),
                    'fees': round(sum(t.fees for t in trades), 6), 'max_drawdown': round(dd, 6),
                    'halted': bool(replay.rejects.get('bot_halted')),
                    'stop_count': sum(t.exit_kind == 'stop' for t in trades),
                    'stop_average_r': (sum(t.r_multiple for t in trades if t.exit_kind == 'stop') /
                                       sum(t.exit_kind == 'stop' for t in trades)) if any(t.exit_kind == 'stop' for t in trades) else None}
    return result


def main():
    os.nice(15)
    OUT.mkdir(exist_ok=True)
    variants = ('LEGACY', 'MECHANICS', 'SESSION', 'SESSION_WIDE')
    jobs = [(variant, sid, symbol) for symbol in v9.SYMBOLS for sid in ('V9.1', 'V9.2', 'V9.3') for variant in variants]
    output = []
    with ProcessPoolExecutor(max_workers=2, mp_context=mp.get_context('fork')) as pool:
        futures = {pool.submit(run_job, job): job for job in jobs}
        for index, future in enumerate(as_completed(futures), 1):
            row = future.result()
            output.append(row)
            (OUT / ('__'.join(futures[future]) + '.json')).write_text(json.dumps(row))
            if index % 10 == 0:
                print(f'Completed {index}/{len(jobs)} stock comparisons', flush=True)
    aggregates = {}
    for variant in variants:
        for sid in ('V9.1', 'V9.2', 'V9.3'):
            stages = {}
            for stage in SPLITS:
                rows = [r['splits'][stage] for r in output if r['variant'] == variant and r['strategy_id'] == sid]
                gain, loss = sum(r['gross_profit'] for r in rows), sum(r['gross_loss'] for r in rows)
                stages[stage] = {'net': round(sum(r['net'] for r in rows), 6), 'trades': sum(r['trades'] for r in rows),
                                 'profit_factor': gain / loss if loss else None,
                                 'worst_drawdown': max(r['max_drawdown'] for r in rows)}
            aggregates[variant + '|' + sid] = stages
    selected = {}
    for sid in ('V9.1', 'V9.2', 'V9.3'):
        eligible = [v for v in variants if v != 'LEGACY' and aggregates[v + '|' + sid]['train']['trades'] >= 100]
        winner = max(eligible, key=lambda v: aggregates[v + '|' + sid]['train']['net']) if eligible else None
        stages = aggregates[winner + '|' + sid] if winner else {}
        selected[sid] = {'selected_on_training_only': winner,
                         'passed_historical_checks': bool(winner and all(
                             s['net'] > 0 and (s['profit_factor'] or 0) >= 1.1 and s['trades'] >= 50
                             for s in stages.values())), 'results': stages}
    report = {'protocol': __doc__, 'source_payload_sha256': globals().get('SOURCE_SHA256'),
              'comparisons': len(output), 'aggregate': aggregates, 'selected': selected, 'bots': output}
    (OUT / 'results.json').write_text(json.dumps(report, indent=2))
    print('STOCK_FIX_RESULTS=' + json.dumps({'aggregate': aggregates, 'selected': selected}), flush=True)


if __name__ == '__main__':
    main()
