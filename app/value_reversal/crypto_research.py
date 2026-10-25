"""Fixed cross-market study of the published VARS signals; paper-only, no orders."""
from __future__ import annotations
import hashlib, json, math
from collections import Counter
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from .crypto_data import sessions, SYMBOLS
from .crypto_indicator import profile, advance

RULE='CRYPTO-LUX-VARS-NY18-v1'
# Explicit conservative historical execution assumptions, not claimed historical exchange metadata.
FILTERS={'BTCUSDT':(.1,.001),'ETHUSDT':(.01,.001),'SOLUSDT':(.001,.1)}
PERIODS={'development':('2022-01-01','2024-01-01'),'comparison':('2024-01-01','2026-01-01')}

def stamp(date):return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()*1000)

@dataclass(frozen=True)
class Config:
    symbol:str='BTCUSDT'
    minutes:int=15
    profile:str='previous'
    max_reversion_bars:int=5
    initial:float=10000.
    risk:float=.005
    fee_bps:float=4.
    slip_bps:float=2.
    funding:bool=True
    rows:int=60
    value_fraction:float=.7
    def __post_init__(self):
        if self.symbol not in SYMBOLS or self.minutes not in (15,30) or self.profile not in ('previous','developing') or self.max_reversion_bars not in (5,10):
            raise ValueError('Unknown fixed crypto experiment')
        if self.initial<=0 or self.risk!=.005 or self.rows!=60 or self.value_fraction!=.7:
            raise ValueError('Frozen risk/signal parameters changed')
        if not all(math.isfinite(x) and x>=0 for x in (self.initial,self.fee_bps,self.slip_bps)):
            raise ValueError('Invalid cost assumptions')
    @property
    def tick(self):return FILTERS[self.symbol][0]
    @property
    def lot(self):return FILTERS[self.symbol][1]
    @property
    def key(self):return f'{self.symbol}-{self.minutes}m-{self.profile}-{self.max_reversion_bars}bars'

def features(groups,symbol,minutes,progress=None):
    """Profiles are shared across four signal variants and four cost runs; no future candles."""
    cfg=Config(symbol=symbol,minutes=minutes);previous=None;out=[]
    for i,g in enumerate(groups):
        states={(p,w):None for p in ('previous','developing') for w in (5,10)};history=[]
        for bar in g['bars']:
            current=profile(history+[bar],cfg)
            row={'bar':bar,'session_close':g['close_ms'],'complete':g['complete'],'signals':{},
                 'previous':{k:previous[k] for k in ('low','high','poc')} if previous else None,
                 'developing':{k:current[k] for k in ('low','high','poc')} if current else None}
            for (p,w),state in states.items():
                levels=previous if p=='previous' else current
                state,sig=advance(state,bar,history[-1] if history else None,levels,w,cfg.tick)
                states[p,w]=state
                if sig:row['signals'][f'{p}-{w}']=sig
            out.append(row);history.append(bar)
        previous=profile(history,cfg) if g['complete'] else None
        if progress and i%200==0:progress(f'Profiles {symbol} {minutes}m: {i}/{len(groups)} sessions',flush=True)
    return out

class Book:
    def __init__(self,cfg):
        self.cfg=cfg;self.cash=cfg.initial;self.peak=cfg.initial;self.dd=0.;self.halted=False
        self.position=None;self.pending=None;self.curve=[];self.trades=[];self.signals=[]
        self.fees=0.;self.slippage=0.;self.funding_cost=0.;self.funding_events=0;self.halt_ts=None
    def px(self,raw,side):
        p=raw*(1+side*self.cfg.slip_bps/10000)/self.cfg.tick
        return (math.ceil(p-1e-7) if side>0 else math.floor(p+1e-7))*self.cfg.tick
    def equity(self,price):
        p=self.position
        return self.cash+((price-p['entry'])*p['side']*p['qty'] if p else 0)
    def mark(self,ts,price):
        eq=self.equity(price);self.peak=max(self.peak,eq);self.dd=max(self.dd,1-eq/self.peak)
        self.curve.append([ts,round(eq,6)])
        if self.dd>=.2 or eq<=0:
            self.halted=True
            if self.halt_ts is None:self.halt_ts=ts
    def fill(self,raw,side,qty):
        price=self.px(raw,side);fee=price*qty*self.cfg.fee_bps/10000
        self.fees+=fee;self.slippage+=abs(price-raw)*qty
        return price,fee
    def exit(self,raw,ts,reason):
        p=self.position;price,fee=self.fill(raw,-p['side'],p['qty'])
        pnl=(price-p['entry'])*p['side']*p['qty'];self.cash+=pnl-fee
        self.trades.append({**p,'exit':price,'exit_ts':ts,'reason':reason,
                           'net':pnl-fee-p['entry_fee']-p['funding_cost'],'fees':fee+p['entry_fee']})
        self.position=None;self.mark(ts,price)
    def process(self,row,funding):
        ts,o,h,l,c,v=row['bar'];end=ts+self.cfg.minutes*60000
        if self.pending:
            sig=self.pending;self.pending=None;side=sig['side'];price=self.px(o,side)
            valid=sig['stop']<price<sig['target'] if side>0 else sig['target']<price<sig['stop']
            stop=self.px(sig['stop'],-side);fee=price*self.cfg.fee_bps/10000
            risk=abs(price-stop)+fee+stop*self.cfg.fee_bps/10000
            units=min(self.cash*self.cfg.risk/risk if risk>0 else 0,self.cash/(price+fee))
            qty=math.floor(units/self.cfg.lot+1e-9)*self.cfg.lot
            reason='entry_outside_stop_target' if not valid else 'below_minimum_lot_or_notional' if qty<=0 or price*qty<5 else None
            if self.halted:reason='drawdown_halt'
            if reason:sig['execution']=reason
            else:
                px,entry_fee=self.fill(o,side,qty);self.cash-=entry_fee;sig['execution']='entered'
                self.position={**sig,'entry':px,'entry_ts':ts,'qty':qty,'entry_fee':entry_fee,'funding_cost':0.}
        p=self.position
        # A price gap exits at the opening timestamp, before a later funding settlement.
        # Intrabar touches have uncertain timing and retain conservative stop-first handling.
        if p:
            if (o<=p['stop'] if p['side']>0 else o>=p['stop']):self.exit(o,ts,'stop_gap')
            elif (o>=p['target'] if p['side']>0 else o<=p['target']):self.exit(p['target'],ts,'target_gap')
            p=self.position
        if p:
            for ft,interval,rate,mark in funding:
                if p['entry_ts']<ft<end:
                    cost=p['side']*p['qty']*mark*rate if self.cfg.funding else 0.
                    self.cash-=cost;self.funding_cost+=cost;p['funding_cost']+=cost
                    if self.cfg.funding:self.funding_events+=1
            side=p['side'];stop_hit=l<=p['stop'] if side>0 else h>=p['stop']
            target_hit=h>=p['target'] if side>0 else l<=p['target']
            if stop_hit:self.exit(min(o,p['stop']) if side>0 else max(o,p['stop']),end-1,'stop')
            elif target_hit:self.exit(p['target'],end-1,'target')
            elif end>=row['session_close']:self.exit(c,end-1,'session_close')
        self.mark(end-1,c)
        if self.halted and self.position:self.exit(c,end-1,'drawdown_halt')
        candidate=row['signals'].get(f'{self.cfg.profile}-{self.cfg.max_reversion_bars}')
        if candidate:
            sig=dict(candidate)
            reason='drawdown_halt' if self.halted else 'already_in_position' if self.position else 'session_close' if end>=row['session_close'] else None
            sig['execution']=reason or 'pending';self.signals.append(sig)
            if reason is None:self.pending=sig

def metrics(b):
    daily={};monthly={}
    for ts,eq in b.curve:
        d=datetime.fromtimestamp(ts/1000,timezone.utc).strftime('%Y-%m-%d');daily[d]=[ts,eq];monthly[d[:7]]=eq
    daily_returns=[];last=b.cfg.initial
    for _,eq in daily.values():daily_returns.append(eq/last-1);last=eq
    months=[];last=b.cfg.initial
    for month,eq in monthly.items():months.append({'month':month,'net_pct':(eq/last-1)*100});last=eq
    avg=sum(daily_returns)/len(daily_returns) if daily_returns else 0
    sd=math.sqrt(sum((r-avg)**2 for r in daily_returns)/(len(daily_returns)-1)) if len(daily_returns)>1 else 0
    downside=math.sqrt(sum(min(0,r)**2 for r in daily_returns)/len(daily_returns)) if daily_returns else 0
    eq=b.cash;years=(b.curve[-1][0]-b.curve[0][0]+b.cfg.minutes*60000)/31557600000 if b.curve else 0
    annual=((eq/b.cfg.initial)**(1/years)-1)*100 if eq>0 and years else None
    return {'equity':eq,'return_pct':(eq/b.cfg.initial-1)*100,'monthly_avg_net_pct':sum(r['net_pct'] for r in months)/len(months) if months else 0,
            'annualized_pct':annual,'sharpe':avg/sd*math.sqrt(365.25) if sd else None,'sortino':avg/downside*math.sqrt(365.25) if downside else None,
            'max_dd_pct':b.dd*100,'calmar':annual/(b.dd*100) if annual is not None and b.dd else None,
            'worst_month_pct':min((r['net_pct'] for r in months),default=0),'positive_months_pct':sum(r['net_pct']>0 for r in months)/len(months)*100 if months else 0,
            'trades':len(b.trades),'signal_count':len(b.signals),'entry_audit':dict(Counter(s['execution'] for s in b.signals)),
            'win_rate_pct':sum(t['net']>0 for t in b.trades)/len(b.trades)*100 if b.trades else None,
            'average_holding_hours':sum((t['exit_ts']-t['entry_ts'])/3600000 for t in b.trades)/len(b.trades) if b.trades else None,
            'fees':b.fees,'slippage':b.slippage,'funding_cost':b.funding_cost,'funding_events':b.funding_events,
            'halted':b.halted,'halt_ts':b.halt_ts,'curve':list(daily.values()),'monthly':months,
            'recent_trades':b.trades,'signals':b.signals}

def simulate(rows,cfg,funding,start,end):
    b=Book(cfg);index=0
    while index<len(funding) and funding[index][0]<start:index+=1
    for row in rows:
        ts=row['bar'][0]
        if ts<start:continue
        if ts>=end:break
        stop=ts+cfg.minutes*60000;events=[]
        while index<len(funding) and funding[index][0]<stop:
            if funding[index][0]>=ts:events.append(funding[index])
            index+=1
        b.process(row,events);last=row
    if b.position:b.exit(last['bar'][4],end-1,'end_of_test')
    if b.pending:b.pending['execution']='end_of_test';b.pending=None
    return metrics(b)

def evaluate(data,root=None,progress=print):
    out={'rule':RULE,'periods':PERIODS,'results':[],'symbols':list(SYMBOLS),'session':'18:00 America/New_York, 24/7, DST-aware',
         'source_video':'https://www.youtube.com/watch?v=dUczefIYKIU',
         'timeframe_evidence':[{'timestamp':'13:02–13:09','minutes':30},{'timestamp':'16:15–16:18','minutes':15},{'timestamp':'17:43','minutes':[15,30]}],
         'costs':{'fee_bps_per_side':4,'slippage_bps_per_side':2,'stress_multiplier':2,'funding':'Actual historical archive rates and settlement mark prices'},
         'risk':{'initial':10000,'per_trade_stop_risk_pct':.5,'notional_cap_pct':100,'drawdown_halt_pct':20},
         'data_manifest':data['manifest'],'funding_mark_method':data['funding_mark_method'],
         'execution_filters':{s:{'tick':t,'lot':l,'min_notional':5,'historical_assumption':True} for s,(t,l) in FILTERS.items()},
         'limitations':['Crypto markets differ from the NQ futures example; these are retrospective adaptations.',
             'Ticks/lots are fixed historical simulation assumptions, not a reconstructed historical filter schedule.',
             'OHLCV profiles approximate volume-at-price; no actual order-flow absorption is measured.',
             'Close-confirmed entry at next open, stop-first intrabar ties, fixed opposite-VA target and 18:00 session exit are execution assumptions.',
             'Funding charged for positions held through recorded settlement; mark uses nominal settlement hourly open.',
             'Gross/no-funding/stress runs are independent wallets; return differences include resulting sizing changes.',
             '20% drawdown halts entries permanently within each period; all subsequent indicator arrows remain counted.',
             '2024–2025 is a retrospective comparison, not newly unseen evidence. No grid winner is promoted.'],
         'source_sha256':{n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in ('crypto_data.py','crypto_indicator.py','crypto_research.py')},
         'created_at':datetime.now(timezone.utc).isoformat()}
    for symbol in SYMBOLS:
        d=data['symbols'][symbol]
        for minutes in (15,30):
            groups=sessions(d['bars'],minutes)
            bad=[g['date'] for g in groups[1:-1] if not g['complete']]
            if bad:raise ValueError('Incomplete crypto sessions: '+str(bad[:5]))
            rows=features(groups,symbol,minutes,progress)
            if root:
                import gzip
                with gzip.open(Path(root)/f'{symbol}-{minutes}m-features.json.gz','wt') as f:json.dump(rows,f,separators=(',',':'))
            for window in (5,10):
                for p in ('previous','developing'):
                    cfg=Config(symbol,minutes,p,window);result={'key':cfg.key,'config':asdict(cfg),'periods':{}}
                    for label,(a,z) in PERIODS.items():
                        period={}
                        for kind,run in [('net',cfg),('without_funding',replace(cfg,funding=False)),
                                         ('gross',replace(cfg,fee_bps=0,slip_bps=0,funding=False)),
                                         ('stress',replace(cfg,fee_bps=8,slip_bps=4))]:
                            period[kind]=simulate(rows,run,d['funding'],stamp(a),stamp(z))
                        period['funding_return_impact_pct']=period['without_funding']['return_pct']-period['net']['return_pct']
                        result['periods'][label]=period
                    out['results'].append(result)
                    progress(f'Evaluated {cfg.key}',flush=True)
    out['primary']='BTCUSDT-15m-previous-5bars';base=next(r for r in out['results'] if r['key']==out['primary'])['periods']['comparison']
    reasons=[]
    if base['net']['return_pct']<=0:reasons.append('Nonpositive net comparison return')
    if base['stress']['return_pct']<=0:reasons.append('Loses under doubled costs')
    if base['net']['max_dd_pct']>=20:reasons.append('20% drawdown threshold reached')
    if base['net']['trades']<30:reasons.append('Fewer than 30 completed trades')
    out['verdict']='FAIL' if reasons else 'RETROSPECTIVE_CHECKS_PASS_UNPROVEN';out['reasons']=reasons
    return out
