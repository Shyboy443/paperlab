from __future__ import annotations
import json, time
from .client import Client, RateLimit
from .model import DAY, HOUR, START_EQUITY, Rule, entry, exit_price, features, settle, signal, study
from .store import Store

def cooldown(store,stop,seconds):
    end=time.time()+seconds
    store.set('status','RATE_LIMITED');store.set('backoff_until_ms',int(end*1000))
    while time.time()<end:
        store.set('heartbeat_ms',int(time.time()*1000))
        if stop.wait(min(30,max(0,end-time.time()))):return True
    store.set('backoff_until_ms',None)
    return False

def paper_tick(store,client,b,now):
    venue,symbol=b['market'].split(':');rule=Rule(**json.loads(b['rule']))
    old=store.bars(b['market'],now-90*DAY)
    cursor=old[-1][0] if old else now-90*DAY
    store.save_bars(b['market'],client.history(venue,symbol,cursor,now))
    bars=store.bars(b['market'],now-90*DAY)
    quote=client.quote(venue,symbol);mid=(quote['bid']+quote['ask'])/2
    fs=features(bars);p=json.loads(b['position']) if b['position'] else None
    fee=.001 if venue=='spot' else .00055;slip=.0003
    previous_quote=b['last_quote'];b['last_quote']=quote['ts'];b['error']=None
    market=store.db.execute('SELECT active,liquid,seen,constraints FROM markets WHERE id=?',(b['market'],)).fetchone()
    eligible=market is not None and market['active'] and market['liquid'] and quote['ts']-market['seen']<=900_000
    if not eligible:b['state']='DRAINING'
    if p:
        if venue=='perp':
            fr=client.funding(symbol,int(p.get('funding_until',p['opened_ts']))+1,quote['ts'])
            for ft,rate,mark in fr:
                p['funding']+=p['side']*p['qty']*(mark or mid)*rate;p['funding_until']=ft
            store.save_funding(b['market'],fr)
        p['held']=max(0,(now-p['opened_ts'])//HOUR)
        raw=quote['bid'] if p['side']>0 else quote['ask'];reason=None
        if (mid<=p['stop'] if p['side']>0 else mid>=p['stop']):reason='stop'
        elif (mid>=p['target'] if p['side']>0 else mid<=p['target']):reason='target'
        elif p['held']>=rule.max_hold:reason='time'
        # Recover missed touches conservatively, never treating pre-entry highs/lows as exits.
        for candle in bars:
            if candle[0]>=p['opened_ts'] and candle[0]>b['last_bar']:
                hit=exit_price(p,candle,rule.max_hold)
                if hit and hit[1]=='stop':
                    raw=min(raw,hit[0]) if p['side']>0 else max(raw,hit[0]);reason='recovered_stop_estimate';break
        mark=b['equity']+(mid-p['entry'])*p['side']*p['qty']-p['entry_fee']-p['funding']-mid*p['qty']*fee
        b['peak']=max(b['peak'],mark);b['drawdown']=max(b['drawdown'],(b['peak']-mark)/b['peak'])
        if b['drawdown']>=.1:reason='drawdown_halt'
        if reason:
            net=settle(p,raw,fee,slip);b['equity']+=net;b['net']+=net;b['trades']+=1;b['position']=None
            if b['drawdown']>=.1 or b['equity']<=START_EQUITY*.9:b['state']='HALTED'
            elif b['state']=='DRAINING':b['state']='RETIRED'
            else:
                previous=[r[0] for r in store.db.execute('SELECT net FROM paper_trades WHERE bot=?',(b['id'],))]+[net]
                gains=sum(x for x in previous if x>0);loss=-sum(x for x in previous if x<0)
                if b['trades']>=30 and now-b['created']>=7*DAY:
                    if b['net']>0 and (gains/loss if loss else 99)>=1.2:b['state']='PAPER_QUALIFIED'
                    else:b['state']='HALTED'
            if bars:b['last_bar']=max(b['last_bar'],bars[-1][0])
            store.trade(b,p,raw,net,now,reason);return
        b['position']=json.dumps(p)
    if not p and b['state']!='DRAINING' and bars and bars[-1][0]>b['last_bar']:
        # No catch-up entry: act only on a newly closed bar, with fresh live bid/ask.
        fresh=0<=quote['ts']-(bars[-1][0]+HOUR)<=180_000
        gap=previous_quote>0 and quote['ts']-previous_quote>180_000
        sig=signal(bars,fs,len(bars)-1,rule,venue) if fresh and not gap else None
        if sig and (quote['ask']-quote['bid'])/mid<=.0012:
            raw=quote['ask'] if sig['side']>0 else quote['bid']
            p=entry(sig,raw,b['equity'],fee,slip,json.loads(market['constraints']))
            if p:
                p['opened_ts']=quote['ts'];p['funding_until']=quote['ts']
                b['position']=json.dumps(p);store.event('paper_entry',f"{b['id']} at {quote['ts']}")
    if bars:b['last_bar']=max(b['last_bar'],bars[-1][0])
    if b['state']=='DRAINING' and not b['position']:b['state']='RETIRED'
    store.update_bot(b)

def worker_main(cfg,stop):
    store=Store(cfg['db']);client=Client(stop);next_universe={'spot':0,'perp':0};next_paper=0;next_prune=0
    store.event('worker_started','Continuous Binance spot/perpetual public data research; paper-only execution')
    try:
        while not stop.is_set():
            now=int(time.time()*1000);store.set('heartbeat_ms',now);store.set('status','RUNNING')
            for venue in ('spot','perp'):
                if now<next_universe[venue]:continue
                try:
                    rows=client.universe(venue);store.markets(venue,rows,int(time.time()*1000))
                    store.set(venue,{'status':'OK','last_scan_ms':int(time.time()*1000),'markets':len(rows),
                                    'liquid':sum(r['liquid'] for r in rows),'error':None})
                    next_universe[venue]=int(time.time()*1000)+300_000
                except RateLimit as e:
                    store.set(venue,{'status':'RATE_LIMITED','error':'Venue requested backoff','retry_seconds':e.seconds})
                    next_universe[venue]=int(time.time()*1000)+int(e.seconds*1000)
                    if cooldown(store,stop,e.seconds):return
                except InterruptedError:return
                except Exception as e:
                    store.set(venue,{'status':'ERROR','error':str(e)[:160] if isinstance(e,RuntimeError) else type(e).__name__})
                    next_universe[venue]=int(time.time()*1000)+300_000
                store.set('heartbeat_ms',int(time.time()*1000))
            now=int(time.time()*1000)
            if now>=next_paper:
                for bot in store.paper_bots():
                    try:paper_tick(store,client,bot,int(time.time()*1000))
                    except RateLimit as e:
                        if cooldown(store,stop,e.seconds):return
                        break
                    except InterruptedError:return
                    except Exception as e:
                        bot['error']=str(e)[:160] if isinstance(e,RuntimeError) else type(e).__name__;store.update_bot(bot)
                    store.set('heartbeat_ms',int(time.time()*1000))
                next_paper=int(time.time()*1000)+60_000
            market=store.next_market(int(time.time()*1000))
            if market:
                mid=market['id'];now=int(time.time()*1000);store.set('working_on',mid)
                try:
                    # Persist and repair full closed history; never include a forming hourly candle.
                    old=store.bars(mid,now-90*DAY)
                    cursor=old[-1][0] if old and all(old[i][0]-old[i-1][0]==HOUR for i in range(1,len(old))) else now-90*DAY
                    store.save_bars(mid,client.history(market['venue'],market['symbol'],cursor,now))
                    bars=store.bars(mid,now-90*DAY)
                    if not bars or now-(bars[-1][0]+HOUR)>HOUR+180_000:
                        report={'state':'STALE_DATA','reason':'Latest completed hour unavailable'}
                    else:
                        funding=[]
                        if market['venue']=='perp':
                            funding=client.funding(market['symbol'],bars[0][0],now);store.save_funding(mid,funding)
                            if not funding or funding[0][0]-bars[0][0]>DAY or now-funding[-1][0]>DAY or any(funding[i][0]-funding[i-1][0]>DAY for i in range(1,len(funding))):
                                report={'state':'FUNDING_HISTORY_INCOMPLETE','reason':'Funding coverage required for perpetual validation'}
                            else:report=study(bars,market['venue'],funding,json.loads(market['constraints']))
                        else:report=study(bars,market['venue'],constraints=json.loads(market['constraints']))
                    store.record_study(mid,report,now);store.admit(mid,report,now,cfg['max_bots'])
                    store.set('last_study_ms',int(time.time()*1000));store.set('last_result',{'market':mid,'state':report['state']})
                except RateLimit as e:
                    store.set('status','RATE_LIMITED')
                    if cooldown(store,stop,e.seconds):return
                except InterruptedError:return
                except Exception as e:
                    with store.db:store.db.execute('UPDATE markets SET studied=?,error=? WHERE id=?',(now-23*HOUR,type(e).__name__,mid))
                    store.event('study_error',f'{mid}: {type(e).__name__}')
                finally:store.set('working_on',None)
            if now>=next_prune:store.prune(now);next_prune=now+DAY
            store.set('heartbeat_ms',int(time.time()*1000))
            if stop.wait(2):break
    finally:
        store.set('status','STOPPED');store.close()
