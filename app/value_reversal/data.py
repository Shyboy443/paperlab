"""Alpaca calendar and consolidated historical bars. GET requests only."""
import math, urllib.parse
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from .model import validate

ET=ZoneInfo('America/New_York')

def parse_ts(value):return int(datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()*1000)
def iso(ts):return datetime.fromtimestamp(ts/1000,timezone.utc).isoformat().replace('+00:00','Z')

async def download(client,start,end,progress=None):
    query=urllib.parse.urlencode({'start':start[:10],'end':end[:10]})
    rows=await client.request('GET','/v2/calendar?'+query)
    sessions=[]
    for s in rows:
        def moment(k):
            return int(datetime.fromisoformat(s['date']+'T'+s[k]).replace(tzinfo=ET).timestamp()*1000)
        sessions.append({'date':s['date'],'open_ms':moment('open'),'close_ms':moment('close')})
    bars=[];token=None;seen=set()
    while True:
        q={'symbols':'QQQ','timeframe':'15Min','start':start,'end':end,'adjustment':'all',
           'feed':'sip','limit':10000,'sort':'asc'}
        if token:q['page_token']=token
        d=await client.request('GET','/v2/stocks/bars?'+urllib.parse.urlencode(q),data=True)
        for r in d.get('bars',{}).get('QQQ',[]):
            b=[parse_ts(r['t']),*[float(r[k]) for k in ('o','h','l','c','v')]];validate(b);bars.append(b)
        if progress:progress(len(bars))
        token=d.get('next_page_token')
        if not token:break
        if token in seen:raise ValueError('Repeated Alpaca page token')
        seen.add(token)
    if not bars or not sessions:raise ValueError('Alpaca returned no QQQ bars/calendar')
    if any(bars[i][0]<=bars[i-1][0] for i in range(1,len(bars))):raise ValueError('Nonmonotone QQQ data')
    return {'bars':bars,'sessions':sessions,'symbol':'QQQ','feed':'SIP adjusted 15-minute bars',
            'source':'Alpaca','start':start,'end':end}

async def recent(client,now):
    # Request ends outside the Basic plan's latest-15-minute restriction.
    end=now-timedelta(minutes=16)
    return await download(client,(now-timedelta(days=8)).isoformat(),end.isoformat())
