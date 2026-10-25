"""Causal session profile reversal. No broker orders, account writes or learned parameters."""
from __future__ import annotations
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

RULE = 'QQQ-VA70-ROWS64-ENGULF5-v1'
MINUTE = 60_000

@dataclass(frozen=True)
class Config:
    minutes: int = 15
    profile: str = 'previous'
    initial: float = 10000.
    risk: float = .005
    fee_bps: float = 1.
    slip_bps: float = 2.
    rows: int = 64
    value_fraction: float = .7

    def __post_init__(self):
        if self.minutes not in (15,30) or self.profile not in ('previous','developing'):
            raise ValueError('Unsupported fixed experiment')
        if not all(math.isfinite(x) and x>=0 for x in (self.initial,self.risk,self.fee_bps,self.slip_bps)):
            raise ValueError('Nonfinite configuration')
        if self.initial<=0 or not 0<self.risk<=.01 or self.rows!=64 or self.value_fraction!=.7:
            raise ValueError('Frozen parameters changed')

    @property
    def key(self):return f'QQQ-{self.minutes}m-{self.profile}'

def validate(b):
    if len(b)!=6 or b[0]%MINUTE or not all(math.isfinite(x) for x in b):
        raise ValueError('Invalid bar timestamp/values')
    _,o,h,l,c,v=b
    if not 0<l<=min(o,c)<=max(o,c)<=h or v<0:raise ValueError('Invalid OHLCV')

def profile(bars, cfg):
    if not bars or sum(b[5] for b in bars)<=0:return None
    low,high=min(b[3] for b in bars),max(b[2] for b in bars)
    if high<=low:return None
    width=(high-low)/cfg.rows;vol=[0.]*cfg.rows
    for _,o,h,l,c,v in bars:
        if h==l:
            vol[min(cfg.rows-1,max(0,int((c-low)/width)))]+=v;continue
        for j in range(cfg.rows):
            overlap=max(0,min(h,low+(j+1)*width)-max(l,low+j*width))
            vol[j]+=v*overlap/(h-l)
    poc=max(range(cfg.rows),key=lambda j:vol[j]);left=right=poc
    captured=vol[poc];needed=sum(vol)*cfg.value_fraction
    while captured+1e-10<needed and (left>0 or right<cfg.rows-1):
        if left>0 and (right==cfg.rows-1 or vol[left-1]>=vol[right+1]):
            left-=1;captured+=vol[left]
        else:right+=1;captured+=vol[right]
    return {'low':low+left*width,'high':low+(right+1)*width,'poc':low+(poc+.5)*width,
            'row_low':low,'row_width':width,'volume':vol,'volume_total':sum(vol)}

def aggregate(bars, sessions, minutes):
    """Only calendar regular-hours bars; missing constituent bars never become a candle."""
    by={int(b[0]):b for b in bars};out=[];step=minutes*MINUTE
    if len(by)!=len(bars):raise ValueError('Duplicate source bars')
    for b in bars:validate(b)
    for s in sessions:
        start,end=s['open_ms'],s['close_ms'];session=[]
        for ts in range(start,end,step):
            if ts+step>end:continue
            parts=[by.get(t) for t in range(ts,ts+step,15*MINUTE)]
            if any(b is None for b in parts):continue
            session.append([ts,parts[0][1],max(b[2] for b in parts),min(b[3] for b in parts),
                            parts[-1][4],sum(b[5] for b in parts)])
        out.append({'date':s['date'],'open_ms':start,'close_ms':end,'bars':session,
                    'complete':len(session)==(end-start)//step})
    return out

class Book:
    def __init__(self,cfg=None):
        self.cfg=cfg or Config();self.cash=self.cfg.initial;self.peak=self.cfg.initial
        self.max_dd=0.;self.position=None;self.halted=False;self.pending=None;self.setup=None
        self.session=None;self.session_bars=[];self.previous=None;self.last_bar=0
        self.trades=[];self.fills=[];self.signals=[];self.curve=[];self.total_fees=0.;self.slippage=0.
        self.latest_profile=None;self.rejects=0;self.forward_start=None

    def snapshot(self):
        return {'rule':RULE,'config':asdict(self.cfg),**{k:v for k,v in self.__dict__.items() if k!='cfg'}}

    @classmethod
    def restore(cls,state,cfg):
        if state.get('rule')!=RULE or state.get('config')!=asdict(cfg):raise ValueError('Frozen book identity mismatch')
        b=cls(cfg)
        for k in b.__dict__:
            if k!='cfg' and k in state:setattr(b,k,state[k])
        return b

    def equity(self,mark):
        p=self.position
        return self.cash+((mark-p['entry'])*p['side']*p['qty'] if p else 0.)

    def mark(self,ts,price,record=True):
        eq=self.equity(price);self.peak=max(self.peak,eq)
        self.max_dd=max(self.max_dd,(self.peak-eq)/self.peak)
        if record:self.curve.append([ts,round(eq,6)])
        if self.max_dd>=.2 or eq<=0:self.halted=True

    def fill(self,raw,side,qty,ts,observed,kind):
        price=self.execution_price(raw,side)
        fee=price*qty*self.cfg.fee_bps/10000
        self.total_fees+=fee;self.slippage+=abs(price-raw)*qty
        self.fills.append({'ts':ts,'observed_ms':observed,'side':'buy' if side>0 else 'sell',
                           'price':price,'raw_price':raw,'qty':qty,'fee':fee,'kind':kind})
        return price,fee

    def execution_price(self,raw,side):
        price=raw*(1+side*self.cfg.slip_bps/10000)
        return (math.ceil(price*100-1e-9) if side>0 else math.floor(price*100+1e-9))/100

    def exit(self,raw,ts,observed,reason):
        p=self.position
        px,fee=self.fill(raw,-p['side'],p['qty'],ts,observed,reason)
        gross=(px-p['entry'])*p['side']*p['qty'];self.cash+=gross-fee
        self.trades.append({**p,'exit':px,'exit_ts':ts,'exit_observed_ms':observed,'reason':reason,
                            'net':gross-fee-p['entry_fee'],'fees':fee+p['entry_fee']})
        self.position=None;self.mark(ts,px)

    def process(self,b,session,observed=None,trade=True):
        validate(b);ts,o,h,l,c,v=b;step=self.cfg.minutes*MINUTE;end=ts+step
        observed=observed or end
        if ts<=self.last_bar:return # checkpoint means a restart cannot replay a fill
        same=self.session is not None and self.session['date']==session['date']
        if not same:
            if self.position:raise ValueError('Overnight paper inventory requires review')
            if self.session:
                expected=(self.session['close_ms']-self.session['open_ms'])//step
                contiguous=len(self.session_bars)==expected and all(
                    x[0]==self.session['open_ms']+i*step for i,x in enumerate(self.session_bars))
                self.previous=profile(self.session_bars,self.cfg) if contiguous else None
            self.session={k:session[k] for k in ('date','open_ms','close_ms')}
            self.session_bars=[];self.setup=None;self.pending=None
        gap=bool(same and self.last_bar and ts!=self.last_bar+step)
        if gap:self.pending=None;self.setup=None;self.rejects+=1
        # A pending close-confirmed signal has no access to this bar's later price range.
        if self.pending:
            sig=self.pending;self.pending=None
            if trade and not gap and not self.halted and ts==sig['ts']+step:
                side=sig['side'];px=self.execution_price(o,side)
                valid=sig['stop']<px<sig['target'] if side>0 else sig['target']<px<sig['stop']
                cost=px*self.cfg.fee_bps/10000
                stop_fill=self.execution_price(sig['stop'],-side)
                risk=abs(px-stop_fill)+cost+stop_fill*self.cfg.fee_bps/10000
                qty=min(math.floor(self.cash*self.cfg.risk/risk) if risk>0 else 0,
                        math.floor(self.cash/(px+cost)))
                if valid and qty>0:
                    entry,fee=self.fill(o,side,qty,ts,observed,'entry');self.cash-=fee
                    self.position={**sig,'entry':entry,'entry_ts':ts,'entry_observed_ms':observed,
                                   'qty':qty,'entry_fee':fee}
                else:self.rejects+=1
        if self.position:
            p=self.position;side=p['side']
            # Stop wins ties, including a bar touching target and stop. Gap exits are adverse.
            stop_hit=l<=p['stop'] if side>0 else h>=p['stop']
            target_hit=h>=p['target'] if side>0 else l<=p['target']
            if stop_hit:self.exit(min(o,p['stop']) if side>0 else max(o,p['stop']),end,observed,'stop')
            elif target_hit:self.exit(p['target'],end,observed,'target')
            elif end>=session['close_ms']:self.exit(c,end,observed,'session_close')
        self.mark(end,c,record=trade or self.forward_start is not None)
        if self.halted and self.position:self.exit(c,end,observed,'drawdown_halt')
        # Profile uses only bars strictly before the signal candle. Excursion levels are frozen.
        levels=self.previous if self.cfg.profile=='previous' else (
            profile(self.session_bars,self.cfg) if len(self.session_bars)>=3 else None)
        self.latest_profile=levels
        prev=self.session_bars[-1] if self.session_bars else None
        if levels and not gap and not self.position and not self.halted:
            if self.setup:
                st=self.setup;st['age']+=1;st['extreme']=min(st['extreme'],l) if st['side']>0 else max(st['extreme'],h)
                inside=st['low']<=c<=st['high'];direction=c>o if st['side']>0 else c<o
                engulf=prev is not None and (prev[4]<prev[1] if st['side']>0 else prev[4]>prev[1]) and (
                    o<=prev[4] and c>=prev[1] if st['side']>0 else o>=prev[4] and c<=prev[1])
                if inside:
                    if trade and st['age']<=5 and st['declining'] and direction and engulf and v>st['last_volume'] and end<session['close_ms']:
                        side=st['side'];sig={'ts':ts,'side':side,'stop':round(st['extreme']-.01 if side>0 else st['extreme']+.01,2),
                            'target':st['high'] if side>0 else st['low'],'value_low':st['low'],'value_high':st['high']}
                        self.pending=sig;self.signals.append({**sig,'observed_ms':observed})
                    self.setup=None
                elif st['age']>=5 or (c>st['high'] if st['side']>0 else c<st['low']):self.setup=None
                else:
                    breakout_direction=c<o if st['side']>0 else c>o
                    st['declining']=st['declining'] or (breakout_direction and st['last_direction'] and v<st['last_volume'])
                    st['last_volume']=v;st['last_direction']=breakout_direction
            elif prev and levels['low']<=prev[4]<=levels['high'] and (c<levels['low'] or c>levels['high']):
                side=1 if c<levels['low'] else -1
                self.setup={'side':side,'age':0,'low':levels['low'],'high':levels['high'],
                            'extreme':l if side>0 else h,'last_volume':v,'declining':False,
                            'last_direction':c<o if side>0 else c>o}
        self.session_bars.append(list(b));self.last_bar=ts

    def summary(self):
        mark=self.session_bars[-1][4] if self.session_bars else 0
        eq=self.equity(mark);net=[t['net'] for t in self.trades];gains=sum(max(0,n) for n in net);loss=-sum(min(0,n) for n in net)
        return {'key':self.cfg.key,'config':asdict(self.cfg),'rule':RULE,'equity':eq,'initial':self.cfg.initial,
                'net':eq-self.cfg.initial,'return_pct':(eq/self.cfg.initial-1)*100,'max_dd_pct':self.max_dd*100,
                'trades':len(net),'win_rate_pct':sum(n>0 for n in net)/len(net)*100 if net else None,
                'profit_factor':gains/loss if loss else None,'fees':self.total_fees,'slippage':self.slippage,
                'position':self.position,'halted':self.halted,'last_bar':self.last_bar,'forward_start':self.forward_start,
                'rejects':self.rejects,'levels':self.latest_profile,'curve':self.curve[-1500:],
                'recent_trades':self.trades[-100:],'signals':self.signals[-50:]}

def metrics(book):
    s=book.summary();daily={};monthly={}
    for ts,eq in book.curve:
        day=datetime.fromtimestamp(ts/1000,timezone.utc).strftime('%Y-%m-%d');daily[day]=eq;monthly[day[:7]]=eq
    returns=[];last=book.cfg.initial
    for eq in daily.values():returns.append(eq/last-1);last=eq
    month_returns=[];last=book.cfg.initial
    for m,eq in monthly.items():month_returns.append({'month':m,'net_pct':(eq/last-1)*100});last=eq
    mean=sum(returns)/len(returns) if returns else 0
    std=math.sqrt(sum((r-mean)**2 for r in returns)/(len(returns)-1)) if len(returns)>1 else 0
    years=(book.curve[-1][0]-book.curve[0][0])/(365.25*86400000) if len(book.curve)>1 else 0
    s.update(monthly=month_returns,monthly_avg_net_pct=sum(x['net_pct'] for x in month_returns)/len(month_returns) if month_returns else 0,
             worst_month_pct=min((x['net_pct'] for x in month_returns),default=0),
             positive_months_pct=100*sum(x['net_pct']>0 for x in month_returns)/len(month_returns) if month_returns else 0,
             annualized_pct=((s['equity']/s['initial'])**(1/years)-1)*100 if years>0 and s['equity']>0 else None,
             sharpe=mean/std*math.sqrt(252) if std else None)
    return s

def backtest(groups,cfg,start,end):
    book=Book(cfg)
    for session in groups:
        for b in session['bars']:
            if b[0]>=end:break
            book.process(b,session,trade=b[0]>=start)
    return book
