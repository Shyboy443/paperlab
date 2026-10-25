"""Auto-resumed, isolated delayed-SIP paper books. Never submits Alpaca orders."""
import asyncio, gzip, hashlib, json, os, time
from datetime import datetime, timezone
from pathlib import Path
from .data import recent, download
from .model import Book, Config, RULE, aggregate, MINUTE, configs
from .research import evaluate

def atomic(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,allow_nan=False),encoding='utf-8');os.replace(tmp,path)

class ValueReversalService:
    def __init__(self,path,providers,can_enter=lambda:True):
        self.path=Path(path);self.providers=providers;self.can_enter=can_enter
        self.books={cfg.key:Book(cfg) for cfg in configs()}
        self.status='STARTING';self.error=None;self.heartbeat=None;self.updated=None;self.started=None
        self.task=None;self.research_task=None;self.enabled=True;self.dataset=None;self.report=None
        self.research_status={'phase':'PENDING','downloaded_bars':0,'error':None}
        if self.path.exists():
            try:
                state=json.loads(self.path.read_text());self.enabled=state['enabled'];self.started=state['started']
                self.books={key:Book.restore(state['books'][key],b.cfg) for key,b in self.books.items()}
            except Exception:
                self.error='Saved paper identity/state requires review; history preserved';self.status='BLOCKED';self.enabled=False
        self.report_path=self.path.with_name('qqq-value-reversal-v2-report.json')
        report=self.report_path
        if report.exists():
            try:
                saved=json.loads(report.read_text())
                hashes={n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest()
                        for n in ('model.py','data.py','research.py','indicator.py')}
                if saved.get('rule')==RULE and saved.get('source_sha256')==hashes:
                    self.report=saved;self.research_status={'phase':'COMPLETE','error':None}
            except (OSError,ValueError,TypeError,AttributeError):
                # A damaged derived report must not stop unrelated live services or reset books.
                self.research_status={'phase':'PENDING','error':'Stored report unreadable; rebuild from cached data'}

    def start(self):self.task=asyncio.create_task(self.loop(),name='qqq-value-reversal')
    async def close(self):
        for task in (self.task,self.research_task):
            if task:task.cancel()
        for task in (self.task,self.research_task):
            if task:
                try:await task
                except asyncio.CancelledError:pass

    def checkpoint(self):
        atomic(self.path,{'rule':RULE,'enabled':self.enabled,'started':self.started,
                          'books':{k:b.snapshot() for k,b in self.books.items()}})

    def summary(self, include_report=True, include_evidence=True):
        return {'rule':RULE,'symbol':'QQQ','source_video':'https://www.youtube.com/watch?v=dUczefIYKIU',
                'status':self.status,'enabled':self.enabled,'error':self.error,'heartbeat':self.heartbeat,
                'updated_at':self.updated,'forward_start_ms':self.started,'execution':'DELAYED_FEED_PAPER_SIMULATION',
                'feed':'Alpaca consolidated SIP, 16+ minute availability delay','broker_orders_enabled':False,
                'books':[b.summary() for b in self.books.values()],'research':self.research_status,
                'report':self.report if include_report else None,'read_only':True,
                'source_parity':self.parity() if include_evidence else None, 'archived_v1':self.archived() if include_evidence else None}

    def parity(self):
        path=Path(__file__).with_name('sources')/'source-parity.json'
        if path.exists():
            proof=json.loads(path.read_text())
            if (proof.get('indicator_sha256')==hashlib.sha256(Path(__file__).with_name('indicator.py').read_bytes()).hexdigest()
                and proof.get('pine_source_sha256')==hashlib.sha256(Path(__file__).with_name('sources').joinpath('value-area-reversion-official.pine').read_bytes()).hexdigest()):
                return proof
        return None

    def replay_historical(self,key,date):
        if key not in self.books:raise ValueError('Unknown frozen experiment')
        try:datetime.strptime(date,'%Y-%m-%d')
        except ValueError:raise ValueError('Use a YYYY-MM-DD session date')
        cache=self.path.with_name('qqq-value-reversal-input.json.gz')
        with gzip.open(cache,'rt',encoding='utf-8') as f:data=json.load(f)
        selected=[s for s in data['sessions'] if s['date']<=date][-2:]
        if not selected or selected[-1]['date']!=date:raise ValueError('No historical session on that date')
        bars=[b for b in data['bars'] if selected[0]['open_ms']<=b[0]<selected[-1]['close_ms']]
        book=Book(self.books[key].cfg);book.forward_start=1
        for group in aggregate(bars,selected,book.cfg.minutes):
            if not group['complete']:raise ValueError('Incomplete historical session')
            for b in group['bars']:book.process(b,group,trade=False)
        return {'key':key,'date':date,'minutes':book.cfg.minutes,'rule':RULE,
                'candles':[r for r in book.replay if selected[-1]['open_ms']<=r['bar'][0]],
                'signals':[s for s in book.signals if selected[-1]['open_ms']<=s['ts']],
                'note':'All closed-candle indicator arrows on this session, independent of trade eligibility; no future bars in levels.'}

    def archived(self):
        path=self.path.with_name('qqq-value-reversal-report.json')
        try:
            report=json.loads(path.read_text())
            p=report['results'][0]['periods']['comparison']['net']
            return {'rule':report['rule'],'status':'ARCHIVED_SUPERSEDED','verdict':report['verdict'],
                    'monthly_avg_net_pct':p['monthly_avg_net_pct'],'trades':p['trades'],
                    'note':'First approximation retained, not combined with corrected v2 results or forward books.'}
        except (OSError,ValueError,KeyError,TypeError):return None

    async def historical(self):
        try:
            self.research_status={'phase':'DOWNLOADING','downloaded_bars':0,'error':None}
            client=self.providers.client('alpaca','testnet')
            cache=self.path.with_name('qqq-value-reversal-input.json.gz')
            if cache.exists():
                with gzip.open(cache,'rt',encoding='utf-8') as f:data=json.load(f)
            else:
                data=await download(client,'2021-12-01T00:00:00Z','2026-01-01T00:00:00Z',
                    lambda n:self.research_status.update(downloaded_bars=n))
                with gzip.open(cache,'wt',encoding='utf-8') as f:json.dump(data,f)
            self.research_status['phase']='EVALUATING'
            self.report=await asyncio.to_thread(evaluate,data,lambda label:self.research_status.update(working_on=label))
            atomic(self.report_path,self.report)
            self.research_status.update(phase='COMPLETE',error=None)
        except asyncio.CancelledError:raise
        except Exception as exc:
            self.research_status.update(phase='BLOCKED',error=self.providers.redact(str(exc)))

    async def tick(self,now=None):
        now=now or datetime.now(timezone.utc);self.heartbeat=now.isoformat()
        if self.status=='BLOCKED' and self.error and 'identity' in self.error:return
        if not self.providers.keys('alpaca','testnet'):
            self.status='NEEDS_ALPACA_PAPER_KEYS';self.error='Configure Alpaca paper data credentials in PaperLab';return
        client=self.providers.client('alpaca','testnet')
        data=await recent(client,now);self.dataset=data
        observed=int(now.timestamp()*1000);initial=self.started is None
        working={k:Book.restore(b.snapshot(),b.cfg) for k,b in self.books.items()}
        for book in working.values():
            groups=aggregate(data['bars'],data['sessions'],book.cfg.minutes)
            for session in groups:
                for bar in session['bars']:
                    if bar[0]+book.cfg.minutes*MINUTE>observed-16*MINUTE:continue
                    # No catch-up entries after downtime. Availability delay is explicitly modeled.
                    fresh=observed-(bar[0]+book.cfg.minutes*MINUTE+16*MINUTE)<=5*MINUTE
                    trade=not initial and self.enabled and self.can_enter() and fresh and bar[0]>=self.started
                    book.process(bar,session,observed=observed,trade=trade)
        self.books=working
        if initial:
            self.started=observed
            for book in self.books.values():book.forward_start=observed
        self.updated=now.isoformat();self.error=None;self.status='OBSERVING' if self.enabled else 'PAUSED_ENTRIES'
        self.checkpoint()
        if not self.research_task and not self.report:self.research_task=asyncio.create_task(self.historical(),name='qqq-retrospective')

    async def loop(self):
        while True:
            try:await self.tick()
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.status='DATA_ERROR';self.error=self.providers.redact(f'{type(exc).__name__}: {exc}')
            await asyncio.sleep(60)
