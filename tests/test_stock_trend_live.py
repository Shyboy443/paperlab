from __future__ import annotations
import gzip, json
from datetime import datetime,timezone
from pathlib import Path
import pytest
from app.exchange.alpaca_client import AlpacaError
from app.stock_trend.model import Blocked,capacity,decision,trend_decision,append_day,plan,select_universe,selection
from app.stock_trend.service import StockTrendService
from app.stock_trend.data import members_at,session_dt

NOW=datetime(2026,10,7,13,30,15,tzinfo=timezone.utc)
NAMES=[f'S{i:02}' for i in range(20)]

def signal(regime='LONG'):
    return {'date':'2026-10-06','universe':NAMES,'selected_stocks':NAMES[:5],'regime':regime}

def quotes():return {s:{'bp':100,'ap':100,'t':NOW.isoformat()} for s in NAMES}

def test_user_balance_can_open_five_but_not_twenty():
    c=capacity(9.5)
    assert c['can_open'] and c['per_stock_usd']==1.88
    assert c['minimum_initial_allocation_usd']==5.06
    assert not capacity(9.5,20)['can_open']
    p=plan(signal(),9.5,[],quotes(),9.5)
    assert len(p['orders'])==5
    assert all(o['notional']=='1.88' for o in p['orders'])
    assert p['buy_total']==pytest.approx(9.40)

@pytest.mark.parametrize('amount',[0,-1,float('nan'),float('inf')])
def test_invalid_allocation(amount):
    with pytest.raises(Blocked):capacity(amount)

def test_open_entire_equal_basket_with_cost_reserve():
    p=plan(signal(),1000,[],quotes(),1000)
    assert len(p['orders'])==5
    assert all(o['notional']=='198.00' and o['side']=='buy' for o in p['orders'])
    assert p['buy_total']==990

def test_small_rebalance_is_explicitly_skipped_not_rounded_up():
    pos=[{'symbol':s,'qty':.0167} for s in NAMES[:5]]
    p=plan(signal(),9.5,pos,quotes(),1.15)
    assert p['orders']==[] and len(p['skipped'])==5
    assert all('below $1' in x['reason'] for x in p['skipped'])

def test_cash_signal_exits_fractional_dust_without_buy():
    pos=[{'symbol':NAMES[0],'qty':'0.000000013'}]
    p=plan(signal('CASH'),9.5,pos,quotes(),8)
    assert p['orders']==[{'symbol':NAMES[0],'qty':'0.000000013','side':'sell'}]

def test_missing_quote_blocks_entire_basket():
    q=quotes();q.pop(NAMES[4])
    with pytest.raises(Blocked,match='quote'):plan(signal(),1000,[],q,1000)

def test_nonfinite_broker_values_are_rejected():
    with pytest.raises(Blocked,match='Invalid broker balance'):
        StockTrendService.validate_account({'id':'a','status':'ACTIVE','cash':'nan','equity':'1000'},1000)
    with pytest.raises(Blocked,match='Invalid or short'):
        plan(signal(),1000,[{'symbol':'S00','qty':'nan'}],quotes(),1000)

def test_signal_is_aggregate_and_strictly_above():
    s={'index':[['2026-10-06',100.] for _ in range(200)],'universe':NAMES}
    assert trend_decision(s)['regime']=='CASH'
    s['index'][-1][1]=101
    assert trend_decision(s)['regime']=='LONG'

def test_month_change_returns_use_old_universe():
    old=NAMES.copy();new=[f'X{i:02}' for i in range(20)]
    history={s:{'observed_before':600,'bars':[['2026-09-30',100,1_000_000]]} for s in old+new}
    dates=['2026-09-'+f'{i:02}' for i in range(1,31)]
    # Supply sufficient prior-session observations for liquidity selection.
    dates=[f'2026-{month:02}-{day:02}' for month in [8,9] for day in range(1,31)]
    for s in new:history[s]['bars']=[[d,100,2_000_000] for d in dates]
    for s in old:history[s]['bars']=[[d,100,1_000_000] for d in dates]
    state={'dates':dates,'stocks':history,'universe':old,'index':[['2026-09-30',100]]}
    px={s:[110 if s in old else 90,1_000_000] for s in old+new}
    append_day(state,'2026-10-01',px,old+new)
    assert state['index'][-1][1]==pytest.approx(110)
    assert state['universe']==new

def test_member_snapshot_cannot_use_future_change():
    assert members_at([('2026-09-01',['A']),('2026-10-10',['B'])],'2026-10-07')==['A']

def test_seed_has_exact_frozen_index_decision():
    path=Path(__file__).parents[1]/'app/stock_trend/seed.json.gz'
    s=json.loads(gzip.open(path,'rt').read())
    d=decision(s)
    assert len(d['universe'])==20 and d['date']=='2025-12-31'
    assert d['index_close']==pytest.approx(1005.4613475387239)
    assert s['run']=='49faaea6df52d2f9'

def test_monthly_selection_does_not_use_later_winners():
    from datetime import date,timedelta
    dates=[(date(2026,6,1)+timedelta(days=i)).isoformat() for i in range(129)]
    first=next(i for i,d in enumerate(dates) if d[:7]==dates[-1][:7])
    state={'dates':dates,'universe':NAMES,'stocks':{}}
    for i,s in enumerate(NAMES):
        state['stocks'][s]={'bars':[[d,100. if j<first else 100.+i,1000] for j,d in enumerate(dates)]}
    chosen=selection(state)
    assert chosen['selected_stocks']==NAMES[-1:-6:-1]
    assert chosen['selection_date']==dates[first]
    # A spectacular later move must not change the already selected basket.
    state['stocks'][NAMES[0]]['bars'][-1][1]=100000.
    assert selection(state)==chosen

def test_skipped_overweight_shares_cannot_fund_extra_buys():
    # Four retained positions are $2.10 each. Their $0.22 trims are skipped.
    pos=[{'symbol':s,'qty':.021} for s in NAMES[:4]]
    p=plan(signal(),9.5,pos,quotes(),1.1)
    assert p['buy_total']<=1.00
    assert len(p['skipped'])==4

def test_migration_pauses_old_rule_without_losing_history(tmp_path):
    s=service(tmp_path);s.config.update(rule='EW20-SMA200-v1',active=True);s.save()
    s.persist_plan('2026-10-07',[{'symbol':'S00','side':'buy','notional':'1.88'}])
    s.db.close()
    new=service(tmp_path)
    assert not new.config['active'] and len(new.pending())==1
    assert new.summary()['rule'].startswith('TREND5')
    new.db.close()

class Broker:
    def __init__(self):self.orders={};self.posts=[];self.cash=1000.;self.timeout_after_accept=False;self.open=True
    async def fetch_account(self):return {'id':'account-one','status':'ACTIVE','currency':'USD','cash':str(self.cash),'equity':'1000'}
    async def close(self):pass
    async def request(self,method,path,body=None,data=False):
        if 'orders:by_client_order_id' in path:
            from urllib.parse import parse_qs,urlsplit
            oid=parse_qs(urlsplit(path).query)['client_order_id'][0]
            if oid not in self.orders:raise AlpacaError(404,'not found')
            return self.orders[oid]
        if path=='/v2/orders' and method=='POST':
            oid=body['client_order_id'];assert oid not in self.orders
            self.posts.append(body);self.orders[oid]={'id':oid,'client_order_id':oid,'status':'filled'}
            self.cash-=float(body.get('notional',0))
            if self.timeout_after_accept:self.timeout_after_accept=False;raise TimeoutError('accepted but response lost')
            return self.orders[oid]
        if path.startswith('/v2/orders') and method=='GET':return []
        if path=='/v2/positions':return []
        if path=='/v2/clock':return {'is_open':self.open,'timestamp':NOW.isoformat()}
        raise AssertionError((method,path,body))

class Providers:
    def __init__(self,b=None):self.b=b or Broker()
    def keys(self,*args):return ('key','secret')
    def client(self,*args):return self.b
    def redact(self,s):return str(s).replace('secret','***')

def service(tmp_path,b=None):
    svc=StockTrendService(tmp_path/'trend.db',Providers(b),env={'STOCK_TREND_MAX_ALLOCATION_USD':'1000'})
    svc.utcnow=lambda:NOW
    svc.sessions=[{'date':'2026-10-06','open':'09:30','close':'16:00'},{'date':'2026-10-07','open':'09:30','close':'16:00'}]
    return svc

@pytest.mark.asyncio
async def test_user_balance_can_arm_paper_without_placing_orders(tmp_path):
    s=service(tmp_path)
    await s.arm('testnet',9.5)
    assert s.config['active'] and not s.providers.b.posts
    assert s.config['unallocated_equity_usd']==990.5
    await s.close()

@pytest.mark.asyncio
async def test_arming_does_not_place_orders_and_live_requires_ack(tmp_path):
    s=service(tmp_path)
    with pytest.raises(Blocked,match='Enter LIVE'):await s.arm('mainnet',1000,'')
    await s.arm('testnet',1000)
    assert s.config['active'] and not s.providers.b.posts
    await s.close()

@pytest.mark.asyncio
async def test_order_timeout_restart_reconciles_without_duplicate(tmp_path):
    b=Broker();s=service(tmp_path,b);await s.arm('testnet',1000)
    s.persist_plan('2026-10-07',[{'symbol':'S00','side':'buy','notional':'49.50'}])
    b.timeout_after_accept=True
    with pytest.raises(TimeoutError):await s.submit_pending(b,NOW)
    assert len(b.posts)==1 and s.pending()[0]['status']=='uncertain'
    await s.close();r=service(tmp_path,b)
    await r.submit_pending(b,NOW)
    assert len(b.posts)==1 and not r.pending()
    await r.close()

@pytest.mark.asyncio
async def test_never_buy_with_margin_buying_power(tmp_path):
    b=Broker();s=service(tmp_path,b);await s.arm('testnet',1000);b.cash=.50
    s.persist_plan('2026-10-07',[{'symbol':'S00','side':'buy','notional':'49.50'}])
    with pytest.raises(Blocked,match='actual cash'):await s.submit_pending(b,NOW)
    assert not b.posts
    await s.close()

@pytest.mark.asyncio
async def test_pause_cancels_unsent_intents_without_touching_positions(tmp_path):
    s=service(tmp_path);await s.arm('testnet',1000)
    s.persist_plan('2026-10-07',[{'symbol':'S00','side':'buy','notional':'49.50'}])
    await s.pause()
    assert not s.pending() and not s.providers.b.posts
    await s.close()

@pytest.mark.asyncio
async def test_closed_market_never_receives_regular_buy(tmp_path):
    s=service(tmp_path);await s.arm('testnet',1000);s.providers.b.open=False
    s.persist_plan('2026-10-07',[{'symbol':'S00','side':'buy','notional':'49.50'}])
    await s.submit_pending(s.providers.b,NOW)
    assert not s.providers.b.posts
    await s.close()

@pytest.mark.asyncio
async def test_operator_rearm_flat_book_creates_distinct_order_ids(tmp_path):
    s=service(tmp_path);await s.arm('testnet',1000)
    order={'symbol':'S00','side':'buy','notional':'49.50'}
    s.persist_plan('2026-10-07',[order]);first=s.pending()[0]['id']
    await s.pause();await s.arm('testnet',1000)
    s.persist_plan('2026-10-07',[order]);second=s.pending()[0]['id']
    assert first!=second
    await s.close()

@pytest.mark.asyncio
async def test_private_routes_auth_and_csrf(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.main import BasicAuthMiddleware
    from app.core.api_stock_trend import router
    app=FastAPI();app.add_middleware(BasicAuthMiddleware,password='pass',allow_no_auth=False)
    app.include_router(router);app.state.stock_trend=service(tmp_path)
    with TestClient(app) as c:
        assert c.get('/api/stock-trend').status_code==401
        assert c.post('/api/stock-trend/start',auth=('admin','pass'),json={}).status_code==403
        r=c.post('/api/stock-trend/start',auth=('admin','pass'),headers={'X-PaperLab':'1'},json={'network':'mainnet','allocation_usd':9.5,'acknowledgement':''})
        assert r.status_code==400 and 'Enter LIVE' in r.json()['error']
    await app.state.stock_trend.close()

class FractionalBroker(Broker):
    def __init__(self):super().__init__();self.shares={};self.exit_response_new=False
    async def request(self,method,path,body=None,data=False):
        if path.startswith('/v2/stocks/quotes/latest?'):
            assert 'feed=iex' in path
            return {'quotes':quotes()}
        if path.startswith('/v2/assets/'):return {'tradable':True,'fractionable':True}
        if path=='/v2/positions':return [{'symbol':s,'qty':str(q)} for s,q in self.shares.items() if q>1e-12]
        if path=='/v2/orders' and method=='POST':
            oid=body['client_order_id'];assert oid not in self.orders
            units=float(body['notional'])/100 if body['side']=='buy' else float(body['qty'])
            self.shares[body['symbol']]=self.shares.get(body['symbol'],0)+(units if body['side']=='buy' else -units)
            self.posts.append(body)
            self.orders[oid]={'id':oid,'client_order_id':oid,'status':'filled','filled_qty':str(units),
                              'filled_avg_price':'100','filled_at':NOW.isoformat()}
            if self.timeout_after_accept:self.timeout_after_accept=False;raise TimeoutError('paper acknowledgement lost')
            if body['side']=='sell' and self.exit_response_new:return {**self.orders[oid],'status':'new','filled_qty':'0'}
            return self.orders[oid]
        return await super().request(method,path,body,data)

@pytest.mark.asyncio
async def test_paper_check_closed_then_round_trip_only_on_paper(tmp_path):
    b=FractionalBroker();s=service(tmp_path,b);s.signal=signal();calls=[]
    s.providers.client=lambda exchange,network:(calls.append(network) or b)
    await s.check_paper();b.open=False;await s.paper_check.tick()
    assert not b.posts and s.paper_check.active
    with pytest.raises(Blocked,match='Wait for'):await s.arm('testnet',9.5)
    b.open=True;await s.paper_check.tick()
    assert s.paper_check.state['phase']=='PASSED' and len(b.posts)==10
    assert not await b.request('GET','/v2/positions')
    assert sum(float(o.get('notional',0)) for o in b.posts)==pytest.approx(9.4)
    assert set(calls)=={'testnet'} and not s.config['active']
    await s.close()

@pytest.mark.asyncio
async def test_paper_check_reconciles_timeout_and_disappearing_exit_after_restart(tmp_path):
    b=FractionalBroker();s=service(tmp_path,b);s.signal=signal();await s.check_paper()
    b.timeout_after_accept=True;await s.paper_check.tick()
    assert len(b.posts)==1 and s.paper_check.state['error']
    await s.close();s=service(tmp_path,b);b.exit_response_new=True
    for _ in range(8):await s.paper_check.tick()
    assert s.paper_check.state['phase']=='PASSED' and len(b.posts)==10
    assert not await b.request('GET','/v2/positions')
    await s.close()

@pytest.mark.asyncio
async def test_live_start_requires_recorded_paper_round_trip(tmp_path):
    s=service(tmp_path)
    with pytest.raises(Blocked,match='paper execution check'):await s.arm('mainnet',9.5,'LIVE STOCKS 5')
    assert not s.providers.b.posts
    await s.close()

@pytest.mark.asyncio
async def test_readiness_exposes_cash_and_existing_holdings_without_orders(tmp_path):
    b=FractionalBroker();b.shares['AAPL']=.01;b.cash=0;s=service(tmp_path,b)
    await s.refresh_brokers()
    assert not b.posts
    assert not s.summary()['broker_readiness']['mainnet']['ready']
    assert s.summary()['broker_readiness']['mainnet']['positions']==['AAPL']
    with pytest.raises(Blocked,match='Available broker cash'):await s.arm('testnet',9.5)
    await s.close()

@pytest.mark.asyncio
async def test_requested_cap_is_clipped_to_actual_cash_not_borrowed(tmp_path):
    b=Broker();b.cash=9.49;s=service(tmp_path,b)
    await s.arm('testnet',9.5)
    assert s.config['allocation_usd']==9.49 and not b.posts
    assert s.config['unallocated_equity_usd']==pytest.approx(1000-9.49)
    await s.close()

@pytest.mark.asyncio
async def test_free_quote_feed_is_explicit_and_stale_quotes_block(tmp_path):
    b=FractionalBroker();s=service(tmp_path,b)
    assert s.summary()['quote_feed']=='iex'
    assert len(await s.quotes(b,set(NAMES[:5]),NOW))==20
    from datetime import timedelta
    with pytest.raises(Blocked,match='Fresh IEX'):await s.quotes(b,set(NAMES[:5]),NOW+timedelta(minutes=2))
    await s.close()

@pytest.mark.asyncio
async def test_paper_check_wont_enter_on_zero_bid_even_with_fresh_timestamp(tmp_path):
    class ZeroBidBroker(FractionalBroker):
        async def request(self,method,path,body=None,data=False):
            r=await super().request(method,path,body,data)
            if path.startswith('/v2/stocks/quotes/latest?'):r['quotes'][NAMES[0]]['bp']=0
            return r
    b=ZeroBidBroker();s=service(tmp_path,b);s.signal=signal()
    await s.check_paper();await s.paper_check.tick()
    assert s.paper_check.state['phase']=='WAIT_OPEN' and 'bid/ask' in s.paper_check.state['error']
    assert not b.posts
    await s.close()

@pytest.mark.asyncio
async def test_quotes_newer_than_old_clock_are_valid_at_receipt(tmp_path):
    from datetime import timedelta
    class UpdatedBroker(FractionalBroker):
        async def request(self,method,path,body=None,data=False):
            r=await super().request(method,path,body,data)
            if path.startswith('/v2/stocks/quotes/latest?'):
                for q in r['quotes'].values():q['t']=(NOW+timedelta(seconds=2)).isoformat()
            return r
    s=service(tmp_path,UpdatedBroker());s.utcnow=lambda:NOW+timedelta(seconds=3)
    assert await s.quotes(s.providers.b,set(NAMES[:5]),NOW)
    s.utcnow=lambda:NOW
    with pytest.raises(Blocked,match='Fresh IEX'):await s.quotes(s.providers.b,set(NAMES[:5]),NOW)
    await s.close()

@pytest.mark.asyncio
async def test_opening_quote_delay_waits_without_orders_or_arming_late(tmp_path):
    from datetime import timedelta
    class DelayedBroker(FractionalBroker):
        async def request(self,method,path,body=None,data=False):
            r=await super().request(method,path,body,data)
            if path.startswith('/v2/stocks/quotes/latest?'):
                for q in r['quotes'].values():q['t']=(NOW-timedelta(minutes=2)).isoformat()
            return r
    s=service(tmp_path,DelayedBroker());s.signal=signal();await s.arm('testnet',9.5)
    s.next_data=s.next_balance=float('inf');await s.tick(NOW)
    assert s.status=='WAITING_FOR_QUOTES' and s.config['active'] and not s.providers.b.posts
    await s.close()
