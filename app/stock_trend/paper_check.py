"""One bounded, persistent PAPER-only fractional basket round trip.

This checks order mechanics, not profitability. Hosts are selected internally;
no operator-supplied network can route verification orders to a live account.
"""
import json, time
from .model import Blocked,RULE
from .service import FINAL
from .data import stamp

class PaperCheck:
    def __init__(self,owner):
        self.owner=owner
        r=owner.db.execute("SELECT v FROM state WHERE k='paper_check'").fetchone()
        self.state=json.loads(r[0]) if r else {'phase':'NOT_RUN','orders':[]}

    @property
    def active(self):return self.state['phase'] not in ('NOT_RUN','PASSED','FAILED')

    def save(self):
        with self.owner.db:self.owner.db.execute("INSERT OR REPLACE INTO state VALUES('paper_check',?)",(json.dumps(self.state),))

    def summary(self):return {k:v for k,v in self.state.items() if k!='account_id'}

    async def begin(self):
        o=self.owner
        if self.active:return self.summary()
        if self.state['phase']=='PASSED':return self.summary()
        if o.config['active'] or o.positions or o.pending():raise Blocked('Pause and flatten the strategy before a paper execution check')
        if not o.signal or len(o.signal.get('selected_stocks',[]))!=5:raise Blocked('A complete five-stock signal is required')
        c=o.providers.client('alpaca','testnet')
        try:
            a=await c.fetch_account();o.validate_account(a,9.5)
            if float(a['cash'])<9.5:raise Blocked('Paper check needs $9.50 available paper cash')
            if await c.request('GET','/v2/positions') or await c.request('GET','/v2/orders?status=open&limit=500'):
                raise Blocked('Paper execution check requires a dedicated flat paper account')
            for s in o.signal['selected_stocks']:
                asset=await c.request('GET','/v2/assets/'+s)
                if not asset.get('tradable') or not asset.get('fractionable'):raise Blocked('Non-fractionable selected asset: '+s)
            self.state={'phase':'WAIT_OPEN','rule':RULE,'account_id':a['id'],'generation':str(time.time_ns()),
                        'symbols':list(o.signal['selected_stocks']),'orders':[],'paper_only':True,
                        'maximum_buy_notional_usd':9.40,'started_at':time.time(),'error':None}
            self.save();o.note('paper_check_scheduled','Five $1.88 paper buys followed by full fractional exits; no live orders')
        finally:await c.close()
        return self.summary()

    async def order(self,c,symbol,side,qty=None):
        key=f'pc5-{self.state["generation"]}-{symbol}-{side}'
        saved=next((x for x in self.state['orders'] if x['client_order_id']==key),None)
        found=await self.owner.lookup(c,key)
        if found:
            status=found.get('status','unknown')
            evidence={'client_order_id':key,'symbol':symbol,'side':side,'status':status,
                      'filled_qty':found.get('filled_qty','0'),'filled_avg_price':found.get('filled_avg_price'),
                      'filled_at':found.get('filled_at')}
            if saved:self.state['orders'].remove(saved)
            self.state['orders'].append(evidence);self.save()
            return status
        if saved and saved['status']!='planned':
            raise Blocked('Uncertain paper submission; awaiting broker reconciliation: '+key)
        if not saved:
            saved={'client_order_id':key,'symbol':symbol,'side':side,'status':'planned'}
            self.state['orders'].append(saved);self.save()
        if side=='buy' and float((await c.fetch_account())['cash'])<1.88:raise Blocked('Insufficient paper cash')
        body={'symbol':symbol,'side':side,'type':'market','time_in_force':'day',
              'extended_hours':False,'client_order_id':key}
        body.update({'notional':'1.88'} if side=='buy' else {'qty':str(qty)})
        saved['status']='submitting';self.save()
        # A response loss leaves an intent to reconcile, never a new client id.
        result=await c.request('POST','/v2/orders',body)
        saved.update(status=result.get('status','unknown'),filled_qty=result.get('filled_qty','0'),
                     filled_avg_price=result.get('filled_avg_price'),filled_at=result.get('filled_at'))
        self.save();return saved['status']

    async def tick(self):
        if not self.active:return
        o=self.owner;c=o.providers.client('alpaca','testnet')
        try:
            a=await c.fetch_account()
            if a.get('id')!=self.state['account_id']:raise Blocked('Paper account identity changed')
            clock=await c.request('GET','/v2/clock')
            if not clock.get('is_open'):return
            positions=await c.request('GET','/v2/positions')
            if any(p['symbol'] not in self.state['symbols'] for p in positions):raise Blocked('Foreign paper positions found')
            orders=await c.request('GET','/v2/orders?status=open&limit=500')
            if any(not str(r.get('client_order_id','')).startswith('pc5-'+self.state['generation']+'-') for r in orders):
                raise Blocked('Foreign paper orders found')
            # An exit can fill between ticks and disappear from positions.
            # Reconcile persisted exits as well as entries before grading.
            for saved in self.state['orders']:
                observed=await o.lookup(c,saved['client_order_id'])
                if observed:
                    saved.update(status=observed.get('status','unknown'),filled_qty=observed.get('filled_qty','0'),
                                 filled_avg_price=observed.get('filled_avg_price'),filled_at=observed.get('filled_at'))
            self.save()
            if self.state['phase']=='WAIT_OPEN':
                await o.quotes(c,set(self.state['symbols']),stamp(clock['timestamp']))
                self.state['quote_feed']=o.quote_feed
                self.state['phase']='BUYING';self.state['error']=None;self.save()
            if self.state['phase']=='BUYING':
                for symbol in self.state['symbols']:
                    status=await self.order(c,symbol,'buy')
                    if status in FINAL and status!='filled':
                        self.state['buy_failed']=True;self.state['phase']='SELLING';self.save();break
                    if status!='filled':return
                else:self.state['phase']='SELLING';self.save()
            if self.state['phase']=='SELLING':
                positions=await c.request('GET','/v2/positions')
                for p in positions:
                    status=await self.order(c,p['symbol'],'sell',p['qty'])
                    if status in FINAL and status!='filled':raise Blocked('Paper exit did not complete; broker review required')
                    if status!='filled':return
                if await c.request('GET','/v2/positions') or await c.request('GET','/v2/orders?status=open&limit=500'):return
                # Five actual entries AND exits are required for a pass.
                fills=self.state['orders']
                passed=len(fills)==10 and all(x['status']=='filled' and float(x.get('filled_qty',0))>0 for x in fills)
                self.state.update(phase='PASSED' if passed else 'FAILED',finished_at=time.time(),error=None,
                                  flat_confirmed=True)
                self.save();o.note('paper_check_finished',self.state['phase']+'; broker paper account confirmed flat')
            self.state['error']=None;self.save()
        except Exception as exc:
            self.state['error']=o.providers.redact(str(exc));self.save()
        finally:await c.close()
