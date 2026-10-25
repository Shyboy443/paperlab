"""Build read-only deployment artifacts from verified research, never broker state."""
import gzip,hashlib,json,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.value_reversal.crypto_data import NY
root=Path('data/crypto_value_reversal');source=Path('app/value_reversal/sources')
report=json.loads(Path('research/crypto_value_reversal/results.json').read_text())
proof=json.loads(Path('data/crypto-pine-parity-verified.json').read_text())
if any(j['mismatches'] for j in proof['jobs']):raise ValueError('Source validation failed')
proof.update(indicator_sha256=hashlib.sha256(Path('app/value_reversal/crypto_indicator.py').read_bytes()).hexdigest(),
             pine_source_sha256=hashlib.sha256((source/'value-area-reversion-official.pine').read_bytes()).hexdigest())
(source/'crypto-source-parity.json').write_text(json.dumps(proof,indent=2))
expected={n:hashlib.sha256(Path('app/value_reversal',n).read_bytes()).hexdigest() for n in report['source_sha256']}
if expected!=report['source_sha256']:raise ValueError('Report code changed; rerun required')
report['source_parity']=proof
replays={}
for symbol in report['symbols']:
    for minutes in (15,30):
        wanted=set()
        for r in report['results']:
            if r['config']['symbol']!=symbol or r['config']['minutes']!=minutes:continue
            for p in r['periods'].values():
                for sig in p['net']['signals']:
                    local=datetime.fromtimestamp(sig['ts']/1000,timezone.utc).astimezone(NY)
                    date=local.date() if local.hour>=18 else local.date()-timedelta(days=1)
                    wanted.add(str(date))
        with gzip.open(root/f'{symbol}-{minutes}m-features.json.gz','rt') as f:rows=json.load(f)
        selected={}
        for row in rows:
            local=datetime.fromtimestamp(row['bar'][0]/1000,timezone.utc).astimezone(NY)
            date=str(local.date() if local.hour>=18 else local.date()-timedelta(days=1))
            if date in wanted:selected.setdefault(date,[]).append(row)
        replays[f'{symbol}-{minutes}m']=selected
for name,data in [('crypto-research-report.json.gz',report),('crypto-research-replay.json.gz',replays)]:
    with gzip.open(source/name,'wt') as f:json.dump(data,f,separators=(',',':'),allow_nan=False)
    print(name,(source/name).stat().st_size,'bytes',flush=True)
Path('research/crypto_value_reversal/results.json').write_text(json.dumps(report,separators=(',',':')))
