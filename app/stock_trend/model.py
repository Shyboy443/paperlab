"""Five-stock small-account variant; preserve the twenty-stock trend index."""
from __future__ import annotations
import math
from decimal import Decimal, ROUND_DOWN

RULE = 'TREND5-SMA200-MOM63-LOT1-v1'
COUNT = 20  # liquid research universe and regime index remain twenty stocks
TRADE_COUNT = 5
MOMENTUM_DAYS = 63
RESERVE = .01  # execution cash reserve, not an investment signal
MIN_ORDER = 1.

class Blocked(ValueError):
    pass

def positive(value):
    x = float(value)
    if not math.isfinite(x) or x <= 0:
        raise Blocked('A positive finite USD amount is required')
    return x

def capacity(amount, count=TRADE_COUNT):
    amount = positive(amount)
    if count not in (TRADE_COUNT, COUNT):raise Blocked('Unsupported portfolio size')
    per_stock = math.floor(amount * (1-RESERVE) / count * 100) / 100
    return {'allocation_usd': amount, 'per_stock_usd': per_stock,
            'stock_count':count,'minimum_initial_allocation_usd': math.ceil(count*MIN_ORDER/(1-RESERVE)*100)/100,
            'can_open':per_stock >= MIN_ORDER,
            'reason': None if per_stock >= MIN_ORDER else
                f'${amount:.2f} gives ${per_stock:.2f} per stock after a 1% execution reserve; '
                f'{count} stocks each need a $1 initial order.'}

def select_universe(history, dates, members):
    """At the first session of a month: prior 504 observations, prior ADV60 >= $10M.

    Sorted symbol order resolves ties like the original panel. Only completed
    observations preceding the first session are passed in.
    """
    ranked = []
    last60 = set(dates[-60:])
    for s in sorted(members):
        h = history.get(s, {})
        bars = h.get('bars', [])
        if h.get('observed_before', 0) + len(bars) < 504:
            continue
        values = [float(c)*float(v) for d,c,v in bars if d in last60]
        if len(values) < 40:
            continue
        adv = sum(values)/len(values)
        if adv >= 10_000_000:
            ranked.append((s,adv))
    ranked.sort(key=lambda x: -x[1])
    if len(ranked) < COUNT:
        raise Blocked('Fewer than 20 eligible S&P 500 stocks with sufficient observed history')
    return [s for s,_ in ranked[:COUNT]]

def append_day(state, day, prices, members):
    previous = state['universe']
    returns = []
    for s in previous:
        old = state['stocks'].get(s, {}).get('bars', [])
        if not old or s not in prices or prices[s][0] <= 0:
            raise Blocked('Incomplete close data for the prior 20-stock universe: '+s)
        returns.append(float(prices[s][0])/old[-1][1]-1)
    new_universe = previous
    if day[:7] != state['dates'][-1][:7]:
        new_universe = select_universe(state['stocks'], state['dates'], members)
    if any(s not in prices for s in new_universe):
        raise Blocked('Incomplete current-universe close data')
    value = state['index'][-1][1] * (1+sum(returns)/COUNT)
    if not math.isfinite(value) or value <= 0:
        raise Blocked('Invalid equal-weight index')
    for s,(c,v) in prices.items():
        h = state['stocks'].setdefault(s, {'observed_before':0,'bars':[]})
        h['bars'].append([day,float(c),float(v)])
    state['dates'].append(day)
    state['index'].append([day,value])
    state['universe'] = new_universe

def rank_winners(state, asof):
    days=[d for d in state['dates'] if d<=asof]
    if len(days)<=MOMENTUM_DAYS:raise Blocked('63 completed session returns are required for ranking')
    base=days[-MOMENTUM_DAYS-1]
    ranks=[]
    for s in state['universe']:
        observations={d:c for d,c,_ in state['stocks'].get(s,{}).get('bars',[]) if base<=d<=asof}
        if base not in observations or asof not in observations:
            raise Blocked('Incomplete momentum ranking history: '+s)
        score=float(observations[asof])/float(observations[base])-1
        if not math.isfinite(score):raise Blocked('Invalid momentum ranking: '+s)
        ranks.append({'symbol':s,'return_63d':score})
    ranks.sort(key=lambda r:(-r['return_63d'],r['symbol']))
    return ranks

def selection(state):
    """Monthly winners use that month's first completed session, never its end.

    The monthly liquid-20 universe remains fixed, so this works for both the
    packaged seed and a refreshed forward history without changing that index.
    """
    month=state['dates'][-1][:7]
    asof=next(d for d in state['dates'] if d[:7]==month)
    ranks=rank_winners(state,asof)
    return {'selected_stocks':[r['symbol'] for r in ranks[:TRADE_COUNT]],
            'selection_date':asof,'selection_lookback':MOMENTUM_DAYS,
            'selection_ranks':ranks[:TRADE_COUNT]}

def trend_decision(state):
    values = [float(v) for _,v in state['index'][-200:]]
    if len(values) != 200 or any(not math.isfinite(v) or v<=0 for v in values):
        raise Blocked('200 complete equal-weight index closes are required')
    average = sum(values)/200
    return {'rule':RULE,'date':state['index'][-1][0], 'index_close':values[-1],
            'sma200':average,'regime':'LONG' if values[-1]>average else 'CASH',
            'universe':list(state['universe'])}

def decision(state):
    return {**trend_decision(state),**selection(state)}

def plan(signal, amount, positions, quotes, available_cash):
    """Preflight the ENTIRE basket. Never round undersized buys up or use margin.

    Whole-position exits use fractional qty, avoiding dollar-sale rounding dust.
    Small-account trades below $1 are explicitly skipped for existing holdings.
    The monthly basket can drift; this is a different rule from daily EW20.
    """
    cap = capacity(amount)
    if signal['regime']=='LONG' and not cap['can_open']:
        raise Blocked(cap['reason'])
    if len(signal['universe']) != COUNT or len(set(signal['universe'])) != COUNT:
        raise Blocked('Exactly 20 unique stocks are required')
    chosen=signal.get('selected_stocks',[])
    if len(chosen)!=TRADE_COUNT or len(set(chosen))!=TRADE_COUNT or not set(chosen)<=set(signal['universe']):
        raise Blocked('Exactly five distinct selected stocks from the liquid universe are required')
    if any(not math.isfinite(float(p.get('qty',0))) or float(p.get('qty',0))<0 for p in positions):
        raise Blocked('Invalid or short broker positions are incompatible with this strategy')
    held = {p['symbol']:p for p in positions if float(p.get('qty',0))>0}
    nav = positive(amount)
    each = math.floor(nav*(1-RESERVE)/TRADE_COUNT*100)/100 if signal['regime']=='LONG' else 0.
    target = {s:each for s in chosen} if each else {}
    orders=[];skipped=[]
    for s in sorted(set(target)|set(held)):
        q=quotes.get(s,{})
        bid,ask=float(q.get('bp',0)),float(q.get('ap',0))
        if not (0<bid<=ask and math.isfinite(ask)):
            raise Blocked('No executable quote for '+s)
        p=held.get(s,{})
        qty=float(p.get('qty',0)); value=qty*(bid+ask)/2
        wanted=target.get(s,0.)
        delta=round(wanted-value,2)
        if wanted==0 and qty>0:
            orders.append({'symbol':s,'side':'sell','qty':str(p['qty'])})
        elif delta<0:
            if abs(delta)<MIN_ORDER:
                skipped.append({'symbol':s,'reason':'Rebalance below $1 minimum','delta_usd':delta});continue
            sell_qty=Decimal(str(abs(delta)/bid)).quantize(Decimal('.000000001'),rounding=ROUND_DOWN)
            if sell_qty<=0 or float(sell_qty)>qty:raise Blocked('Invalid fractional sale: '+s)
            orders.append({'symbol':s,'side':'sell','qty':str(sell_qty)})
        elif delta>0:
            if delta<MIN_ORDER:
                skipped.append({'symbol':s,'reason':'Rebalance below $1 minimum','delta_usd':delta});continue
            orders.append({'symbol':s,'side':'buy','notional':f'{delta:.2f}'})
    orders.sort(key=lambda x: (x['side']=='buy',x['symbol']))
    # Skipped fractional adjustments retain their actual value. They cannot be
    # ignored when calculating how much capital remains for other buys.
    remaining_value=sum(float(p['qty'])*(float(quotes[s]['bp'])+float(quotes[s]['ap']))/2 for s,p in held.items())
    for o in orders:
        if o['side']=='sell':
            q=quotes[o['symbol']];remaining_value-=float(o['qty'])*(float(q['bp'])+float(q['ap']))/2
    buy_budget=max(0.,nav*(1-RESERVE)-remaining_value)
    bounded=[]
    for o in orders:
        if o['side']=='buy':
            value=min(float(o['notional']),math.floor((buy_budget+1e-10)*100)/100)
            if value<MIN_ORDER:
                skipped.append({'symbol':o['symbol'],'reason':'Remaining strategy capital below $1 minimum','delta_usd':value});continue
            o={**o,'notional':f'{value:.2f}'};buy_budget-=value
        bounded.append(o)
    orders=bounded
    buy_total=sum(float(o.get('notional',0)) for o in orders)
    # Selling proceeds must actually settle/fill before any buy is sent.
    return {'orders':orders,'buy_total':buy_total,'cash_now':max(0,float(available_cash)),
            'target_each_usd':each,'reserve_usd':nav*RESERVE,'skipped':skipped}
