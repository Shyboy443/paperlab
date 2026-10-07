from __future__ import annotations
from dataclasses import asdict, dataclass
from collections import deque
import math

HOUR = 3_600_000
DAY = 24 * HOUR
VERSION = 'AR1'
WARMUP = 240
START_EQUITY = 1000.0

@dataclass(frozen=True)
class Rule:
    family: str
    lookback: int
    stop_atr: float
    max_hold: int = 24

    @property
    def key(self):
        return f'{VERSION}-{self.family}-{self.lookback}-{self.stop_atr:g}'

    def data(self): return asdict(self)

RULES = [Rule(f, n, s) for f in ['breakout', 'pullback', 'reversion']
         for n in [20, 40] for s in [1.5, 2.5]]

def closed_bars(rows, now):
    result = {}
    for r in rows:
        ts = int(r[0]); o,h,l,c,v = map(float, r[1:6])
        if ts % HOUR or ts + HOUR > now or not all(math.isfinite(x) for x in [o,h,l,c,v]): continue
        if min(o,h,l,c) <= 0 or h < max(o,c) or l > min(o,c) or h < l or v < 0: continue
        result[ts] = [ts,o,h,l,c,v]
    return [result[t] for t in sorted(result)]

def features(bars):
    """A 4h value becomes usable only after all four contiguous UTC hours close."""
    out=[]; fast=slow=atr=None; groups={}; last4=None; hfast=hslow=None
    for i,b in enumerate(bars):
        t,o,h,l,c,v=b
        prev=bars[i-1][4] if i else c
        tr=max(h-l,abs(h-prev),abs(l-prev))
        atr=tr if atr is None else (atr*13+tr)/14
        fast=c if fast is None else fast+(c-fast)*2/21
        slow=c if slow is None else slow+(c-slow)*2/61
        g=t//(4*HOUR)*(4*HOUR); group=groups.setdefault(g,[]); group.append(b)
        if len(group)==4 and [x[0] for x in group]==[g+j*HOUR for j in range(4)]:
            hfast=c if hfast is None else hfast+(c-hfast)*2/13
            hslow=c if hslow is None else hslow+(c-hslow)*2/41
            last4=g+4*HOUR
            groups={g:group}
        # Context must be recent, not carried across a feed gap.
        valid=last4 is not None and t+HOUR-last4<=4*HOUR
        out.append({'atr':atr,'fast':fast,'slow':slow,'hf':hfast if valid else None,
                    'hs':hslow if valid else None,'context_close':last4})
    return out

def signal(bars, fs, i, rule, venue):
    if i < max(WARMUP,rule.lookback): return None
    b=bars[i]; f=fs[i]; window=bars[i-rule.lookback:i]
    if any(window[j+1][0]-window[j][0]!=HOUR for j in range(len(window)-1)) or b[0]-window[-1][0]!=HOUR: return None
    if f['hf'] is None or f['atr']<=0: return None
    c=b[4]; trend=1 if f['hf']>f['hs'] else -1
    if rule.family=='breakout':
        side=1 if c>max(x[2] for x in window) else -1 if c<min(x[3] for x in window) else 0
        if side!=trend: return None
    elif rule.family=='pullback':
        previous=bars[i-1][4]; pf=fs[i-1]
        side=1 if trend==1 and previous<=pf['fast'] and c>f['fast'] else -1 if trend==-1 and previous>=pf['fast'] and c<f['fast'] else 0
    else:
        mean=sum(x[4] for x in window)/len(window)
        sd=(sum((x[4]-mean)**2 for x in window)/len(window))**.5
        if sd==0 or abs(f['hf']/f['hs']-1)>.01: return None
        side=1 if c<mean-2*sd else -1 if c>mean+2*sd else 0
    if not side or (venue=='spot' and side<0): return None
    distance=f['atr']*rule.stop_atr
    if not .002 <= distance/c <= .08: return None
    return {'side':side,'distance':distance,'signal_ts':b[0]+HOUR,'context_close':f['context_close']}

def entry(sig, price, equity, fee, slip, constraints=None):
    fill=price*(1+sig['side']*slip)
    constraints=constraints or {};step=constraints.get('step',0);tick=constraints.get('tick',0)
    stop=fill-sig['side']*sig['distance'];target=fill+sig['side']*sig['distance']*2
    if tick:
        round_level=math.floor if sig['side']>0 else math.ceil
        stop=round_level(stop/tick)*tick;target=round_level(target/tick)*tick
    distance=abs(fill-stop)
    if distance<=0 or stop<=0 or sig['side']*(target-fill)<=0:return None
    qty=min(equity*.005/distance,equity*.95/fill)
    if step:qty=math.floor(qty/step+1e-10)*step
    if constraints.get('max_qty',0):qty=min(qty,constraints['max_qty'])
    if qty<=0 or qty<constraints.get('min_qty',0) or qty*fill<constraints.get('min_notional',0):return None
    return {**sig,'distance':distance,'entry':fill,'qty':qty,'stop':stop,
            'target':target,'entry_fee':qty*fill*fee,'funding':0.0,'held':0}

def exit_price(p,b,max_hold):
    side=p['side']; o,h,l,c=b[1:5]
    stop=h>=p['stop'] if side<0 else l<=p['stop']
    target=l<=p['target'] if side<0 else h>=p['target']
    # Stop wins if the candle touches both: no invented intrabar path.
    if stop: return (max(o,p['stop']) if side<0 else min(o,p['stop']),'stop')
    if target: return (p['target'],'target')
    if p['held']>=max_hold: return c,'time'
    return None

def settle(p,raw,fee,slip):
    fill=raw*(1-p['side']*slip)
    return (fill-p['entry'])*p['side']*p['qty']-p['entry_fee']-fill*p['qty']*fee-p['funding']

def replay(bars, fs, rule, venue, start, end, funding=(), multiplier=1, constraints=None):
    fee=(.001 if venue=='spot' else .00055)*multiplier; slip=.001*multiplier
    equity=START_EQUITY; peak=START_EQUITY; dd=0.; p=None; trades=[]; pending=None; rejected=0
    fr=sorted(funding,key=lambda x:x[0]); fi=0
    for i in range(start,end):
        b=bars[i]; t=b[0]
        if pending and pending['signal_ts']==t:
            p=entry(pending,b[1],equity,fee,slip,constraints)
            if p:p['opened_ts']=t
            else:rejected+=1
            pending=None
        elif pending: pending=None
        while fi<len(fr) and fr[fi][0]<t: fi+=1
        if p:
            while fi<len(fr) and fr[fi][0]<t+HOUR:
                ft,rate,mark=fr[fi]
                # Charge even on an ambiguous exit candle: conservative funding timing.
                p['funding']+=p['side']*p['qty']*(mark or b[4])*rate;fi+=1
            p['held']+=1; hit=exit_price(p,b,rule.max_hold)
            if hit or i==end-1:
                raw,reason=hit or (b[4],'window_end'); net=settle(p,raw,fee,slip)
                equity+=net;trades.append(net);p=None
        marked=equity if not p else equity+(b[4]-p['entry'])*p['side']*p['qty']-p['entry_fee']-p['funding']-p['qty']*b[4]*fee
        peak=max(peak,marked);dd=max(dd,(peak-marked)/peak)
        if equity<=START_EQUITY*.75: break
        if p is None and i<end-1: pending=signal(bars,fs,i,rule,venue)
    gains=sum(x for x in trades if x>0);loss=-sum(x for x in trades if x<0)
    return {'trades':len(trades),'execution_rejects':rejected,'net':round(equity-START_EQUITY,6),'profit_factor':round(gains/loss if loss else (99 if gains else 0),4),
            'drawdown':round(dd,6),'expectancy':round(sum(trades)/len(trades),6) if trades else 0,
            'winner_concentration':max([x for x in trades if x>0],default=0)/gains if gains else 1}

def study(bars,venue,funding=(),constraints=None):
    if len(bars)<1800 or bars[-1][0]+HOUR-bars[0][0]<75*DAY:
        return {'state':'INSUFFICIENT_HISTORY','reason':'At least 75 days and 1800 completed hours required'}
    if any(bars[i][0]-bars[i-1][0]!=HOUR for i in range(1,len(bars))):
        return {'state':'DATA_GAP','reason':'Missing hourly intervals; history repair required'}
    fs=features(bars);a=int(len(bars)*.6);b=int(len(bars)*.8)
    # Rule selection NEVER uses validation or the untouched final holdout.
    trained=[(rule,replay(bars,fs,rule,venue,WARMUP,a,funding,constraints=constraints)) for rule in RULES]
    rule,train=max(trained,key=lambda x:(x[1]['net'] if x[1]['trades']>=20 else -1e9,-x[1]['drawdown'],x[0].key))
    validation=replay(bars,fs,rule,venue,a,b,funding,constraints=constraints)
    holdout=replay(bars,fs,rule,venue,b,len(bars),funding,constraints=constraints)
    stress=replay(bars,fs,rule,venue,b,len(bars),funding,2,constraints)
    reasons=[]
    for name,m,minimum in [('train',train,20),('validation',validation,10),('holdout',holdout,10)]:
        if m['trades']<minimum: reasons.append(f'{name}: insufficient trades ({m["trades"]}/{minimum})')
        if m['net']<=0: reasons.append(f'{name}: nonpositive net after costs')
        if m['profit_factor']<1.2: reasons.append(f'{name}: profit factor below 1.2')
        if m['drawdown']>.1: reasons.append(f'{name}: drawdown above 10%')
    if holdout['winner_concentration']>.4: reasons.append('holdout: one winner accounts for over 40% of gains')
    if stress['net']<=0: reasons.append('holdout: loses with doubled fees/slippage')
    return {'state':'HISTORICALLY_QUALIFIED' if not reasons else 'REJECTED','reasons':reasons,
            'rule':rule.data(),'rule_key':rule.key,'train':train,'validation':validation,'holdout':holdout,'stress':stress,
            'split':{'train':[bars[WARMUP][0],bars[a-1][0]+HOUR],'validation':[bars[a][0],bars[b-1][0]+HOUR],
                     'holdout':[bars[b][0],bars[-1][0]+HOUR]},'variants_tried':len(RULES),
            'starting_paper_equity':START_EQUITY,'constraints':constraints or {},
            'note':'Repeated searches remain subject to selection bias; independent forward paper evidence is required.'}
