"""Build a reproducible stock repair report from the frozen comparison output."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import zlib

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/stock-bot-audit'
results = json.loads((OUT / 'V9_FIX_VALIDATION_RESULTS.json').read_text())
freeze = json.loads((ROOT / 'docs/V9_FREEZE.json').read_text())
bootstrap = zlib.decompress(base64.b64decode((OUT / 'stock-fix-bootstrap.b64').read_text())).decode()
assignment = next(n for n in ast.parse(bootstrap).body if isinstance(n, ast.Assign) and n.targets[0].id == 'data')
frozen_sources = json.loads(ast.literal_eval(assignment.value.args[0]))
assert hashlib.sha256(json.dumps(frozen_sources).encode()).hexdigest() == results['source_payload_sha256']

def methods(source):
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'StockReplayEngine')
    return {n.name: ast.dump(n) for n in cls.body if isinstance(n, ast.FunctionDef)}

old = methods(frozen_sources['app.live.stock_engine'])
current = methods((ROOT / 'app/live/stock_engine.py').read_text())
for name in ('_execute_pending', '_level_between', '_step', '_walk', '_close'):
    assert old[name] == current[name], f'Unvalidated CONTROL execution change: {name}'
# The final stock strategy differs only in its explanatory module docstring.
def behaviour(source):
    tree = ast.parse(source)
    tree.body = tree.body[1:]
    return ast.dump(tree)
assert behaviour(frozen_sources['app.strategies.v9.fix_candidate']) == behaviour((ROOT / 'app/strategies/v9/stocks.py').read_text())
assert behaviour(frozen_sources['app.live.fix_candidate']) == behaviour((ROOT / 'app/live/alpaca_market.py').read_text())

lines = [
    '# Stock bot repair audit — 7 October 2026', '',
    'The execution and session bugs are repaired. The historical comparisons still show negative expectancy '
    'for all three families. This is a correctness repair and a fresh paper experiment, not a demonstrated profitable bot.', '',
    'A 1,000% return is not an acceptance benchmark. It means turning $1,000 into $11,000; these tests provide no evidence '
    'for that outcome. More leverage would amplify the measured losses.', '',
    '## Concrete faults and repairs', '',
    '1. Stops were executed at a later minute extreme rather than the crossed stop level. The stock engine now crosses '
    'the level and charges friction. Real gaps still execute at the opening price; stop/target ambiguity uses the adverse path first.',
    '2. Targets received the subsequent favourable extreme. They now act as resting limits: 0.5 bp trade-through is required '
    'and the fill is at the target, with a regulatory-fee proxy on both market and limit fills.',
    '3. Silent opening minutes could inherit yesterday’s closing price. Repair now requires a timestamp proving the previous '
    'bar belongs to today’s session. No current real bar means no invented opening price.',
    '4. EMA pullbacks counted any distant low below the average as a touch, and compared past bars with the current EMA. '
    'They now require genuine overlap with each bar’s own EMA band and a subsequent resumption close.',
    '5. Stock VWAP was a rolling four-hour average across sessions. The reversal setup now uses today’s HLC3/volume proxy, '
    'checks the prior stretch before the reversal bar, and requires at least 0.75R to the session-average target.',
    '6. Setup windows could cross the overnight boundary, and zero-volume synthetic bars could trigger entries. '
    'Structural windows are session-local and synthetic signal bars are rejected.',
    '7. Position deadlines relied on the entry window to prevent overnight exposure. Filled positions now explicitly cap '
    'their deadline one minute before the calendar close, including half days.',
    '8. Changed strategies could reuse a result cached under the same bot key. V9 caches now include the freeze identity and test window.',
    '9. AI-rejected counterfactual positions now use the same stock exit rules, target prices, costs and session deadlines. '
    'Regression tests compare them directly with accepted trades.', '',
    'Shared crypto execution code was not changed. The repaired stock engine is isolated. Old sources, freezes, forward '
    'experiments and historical results remain available.', '',
    '## Bounded comparison', '',
    '120 variant/family/symbol comparisons, each with three independently funded $1,000 books. Ten symbols per family. '
    'Within each period a book runs continuously; there are no daily wallet resets. The 30% peak sizing halt remains; '
    'daily and strategy halts are disabled for this research comparison. Production elimination remains unchanged.', '',
    '| Period | Dates, end exclusive |', '|---|---|',
    '| Training | 2024-10-01 to 2025-10-01 |',
    '| Validation | 2025-10-01 to 2026-04-01 |',
    '| Final historical check | 2026-04-01 to 2026-10-01 |', '',
    'LEGACY uses the archived original stock rules and execution. MECHANICS changes the execution/data handling and fee proxy. '
    'SESSION adds the stock-specific correctness repairs. SESSION_WIDE additionally changes only the stop floor from 0.25% to 0.5%. '
    'Candidates rank on training net only. Passing requires positive net, PF ≥ 1.1 and ≥ 50 trades in every stage.', '',
    '**No selected family passed.** Wider stops ranked highest on training but still lost in every aggregate stage, '
    'so that parameter change is not adopted. SESSION is the deployed correctness version; it is not described as the winning strategy.', '',
    'The dates were inspected in earlier research. These later periods are temporal checks, not an independent unseen test. '
    'Fresh paper results must provide genuinely new evidence.', '',
    '## Family returns', '',
    'Each percentage below is aggregate net divided by the ten independently funded books ($10,000 per family per stage). '
    'Stage percentages must not be added or compounded into a two-year return.', '',
    '| Family / variant | Training | Validation | Final historical check | Final PF |', '|---|---:|---:|---:|---:|',
]
for sid in ('V9.1', 'V9.2', 'V9.3'):
    for variant in ('LEGACY', 'MECHANICS', 'SESSION', 'SESSION_WIDE'):
        stages = results['aggregate'][variant + '|' + sid]
        values = [f"{stages[s]['net']/100:.2f}%" for s in ('train', 'validation', 'historical_check')]
        lines.append(f"| {sid} / {variant} | {' | '.join(values)} | {stages['historical_check']['profit_factor']:.3f} |")
legacy_last = sum(results['aggregate']['LEGACY|' + sid]['historical_check']['net'] for sid in ('V9.1','V9.2','V9.3'))
session_last = sum(results['aggregate']['SESSION|' + sid]['historical_check']['net'] for sid in ('V9.1','V9.2','V9.3'))
lines += ['', f'Across all 30 CONTROL books in April–September 2026, the original version lost ${-legacy_last:,.2f} '
          f'({legacy_last/300:.2f}% on $30,000) and SESSION lost ${-session_last:,.2f} ({session_last/300:.2f}%). '
          f'That is {(1-session_last/legacy_last)*100:.1f}% less aggregate loss, not a positive return.', '',
          '## The three bots in the screenshot', '',
          'Final historical check only (April–September 2026); each starts with a separate $1,000. '
          'Improvement is not uniform. Iris regressed under the session-target change.', '',
          '| Bot | Original return | Repaired SESSION return | Repaired PF | Repaired trades |', '|---|---:|---:|---:|---:|']
for name, sid, symbol in [('Halo','V9.1','TSLA'),('Iris','V9.3','AAPL'),('Oscar','V9.1','AMZN')]:
    before = next(r for r in results['bots'] if (r['variant'],r['strategy_id'],r['symbol']) == ('LEGACY',sid,symbol))['splits']['historical_check']
    after = next(r for r in results['bots'] if (r['variant'],r['strategy_id'],r['symbol']) == ('SESSION',sid,symbol))['splits']['historical_check']
    lines.append(f"| {name} ({symbol}) | {before['net']/10:.2f}% | {after['net']/10:.2f}% | {after['profit_factor']:.3f} | {after['trades']} |")
lines += ['', 'The original continuous two-year peak/wallet audit remains in '
          '[Stock-Bot-Performance-Audit.md](stock-bot-audit/Stock-Bot-Performance-Audit.md). '
          'Its balances are not replaced with the independent split balances above.', '',
          '## Why results remain weak', '',
          'The bugs distorted exits, but the short-horizon signals still lack demonstrated edge after costs. '
          'Five-minute setups with a 45-minute holding limit do not capture the whole multi-month stock trend. '
          'The original two-year price-only buy-and-hold changes were TSLA +35.08%, AAPL +45.18%, AMZN +34.74%, '
          'SPY +32.94% and QQQ +51.63% (no dividends, costs or financing). Those benchmarks are not bot returns.', '',
          'Minute OHLC cannot reveal the actual intrabar sequence or order queue. Historical runs use modelled spread; '
          'production uses IEX quotes capped at 1 bp. That cap is an assumption and is not verified NBBO routing. '
          'The session VWAP is a bar-price proxy, not a consolidated tick VWAP. Regulatory fees are approximated.', '',
          '## Verification and deployment', '',
          f"- Final strategy freeze: `{freeze['fingerprint']}`; a new experiment starts separate $1,000 paper books.",
          f"- Frozen historical study SHA-256: `{results['source_payload_sha256']}`.",
          '- The final CONTROL execution and signal bodies were checked against the frozen study source. '
          'Subsequent counterfactual repairs affect the AI shadow book only.',
          '- Focused regressions cover stops, gaps, limit fills, AI counterfactual parity, session repair, EMA contact, '
          'VWAP time boundaries, silent-bar rejection and stale-cache prevention.',
          '- Full suite: **1,537 passed, 4 skipped**. Final focused stock/arena suite: **81 passed**. '
          'Output: `stock-bot-audit/stock-fix-full-tests.txt` and `stock-fix-focused-tests.txt`.',
          '- Deployment evidence: `stock-bot-audit/stock-fix-deployment.json`.',
          '- Machine-readable comparison: `stock-bot-audit/V9_FIX_VALIDATION_RESULTS.json`.', '',
          'The repaired books remain paper-only. No live-money bot was enabled.']
deployment_path = OUT / 'stock-fix-deployment.json'
if deployment_path.exists():
    deployment = json.loads(deployment_path.read_text())
    assert all(deployment['checks'].values())
    lines += ['', '## Confirmed production state', '',
              f"Railway deployment `{deployment['deployment_id']}` succeeded. The stock worker is "
              f"`{deployment['stock_status']['status']}` with 60 ready books under experiment "
              f"`{deployment['stock_status']['experiment_id']}`. All start at $1,000 with zero trades. "
              'The market is closed; the first new trades can occur at the next regular session.', '',
              'All previous experiment identities and trade counts were compared before and after deployment and match. '
              'The previous active experiment retains its 248 trades.', '',
              'The continuous research service is RUNNING with healthy feeds for 502 spot and 525 perpetual markets. '
              'It remains PAPER_ONLY. Other programs have no frozen-version errors; large books may be warming up '
              'while their histories are restored.', '',
              f"Evidence captured at `{deployment['observed_at']}`."]
(ROOT / 'docs/V9_STOCK_REPAIR_AUDIT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(json.dumps({'comparisons':results['comparisons'], 'freeze':freeze['fingerprint'],
                  'legacy_last_net':legacy_last, 'session_last_net':session_last,
                  'source_parity':'CONTROL execution and signals match frozen study'}))
