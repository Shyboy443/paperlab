"""Vectorized, strictly lagged daily features and point-in-time universes."""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
import numpy as np
import pandas as pd

def wilder_atr(values,period=14):
    out=np.full(values.shape,np.nan)
    for j in range(values.shape[1]):
        count=0;total=0.;previous=np.nan
        for i,v in enumerate(values[:,j]):
            if not np.isfinite(v):count=0;total=0.;previous=np.nan
            elif count<period:
                count+=1;total+=v
                if count==period:previous=total/period;out[i,j]=previous
            else:previous+=(v-previous)/period;out[i,j]=previous
    return out

@dataclass(frozen=True)
class Config:
    lookback:int=63
    breadth:float=.50
    regime:bool=True
    momentum:bool=True
    harvest:bool=True
    fee:float=.0004
    slippage:float=.0002
    funding:bool=True
    transfer_fee:float=1.
    seed:int|None=None
    starting_equity:float=100000.
    gross_cap:float=1.5
    coin_cap:float=.05
    coin_target_vol:float=.15
    portfolio_target_vol:float=.20
    kill_dd:float=.20
    rank_buffer:int=2

class DailyData:
    def __init__(self,root):
        self.root=Path(root);meta=json.loads((self.root/'metadata.json').read_text())
        self.symbols=sorted(meta);self.index={s:i for i,s in enumerate(self.symbols)}
        self.dates=pd.date_range('2019-01-01','2025-12-31',tz='UTC')
        self.raw={k:pd.DataFrame(index=self.dates,columns=self.symbols,dtype=float)
                  for k in ('open','high','low','close','quote_volume')}
        for s in self.symbols:
            f=pd.read_csv(self.root/'daily'/(s+'.csv'))
            f.index=pd.to_datetime(f.timestamp,unit='ms',utc=True)
            for k in self.raw:self.raw[k][s]=f[k].reindex(self.dates)
        c=self.raw['close'];h=self.raw['high'];l=self.raw['low']
        self.vol=np.log(c/c.shift()).rolling(30,min_periods=30).std(ddof=1).mul(np.sqrt(365)).shift()
        self.volume=self.raw['quote_volume'].rolling(30,min_periods=30).mean().shift()
        self.close=c.shift()
        self.ma50=c.rolling(50,min_periods=50).mean().shift()
        tr=np.fmax.reduce([h.to_numpy()-l.to_numpy(),(h-c.shift()).abs().to_numpy(),(l-c.shift()).abs().to_numpy()])
        self.atr=pd.DataFrame(wilder_atr(tr),index=self.dates,columns=self.symbols).shift()
        self.momentum={n:(c/c.shift(n)-1).where(c.rolling(n+1).count()==n+1).shift() for n in (42,63,90)}
        eligible=np.zeros(c.shape,dtype=bool)
        for j,s in enumerate(self.symbols):
            for session in meta[s]['sessions']:
                first=pd.to_datetime(session['first_ms'],unit='ms',utc=True)
                end=pd.to_datetime(session['last_ms'],unit='ms',utc=True)+pd.Timedelta(days=1)
                eligible[:,j]|=(self.dates>=first+pd.Timedelta(days=181))&(self.dates<=end)
        self.eligible=pd.DataFrame(eligible,index=self.dates,columns=self.symbols)
        self.eligible &= self.close.notna() & self.vol.gt(0) & self.volume.gt(0) & c.rolling(91).count().shift().eq(91)
        self.universe=[];self.available=[]
        for d in self.dates:
            v=self.volume.loc[d].where(self.eligible.loc[d]).dropna().sort_values(ascending=False,kind='stable')
            self.available.append(len(v));self.universe.append(v.head(50).index.tolist() if len(v)>=50 else [])
        self.breadth=np.array([float((self.close.iloc[i][u]>self.ma50.iloc[i][u]).mean()) if u else np.nan
                              for i,u in enumerate(self.universe)])
        self.btc_up=np.zeros(len(self.dates),dtype=bool)
        if (self.root/'btc_spot.csv').exists():
            b=pd.read_csv(self.root/'btc_spot.csv');b.index=pd.to_datetime(b.timestamp,unit='ms',utc=True)
            v=b.close;self.btc_up=(v.shift()>v.rolling(200,min_periods=200).mean().shift()).reindex(self.dates).eq(True).to_numpy()
        self.cache={}

    def targets(self,config,symbols):
        key=(config.lookback,config.breadth,config.regime,config.momentum,tuple(symbols))
        if key in self.cache:return self.cache[key]
        ranks=np.full((len(self.dates),len(symbols)),np.nan);weights=np.zeros_like(ranks);risk_weights=np.zeros_like(ranks)
        members=np.zeros(ranks.shape,dtype=bool);idx={s:j for j,s in enumerate(symbols)}
        up=self.btc_up&(self.breadth>config.breadth)
        for i,u in enumerate(self.universe):
            if not u:continue
            ordered=self.momentum[config.lookback].iloc[i][u].sort_values(ascending=False,kind='stable').index.tolist()
            for rank,s in enumerate(ordered,1):
                ranks[i,idx[s]]=rank;members[i,idx[s]]=True
                risk_weights[i,idx[s]]=min(config.coin_cap,config.coin_target_vol/self.vol.iloc[i][s])
            if config.momentum and (not config.regime or up[i]):
                for s in ordered[:10]:weights[i,idx[s]]=risk_weights[i,idx[s]]
        out={'ranks':ranks,'weights':weights,'risk_weights':risk_weights,'members':members,'up':up,
             'atr':self.atr.reindex(columns=symbols).to_numpy()}
        self.cache[key]=out;return out

    def selection(self,path):
        selected={};coverage=[]
        for d,u,n,b in zip(self.dates,self.universe,self.available,self.breadth):
            coverage.append({'date':d.isoformat(),'eligible':n,'universe_size':len(u),'breadth':b})
            for s in u:
                for off in (-1,0,1):
                    m=d+pd.DateOffset(months=off)
                    if '2020-01'<=m.strftime('%Y-%m')<='2025-12':selected.setdefault(s,set()).add(m.strftime('%Y-%m'))
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({s:sorted(v) for s,v in sorted(selected.items())},indent=2))
        pd.DataFrame(coverage).to_csv(path.parent/'universe_coverage.csv',index=False)
        print('Selected',len(selected),'contracts /',sum(len(v) for v in selected.values()),'months; first full top50:',
              next((x['date'] for x in coverage if x['universe_size']==50),None),flush=True)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();DailyData(a.data).selection(a.output/'required_months.json')
