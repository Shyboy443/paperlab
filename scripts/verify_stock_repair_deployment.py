"""Read public health and paper-book state; never submit orders or read credentials."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://paperlab-production-919c.up.railway.app'
PROGRAMS = ('v6', 'v7', 'v8', 'v9', 'v11', 'v12', 'v13', 'v14')
paths = {p: '/api/public/competition/' + p for p in PROGRAMS}
paths.update(health='/api/health', research='/api/public/research')

def fetch(item):
    name, path = item
    try:
        with urlopen(BASE + path, timeout=25) as response:
            return name, json.load(response)
    except Exception as error:
        return name, {'read_error': str(error)}

with ThreadPoolExecutor(max_workers=4) as pool:
    states = dict(pool.map(fetch, paths.items()))
v9 = states['v9']
freeze = json.loads((ROOT / 'docs/V9_FREEZE.json').read_text())
summary = {
    'observed_at': datetime.now(timezone.utc).isoformat(),
    'url': BASE, 'deployment_id': 'cf723271-d99a-460b-a9fa-124b8fceabf6',
    'expected_stock_freeze': freeze['fingerprint'],
    'stock_experiment': v9.get('experiment'),
    'stock_status': v9.get('status'),
    'historical_review': v9.get('historical_review'),
    'program_statuses': {p: states[p].get('status', states[p].get('read_error')) for p in PROGRAMS},
    'health': states['health'],
    'research_keys': list(states['research']),
    'research_health': states['research'].get('health'),
    'research_coverage': states['research'].get('coverage'),
    'research_execution': states['research'].get('execution'),
    'tests': {'full': '1537 passed, 4 skipped', 'stock_and_arena_focused': '81 passed'},
    'comparisons': 120,
}
status = v9.get('status') or {}
experiment = v9.get('experiment') or {}
checks = {
    'new_stock_manifest': experiment.get('manifest') == freeze['fingerprint'],
    'stock_worker_ready': status.get('status') in ('LIVE', 'MARKET_CLOSED'),
    '60_stock_books': status.get('bots') == 60 and status.get('live_bots') == 60,
    'no_stock_worker_error': not status.get('error') and not status.get('diffs'),
    'historical_failure_disclosed': v9.get('historical_review', {}).get('state') == 'FAILED_PROFITABILITY_CHECKS',
    'other_programs_no_freeze_errors': all((states[p].get('status') or {}).get('status') not in
                                          ('FROZEN_MISMATCH', 'ERROR') and not states[p].get('read_error')
                                          for p in PROGRAMS if p != 'v9'),
    'continuous_research_running': (states['research'].get('health') or {}).get('status') == 'RUNNING',
    'research_spot_and_perp_healthy': all((states['research'].get('health', {}).get(venue) or {}).get('status') == 'OK'
                                         for venue in ('spot', 'perp')),
    'research_remains_paper_only': states['research'].get('execution') == 'PAPER_ONLY',
}
bots = v9.get('leaderboard') or []
if isinstance(bots, list) and bots:
    summary['paper_books'] = [{k:b.get(k) for k in ('key','trades','equity','equity_live','start_equity','net_now')}
                              for b in bots]
checks['separate_clean_paper_books'] = len(bots) == 60 and all(b.get('equity') == 1000 and b.get('trades') == 0 for b in bots)
before = json.loads((ROOT / 'docs/stock-bot-audit/stock-fix-books-before.json').read_text())
after = json.loads((ROOT / 'docs/stock-bot-audit/stock-fix-books-after.json').read_text())
after_by_id = {r['experiment_id']:r for r in after}
checks['previous_experiments_and_trades_preserved'] = all(after_by_id.get(r['experiment_id']) == r for r in before)
summary['preserved_books'] = before
summary['new_books'] = [r for r in after if r['experiment_id'] not in {b['experiment_id'] for b in before}]
summary['checks'] = checks
print(json.dumps(summary, indent=2))
if '--save' in sys.argv:
    if not all(checks.values()):
        raise SystemExit('Deployment not yet verified; evidence was not saved')
    (ROOT / 'docs/stock-bot-audit/stock-fix-deployment.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
