"""Public Binance USD-M archives, including delisted symbols; no account keys.

python download.py daily --data ../../data/cross_sectional_momentum
python download.py intraday --data ../../data/cross_sectional_momentum --selection output/required_months.json
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError
import xml.etree.ElementTree as ET
import zipfile

BUCKET = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'
PUBLIC = 'https://data.binance.vision/'
API = 'https://fapi.binance.com'
NS = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}
START_MS = 1546300800000  # 2019-01-01
END_MS = 1735689600000    # 2025-01-01, exclusive
EXCLUDED = {'USDC','BUSD','TUSD','DAI','USDP','PAX','USDD','USDE','FDUSD','USD1','AEUR','USDS','UST','USTC',
            'WBTC','WETH','BTCB','WBETH','BETH','STETH','WSTETH','WEETH','CBETH','RETH','JITOSOL',
            'BTCDOM','DEFI','BLUEBIRD'}
HISTORICAL_ALIASES={'BNXUSDT':'BNXUSDTSETTLED'}


def get(url, optional=False):
    for attempt in range(5):
        try:
            with urlopen(Request(url, headers={'User-Agent':'MomentumResearch/1.0'}), timeout=40) as r:
                return r.read()
        except HTTPError as e:
            if optional and e.code == 404:
                return None
            if e.code not in (429, 500, 502, 503, 504):
                raise
            time.sleep(min(30, 2 ** attempt))
        except (OSError, TimeoutError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError('Download exhausted: ' + url)


def listing(prefix, delimiter=None):
    keys, prefixes, marker = [], [], None
    while True:
        q = {'prefix':prefix}
        if delimiter:
            q['delimiter'] = delimiter
        if marker:
            q['marker'] = marker
        root = ET.fromstring(get(BUCKET + '?' + urlencode(q)))
        keys += [n.text for n in root.findall('s:Contents/s:Key', NS)]
        prefixes += [n.text for n in root.findall('s:CommonPrefixes/s:Prefix', NS)]
        if root.findtext('s:IsTruncated', namespaces=NS) != 'true':
            return keys, prefixes
        marker = root.findtext('s:NextMarker', namespaces=NS) or keys[-1]


def archive_keys(symbol, kind='klines', interval='1d'):
    prefix = f'data/futures/um/monthly/{kind}/{symbol}/'
    if kind != 'fundingRate':
        prefix += interval + '/'
    keys, _ = listing(prefix)
    return [k for k in keys if k.endswith('.zip') and '2020-01' <= k[-11:-4] <= '2024-12']


def download_one(root, key, checksums=False):
    path = root / 'raw' / key
    if path.exists():
        body = path.read_bytes()
    else:
        body = get(PUBLIC + key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.part')
        temporary.write_bytes(body)
        temporary.replace(path)
    digest = hashlib.sha256(body).hexdigest()
    if checksums:
        check_path = path.with_suffix('.zip.CHECKSUM')
        if not check_path.exists():
            check_path.write_bytes(get(PUBLIC + key + '.CHECKSUM'))
        expected = check_path.read_text().split()[0]
        if expected != digest:
            raise ValueError('Checksum mismatch: ' + key)
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        if z.testzip():
            raise ValueError('ZIP CRC failed: ' + key)
    return {'key':key, 'bytes':len(body), 'sha256':digest, 'official_checksum_verified':checksums}


def rows(path):
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            for row in csv.reader(io.TextIOWrapper(f)):
                try:
                    int(row[0])
                except (ValueError, IndexError):
                    continue
                yield row


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding='utf-8')


def funding_api_month(root,symbol,month):
    """Archive gaps use actual exchange history, never invented funding rates."""
    year,number=map(int,month.split('-'))
    start=int(datetime(year,number,1,tzinfo=timezone.utc).timestamp()*1000)
    end=int(datetime(year+int(number==12),1 if number==12 else number+1,1,tzinfo=timezone.utc).timestamp()*1000)
    path=root/'api_funding'/symbol/(month+'.json')
    if path.exists():
        data=json.loads(path.read_text())
    else:
        records=[]
        cursor=start-16*3_600_000
        while cursor<end+16*3_600_000:
            url=API+'/fapi/v1/fundingRate?'+urlencode(dict(symbol=symbol,startTime=cursor,endTime=end+16*3_600_000,limit=1000))
            batch=json.loads(get(url))
            if not batch:
                break
            records+=batch
            cursor=int(batch[-1]['fundingTime'])+1
            if len(batch)<1000:
                break
        data={'symbol':symbol,'month':month,'records':records,'source':API+'/fapi/v1/fundingRate',
              'request_start_ms':start-16*3_600_000,'request_end_ms':end+16*3_600_000}
        write_json(path,data)
    ordered=sorted(data['records'],key=lambda r:int(r['fundingTime']))
    output=[]
    for i,row in enumerate(ordered):
        t=int(row['fundingTime'])
        if not start<=t<end:
            continue
        # Timestamp spacing identifies the historical schedule. Padding allows
        # the final month's record to use the following observed settlement.
        if i+1<len(ordered):
            interval=round((int(ordered[i+1]['fundingTime'])-t)/3_600_000)
        elif i:
            interval=round((t-int(ordered[i-1]['fundingTime']))/3_600_000)
        else:
            raise ValueError('Cannot verify funding interval for '+symbol+' '+month)
        if interval not in (1,2,4,8):
            # A schedule change can bridge the old and new UTC grids, e.g.
            # BNX 2022-12-14 02:00 -> 08:00 when 2h becomes 8h. Accept only
            # an observed bridge between distinct ordinary schedules.
            before=round((t-int(ordered[i-1]['fundingTime']))/3_600_000) if i else None
            after=round((int(ordered[i+2]['fundingTime'])-int(ordered[i+1]['fundingTime']))/3_600_000) if i+2<len(ordered) else None
            if not (before in (1,2,4,8) and after in (1,2,4,8)
                    and before!=after and 0<interval<=max(before,after)):
                raise ValueError('Unexpected funding gap from API: '+symbol+' '+month)
        output.append([t,interval,row['fundingRate']])
    if not output:
        raise ValueError('Actual funding unavailable during traded month: '+symbol+' '+month)
    return output


def repair_mark_gaps(root,symbol,collected):
    """Recover actual historical mark bars only where a traded bar exists."""
    with (root/'hourly'/(symbol+'.csv')).open() as f:
        required={int(r['timestamp']) for r in csv.DictReader(f)}
    missing=sorted(required-set(collected))
    groups=[]
    for stamp in missing:
        if groups and stamp==groups[-1][-1]+3_600_000 and len(groups[-1])<1500:
            groups[-1].append(stamp)
        else:
            groups.append([stamp])
    for group in groups:
        path=root/'api_mark'/symbol/(str(group[0])+'-'+str(group[-1])+'.json')
        if path.exists():
            payload=json.loads(path.read_text())
        else:
            url=API+'/fapi/v1/markPriceKlines?'+urlencode(dict(symbol=symbol,interval='1h',
                    startTime=group[0],endTime=group[-1]+3_600_000-1,limit=1500))
            payload={'url':url,'records':json.loads(get(url))}
            write_json(path,payload)
        for row in payload['records']:
            collected[int(row[0])]=row[:5]
        still_missing=set(group)-set(collected)
        for date in sorted({datetime.fromtimestamp(t/1000,timezone.utc).strftime('%Y-%m-%d') for t in still_missing}):
            # Some terminated symbols retain daily archive marks although the
            # REST endpoint no longer returns their old mark-price bars.
            key=f'data/futures/um/daily/markPriceKlines/{symbol}/1h/{symbol}-1h-{date}.zip'
            try:
                item=download_one(root,key)
            except HTTPError as exc:
                if exc.code==404:
                    continue
                raise
            metadata_path=root/'api_mark'/symbol/('daily_'+date+'.json')
            write_json(metadata_path,{'archive':item,'source':PUBLIC+key})
            for row in rows(root/'raw'/key):
                collected[int(row[0])]=row[:5]
        if set(group)-set(collected):
            raise ValueError('Actual mark bars unavailable: '+symbol+' '+str(group[0]))
    return collected


def repair_trade_gaps(root,symbol,months,collected):
    """Internal empty/padded archive bars must be verified against real trades."""
    if not collected:
        return collected
    first,last=min(collected),max(collected)
    metadata=json.loads((root/'metadata.json').read_text())[symbol]
    sessions=metadata.get('sessions',[{'first_ms':metadata['first_observed_ms'],'last_ms':metadata['last_observed_ms']}])
    bounds=[]
    for session in sessions:
        active=[t for t in collected if session['first_ms']<=t<session['last_ms']+86_400_000]
        if active:
            active_end=max(active)+3_600_000 if max(active)//86_400_000==session['last_ms']//86_400_000 else session['last_ms']+86_400_000
            bounds.append((min(active),active_end))
    required=set()
    for month in months:
        year,number=map(int,month.split('-'))
        begin=int(datetime(year,number,1,tzinfo=timezone.utc).timestamp()*1000)
        end=int(datetime(year+int(number==12),1 if number==12 else number+1,1,tzinfo=timezone.utc).timestamp()*1000)
        for active_start,active_end in bounds:
            required.update(range(max(begin,first,active_start),min(end,active_end),3_600_000))
    missing=sorted(required-set(collected))
    groups=[]
    for stamp in missing:
        if groups and stamp==groups[-1][-1]+3_600_000 and len(groups[-1])<1500:
            groups[-1].append(stamp)
        else:
            groups.append([stamp])
    for group in groups:
        path=root/'api_trade'/symbol/(str(group[0])+'-'+str(group[-1])+'.json')
        if path.exists():
            payload=json.loads(path.read_text())
        else:
            url=API+'/fapi/v1/klines?'+urlencode(dict(symbol=symbol,interval='1h',
                    startTime=group[0],endTime=group[-1]+3_600_000-1,limit=1500))
            try:
                payload={'url':url,'records':json.loads(get(url))}
            except HTTPError as exc:
                if exc.code!=400:
                    raise
                payload={'url':url,'records':[],'error':exc.read().decode(),'http_status':400}
            write_json(path,payload)
        for row in payload['records']:
            # A REST-confirmed zero-trade hour is a real quiet/halted bar.
            # Keep its observed mark, but the engine prohibits executions.
            collected[int(row[0])]=row[:5]+[row[5],row[7]]
        if set(group)-set(collected) and symbol in HISTORICAL_ALIASES:
            alias=HISTORICAL_ALIASES[symbol]
            path=root/'api_trade'/symbol/(alias+'-'+str(group[0])+'-'+str(group[-1])+'.json')
            if path.exists():
                payload=json.loads(path.read_text())
            else:
                url=API+'/fapi/v1/klines?'+urlencode(dict(symbol=alias,interval='1h',
                        startTime=group[0],endTime=group[-1]+3_600_000-1,limit=1500))
                payload={'url':url,'records':json.loads(get(url))}
                write_json(path,payload)
            for row in payload['records']:
                collected[int(row[0])]=row[:5]+[row[5],row[7]]
        for date in sorted({datetime.fromtimestamp(t/1000,timezone.utc).strftime('%Y-%m-%d') for t in set(group)-set(collected)}):
            key=f'data/futures/um/daily/klines/{symbol}/1h/{symbol}-1h-{date}.zip'
            try:
                item=download_one(root,key)
            except HTTPError as exc:
                if exc.code==404:
                    continue
                raise
            write_json(root/'api_trade'/symbol/('daily_'+date+'.json'),{'archive':item,'source':PUBLIC+key})
            for row in rows(root/'raw'/key):
                collected[int(row[0])]=row[:5]+[row[5],row[7]]
        remaining=set(group)-set(collected)
        for session in sessions:
            observed=[t for t in collected if session['first_ms']<=t<session['last_ms']+86_400_000]
            if observed and max(observed)//86_400_000==session['last_ms']//86_400_000:
                remaining={t for t in remaining if not (max(observed)<t<session['last_ms']+86_400_000)}
        if remaining:
            raise ValueError('Actual internal trade bars unavailable: '+symbol+' '+str(group[0]))
    # Even REST may pad terminated contracts with flat zero-trade candles.
    # Quiet bars are usable only between real trades within the same lifecycle.
    for session in sessions:
        active=[t for t,row in collected.items() if session['first_ms']<=t<session['last_ms']+86_400_000
                and float(row[5])>0 and float(row[6])>0]
        if active:
            active_first,active_last=min(active),max(active)
            for t in list(collected):
                if session['first_ms']<=t<session['last_ms']+86_400_000 and not active_first<=t<=active_last:
                    del collected[t]
    return collected


def repair_daily(root,workers=8):
    """Repair internal daily archive holes without filling genuine delistings."""
    metadata=json.loads((root/'metadata.json').read_text())
    def repair(symbol):
        path=root/'daily'/(symbol+'.csv')
        with path.open() as f:
            collected={int(r['timestamp']):r for r in csv.DictReader(f)}
        stamps=sorted(collected)
        gaps=[(a+86_400_000,b-1) for a,b in zip(stamps,stamps[1:]) if b-a>86_400_000]
        for start,end in gaps:
            for source in (symbol,HISTORICAL_ALIASES.get(symbol)):
                if not source:
                    continue
                cache=root/'api_daily'/symbol/(source+'-'+str(start)+'-'+str(end)+'.json')
                if cache.exists():
                    payload=json.loads(cache.read_text())
                else:
                    url=API+'/fapi/v1/klines?'+urlencode(dict(symbol=source,interval='1d',startTime=start,endTime=end,limit=1500))
                    payload={'url':url,'records':json.loads(get(url))}
                    write_json(cache,payload)
                for r in payload['records']:
                    if start<=int(r[0])<=end and float(r[5])>0 and float(r[7])>0:
                        collected[int(r[0])]=dict(zip(['timestamp','open','high','low','close','volume','quote_volume'],
                                                       [r[0],r[1],r[2],r[3],r[4],r[5],r[7]]))
            if end-start<7*86_400_000:
                for stamp in range(start,end+1,86_400_000):
                    if stamp in collected:
                        continue
                    date=datetime.fromtimestamp(stamp/1000,timezone.utc).strftime('%Y-%m-%d')
                    key=f'data/futures/um/daily/klines/{symbol}/1d/{symbol}-1d-{date}.zip'
                    try:
                        item=download_one(root,key)
                    except HTTPError as exc:
                        if exc.code==404:
                            continue
                        raise
                    write_json(root/'api_daily'/symbol/('daily_'+date+'.json'),{'archive':item,'source':PUBLIC+key})
                    for r in rows(root/'raw'/key):
                        if float(r[5])>0 and float(r[7])>0:
                            collected[int(r[0])]=dict(zip(['timestamp','open','high','low','close','volume','quote_volume'],
                                                           [r[0],r[1],r[2],r[3],r[4],r[5],r[7]]))
        stamps=sorted(collected)
        sessions=[]
        for t in stamps:
            if sessions and t-sessions[-1]['last_ms']<7*86_400_000:
                sessions[-1]['last_ms']=t
            else:
                sessions.append({'first_ms':t,'last_ms':t})
        with path.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['timestamp','open','high','low','close','volume','quote_volume'])
            writer.writeheader()
            writer.writerows(collected[t] for t in stamps)
        metadata[symbol].update(days=len(stamps),sessions=sessions,daily_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        return len(stamps)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(repair,sorted(metadata)))
    write_json(root/'metadata.json',metadata)
    manifest=json.loads((root/'daily_manifest.json').read_text())
    manifest['api_daily']=[{'path':str(p.relative_to(root)).replace('\\','/'),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                           for p in sorted((root/'api_daily').glob('*/*.json'))]
    write_json(root/'daily_manifest.json',manifest)
    print('DAILY REPAIR COMPLETE: '+str(len(manifest['api_daily']))+' responses; '+str(sum(len(m['sessions']) for m in metadata.values()))+' contract lifecycles',flush=True)


def daily(root, workers, checksums):
    catalog_path = root / 'catalog.json'
    if catalog_path.exists():
        catalog = json.loads(catalog_path.read_text())
    else:
        _, dirs = listing('data/futures/um/monthly/klines/', '/')
        symbols = sorted(p.rstrip('/').split('/')[-1] for p in dirs)
        eligible = [s for s in symbols if s.endswith('USDT') and '_' not in s and s[:-4] not in EXCLUDED]
        catalog = {'downloaded_at':datetime.now(timezone.utc).isoformat(), 'symbols':{},
                   'excluded_bases':sorted(EXCLUDED), 'discovered_archive_symbols':len(symbols)}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(archive_keys, s):s for s in eligible}
            for i, future in enumerate(as_completed(futures), 1):
                keys = future.result()
                if keys:
                    catalog['symbols'][futures[future]] = keys
                if i % 100 == 0:
                    print(f'Catalog {i}/{len(eligible)}; historical contracts {len(catalog["symbols"])}', flush=True)
        write_json(catalog_path, catalog)
    jobs = [k for ks in catalog['symbols'].values() for k in ks]
    manifest = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(download_one, root, k, checksums) for k in jobs]
        for i, future in enumerate(as_completed(futures), 1):
            manifest.append(future.result())
            if i % 250 == 0:
                print(f'Daily archives {i}/{len(jobs)}', flush=True)
    metadata = {}
    for symbol, keys in sorted(catalog['symbols'].items()):
        all_rows = {}
        for key in keys:
            for row in rows(root / 'raw' / key):
                t = int(row[0])
                if START_MS <= t < END_MS and float(row[5])>0 and float(row[7])>0:
                    all_rows[t] = row
        # REST supplies 2019 warm-up missing from monthly archives. This is queried
        # for ALL January-2020 contracts, including archived delistings.
        if keys[0][-11:-4] == '2020-01':
            rest_path = root / 'rest_2019' / (symbol + '.json')
            if rest_path.exists():
                older = json.loads(rest_path.read_text())
            else:
                older = json.loads(get(API + '/fapi/v1/klines?' + urlencode(
                    dict(symbol=symbol, interval='1d', startTime=START_MS, endTime=1577836799999, limit=1500))))
                write_json(rest_path, older)
                time.sleep(0.1)
            for row in older:
                if START_MS <= int(row[0]) < 1577836800000 and float(row[5])>0 and float(row[7])>0:
                    all_rows[int(row[0])] = row
        if not all_rows:
            continue
        ordered = [all_rows[t] for t in sorted(all_rows)]
        destination = root / 'daily' / (symbol + '.csv')
        destination.parent.mkdir(exist_ok=True)
        with destination.open('w', newline='') as f:
            out = csv.writer(f)
            out.writerow(['timestamp','open','high','low','close','volume','quote_volume'])
            out.writerows([r[0],r[1],r[2],r[3],r[4],r[5],r[7]] for r in ordered)
        metadata[symbol] = {'first_observed_ms':int(ordered[0][0]),'last_observed_ms':int(ordered[-1][0]),
                            'days':len(ordered), 'daily_sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}
    write_json(root / 'metadata.json', metadata)
    write_json(root / 'daily_manifest.json', {'archives':sorted(manifest, key=lambda x:x['key']), 'source':PUBLIC,
                                             'created_at':datetime.now(timezone.utc).isoformat()})
    print(f'DAILY COMPLETE: {len(metadata)} historical USDT contracts; {len(jobs)} archives', flush=True)
    repair_daily(root,min(8,workers))


def intraday(root, selection, workers, checksums):
    selected = json.loads(selection.read_text())
    metadata=json.loads((root/'metadata.json').read_text())
    jobs = []
    for symbol, months in selected.items():
        for month in months:
            for kind in ('klines','markPriceKlines','fundingRate'):
                folder = f'data/futures/um/monthly/{kind}/{symbol}/'
                tail = f'{symbol}-fundingRate-{month}.zip' if kind == 'fundingRate' else f'1h/{symbol}-1h-{month}.zip'
                jobs.append(folder + tail)
    manifest, missing = [], []
    previous_path=root/'intraday_manifest.json'
    previous_missing={r['missing'] for r in json.loads(previous_path.read_text()).get('missing',[])} if previous_path.exists() else set()
    def job(key):
        if key in previous_missing:
            return {'missing':key}
        try:
            return download_one(root, key, checksums)
        except HTTPError as e:
            if e.code == 404:
                return {'missing':key}
            raise
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(job, k) for k in jobs]
        for i, future in enumerate(as_completed(futures),1):
            item = future.result()
            (missing if 'missing' in item else manifest).append(item)
            if i % 250 == 0:
                print(f'Hourly/funding archives {i}/{len(jobs)}; absent {len(missing)}', flush=True)
    def compile_symbol(item):
        symbol,months=item
        for kind, folder, header in [('klines','hourly',['timestamp','open','high','low','close','volume','quote_volume']),
                                     ('markPriceKlines','mark_hourly',['timestamp','open','high','low','close']),
                                     ('fundingRate','funding',['timestamp','interval_hours','rate'])]:
            collected = {}
            for month in months:
                key = f'data/futures/um/monthly/{kind}/{symbol}/'
                key += f'{symbol}-fundingRate-{month}.zip' if kind == 'fundingRate' else f'1h/{symbol}-1h-{month}.zip'
                path = root / 'raw' / key
                if not path.exists():
                    if kind=='fundingRate':
                        first=datetime.fromtimestamp(metadata[symbol]['first_observed_ms']/1000,timezone.utc).strftime('%Y-%m')
                        last=datetime.fromtimestamp(metadata[symbol]['last_observed_ms']/1000,timezone.utc).strftime('%Y-%m')
                        if first<=month<=last:
                            for row in funding_api_month(root,symbol,month):
                                collected[int(row[0])]=row
                    continue
                for row in rows(path):
                    if kind=='klines' and (float(row[5])<=0 or float(row[7])<=0):
                        continue  # Dummy candles after delisting are not executable prices.
                    collected[int(row[0])] = row[:3] if kind == 'fundingRate' else row[:5]+[row[5],row[7]] if kind=='klines' else row[:5]
            if kind=='klines':
                collected=repair_trade_gaps(root,symbol,months,collected)
            elif kind=='markPriceKlines':
                collected=repair_mark_gaps(root,symbol,collected)
            elif kind=='fundingRate':
                # Archive interval fields describe the preceding schedule at a
                # change. Verify those transitions with complete REST history,
                # retaining the actual rates but deriving forward event spacing.
                ordered=sorted(collected)
                transitions=set()
                for t,nxt in zip(ordered,ordered[1:]):
                    gap=round((nxt-t)/3_600_000)
                    if 0<gap<=24 and gap!=int(float(collected[t][1])):
                        transitions.update(datetime.fromtimestamp(x/1000,timezone.utc).strftime('%Y-%m') for x in (t,nxt))
                for month in sorted(transitions):
                    for row in funding_api_month(root,symbol,month):
                        collected[int(row[0])]=row
            destination = root / folder / (symbol + '.csv')
            destination.parent.mkdir(exist_ok=True)
            with destination.open('w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(collected[t] for t in sorted(collected))
        return symbol
    with ThreadPoolExecutor(max_workers=min(8,workers)) as pool:
        for i,symbol in enumerate(pool.map(compile_symbol,selected.items()),1):
            if i%50==0:
                print(f'Compiled {i}/{len(selected)} contracts',flush=True)
    api=[{'path':str(p.relative_to(root)).replace('\\','/'),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
         for p in sorted((root/'api_funding').glob('*/*.json'))]
    api_mark=[{'path':str(p.relative_to(root)).replace('\\','/'),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
              for p in sorted((root/'api_mark').glob('*/*.json'))]
    api_trade=[{'path':str(p.relative_to(root)).replace('\\','/'),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
               for p in sorted((root/'api_trade').glob('*/*.json'))]
    write_json(root / 'intraday_manifest.json', {'archives':sorted(manifest,key=lambda x:x['key']), 'missing':sorted(missing,key=lambda x:x['missing']),'api_funding':api,'api_mark':api_mark,'api_trade':api_trade})
    print(f'INTRADAY COMPLETE: {len(selected)} contracts, {len(manifest)} archives, {len(missing)} absent', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['daily','repair-daily','intraday'])
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--selection', type=Path)
    parser.add_argument('--workers', type=int, default=16)
    parser.add_argument('--verify-checksums', action='store_true')
    args = parser.parse_args()
    args.data.mkdir(parents=True,exist_ok=True)
    if args.phase == 'daily':
        daily(args.data,args.workers,args.verify_checksums)
    elif args.phase == 'repair-daily':
        repair_daily(args.data,min(8,args.workers))
    elif args.selection:
        intraday(args.data,args.selection,args.workers,args.verify_checksums)
    else:
        parser.error('--selection is required for intraday')
