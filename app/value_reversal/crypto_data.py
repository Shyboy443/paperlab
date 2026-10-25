"""Checksum-verified public Binance archives. No credentials or order endpoints."""
from __future__ import annotations
import csv, gzip, hashlib, io, json, time, zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from .model import validate

SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')
PUBLIC = 'https://data.binance.vision/'
NY = ZoneInfo('America/New_York')

def get(url):
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={'User-Agent':'PaperLab-ValueReversal/3'}),timeout=35) as r:
                return r.read()
        except OSError:
            if attempt == 3: raise
            time.sleep(2**attempt)

def archive(root, key):
    path = root/'raw'/key
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(get(PUBLIC+key))
    check = path.with_suffix('.zip.CHECKSUM')
    if not check.exists(): check.write_bytes(get(PUBLIC+key+'.CHECKSUM'))
    body=path.read_bytes();sha=hashlib.sha256(body).hexdigest()
    if sha != check.read_text().split()[0]: raise ValueError('Archive checksum mismatch: '+key)
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        if z.testzip(): raise ValueError('Archive CRC mismatch: '+key)
        rows=[]
        for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
            try: int(r[0])
            except (ValueError,IndexError): continue
            rows.append(r)
    return key, rows, {'key':key,'sha256':sha,'official_checksum_verified':True}

def load(root, progress=print):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    cache=root/'input.json.gz'
    if cache.exists():
        with gzip.open(cache,'rt') as f: return json.load(f)
    months=['2021-12']+[f'{y}-{m:02}' for y in range(2022,2026) for m in range(1,13)]
    keys=[]
    for symbol in SYMBOLS:
        for month in months:
            for kind,interval in [('klines','15m'),('markPriceKlines','1h'),('fundingRate',None)]:
                keys.append(f'data/futures/um/monthly/{kind}/{symbol}/'+
                    (interval+'/' if interval else '')+symbol+'-'+(interval or kind)+'-'+month+'.zip')
    collected={s:{'bars':[],'marks':[],'funding':[]} for s in SYMBOLS};manifest=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for n,(key,rows,proof) in enumerate(pool.map(lambda k:archive(root,k),keys),1):
            symbol=key.split('/')[5];kind=key.split('/')[4]
            if kind=='klines': collected[symbol]['bars'].extend([int(r[0])]+[float(x) for x in r[1:6]] for r in rows)
            elif kind=='markPriceKlines':collected[symbol]['marks'].extend([int(r[0]),float(r[1])] for r in rows)
            else:collected[symbol]['funding'].extend([int(r[0]),int(r[1]),float(r[2])] for r in rows)
            manifest.append(proof)
            if n%21==0 or n==len(keys):progress(f'Verified {n}/{len(keys)} archives',flush=True)
    for symbol,d in collected.items():
        for field in d:
            d[field].sort(key=lambda r:r[0])
            if len({r[0] for r in d[field]})!=len(d[field]):raise ValueError('Duplicate '+symbol+' '+field)
        for b in d['bars']:validate(b)
        gaps=[(a[0]+900000,b[0]) for a,b in zip(d['bars'],d['bars'][1:]) if b[0]-a[0]!=900000]
        for a,z in gaps:
            cursor=a
            while cursor<z:
                url=f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=15m&startTime={cursor}&endTime={z-1}&limit=1500'
                path=root/'api_repairs'/f'{symbol}-bars-{cursor}-{z}.json';path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():path.write_bytes(get(url))
                raw=path.read_bytes();batch=json.loads(raw)
                if not batch:raise ValueError('Cannot repair missing candles: '+symbol)
                d['bars'].extend([int(r[0])]+[float(x) for x in r[1:6]] for r in batch)
                manifest.append({'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'kind':'actual_exchange_gap_repair'})
                cursor=int(batch[-1][0])+900000
            progress(f'Repaired actual {symbol} candle gap',flush=True)
        d['bars'].sort(key=lambda r:r[0])
        if any(b[0]-a[0]!=900000 for a,b in zip(d['bars'],d['bars'][1:])):raise ValueError('Unrepaired candle gap')
        marks=dict(d['marks'])
        for row in d['funding']:
            # Archive calc_time can be milliseconds after the nominal settlement.
            nominal=round(row[0]/3600000)*3600000
            if abs(row[0]-nominal)>60000:raise ValueError('Unexpected funding settlement timestamp: '+symbol)
            if nominal not in marks:
                url=f'https://fapi.binance.com/fapi/v1/markPriceKlines?symbol={symbol}&interval=1h&startTime={nominal}&endTime={nominal+3599999}&limit=2'
                path=root/'api_repairs'/f'{symbol}-mark-{nominal}.json';path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():path.write_bytes(get(url))
                raw=path.read_bytes();batch=json.loads(raw)
                if not batch or int(batch[0][0])!=nominal:raise ValueError('No historical settlement mark: '+symbol)
                marks[nominal]=float(batch[0][1]);d['marks'].append([nominal,marks[nominal]])
                manifest.append({'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'kind':'actual_exchange_mark_repair'})
            row.append(marks[nominal])
        for a,b in zip(d['funding'],d['funding'][1:]):
            interval=round((b[0]-a[0])/3600000)
            if interval not in (a[1],b[1]) or abs(b[0]-a[0]-interval*3600000)>60000:
                raise ValueError('Unexplained funding gap: '+symbol)
    data={'symbols':collected,'manifest':manifest,'source':PUBLIC,'retrieved_at':datetime.now(timezone.utc).isoformat(),
          'funding_mark_method':'Historical mark-price 1h OPEN at nominal settlement; archive calc_time offsets retained.'}
    with gzip.open(cache,'wt') as f:json.dump(data,f,separators=(',',':'))
    return data

def sessions(bars, minutes):
    """18:00 NY anchors, 23/25-hour DST days included. Partial boundary sessions flagged."""
    grouped={}
    for bar in bars:
        local=datetime.fromtimestamp(bar[0]/1000,timezone.utc).astimezone(NY)
        date=local.date() if local.hour>=18 else local.date()-timedelta(days=1)
        if date not in grouped:
            start=datetime.combine(date,datetime.min.time(),NY).replace(hour=18)
            end=datetime.combine(date+timedelta(days=1),datetime.min.time(),NY).replace(hour=18)
            grouped[date]={'date':str(date),'open_ms':int(start.timestamp()*1000),
                           'close_ms':int(end.timestamp()*1000),'bars':[]}
        grouped[date]['bars'].append(bar)
    out=[];step=minutes*60000
    for group in grouped.values():
        source={b[0]:b for b in group['bars']};combined=[]
        for t in range(group['open_ms'],group['close_ms'],step):
            parts=[source.get(x) for x in range(t,t+step,900000)]
            if any(p is None for p in parts):continue
            combined.append([t,parts[0][1],max(p[2] for p in parts),min(p[3] for p in parts),
                             parts[-1][4],sum(p[5] for p in parts)])
        group['bars']=combined;group['complete']=len(combined)==(group['close_ms']-group['open_ms'])//step
        out.append(group)
    return out
