"""Vectorized asset accounting inside an hourly, stateful execution loop.

Stops, inventory-dependent funding and permanent kills require chronological
state; feature calculation and all multi-asset book arithmetic are vectorized.
Spot is real Bybit spot, futures are Binance native linear contracts.
"""
from __future__ import annotations
from dataclasses import asdict
import json,tempfile
from pathlib import Path
import numpy as np
import pandas as pd
from model import Config

HOUR=3600000
class IntegrityError(RuntimeError):pass

class MarketBook:
    def __init__(self,daily,selection):
        self.daily=daily;root=daily.root;selected=json.loads(Path(selection).read_text())
        self.symbols=sorted(selected);n=len(self.symbols)
        self.hours=pd.date_range('2019-01-01','2025-12-31 23:00',freq='h',tz='UTC')
        self.start_ms=int(self.hours[0].timestamp()*1000);shape=(len(self.hours),n)
        cache=root/'matrix_cache';cache.mkdir(exist_ok=True)
        self.cache=Path(tempfile.mkdtemp(prefix='book_',dir=cache))
        self.price={}
        for k in ('open','high','low','close','mark','spot_open','spot_close'):
            # Column-contiguous disk-backed doubles retain source precision and
            # keep the seven-year dataset usable on memory-constrained machines.
            a=np.memmap(self.cache/(k+'.bin'),dtype='float64',mode='w+',shape=shape,order='F')
            a[:]=np.nan;a.flush();self.price[k]=a
        self.tradable=np.zeros(shape,bool);self.spot_tradable=np.zeros(shape,bool)
        self.rates=np.memmap(self.cache/'rates.bin',dtype='float64',mode='w+',shape=shape,order='F');self.rates[:]=0;self.rates.flush()
        self.events=np.zeros(shape,bool);self.expected=np.zeros(shape,bool)
        # Only the >threshold decision is needed by execution. Its three-rate
        # mean is calculated in double precision before storing a boolean.
        self.score=np.zeros(shape,bool);self.spot_known=np.zeros((len(daily.dates),n),bool)
        self.terminal=np.zeros(shape,bool);self.spot_terminal=np.zeros(shape,bool)
        self.quality=[];meta=json.loads((root/'metadata.json').read_text());sm=json.loads((root/'spot_metadata.json').read_text())
        for j,s in enumerate(self.symbols):
            for folder,keys in [('hourly',('open','high','low','close')),('mark_hourly',('open',)),('spot_hourly',('open','close'))]:
                path=root/folder/(s+'.csv')
                if not path.exists():
                    if folder=='spot_hourly':continue
                    raise IntegrityError('Missing '+str(path))
                f=pd.read_csv(path);ix=(f.timestamp.to_numpy(dtype='int64')-self.start_ms)//HOUR
                ok=(ix>=0)&(ix<len(self.hours));ix=ix[ok]
                if f.timestamp.duplicated().any():raise IntegrityError('Duplicate '+str(path))
                mult=sm[s]['multiplier'] if folder=='spot_hourly' else 1
                for k in keys:
                    dest='mark' if folder=='mark_hourly' else 'spot_'+k if folder=='spot_hourly' else k
                    self.price[dest][ix,j]=f[k].to_numpy()[ok]*mult
                if folder=='hourly':self.tradable[ix,j]=f.volume.to_numpy()[ok]>0
                if folder=='spot_hourly':self.spot_tradable[ix,j]=f.volume.to_numpy()[ok]>0
            p=root/'spot_daily'/(s+'.csv')
            if p.exists():
                f=pd.read_csv(p);f.index=pd.to_datetime(f.timestamp,unit='ms',utc=True)
                self.spot_known[:,j]=f.close.reindex(daily.dates).shift().notna().to_numpy()
            # Lifecycle end is used only for mandatory terminal accounting, never
            # to select the earlier tradable universe.
            for session in meta[s]['sessions']:
                a=(session['first_ms']-self.start_ms)//HOUR;b=(session['last_ms']-self.start_ms)//HOUR+24
                ix=np.flatnonzero(self.tradable[max(0,a):min(len(self.hours),b),j])+max(0,a)
                if len(ix) and ix[-1]+1<len(self.hours) and self.hours[ix[-1]].normalize()==pd.to_datetime(session['last_ms'],unit='ms',utc=True):
                    self.terminal[ix[-1]+1,j]=True
            # Never hedge into a data-series ending unexpectedly. Endpoints are
            # disclosed last-trade liquidation proxies, not forecast exits.
            spots=np.flatnonzero(self.spot_tradable[:,j])
            if len(spots) and spots[-1]+1<len(self.hours) and sm[s].get('last_ms',0)<1767139200000:
                self.spot_terminal[spots[-1]+1,j]=True
            f=pd.read_csv(root/'funding'/(s+'.csv'))
            stamps=f.timestamp.to_numpy(dtype='int64');ix=(stamps-self.start_ms)//HOUR
            ok=(ix>=0)&(ix<len(self.hours));values=f.rate.to_numpy()[ok];ix=ix[ok]
            if len(np.unique(ix))!=len(ix):raise IntegrityError('Duplicate settlement hour '+s)
            self.rates[ix,j]=values;self.events[ix,j]=True
            for row in f.itertuples(index=False):
                k=(int(row.timestamp)+int(row.interval_hours*HOUR)-self.start_ms)//HOUR
                if 0<=k<len(self.hours) and self.hours[k].strftime('%Y-%m') in selected[s]:self.expected[k,j]=True
            # Preceding observed intervals normalize only the funding SIGNAL.
            # A gap greater than eight hours restarts the three-event history.
            score=lagged_funding_score(stamps,f.rate.to_numpy(),self.start_ms,len(self.hours))
            self.score[:,j]=score>.00005
            missing=self.expected[:,j]&~self.events[:,j]&self.tradable[:,j]
            marks=self.events[:,j]&~np.isfinite(self.price['mark'][:,j])&self.tradable[:,j]
            self.quality.append({'symbol':s,'expected_rate_gaps':int(missing.sum()),'missing_settlement_marks':int(marks.sum()),
                                 'bybit_spot_days':sm[s].get('days',0),'bybit_spot_identity':sm[s]})
            if (j+1)%50==0:print('Loaded historical book',j+1,'/',n,flush=True)
        for array in [*self.price.values(),self.rates]:array.flush()

    def close(self):
        for array in [*self.price.values(),self.rates]:array._mmap.close()
        # Delete only this instance's verified cache, never source data.
        parent=(self.daily.root/'matrix_cache').resolve();target=self.cache.resolve()
        if not target.is_relative_to(parent):raise IntegrityError('Unsafe cache path')
        for p in target.glob('*.bin'):p.unlink()
        target.rmdir()

def lagged_funding_score(stamps,rates,start_ms,hours):
    """At hour i, use only settlements strictly before that hour's boundary."""
    out=np.full(hours,np.nan);recent=[];last=None
    for t,r in zip(stamps,rates):
        interval=round((int(t)-last)/HOUR) if last is not None else np.nan
        if not np.isfinite(interval) or interval>8.1 or interval<=0:recent=[]
        if np.isfinite(interval) and 0<interval<=8.1:recent.append(float(r)*8/interval)
        recent=recent[-3:]
        k=(int(t)-start_ms)//HOUR+1
        if 0<=k<hours:out[k]=np.mean(recent) if len(recent)==3 else np.nan
        last=int(t)
    # Explicitly propagate unknown resets too; ffill of nan alone would leak the
    # last known three-event score across a provider/lifecycle gap.
    event_hours={(int(t)-start_ms)//HOUR+1 for t in stamps}
    prev=np.nan;last_observed=None;event_stamps={((int(t)-start_ms)//HOUR+1):int(t) for t in stamps}
    for i in range(hours):
        if i in event_hours:prev=out[i];last_observed=event_stamps[i]
        else:out[i]=prev
        if last_observed is None or start_ms+i*HOUR-last_observed>8.1*HOUR:out[i]=np.nan
    return out

class Engine:
    def __init__(self,book,config):
        self.b=book;self.c=config;n=len(book.symbols);self.q=np.zeros(n);self.h=np.zeros(n)
        self.p=np.zeros(n);self.s=np.zeros(n);self.stop=np.full(n,np.nan);self.anchor=np.full(n,np.nan)
        self.binance=config.starting_equity/2;self.spot_cash=config.starting_equity/2;self.pending=None
        self.fees=0.;self.slips=0.;self.funding_cash=0.;self.transfers=0.;self.price_pnl=0.
        self.peak=config.starting_equity;self.killed=False;self.kill_time=None;self.reason=None
        self.orders=[];self.trades=[];self.open_time=np.full(n,-1,dtype=int);self.hedge_time=np.full(n,-1,dtype=int)
        self.rng=np.random.default_rng(config.seed);self.daily_equity=[];self.scale=1.;self.cap_actions=0;self.terminal_proxies=0

    def equity(self):
        return self.binance+self.spot_cash+np.dot(self.h,self.s)+(self.pending[2] if self.pending else 0.)

    def mark(self,p,s):
        valid=np.isfinite(p)&(p>0);change=np.where(valid,p,self.p)-self.p
        pnl=float(np.dot(self.q-self.h,change));self.binance+=pnl;self.price_pnl+=pnl
        self.p[valid]=p[valid]
        valid=np.isfinite(s)&(s>0)
        self.s[valid]=s[valid]

    def trade(self,kind,j,target,time,reason,atr=None,force=False):
        old=self.q[j] if kind=='momentum' else self.h[j];delta=float(target-old)
        if abs(delta)*max(self.p[j],self.s[j])<1e-7:return
        slip=self.c.slippage+(self.rng.uniform(0,.0002) if self.c.seed is not None else 0.)
        f_notional=abs(delta)*self.p[j];s_notional=abs(delta)*self.s[j] if kind=='hedge' else 0.
        fee=(f_notional+s_notional)*self.c.fee;slips=(f_notional+s_notional)*slip
        self.binance-=f_notional*(self.c.fee+slip)
        if kind=='hedge':
            self.spot_cash-=delta*self.s[j]+s_notional*(self.c.fee+slip)
            self.h[j]=target
            if old==0:self.hedge_time[j]=time
            if target==0:self.trades.append({'kind':kind,'symbol':self.b.symbols[j],'holding_hours':time-self.hedge_time[j],'reason':reason})
        else:
            self.q[j]=target
            if old==0:
                self.stop[j]=self.p[j]*(1+slip)-2*float(atr);self.open_time[j]=time
            if target==0:
                self.trades.append({'kind':kind,'symbol':self.b.symbols[j],'holding_hours':time-self.open_time[j],'reason':reason})
                self.stop[j]=np.nan
        self.fees+=fee;self.slips+=slips
        self.orders.append({'hour':time,'symbol':self.b.symbols[j],'kind':kind,'reason':reason,
            'delta_contract_units':delta,'perp_reference':self.p[j],'spot_reference_per_contract_unit':self.s[j] if kind=='hedge' else None,
            'fee':fee,'slippage':slips,'adverse_slippage_rate':slip,'equity_after':self.equity()})

    def can_trade(self,i,j,hedge=False):
        return self.b.tradable[i,j] and (not hedge or self.b.spot_tradable[i,j])

    def flatten(self,i,reason,force=False):
        for j in np.flatnonzero(self.q):
            if force or self.can_trade(i,j):self.trade('momentum',j,0,i,reason)
        for j in np.flatnonzero(self.h):
            if force or self.can_trade(i,j,True):self.trade('hedge',j,0,i,reason)

    def risk(self,i):
        eq=self.equity();self.peak=max(self.peak,eq)
        if not self.killed and (eq<self.peak*(1-self.c.kill_dd) or self.binance<=0 or self.spot_cash+np.dot(self.h,self.s)<=0):
            self.killed=True;self.kill_time=self.b.hours[i].isoformat();self.reason='drawdown_kill' if eq<self.peak*(1-self.c.kill_dd) else 'venue_insolvency'
        if self.killed:self.flatten(i,self.reason);return
        # Reductions are mandatory and override deadbands; iterate to cover the
        # small NAV loss caused by the reductions themselves.
        for _ in range(5):
            eq=self.equity();changed=False
            for j in np.flatnonzero(self.q*self.p>self.c.coin_cap*eq*(1+1e-9)):
                if self.can_trade(i,j):self.trade('momentum',j,self.c.coin_cap*eq/self.p[j],i,'position_cap');changed=True
            m=np.dot(self.q,self.p);f=np.dot(self.h,self.p);spot=np.dot(self.h,self.s)
            gross=m+f+spot;future=m+f
            ratio=min(1.,self.c.gross_cap*max(eq,0)/gross if gross>0 else 1.,
                      1.9*max(self.binance,0)/future if future>0 else 1.)
            if ratio<1-1e-8:
                for j in np.flatnonzero(self.q):
                    if self.can_trade(i,j):self.trade('momentum',j,self.q[j]*ratio*.9999,i,'gross_or_venue_margin');changed=True
                for j in np.flatnonzero(self.h):
                    if self.can_trade(i,j,True):self.trade('hedge',j,self.h[j]*ratio*.9999,i,'gross_or_venue_margin');changed=True
            if self.spot_cash<0:
                need=-self.spot_cash
                total=np.dot(self.h,self.s)
                for j in np.flatnonzero(self.h):
                    if self.can_trade(i,j,True):self.trade('hedge',j,self.h[j]*max(0,1-need/total-.0002),i,'spot_cash_constraint');changed=True
            if not changed:break
            self.cap_actions+=1

    def transfer(self,i):
        if self.pending:return
        eq=self.equity();imbalance=self.binance-eq/2
        if abs(imbalance)<100:return
        if imbalance>0:
            free=max(0,self.binance*.95-np.dot(self.q+self.h,self.p)/2)
            amount=min(imbalance,free)
            if amount<=100:return
            self.binance-=amount+self.c.transfer_fee;dest='spot'
        else:
            amount=min(-imbalance,max(0,self.spot_cash-.05*(self.spot_cash+np.dot(self.h,self.s))))
            if amount<=100:return
            self.spot_cash-=amount+self.c.transfer_fee;dest='binance'
        self.transfers+=self.c.transfer_fee;self.pending=(i+1,dest,amount)

    def daily_rebalance(self,i,d,targets):
        if self.killed:return
        if len(self.daily_equity)>=31:
            r=np.diff(np.log(self.daily_equity[-31:]));v=float(np.std(r,ddof=1)*np.sqrt(365))
            self.scale=min(1,self.c.portfolio_target_vol/v) if v>0 else 1.
        rank=targets['ranks'][d];weights=targets['weights'][d]*self.scale
        # Exits first. Rank 11 may retain a rank-10 position until a >=2 move,
        # as specified; lost eligibility and regime-off are mandatory exits.
        desired=self.q.copy();eq=self.equity()
        for j in range(len(self.q)):
            held=self.q[j]>0
            mandatory=held and (not targets['members'][d,j] or not self.c.momentum or (self.c.regime and not targets['up'][d]))
            moved=not np.isfinite(self.anchor[j]) or not np.isfinite(rank[j]) or abs(rank[j]-self.anchor[j])>=self.c.rank_buffer
            if mandatory:desired[j]=0
            elif moved or not held:desired[j]=weights[j]*eq/self.p[j] if self.p[j]>0 else 0
            # Coin and portfolio volatility reductions override rank deadband,
            # including a retained rank-11 asset outside today's entry decile.
            if held and targets['members'][d,j] and self.p[j]>0:
                risk_weight=targets.get('risk_weights',targets['weights'])[d,j]*self.scale
                desired[j]=min(desired[j],risk_weight*eq/self.p[j])
        for grow in (False,True):
            for j in np.flatnonzero((desired>self.q) if grow else (desired<self.q)):
                if self.can_trade(i,j):
                    self.trade('momentum',j,desired[j],i,'daily_rank_or_regime',targets['atr'][d,j]);self.anchor[j]=rank[j]
        eq=self.equity();momentum=np.dot(self.q,self.p)
        good=(targets['members'][d]&self.b.spot_known[d]&(self.b.score[i]>.00005)&self.b.spot_tradable[i]&self.b.tradable[i]
              &(self.p>0)&(self.s>0)&(np.abs(self.s/np.maximum(self.p,1e-30)-1)<.05))
        if not self.c.harvest:good[:]=False
        count=int(good.sum());desired_h=np.zeros(len(self.h))
        if count:
            ratio=float(np.mean(self.s[good]/self.p[good]))
            venue_spot=self.spot_cash+np.dot(self.h,self.s)
            future_capacity=max(0,1.9*self.binance-momentum)
            available=min(max(0,self.c.gross_cap*eq*self.scale-momentum)/(1+ratio),max(0,venue_spot*.95)/ratio,future_capacity)
            desired_h[good]=available/count/self.p[good]
        for grow in (False,True):
            for j in np.flatnonzero((desired_h>self.h) if grow else (desired_h<self.h)):
                if self.can_trade(i,j,True):self.trade('hedge',j,desired_h[j],i,'funding_allocation')
        self.risk(i)

    def run(self,start,end):
        b=self.b;start=pd.Timestamp(start,tz='UTC');end=pd.Timestamp(end,tz='UTC')
        first=int((start.value//1000000-b.start_ms)//HOUR);finish=int((end.value//1000000-b.start_ms)//HOUR)
        targets=b.daily.targets(self.c,b.symbols);rows=[]
        archive_quiet=np.array([q['bybit_spot_identity'].get('archive_fallback',False) for q in b.quality])
        for i in range(first,finish):
            d=i//24;op=b.price['open'][i];sp=b.price['spot_open'][i]
            self.mark(op,sp)
            if self.pending and i>=self.pending[0]:
                _,dest,amount=self.pending
                if dest=='spot':self.spot_cash+=amount
                else:self.binance+=amount
                self.pending=None
            held=(self.q!=0)|(self.h!=0)
            if np.any(held&b.expected[i]&~b.events[i]&b.tradable[i]):
                raise IntegrityError('Missing actual funding while held at '+str(b.hours[i]))
            event=held&b.events[i]
            if np.any(event&~np.isfinite(b.price['mark'][i])):raise IntegrityError('Missing settlement mark')
            if self.c.funding:
                flow=float(-np.sum((self.q[event]-self.h[event])*b.price['mark'][i,event]*b.rates[i,event]))
                self.binance+=flow;self.funding_cash+=flow
            for j in np.flatnonzero(held&(b.terminal[i]|b.spot_terminal[i])):
                if self.q[j]:self.trade('momentum',j,0,i,'archival_terminal_proxy')
                if self.h[j]:self.trade('hedge',j,0,i,'archival_terminal_proxy')
                self.terminal_proxies+=1
            if np.any(((self.q>0)|(self.h>0))&~np.isfinite(op)):
                raise IntegrityError('Unobserved futures price while held '+str(b.hours[i]))
            # Trades archive aggregation proves no-trade intervals for its
            # missing bars. Missing API candles are not granted that exemption.
            if np.any((self.h>0)&~np.isfinite(sp)&~archive_quiet):
                raise IntegrityError('Unobserved Bybit spot price while held '+str(b.hours[i]))
            self.risk(i)
            # Gap stop before an allocation. An existing stopped long may not
            # immediately re-enter at that same hour's daily decision.
            stopped=np.zeros(len(self.q),bool)
            for j in np.flatnonzero((self.q>0)&(self.p<self.stop)&b.tradable[i]):
                self.trade('momentum',j,0,i,'gap_stop');stopped[j]=True
            if i%24==0:
                if not self.killed:self.transfer(i)
                save=targets['weights'][d].copy();targets['weights'][d,stopped]=0
                self.daily_rebalance(i,d,targets);targets['weights'][d]=save
            # Eligibility uses strictly earlier settlements. The current event
            # affects decisions beginning at the next observed hourly boundary.
            for j in np.flatnonzero((self.h>0)&(~np.isfinite(b.score[i])|(b.score[i]<=.00005))):
                if self.can_trade(i,j,True):self.trade('hedge',j,0,i,'funding_threshold_exit')
            for j in np.flatnonzero((self.q>0)&(b.price['low'][i]<self.stop)&b.tradable[i]):
                # Mark inventory to crossed stop for exact realized PnL, then
                # resume the remaining book's actual hour-end valuation.
                ref=min(self.p[j],self.stop[j]);change=(ref-self.p[j])*self.q[j]
                self.binance+=change;self.price_pnl+=change
                original=self.p[j];self.p[j]=ref;self.trade('momentum',j,0,i,'entry_atr_stop');self.p[j]=original
            self.mark(b.price['close'][i],b.price['spot_close'][i]);self.risk(i)
            if i==finish-1:self.flatten(i,'period_end',True)
            eq=self.equity();m=float(np.dot(self.q,self.p));h=float(np.dot(self.h,self.p));s=float(np.dot(self.h,self.s))
            rows.append((b.hours[i]+pd.Timedelta(hours=1),eq,(m+h+s)/eq if eq>0 else 0,m/eq if eq>0 else 0,
                         self.fees,self.slips,self.funding_cash,self.transfers,self.binance,self.spot_cash+s,self.scale,
                         float(np.max(self.q*self.p))/eq if eq>0 else 0))
            if i%24==23:self.daily_equity.append(eq)
        names=['timestamp','equity','gross_exposure','momentum_exposure','fees','slippage','funding_cash','transfer_fees',
               'binance_equity','bybit_equity','vol_scale','max_momentum_weight']
        history=pd.DataFrame(rows,columns=names)
        # All positions are flat; book conservation includes paid execution and
        # transfer costs, realized futures PnL, and actual spot purchase/sale cash.
        return {'history':history,'orders':pd.DataFrame(self.orders),'trades':pd.DataFrame(self.trades),
                'config':asdict(self.c),'kill_time':self.kill_time,'kill_reason':self.reason,'terminal_proxies':self.terminal_proxies,
                'cap_actions':self.cap_actions}
