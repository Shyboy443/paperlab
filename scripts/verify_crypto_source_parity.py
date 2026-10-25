"""Execute the original Pine on crypto candles, including both New York DST changes."""
import gzip,json,sys,subprocess
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.value_reversal.crypto_data import sessions
from app.value_reversal.crypto_research import features,FILTERS
with gzip.open('data/crypto_value_reversal/input.json.gz','rt') as f:data=json.load(f)
jobs=[]
for symbol,d in data['symbols'].items():
    for minutes in (15,30):
        groups=[g for g in sessions(d['bars'],minutes) if '2024-03-04'<=g['date']<='2024-03-14' or '2024-10-28'<=g['date']<='2024-11-07']
        rows=features(groups,symbol,minutes)
        for window in (5,10):
            candles=[];expected=[]
            for row in rows:
                ts,o,h,l,c,v=row['bar'];candles.append(dict(openTime=ts,closeTime=ts+minutes*60000,open=o,high=h,low=l,close=c,volume=v))
                e={'time':ts}
                for p in ('previous','developing'):
                    e[p]={k:row[p][k] if row[p] else None for k in ('low','high','poc')}
                    e[p]['signal']=row['signals'].get(f'{p}-{window}',{}).get('side',0)
                expected.append(e)
            jobs.append(dict(symbol=symbol,tick=FILTERS[symbol][0],minutes=minutes,window=window,candles=candles,expected=expected))
Path('data/crypto-parity-input.json').write_text(json.dumps(jobs))
subprocess.run(['node','scripts/check_crypto_source_parity.cjs'],check=True)
