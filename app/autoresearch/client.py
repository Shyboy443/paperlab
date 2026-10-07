"""Public data only. No keys, signatures, account endpoints or order methods."""
from __future__ import annotations
import json, math, time, urllib.error, urllib.parse, urllib.request
from app.config import AUTORESEARCH_SPOT_REST, AUTORESEARCH_PERP_REST
from .model import HOUR, closed_bars

class RateLimit(Exception):
    def __init__(self,seconds):self.seconds=max(60,min(3600,float(seconds)))

class Client:
    def __init__(self,stop,opener=None):
        self.stop=stop;self.opener=opener or urllib.request.urlopen;self.last=0.;self.window=time.monotonic();self.weight=0
    def get(self,venue,endpoint,params=None,weight=5):
        if venue not in ('spot','perp') or endpoint not in ('exchangeInfo','ticker/24hr','ticker/bookTicker','klines','fundingRate'):
            raise ValueError('Only whitelisted public market data endpoints are permitted')
        if venue=='spot' and endpoint=='fundingRate':raise ValueError('Spot has no funding')
        wait=max(0,self.last+1-time.monotonic())
        if time.monotonic()-self.window>=60:self.window=time.monotonic();self.weight=0
        if self.weight+weight>300:wait=max(wait,60-(time.monotonic()-self.window))
        if self.stop.wait(wait):raise InterruptedError()
        if time.monotonic()-self.window>=60:self.window=time.monotonic();self.weight=0
        self.weight+=weight;self.last=time.monotonic()
        base=AUTORESEARCH_SPOT_REST if venue=='spot' else AUTORESEARCH_PERP_REST
        path='/api/v3/' if venue=='spot' else '/fapi/v1/'
        url=base+path+endpoint+('?' + urllib.parse.urlencode(params) if params else '')
        req=urllib.request.Request(url,method='GET',headers={'User-Agent':'PaperLab-Autoresearch/1'})
        try:
            with self.opener(req,timeout=20) as res:
                if urllib.parse.urlsplit(res.geturl()).netloc!=urllib.parse.urlsplit(base).netloc:raise ValueError('Unexpected data host redirect')
                used=float(res.headers.get('X-MBX-USED-WEIGHT-1M','0'))
                if used>=1200:self.weight=300
                return json.load(res)
        except urllib.error.HTTPError as e:
            if e.code in (418,429):raise RateLimit(e.headers.get('Retry-After',300)) from None
            raise RuntimeError(f'{venue} market data HTTP {e.code}') from None
    def universe(self,venue):
        info=self.get(venue,'exchangeInfo',weight=20)
        ticks={r['symbol']:r for r in self.get(venue,'ticker/24hr',weight=80)}
        books={r['symbol']:r for r in self.get(venue,'ticker/bookTicker',weight=10)}
        rows=[]
        for s in info['symbols']:
            if s.get('quoteAsset')!='USDT' or s.get('status')!='TRADING':continue
            if venue=='perp' and s.get('contractType')!='PERPETUAL':continue
            if venue=='spot' and not s.get('isSpotTradingAllowed',False):continue
            ticker=ticks.get(s['symbol'],{}); book=books.get(s['symbol'],{})
            price=float(ticker.get('lastPrice',0)); volume=float(ticker.get('quoteVolume',0))
            bid=float(book.get('bidPrice',0));ask=float(book.get('askPrice',0))
            spread=(ask-bid)/((ask+bid)/2)*10000 if ask>=bid>0 else 99999
            finite=all(math.isfinite(x) for x in [price,volume,spread,bid,ask])
            liquid=finite and volume>=2_000_000 and spread<=12 and price>0
            filters={f['filterType']:f for f in s.get('filters',[])}
            lot=filters.get('MARKET_LOT_SIZE',{})
            if not float(lot.get('stepSize',0)):lot=filters.get('LOT_SIZE',{})
            notional=filters.get('NOTIONAL',filters.get('MIN_NOTIONAL',{}))
            constraints={'step':float(lot.get('stepSize',0)),'min_qty':float(lot.get('minQty',0)),
                         'max_qty':float(lot.get('maxQty',0)),'min_notional':float(notional.get('minNotional',notional.get('notional',0))),
                         'tick':float(filters.get('PRICE_FILTER',{}).get('tickSize',0))}
            if not all(math.isfinite(x) and x>=0 for x in constraints.values()):liquid=False;constraints={}
            rows.append({'symbol':s['symbol'],'price':price if finite else 0,'volume':volume if finite else 0,
                         'spread':spread if finite else 99999,'liquid':liquid,'constraints':constraints})
        return rows
    def history(self,venue,symbol,start,end):
        rows=[];cursor=start//HOUR*HOUR
        while cursor<end:
            part=self.get(venue,'klines',{'symbol':symbol,'interval':'1h','startTime':cursor,'endTime':end-1,'limit':1000})
            if not part:break
            rows+=part; nxt=int(part[-1][0])+HOUR
            if nxt<=cursor:raise ValueError('Market data pagination made no progress')
            cursor=nxt
            if len(part)<1000:break
        return closed_bars(rows,end)
    def funding(self,symbol,start,end):
        rows=[];cursor=start
        while cursor<end:
            part=self.get('perp','fundingRate',{'symbol':symbol,'startTime':cursor,'endTime':end-1,'limit':1000})
            if not part:break
            for x in part:
                ft,rate,mark=int(x['fundingTime']),float(x['fundingRate']),float(x.get('markPrice') or 0)
                if not math.isfinite(rate) or not math.isfinite(mark) or mark<0:raise ValueError('Invalid funding data')
                if start<=ft<end:rows.append((ft,rate,mark))
            nxt=int(part[-1]['fundingTime'])+1
            if nxt<=cursor:raise ValueError('Funding pagination made no progress')
            cursor=nxt
            if len(part)<1000:break
        return rows
    def quote(self,venue,symbol):
        r=self.get(venue,'ticker/bookTicker',{'symbol':symbol})
        bid,ask=float(r['bidPrice']),float(r['askPrice'])
        if not all(math.isfinite(x) for x in (bid,ask)) or not 0<bid<=ask:raise ValueError('Invalid live quote')
        return {'bid':bid,'ask':ask,'ts':int(time.time()*1000)}
