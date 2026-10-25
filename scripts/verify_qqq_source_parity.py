import sys,subprocess
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gzip,json
from pathlib import Path
from app.value_reversal.model import Book,Config,aggregate
with gzip.open('data/qqq-value-reversal-input.json.gz','rt') as f:data=json.load(f)
out=[]
for minutes in (15,30):
 groups=[s for s in aggregate(data['bars'],data['sessions'],minutes) if '2024-01-01'<=s['date']<='2025-12-31']
 for window in (5,10):
  books=[Book(Config(minutes,p,max_reversion_bars=window)) for p in ('previous','developing')]
  for b in books:b.forward_start=1
  candles=[];expected=[]
  for group in groups:
   for bar in group['bars']:
    ts,o,h,l,c,v=bar;candles.append({'openTime':ts,'closeTime':ts+minutes*60000,'open':o,'high':h,'low':l,'close':c,'volume':v})
    row={'time':ts}
    for b in books:
     b.process(bar,group,trade=False);p=b.latest_profile
     row[b.cfg.profile]={'low':p['low'] if p else None,'high':p['high'] if p else None,'poc':p['poc'] if p else None,'signal':b.replay[-1]['signal']}
    expected.append(row)
  out.append({'minutes':minutes,'window':window,'candles':candles,'expected':expected})
Path('data/qqq-parity-input.json').write_text(json.dumps(out))
print('Prepared parity candles:',[(x['minutes'],x['window'],len(x['candles'])) for x in out])

subprocess.run(['node','scripts/check_qqq_source_parity.cjs'],check=True)
