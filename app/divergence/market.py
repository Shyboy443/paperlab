"""Binance perpetual public data, completed candles only, one consistent venue."""
from __future__ import annotations
import math
from app.autoresearch.client import Client

MIN=60_000
DAY=86_400_000
UNIVERSE=('BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','SUIUSDT')


def clean(rows, interval, end):
    bars={}
    for r in rows:
        t=int(r[0]);o,h,l,c,v=map(float,r[1:6])
        if t % interval or not all(math.isfinite(x) for x in (o,h,l,c,v)) or not 0<l<=min(o,c)<=max(o,c)<=h or v<0:
            raise ValueError('Invalid candle data')
        if t+interval<=end:bars[t]={'t':t,'o':o,'h':h,'l':l,'c':c,'v':v}
    return sorted(bars.values(),key=lambda b:b['t'])


class Market:
    def __init__(self, stop):self.client=Client(stop)

    def bars(self,symbol,start,end,tf='15m'):
        if symbol not in UNIVERSE or tf not in ('1m','15m'):raise ValueError('Unsupported market')
        interval=MIN if tf=='1m' else 15*MIN
        cursor=start//interval*interval;out=[]
        while cursor+interval<=end:
            part=self.client.get('perp','klines',{'symbol':symbol,'interval':tf,'startTime':cursor,'endTime':end-1,'limit':1500},weight=10)
            if not part:break
            out.extend(clean(part,interval,end))
            nxt=int(part[-1][0])+interval
            if nxt<=cursor:raise ValueError('Candle pagination stalled')
            cursor=nxt
            if len(part)<1500:break
        return out

    def quote(self,symbol):return self.client.quote('perp',symbol)
    def funding(self,symbol,start,end):return self.client.funding(symbol,start,end)
    def universe(self):return {r['symbol']:r for r in self.client.universe('perp') if r['symbol'] in UNIVERSE}
