"""Operator-armed Alpaca execution, persistent intents, no automatic live enable."""
from __future__ import annotations
import asyncio, hashlib, json, math, os, sqlite3, time, urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .data import signal_data, session_dt, stamp
from .model import Blocked, RULE, capacity, plan, positive

FINAL={'filled','canceled','expired','rejected','done_for_day'}

class StockTrendService:
    def __init__(self, path, providers, env=None, secure=True):
        self.providers=providers;self.env=os.environ if env is None else env;self.secure=secure
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(self.path));self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY,v TEXT);
          CREATE TABLE IF NOT EXISTS intents(id TEXT PRIMARY KEY,session TEXT,body TEXT,status TEXT,response TEXT);
          CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY,ts TEXT,event TEXT,detail TEXT);
        ''')
        r=self.db.execute("SELECT v FROM state WHERE k='config'").fetchone()
        self.config=json.loads(r[0]) if r else {'active':False,'network':'testnet','allocation_usd':9.5,'account_id':None,'rule':RULE}
        if self.config.get('rule')!=RULE:
            self.config['active']=False;self.config['rule']=RULE
            self.note('rule_updated','Five-stock variant installed paused; prior intents and holdings retained')
        self.save();self.signal=None;self.error=None;self.status='PAUSED';self.heartbeat=None
        self.account=None;self.positions=[];self.task=None;self.lock=asyncio.Lock();self.closing=False
        self.next_data=0;self.sessions=[];self.tick_error=None
        self.last_plan=None
        from .paper_check import PaperCheck
        self.paper_check=PaperCheck(self)
        self.research_verdict=json.loads((Path(__file__).parent/'research_verdict.json').read_text())
        self.broker_readiness={};self.next_balance=0
        self.quote_feed=self.env.get('STOCK_TREND_QUOTE_FEED','iex')
        if self.quote_feed not in ('iex','sip'):raise ValueError('Stock quote feed must be iex or sip')
        self.utcnow=lambda:datetime.now(timezone.utc)

    def save(self):
        with self.db:self.db.execute("INSERT OR REPLACE INTO state VALUES('config',?)",(json.dumps(self.config),))

    def note(self,event,detail):
        with self.db:self.db.execute('INSERT INTO journal(ts,event,detail) VALUES(?,?,?)',
            (datetime.now(timezone.utc).isoformat(),event,self.providers.redact(detail)))

    def max_allocation(self):
        try:return positive(self.env.get('STOCK_TREND_MAX_ALLOCATION_USD','9.50'))
        except (Blocked,ValueError):return 9.5

    def uses_provider(self,network):
        pending=self.db.execute("SELECT count(*) FROM intents WHERE status NOT IN ('filled','canceled','expired','rejected','done_for_day')").fetchone()[0]
        return (network=='testnet' and self.paper_check.active) or (self.config['network']==network and bool(self.config['active'] or self.positions or pending))

    def summary(self):
        c={k:v for k,v in self.config.items() if k!='account_id'}
        pending=[dict(r) for r in self.db.execute('SELECT id,session,status FROM intents ORDER BY rowid DESC LIMIT 50')]
        return {'rule':RULE,'status':self.status,'config':c,'capacity':capacity(c['allocation_usd']),
            'maximum_allocation_usd':self.max_allocation(),'signal':self.signal,'heartbeat':self.heartbeat,
            'error':self.error,'paper_keys_configured':bool(self.providers.keys('alpaca','testnet')),
            'live_keys_configured':bool(self.providers.keys('alpaca','mainnet')),
            'positions':self.positions,'account':self.account,'orders':pending,
            'last_plan':self.last_plan,
            'paper_check':self.paper_check.summary(),
            'research_verdict':self.research_verdict,
            'broker_readiness':self.broker_readiness,
            'quote_feed':self.quote_feed,
            'journal':[dict(r) for r in self.db.execute('SELECT ts,event,detail FROM journal ORDER BY id DESC LIMIT 30')],
            'cash_interest':'Actual broker credits only; no simulated T-bill accrual.',
            'execution':'Fractional day market orders within two minutes after the regular open. No shorts or margin.',
            'notice':'Five stocks ranked by prior 63-session return, selected monthly. This is a new strategy; it does not inherit the original 20-stock backtest returns.'}

    def start(self):
        self.task=asyncio.create_task(self.loop(),name='stock-trend-monitor')

    async def close(self):
        self.closing=True
        if self.task:
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass
        self.db.close()

    async def loop(self):
        while not self.closing:
            try:await self.tick(datetime.now(timezone.utc))
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.error=self.providers.redact(f'{type(exc).__name__}: {exc}')
                self.status='BLOCKED';self.tick_error=time.time()
                if self.config['active']:
                    # Any unresolved execution incident pauses rather than retrying a new basket.
                    self.config['active']=False;self.save();self.note('paused_on_error',self.error)
            self.heartbeat=datetime.now(timezone.utc).isoformat()
            await asyncio.sleep(20)

    async def arm(self, network, amount, acknowledgement=''):
        if network not in ('testnet','mainnet'):raise Blocked('Choose Alpaca paper or live')
        amount=positive(amount)
        if amount>self.max_allocation():raise Blocked('Allocation exceeds the configured strategy cap')
        cap=capacity(amount)
        if not cap['can_open']:raise Blocked(cap['reason'])
        if network=='mainnet':
            if not self.secure:raise Blocked('Live stocks require an authenticated private dashboard')
            if acknowledgement!='LIVE STOCKS 5':raise Blocked('Enter LIVE STOCKS 5 to arm real-money stocks')
            if self.paper_check.state['phase']!='PASSED':raise Blocked('Complete the five-stock paper execution check before enabling live orders')
        async with self.lock:
            if self.paper_check.active:raise Blocked('Wait for the paper execution check to finish and confirm the account flat')
            if self.config['active']:raise Blocked('Pause the strategy before changing its configuration')
            if self.uses_provider(self.config['network']):raise Blocked('Existing positions or unfinished orders must be resolved first')
            client=self.providers.client('alpaca',network)
            try:
                a=await client.fetch_account()
                self.validate_account(a,0)
                # Authorization is a ceiling, not permission to borrow the
                # missing cents or refill from unrelated account holdings.
                funded=min(amount,float(a.get('cash',0)),float(a.get('equity',0)))
                funded=math.floor((funded+1e-10)*100)/100
                if funded<cap['minimum_initial_allocation_usd']:
                    raise Blocked('Available broker cash is below the five-stock minimum allocation')
                amount=funded
                positions=await client.request('GET','/v2/positions')
                orders=await client.request('GET','/v2/orders?status=open&limit=500')
                if positions or orders:raise Blocked('Use a dedicated flat Alpaca account with no open orders')
                self.config={'active':True,'network':network,'allocation_usd':amount,'account_id':a['id'],
                             'generation':str(time.time_ns()),'rule':RULE,
                             'unallocated_equity_usd':float(a['equity'])-amount}
                # Starting a newly flat book is an operator action, not an
                # automatic retry of a failed or canceled historical basket.
                with self.db:self.db.execute("DELETE FROM state WHERE k='last_session'")
                self.save();self.status='WAITING_FOR_DATA';self.error=None;self.next_data=0
                self.note('armed',f'{network} allocation ${amount:.2f}; execution cap and broker minimums checked')
            finally:await client.close()
        return self.summary()

    @staticmethod
    def validate_account(a,amount):
        if a.get('status')!='ACTIVE' or a.get('currency','USD')!='USD':raise Blocked('Alpaca account must be active and USD-denominated')
        if a.get('trading_blocked') or a.get('account_blocked') or a.get('trade_suspended_by_user'):
            raise Blocked('Broker account trading is blocked')
        if not a.get('id'):raise Blocked('Missing account identity')
        # Never count margin buying power as spendable cash.
        cash,equity=float(a.get('cash',0)),float(a.get('equity',0))
        if not math.isfinite(cash) or not math.isfinite(equity):raise Blocked('Invalid broker balance')
        if cash<0:raise Blocked('Negative account cash / margin borrowing is not allowed')
        if equity<amount:raise Blocked('Account equity is below the strategy allocation')

    async def pause(self):
        self.config['active']=False;self.save();self.status='PAUSED';self.note('paused','New orders paused; existing positions are retained')
        # Only this strategy's orders may be canceled. Filled positions require Flatten.
        async with self.lock:
            if self.config.get('account_id'):
                c=self.providers.client('alpaca',self.config['network'])
                try:
                    a=await c.fetch_account()
                    if a.get('id')!=self.config['account_id']:raise Blocked('Account identity changed')
                    for r in self.pending():
                        o=await self.lookup(c,r['id'])
                        if o and o.get('status') not in FINAL:
                            await c.request('DELETE','/v2/orders/'+str(o['id']))
                        elif not o and r['status']=='planned':
                            self.remember(r['id'],{'status':'canceled','reason':'Paused before submission'})
                finally:await c.close()
        return self.summary()

    def pending(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM intents WHERE status NOT IN ('filled','canceled','expired','rejected','done_for_day') ORDER BY rowid")]

    async def lookup(self,c,oid):
        from app.exchange.alpaca_client import AlpacaError
        try:return await c.request('GET','/v2/orders:by_client_order_id?'+urllib.parse.urlencode({'client_order_id':oid}))
        except AlpacaError as exc:
            if exc.status==404:return None
            raise

    def remember(self,oid,result):
        with self.db:self.db.execute('UPDATE intents SET status=?,response=? WHERE id=?',
            (result.get('status','unknown'),json.dumps(result),oid))

    async def reconcile(self,c):
        for r in self.pending():
            o=await self.lookup(c,r['id'])
            if o:self.remember(r['id'],o)
        self.positions=await c.request('GET','/v2/positions')
        owned={json.loads(r[0])['symbol'] for r in self.db.execute('SELECT body FROM intents')}
        if any(p['symbol'] not in owned for p in self.positions):raise Blocked('Foreign positions found; dedicated account required')
        open_orders=await c.request('GET','/v2/orders?status=open&limit=500')
        mine={r[0] for r in self.db.execute('SELECT id FROM intents')}
        if any(o.get('client_order_id') not in mine for o in open_orders):raise Blocked('Foreign open orders found; execution paused')

    async def quotes(self,c,symbols,now):
        if not symbols:return {}
        q=urllib.parse.urlencode({'symbols':','.join(sorted(symbols)),'feed':self.quote_feed})
        out=(await c.request('GET','/v2/stocks/quotes/latest?'+q,data=True)).get('quotes',{})
        # Clock was read BEFORE asset/position/quote requests. A quote that
        # updates during those requests is newer than that snapshot, not a
        # future observation. Validate against receipt time instead.
        received=max(now,self.utcnow())
        for s in symbols:
            x=out.get(s,{})
            if not x.get('t') or not 0<=(received-stamp(x['t'])).total_seconds()<=60:
                raise Blocked('Fresh '+self.quote_feed.upper()+' sizing quote required: '+s)
            bid,ask=float(x.get('bp',0)),float(x.get('ap',0))
            if not (math.isfinite(bid) and math.isfinite(ask) and 0<bid<=ask):
                raise Blocked('Executable '+self.quote_feed.upper()+' bid/ask required: '+s)
        return out

    async def submit_pending(self,c,now,flatten=False):
        rows=self.pending()
        # A persisted submission intent is reconciled before it can ever POST again.
        for r in rows:
            if not flatten and not self.config['active']:return
            if not flatten:
                clock=await c.request('GET','/v2/clock')
                if not clock.get('is_open'):return
                # No late restart/catch-up orders, including unsubmitted buys.
                actual=stamp(clock['timestamp'])
                session=next((s for s in self.sessions if s['date']==r['session']),None)
                if not session or not 0<=(actual-session_dt(session,'open')).total_seconds()<=120:
                    self.config['active']=False;self.save();self.status='MISSED_OPEN';return
            body=json.loads(r['body'])
            found=await self.lookup(c,r['id'])
            if found:
                self.remember(r['id'],found)
                if found.get('status')!='filled':return
                continue
            if body['side']=='buy':
                # A buy never anticipates sale proceeds or uses broker margin buying power.
                a=await c.fetch_account();cash=float(a.get('cash',0))
                if cash<float(body['notional']):raise Blocked('Insufficient actual cash; no margin orders sent')
            # Journal BEFORE network mutation. The deterministic id survives restarts/timeouts.
            with self.db:self.db.execute("UPDATE intents SET status='submitting' WHERE id=?",(r['id'],))
            try:result=await c.request('POST','/v2/orders',body)
            except Exception:
                with self.db:self.db.execute("UPDATE intents SET status='uncertain' WHERE id=?",(r['id'],))
                raise
            self.remember(r['id'],result)
            # Sequential fill confirmation lets fractional orders finish
            # near the open without assuming that a POST acknowledgement is a fill.
            deadline=time.monotonic()+10
            while result.get('status') not in FINAL and time.monotonic()<deadline:
                await asyncio.sleep(.5)
                observed=await self.lookup(c,r['id'])
                if observed:
                    result=observed;self.remember(r['id'],result)
            if result.get('status')!='filled':
                if result.get('status') in FINAL:raise Blocked('Basket order failed: '+body['symbol']+' '+result['status'])
                return

    def persist_plan(self,session,orders):
        # Config/account pin stops ids from crossing accounts, modes or allocations.
        identity=hashlib.sha256(json.dumps(self.config,sort_keys=True).encode()).hexdigest()[:10]
        with self.db:
            for order in orders:
                oid=f'trend5-{identity}-{session}-{order["symbol"]}-{order["side"]}'
                body={**order,'type':'market','time_in_force':'day','extended_hours':False,'client_order_id':oid}
                self.db.execute('INSERT INTO intents VALUES(?,?,?,?,?)',(oid,session,json.dumps(body),'planned',None))

    async def tick(self,now):
        self.heartbeat=now.isoformat()
        if self.paper_check.active:
            async with self.lock:await self.paper_check.tick()
        if time.time()>=self.next_balance:
            self.next_balance=time.time()+60
            await self.refresh_brokers()
        # Read-only signal monitoring starts automatically when data keys exist.
        net='testnet' if self.providers.keys('alpaca','testnet') else self.config['network']
        if not self.providers.keys('alpaca',net):
            self.status='CONNECT_ALPACA';return
        if time.time()>=self.next_data:
            self.next_data=time.time()+300
            self.status='UPDATING_DAILY_DATA'
            c=self.providers.client('alpaca',net)
            try:self.signal,self.sessions=await signal_data(c,self.path.parent/'stock-trend-data',now)
            finally:await c.close()

            self.error=None
        if not self.config.get('account_id'):
            self.status='ALLOCATION_TOO_SMALL' if not capacity(self.config['allocation_usd'])['can_open'] else 'PAUSED'
            return
        async with self.lock:
            c=self.providers.client('alpaca',self.config['network'])
            try:
                a=await c.fetch_account()
                if a.get('id')!=self.config['account_id']:raise Blocked('Account identity changed')
                self.validate_account(a,0)
                self.account={k:a.get(k) for k in ('equity','cash','currency','status')}
                await self.reconcile(c)
                if self.config.get('flatten_pending'):
                    await self.submit_pending(c,now,flatten=True)
                    if not self.pending():
                        self.config.pop('flatten_pending',None);self.save()
                    self.status='FLATTENING' if self.pending() else 'PAUSED'
                    return
                if not self.config['active']:self.status='PAUSED_WITH_POSITIONS' if self.positions else 'PAUSED';return
                failed=self.db.execute("SELECT status FROM intents WHERE status IN ('rejected','expired','canceled','done_for_day') AND session=(SELECT json_extract(v,'$') FROM state WHERE k='last_session') LIMIT 1").fetchone()
                if failed:raise Blocked('The last basket did not fill completely; review broker orders before resuming')
                if self.pending():
                    await self.submit_pending(c,now);self.status='EXECUTING';return
                clock=await c.request('GET','/v2/clock')
                actual=stamp(clock['timestamp'])
                session=next((s for s in self.sessions if s['date']==actual.astimezone(session_dt(self.sessions[-1],'open').tzinfo).date().isoformat()),None)
                if not clock.get('is_open') or not session or not 0<=(actual-session_dt(session,'open')).total_seconds()<=120:
                    self.status='WAIT_NEXT_OPEN';return
                prior=[s for s in self.sessions if s['date']<session['date']]
                if not self.signal or not prior or self.signal['date']!=prior[-1]['date']:
                    raise Blocked('Signal must use the immediately preceding completed session')
                previous=self.db.execute("SELECT v FROM state WHERE k='last_session'").fetchone()
                if previous and json.loads(previous[0])==session['date']:self.status='SESSION_COMPLETE';return
                # Check the complete selected basket before any order.
                if self.signal['regime']=='LONG':
                    for s in self.signal['selected_stocks']:
                        asset=await c.request('GET','/v2/assets/'+urllib.parse.quote(s))
                        if not asset.get('tradable') or not asset.get('fractionable'):
                            raise Blocked('Selected stock cannot trade fractionally: '+s)
                symbols=set(self.signal['selected_stocks'])|{p['symbol'] for p in self.positions}
                try:quotes=await self.quotes(c,symbols,actual)
                except Blocked as exc:
                    # Nothing has been submitted: let opening quotes settle
                    # within the existing two-minute window. No late entry.
                    self.status='WAITING_FOR_QUOTES';self.error=str(exc);return
                self.error=None
                book_nav=max(0.,float(a.get('equity',0))-self.config.get('unallocated_equity_usd',0.))
                amount=min(self.config['allocation_usd'],book_nav)
                if amount<=0:raise Blocked('Strategy capital exhausted; unallocated account cash is not used to refill losses')
                basket=plan(self.signal,amount,self.positions,quotes,float(a.get('cash',0)))
                self.last_plan={'session':session['date'],'target_each_usd':basket['target_each_usd'],
                                'skipped':basket['skipped'],'order_count':len(basket['orders'])}
                if not any(o['side']=='sell' for o in basket['orders']) and basket['buy_total']>float(a.get('cash',0)):
                    raise Blocked('Insufficient actual cash for the complete basket')
                self.persist_plan(session['date'],basket['orders'])
                with self.db:self.db.execute("INSERT OR REPLACE INTO state VALUES('last_session',?)",(json.dumps(session['date']),))
                self.note('basket_planned',f'{session["date"]} {self.signal["regime"]} {len(basket["orders"])} orders')
                await self.submit_pending(c,actual);self.status='EXECUTING' if self.pending() else 'SESSION_COMPLETE'
            finally:await c.close()

    async def check_paper(self):
        async with self.lock:return await self.paper_check.begin()

    async def refresh_brokers(self):
        """Read-only cash/holdings visibility, including accounts not yet armed."""
        for network in ('testnet','mainnet'):
            if not self.providers.keys('alpaca',network):
                self.broker_readiness.pop(network,None);continue
            c=self.providers.client('alpaca',network)
            try:
                a=await c.fetch_account();self.validate_account(a,0)
                positions=await c.request('GET','/v2/positions')
                orders=await c.request('GET','/v2/orders?status=open&limit=500')
                cash=float(a.get('cash',0));equity=float(a.get('equity',0))
                self.broker_readiness[network]={'cash_usd':cash,'equity_usd':equity,
                    'positions':[p['symbol'] for p in positions],'open_orders':len(orders),
                    'ready':not positions and not orders and cash>=capacity(9.5)['minimum_initial_allocation_usd'],
                    'updated_at':datetime.now(timezone.utc).isoformat(),'error':None}
            except Exception as exc:self.broker_readiness[network]={'ready':False,'error':self.providers.redact(str(exc))}
            finally:await c.close()

    async def flatten(self,ack):
        if self.config['network']=='mainnet' and ack!='FLATTEN STOCKS 5':raise Blocked('Enter FLATTEN STOCKS 5 to sell live holdings')
        await self.pause()
        async with self.lock:
            if not self.config.get('account_id'):return self.summary()
            c=self.providers.client('alpaca',self.config['network'])
            try:
                a=await c.fetch_account()
                if a.get('id')!=self.config['account_id']:raise Blocked('Account identity changed')
                await self.reconcile(c)
                if self.pending():raise Blocked('Wait for existing orders to reach a final broker status before Flatten')
                if not (await c.request('GET','/v2/clock')).get('is_open'):raise Blocked('Flatten requires the regular market session to be open')
                label='flat-'+str(int(time.time()))
                self.persist_plan(label,[{'symbol':p['symbol'],'side':'sell','qty':str(p['qty'])} for p in self.positions])
                # Flatten batch must be polled by the monitor without entering a new buy batch.
                self.config['flatten_pending']=label;self.save();self.note('flatten_requested',label)
                await self.submit_pending(c,datetime.now(timezone.utc),flatten=True)
                return self.summary()
            finally:await c.close()
