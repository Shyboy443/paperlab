"""Forward-only paper accounting, two isolated comparative portfolios.

Entry uses an observed bid/ask after signal/review, never a past candle's open.
0.04% taker fee and 0.02% adverse slippage each side, plus actual funding.
"""
from __future__ import annotations
import math

FEE=.0004
SLIP=.0002
RISK=.005
MAX_OPEN=3


def empty():
    return {'start_equity':1000.,'cash':1000.,'equity':1000.,'realized_net':0.,'fees':0.,'funding':0.,'closed_trades':0,'open':[],'trades':[],'equity_curve':[],'peak':1000.,'halted':False}


def mark(book,quotes,now):
    unrealized=0.
    for p in book['open']:
        q=quotes.get(p['symbol'])
        price=(q['bid'] if p['side']=='long' else q['ask']) if q else p.get('mark',p['entry'])
        p['mark']=price
        p['unrealized']=(price-p['entry'])*p['qty']*(1 if p['side']=='long' else -1)
        unrealized+=p['unrealized']
    book['equity']=book['cash']+unrealized
    book['peak']=max(book['peak'],book['equity'])
    book['drawdown']=1-book['equity']/book['peak'] if book['peak'] else 0
    # Suspend new exposure at 20% equity drawdown; existing hard stops remain active.
    if book['drawdown']>=.2:book['halted']=True
    if not book['equity_curve'] or now-book['equity_curve'][-1]['time']>=900_000:
        book['equity_curve'].append({'time':now,'value':book['equity']})
        book['equity_curve']=book['equity_curve'][-10000:]


def open_position(book,signal,quote,constraints,now,max_age=900_000):
    if book['halted']:return 'Portfolio drawdown halt'
    if len(book['open'])>=MAX_OPEN:return 'Three-position limit'
    if any(p['symbol']==signal['symbol'] for p in book['open']):return 'An open position already uses this symbol'
    # Automatic entries must be within 15 minutes of the confirmation; operator-approved ones within the approval window.
    if now-signal['confirmed_at']>max_age or now<signal['confirmed_at']:return 'Confirmation expired before observed entry'
    if now-quote['ts']>15_000 or quote['ts']>now+1000:return 'Stale quote'
    sign=1 if signal['side']=='long' else -1
    entry=(quote['ask'] if sign==1 else quote['bid'])*(1+sign*SLIP)
    distance=(entry-signal['stop'])*sign
    reference=abs(signal['entry_reference']-signal['stop'])
    if distance<=0 or reference<=0 or abs(entry-signal['entry_reference'])>.25*reference:return 'Price moved beyond entry tolerance'
    target=entry+sign*2*distance
    equity=max(0,book['equity']);available=max(0,equity-sum(p['qty']*p.get('mark',p['entry']) for p in book['open']))
    # Risk reserves exit slippage and both fees at the stop; cash is unlevered equity.
    stop_fill=signal['stop']*(1-sign*SLIP)
    loss=abs(entry-stop_fill)+FEE*(entry+stop_fill)
    qty=min(equity*RISK/loss,available/(entry*(1+FEE)),constraints.get('max_qty') or float('inf'))
    step=constraints.get('step') or 0
    if step:qty=math.floor(qty/step)*step
    if qty<=0 or qty<(constraints.get('min_qty') or 0) or qty*entry<(constraints.get('min_notional') or 5):return 'Below exchange minimum size'
    fee=qty*entry*FEE
    p={'id':signal['id'],'symbol':signal['symbol'],'side':signal['side'],'divergence_tf':signal['divergence_tf'],'trigger_tf':signal['trigger_tf'],'entry':entry,'entry_ts':now,'stop':signal['stop'],'target':target,'qty':qty,'fees':fee,'funding':0.,'last_funding':now,'last_bar':now//60_000*60_000-60_000,'risk_usd':qty*loss,'mark':entry,'signal':signal,'execution':'PAPER_ONLY'}
    book['cash']-=fee;book['fees']+=fee;book['open'].append(p)
    return None


def update_position(book,p,bars,funding,quote,now):
    """Use closed minute bars; quote closes cover the incomplete current minute.

    If there is a missing minute, stop at the first gap and defer accounting until
    it can be repaired. This prevents inventing price paths across outages.
    """
    sign=1 if p['side']=='long' else -1
    exit_price=None;exit_ts=None;reason=None
    if now-quote['ts']>15_000 or quote['ts']>now+1000:raise ValueError('Stale exit quote')
    if any(price<=0 for ts,rate,price in funding if ts>p['last_funding']):raise ValueError('Funding mark price unavailable')
    eligible=[b for b in bars if b['t']>=p['last_bar']+60_000 and b['t']>=p['entry_ts']//60_000*60_000 and b['t']+60_000<=now]
    expected=p['last_bar']+60_000
    contiguous=True
    for b in eligible:
        if b['t']!=expected:contiguous=False;break
        expected=b['t']+60_000;p['last_bar']=b['t']
        stop_hit=b['l']<=p['stop'] if sign==1 else b['h']>=p['stop']
        target_hit=b['h']>=p['target'] if sign==1 else b['l']<=p['target']
        partial=b['t']<p['entry_ts']
        if stop_hit:
            exit_price=p['stop'] if partial else (min(b['o'],p['stop']) if sign==1 else max(b['o'],p['stop']))
            reason='entry_minute_ambiguous_stop' if partial else 'stop'
        elif target_hit and not partial:exit_price=p['target'];reason='target'
        if exit_price is not None:exit_ts=b['t']+60_000;break
    if exit_price is None and expected+60_000<=now:contiguous=False
    if exit_price is None and contiguous and now-(p['last_bar']+60_000)<120_000:
        px=quote['bid'] if sign==1 else quote['ask']
        if (px-p['stop'])*sign<=0:exit_price=px;reason='stop';exit_ts=quote['ts']
        elif (px-p['target'])*sign>=0:exit_price=p['target'];reason='target';exit_ts=quote['ts']
    # Bar exits are timestamped at close; intraminute funding is conservatively
    # applied before exit. Entry-time funding is excluded. Entry-minute targets
    # cannot be assumed; ambiguous entry-minute stops count as losses.
    until=exit_ts if exit_ts is not None else (now if contiguous else expected)
    for ts,rate,price in funding:
        if p['last_funding']<ts<=until:
            if price<=0:raise ValueError('Funding mark price unavailable')
            cash=-sign*p['qty']*price*rate
            if exit_ts is not None and reason not in (None,) and exit_ts!=quote['ts'] and ts>exit_ts-60_000 and cash>0:
                p['withheld_ambiguous_funding_credits']=p.get('withheld_ambiguous_funding_credits',0)+1
                p['last_funding']=ts
                continue
            p['funding']+=cash;book['cash']+=cash;book['funding']+=cash;p['last_funding']=ts
    if exit_price is None:
        p['data_gap']=not contiguous
        return None
    filled=exit_price*(1-sign*SLIP);fee=p['qty']*filled*FEE
    gross=(filled-p['entry'])*sign*p['qty'];net=gross-p['fees']-fee+p['funding']
    book['cash']+=gross-fee;book['fees']+=fee
    trade={**p,'exit':filled,'exit_ts':exit_ts,'reason':reason,'fees':p['fees']+fee,'gross_after_slippage':gross,'net':net,'r':net/p['risk_usd']}
    trade.pop('signal',None)
    book['open'].remove(p);book['trades'].append(trade);book['closed_trades']+=1;book['realized_net']+=net
    return trade
