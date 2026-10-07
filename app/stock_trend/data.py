"""Continue the frozen index using adjusted SIP closes and dated membership.

    Re-anchor provider price levels on 2025-12-31, preserving returns after the
    anchor. Persist each refreshed observation set. Never fabricate a daily bar.
"""
from __future__ import annotations
import asyncio, csv, gzip, hashlib, io, json, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .model import Blocked, RULE, append_day, decision

ET=ZoneInfo('America/New_York')
MEMBERS_URL='https://raw.githubusercontent.com/fja05680/sp500/master/'+urllib.parse.quote('S&P 500 Historical Components & Changes (Updated).csv',safe='')

def memberships(text):
    out=[]
    for r in list(csv.reader(io.StringIO(text)))[1:]:
        if len(r)<2:continue
        date.fromisoformat(r[0])
        names=sorted(set(r[1].split(',')))
        if not 450<=len(names)<=550:raise Blocked('Invalid S&P 500 membership snapshot')
        out.append((r[0],names))
    if not out:raise Blocked('Empty S&P 500 membership history')
    return sorted(out)

def members_at(rows, day):
    known=[names for d,names in rows if d<=day]
    if not known:raise Blocked('No dated membership for '+day)
    return known[-1]

def fetch_members():
    req=urllib.request.Request(MEMBERS_URL,headers={'User-Agent':'PaperLab-Stock-Trend/1'})
    with urllib.request.urlopen(req,timeout=20) as r:return r.read().decode()

def stamp(s):
    return datetime.fromisoformat(s.replace('Z','+00:00'))

def session_dt(row, key):
    return datetime.combine(date.fromisoformat(row['date']),datetime.strptime(row[key],'%H:%M').time(),ET)

async def calendar(client, now):
    today=now.astimezone(ET).date()
    q=urllib.parse.urlencode({'start':'2025-12-01','end':str(today+timedelta(days=10))})
    return await client.request('GET','/v2/calendar?'+q)

async def signal_data(client, folder, now):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    sessions=await calendar(client,now)
    # Allow EOD consolidation; today never enters a next-open signal.
    closed=[r for r in sessions if session_dt(r,'close')+timedelta(minutes=20)<=now]
    if not closed:raise Blocked('No completed market session')
    day=closed[-1]['date']
    cached=folder/'signal.json'
    if cached.exists():
        old=json.loads(cached.read_text())
        if old.get('date')==day and old.get('rule')==RULE:return old,sessions
    text=await asyncio.to_thread(fetch_members)
    m=memberships(text)
    (folder/'membership.csv').write_text(text)
    seed=json.loads(gzip.open(Path(__file__).with_name('seed.json.gz'),'rt',encoding='utf-8').read())
    names=set(seed['stocks'])
    for d,ss in m:
        if seed['anchor']<=d<=day:names.update(ss)
    names.update(members_at(m,day));names=sorted(names)
    bars={s:{} for s in names}
    for offset in range(0,len(names),50):
        token=None;seen=set()
        while True:
            q={'symbols':','.join(names[offset:offset+50]),'timeframe':'1Day','start':'2023-01-01T00:00:00Z',
               'end':str(date.fromisoformat(day)+timedelta(days=1))+'T00:00:00Z',
               'adjustment':'all','feed':'sip','limit':10000,'sort':'asc'}
            if token:q['page_token']=token
            result=await client.request('GET','/v2/stocks/bars?'+urllib.parse.urlencode(q),data=True)
            for s,rows in result.get('bars',{}).items():
                for b in rows:
                    d=stamp(b['t']).astimezone(ET).date().isoformat()
                    if d<=day:bars[s][d]=[float(b['c']),float(b['v'])]
            token=result.get('next_page_token')
            if not token:break
            if token in seen:raise Blocked('Repeated market-data pagination token')
            seen.add(token)
    # New candidates need their observed listing history before the frozen anchor.
    anchor=seed['anchor'];factors={}
    for s,rows in bars.items():
        old=seed['stocks'].get(s)
        if old and rows.get(anchor):
            factors[s]=old['bars'][-1][1]/rows[anchor][0]
        elif not old:
            seed['stocks'][s]={'observed_before':0,'bars':[[d,c,v] for d,(c,v) in sorted(rows.items()) if d<=anchor]}
            factors[s]=1.
    for row in closed:
        d=row['date']
        if d<=anchor:continue
        prices={s:(rows[d][0]*factors[s],rows[d][1]) for s,rows in bars.items() if s in factors and d in rows}
        append_day(seed,d,prices,members_at(m,d))
    out=decision(seed)
    out.update({'feed':'SIP adjusted daily','source':'Alpaca + dated fja05680 membership',
                'membership_sha256':hashlib.sha256(text.encode()).hexdigest(),
                'membership_last_change':max(d for d,_ in m if d<=day),
                'updated_at':now.isoformat(),'anchor':anchor,'seed_run':seed['run'],
                'deviations':['This holds five momentum-ranked stocks, not the backtested daily equal-weight twenty.',
                              'Existing-position rebalance orders below $1 are skipped; weights can drift.',
                              'Live fractional market orders run just after the open, not at the historical opening print.',
                              'Broker cash earns only its actual credited interest; the backtest T-bill yield is not promised.',
                              'A 1% execution reserve and broker minimums affect achievable weights.',
                              'Forward prices come from Alpaca SIP, rather than the frozen Yahoo observations.']})
    tmp=folder/'observations.json.gz.tmp'
    with gzip.open(tmp,'wt',encoding='utf-8') as f:json.dump({'bars':bars,'signal':out},f)
    tmp.replace(folder/'observations.json.gz')
    tmp=cached.with_suffix('.tmp');tmp.write_text(json.dumps(out,indent=2));tmp.replace(cached)
    return out,sessions
