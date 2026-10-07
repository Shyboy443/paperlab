"""Read the bounded stock study without exposing service variables or credentials."""
import base64
import json
from pathlib import Path
import subprocess
import sys
import os

PROJECT = '10add7d7-f7f5-40be-9197-04afa241aa82'
ROOT = Path(__file__).resolve().parents[1]
mode = sys.argv[1] if len(sys.argv) > 1 else 'status'
if mode == 'status':
    source = '''from pathlib import Path
import json
root=Path('/data/stock-fix-audit-20261007')
processes=[]
for p in Path('/proc').glob('[0-9]*'):
    try:
        lines=p.joinpath('status').read_text().splitlines()
        fields={k:v.strip() for line in lines if ':' in line for k,v in [line.split(':',1)]}
        if 'python' in fields.get('Name',''):
            processes.append({k:fields.get(k) for k in ('Name','State','Pid','PPid','VmRSS')})
    except (FileNotFoundError,ProcessLookupError,PermissionError):
        pass
print(json.dumps({'completed':len(list(root.glob('*__*.json'))),'results':root.joinpath('results.json').exists(),'processes':processes}))
'''
elif mode == 'fetch':
    source = "from pathlib import Path; print(Path('/data/stock-fix-audit-20261007/results.json').read_text())"
elif mode == 'books':
    source = '''import sqlite3,json
db=sqlite3.connect('file:/data/v9-forward.db?mode=ro',uri=True)
db.row_factory=sqlite3.Row
rows=db.execute('SELECT experiment_id, created_ts, forward_start_ms FROM fwd6_experiments ORDER BY created_ts').fetchall()
output=[]
for row in rows:
    item=dict(row)
    item['trades']=db.execute('SELECT count(*) FROM fwd6_trades WHERE experiment_id=? AND counterfactual=0',(row['experiment_id'],)).fetchone()[0]
    output.append(item)
print(json.dumps(output))
'''
else:
    raise SystemExit('Use status, fetch or books')
encoded = base64.b64encode(source.encode()).decode()
command = f"python -c 'exec(__import__(\"base64\").b64decode(\"{encoded}\"))'"
cli = Path(os.environ['APPDATA']) / 'npm/node_modules/@railway/cli/bin/railway.exe'
result = subprocess.run([str(cli), 'ssh', '--project', PROJECT, '--service', 'paperlab',
                         '--environment', 'production', '--', command], capture_output=True, text=True)
if result.returncode:
    raise SystemExit(result.stderr + result.stdout)
if mode == 'fetch':
    parsed = json.loads(result.stdout)
    path = ROOT / 'docs/stock-bot-audit/V9_FIX_VALIDATION_RESULTS.json'
    path.write_text(json.dumps(parsed, indent=2), encoding='utf-8')
    print(json.dumps({'comparisons':parsed['comparisons'], 'aggregate':parsed['aggregate'], 'selected':parsed['selected']}))
else:
    print(result.stdout)
    if mode == 'books':
        destination = sys.argv[2] if len(sys.argv) > 2 else 'stock-fix-books.json'
        (ROOT / 'docs/stock-bot-audit' / destination).write_text(result.stdout, encoding='utf-8')
