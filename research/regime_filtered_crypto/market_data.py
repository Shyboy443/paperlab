"""Public historical Bybit spot/Binance BTC spot. Cache every API response.

Never substitute spot prices from another venue or current instrument rosters.
Unsupported spot symbols are disclosed rather than silently added to the hedge.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
import binance

START=1546300800000
END=1767225600000
HOUR=3600000
# Reused token tickers cannot safely identify the same historical underlying.
AMBIGUOUS={'LUNAUSDT','BNXUSDT','BTTCUSDT'}

def spot_identity(symbol):
    if symbol in AMBIGUOUS:
        return None,1
    base=symbol[:-4]
    multiplier=1
    for prefix in ('1000000','10000','1000'):
        if base.startswith(prefix) and len(base)>len(prefix):
            multiplier=int(prefix);base=base[len(prefix):];break
    # Binance uses LUNA2 for the new Terra asset; Bybit uses LUNA.
    if base=='LUNA2':base='LUNA'
    return base+'USDT',multiplier

def cached(root,url,folder,tag):
    p=root/folder/(tag+'.json')
    transient={10000,10002,10006,10016}
    if p.exists():
        previous=json.loads(p.read_text());response=previous['response']
        if not isinstance(response,dict) or response.get('retCode') not in transient:return previous
        binance.write_json(root/'bybit_api_errors'/(tag+'.json'),previous)
    for attempt in range(8):
        obj=json.loads(binance.get(url))
        if not isinstance(obj,dict) or obj.get('retCode') not in transient:break
        time.sleep(min(8,2**attempt))
    else:raise RuntimeError('Bybit transient errors exhausted: '+url)
    result={'url':url,'retrieved_at':datetime.now(timezone.utc).isoformat(),'response':obj}
    binance.write_json(p,result)
    return result

def bybit_range(root,symbol,interval,start,end):
    records={};cursor=end-1
    while cursor>=start:
        url='https://api.bybit.com/v5/market/kline?'+urlencode(dict(category='spot',symbol=symbol,
            interval=interval,start=start,end=cursor,limit=1000))
        obj=cached(root,url,'bybit_api',f'{symbol}-{interval}-{start}-{cursor}')['response']
        if obj.get('retCode')!=0:
            if obj.get('retCode')==10001:return [],{'error':obj.get('retMsg'),'code':10001}
            raise RuntimeError(f'Bybit error {symbol}: {obj}')
        batch=obj['result']['list']
        if not batch:break
        for r in batch:
            t=int(r[0])
            if start<=t<end:records[t]=r
        nxt=min(int(r[0]) for r in batch)-1
        if nxt>=cursor:raise RuntimeError('Bybit pagination did not advance')
        cursor=nxt
        if len(batch)<1000:break
    return [records[t] for t in sorted(records)],{}

def write_bars(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['timestamp','open','high','low','close','volume','quote_volume'])
        w.writerows(rows)

def recover_archived_spot(root,contract,mapped):
    """Recover retired spot history from actual Bybit trade archives.

    Chunked aggregation preserves chronological first/last trades and quote
    volume. Do not replace a retired asset with a currently listed survivor.
    """
    import pandas as pd
    url=f'https://public.bybit.com/spot/{mapped}/'
    index=root/'bybit_trade_index'/(mapped+'.html')
    if not index.exists():index.parent.mkdir(parents=True,exist_ok=True);index.write_bytes(binance.get(url))
    names=re.findall(r'href="([^"]+\.csv.gz)"',index.read_text())
    names=[n for n in names if re.search(r'-202[12345]-\d\d\.csv.gz$',n)]
    if not names:return []
    def job(name):
        raw=root/'bybit_trades'/mapped/name;raw.parent.mkdir(parents=True,exist_ok=True)
        if not raw.exists():
            tmp=raw.with_suffix('.part');tmp.write_bytes(binance.get(url+name));tmp.replace(raw)
        compiled=root/'bybit_trade_bars'/mapped/(name+'.csv')
        if compiled.exists():
            old=pd.read_csv(compiled)
            if len(old) and old.timestamp.min()>=1514764800000:return old
        chunks=[]
        # Bybit added a sixth trade flag mid-March 2025 while retaining the
        # five-column header. The four numeric fields keep their positions.
        for f in pd.read_csv(raw,compression='gzip',chunksize=500000,usecols=[0,1,2,3],index_col=False,
                names=['id','timestamp','price','volume','side','trade_flag'],skiprows=1):
            if (f.timestamp<1514764800000).any():raise RuntimeError('Invalid trade timestamp '+name)
            f=f.sort_values('timestamp',kind='stable')
            f['bucket']=(f.timestamp//HOUR)*HOUR;f['quote']=f.price*f.volume
            g=f.groupby('bucket',sort=True).agg(open=('price','first'),high=('price','max'),low=('price','min'),
                close=('price','last'),volume=('volume','sum'),quote_volume=('quote','sum'))
            chunks.append(g)
        f=pd.concat(chunks).groupby(level=0,sort=True).agg(dict(open='first',high='max',low='min',close='last',volume='sum',quote_volume='sum'))
        f.index.name='timestamp';compiled.parent.mkdir(parents=True,exist_ok=True);f.to_csv(compiled)
        print('Recovered actual Bybit trades',name,flush=True);return f.reset_index()
    with ThreadPoolExecutor(max_workers=4) as pool:frames=list(pool.map(job,names))
    f=pd.concat(frames).sort_values('timestamp').drop_duplicates('timestamp')
    f=f[(f.timestamp>=START)&(f.timestamp<END)]
    write_bars(root/'spot_hourly'/(contract+'.csv'),f.to_numpy().tolist())
    f['day']=(f.timestamp//86400000)*86400000
    d=f.groupby('day').agg(dict(open='first',high='max',low='min',close='last',volume='sum',quote_volume='sum'))
    return d.reset_index().to_numpy().tolist()

def daily(root,selection,workers=8):
    symbols=sorted(json.loads(selection.read_text()))
    archive_url='https://public.bybit.com/spot/'
    index=root/'bybit_spot_archive_index.html'
    if not index.exists():index.write_bytes(binance.get(archive_url))
    archived=set(re.findall(r'href="([A-Z0-9]+USDT)"',index.read_text()))
    def job(s):
        mapped,mult=spot_identity(s)
        if mapped is None:return s,{'bybit_symbol':None,'reason':'ambiguous reused ticker','multiplier':mult}
        rows,error=bybit_range(root,mapped,'D',START,END)
        fallback=False
        if error and mapped in archived:
            rows=recover_archived_spot(root,s,mapped);fallback=bool(rows)
        positive=[r for r in rows if float(r[5])>0 and float(r[6])>0]
        write_bars(root/'spot_daily'/(s+'.csv'),positive)
        return s,{'bybit_symbol':mapped,'multiplier':mult,'days':len(positive),
                  'first_ms':int(positive[0][0]) if positive else None,
                  'last_ms':int(positive[-1][0]) if positive else None,
                  'present_in_historical_archive':mapped in archived,'archive_fallback':fallback,**error}
    result={}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fs=[pool.submit(job,s) for s in symbols]
        for i,f in enumerate(as_completed(fs),1):
            s,r=f.result();result[s]=r
            if i%25==0:print(f'Bybit daily {i}/{len(symbols)}',flush=True)
    binance.write_json(root/'spot_metadata.json',result)
    unavailable=[s for s,r in result.items() if r.get('code') and r.get('present_in_historical_archive') and not r.get('archive_fallback')]
    # Archived but unavailable history is a coverage limitation, not a false
    # assertion that the asset was never listed.
    print('Bybit daily complete; API-unavailable archived pairs:',unavailable,flush=True)
    btc_spot(root)
    manifest(root)

def btc_spot(root):
    records={};cursor=1514764800000  # genuine 2018 warm-up for MA200
    while cursor<END:
        url='https://api.binance.com/api/v3/klines?'+urlencode(dict(symbol='BTCUSDT',interval='1d',
            startTime=cursor,endTime=END-1,limit=1000))
        obj=cached(root,url,'btc_spot_api',str(cursor))['response']
        if not isinstance(obj,list) or not obj:raise RuntimeError('BTC spot history unavailable')
        for r in obj:
            t=int(r[0]);records[t]=[r[0],*r[1:6],r[7]]
        cursor=int(obj[-1][0])+86400000
    write_bars(root/'btc_spot.csv',[records[t] for t in sorted(records) if t<END])

def hourly(root,selection,workers=8):
    selected=json.loads(selection.read_text());meta=json.loads((root/'spot_metadata.json').read_text())
    jobs=[]
    for s,months in selected.items():
        m=meta[s]
        if not m.get('days') or m.get('archive_fallback'):continue
        for month in months:
            y,n=map(int,month.split('-'))
            start=int(datetime(y,n,1,tzinfo=timezone.utc).timestamp()*1000)
            end=int(datetime(y+(n==12),1 if n==12 else n+1,1,tzinfo=timezone.utc).timestamp()*1000)
            if start<=m['last_ms'] and end>m['first_ms']:
                jobs.append((s,month,start,end,m['bybit_symbol']))
    def job(x):
        s,month,start,end,mapped=x
        rows,error=bybit_range(root,mapped,'60',start,end)
        if error:raise RuntimeError('Daily supported but hourly unsupported: '+s)
        p=root/'spot_monthly'/s/(month+'.csv');write_bars(p,rows)
        return s,p
    parts={}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fs=[pool.submit(job,x) for x in jobs]
        for i,f in enumerate(as_completed(fs),1):
            s,p=f.result();parts.setdefault(s,[]).append(p)
            if i%100==0:print(f'Bybit hourly {i}/{len(jobs)}',flush=True)
    for s,paths in parts.items():
        records={}
        for p in paths:
            with p.open() as f:
                for r in csv.DictReader(f):records[int(r['timestamp'])]=list(r.values())
        write_bars(root/'spot_hourly'/(s+'.csv'),[records[t] for t in sorted(records)])
    manifest(root)
    print('Bybit hourly complete',len(parts),'pairs',len(jobs),'months',flush=True)

def manifest(root):
    entries=[]
    for folder in ['bybit_api','btc_spot_api','spot_daily','spot_hourly']:
        for p in sorted((root/folder).glob('*.json' if 'api' in folder else '*.csv')):
            entries.append({'path':p.relative_to(root).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    for p in sorted((root/'bybit_trades').glob('*/*.gz')):
        entries.append({'path':p.relative_to(root).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    for name in ['spot_metadata.json','btc_spot.csv','bybit_spot_archive_index.html']:
        p=root/name
        if p.exists():entries.append({'path':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    binance.write_json(root/'spot_manifest.json',{'files':entries,'spot_venue':'Bybit','regime_btc_venue':'Binance spot'})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['daily','hourly']);p.add_argument('--data',type=Path,required=True)
    p.add_argument('--selection',type=Path,required=True);p.add_argument('--workers',type=int,default=8)
    a=p.parse_args();globals()[a.phase](a.data,a.selection,a.workers)
