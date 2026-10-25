"""Continuous divergence scanner plus bounded AI review and persistent paper books."""
from __future__ import annotations
import json
import os
import queue
import threading
import time
from pathlib import Path

from .market import Market, UNIVERSE, DAY, MIN
from .store import Store
from . import paper


class DivergenceService:
    def __init__(self,path,llm=None,can_enter=lambda:True,env=None,market=None,clock=time.time,telegram=None):
        env=os.environ if env is None else env
        self.enabled=env.get('DIVERGENCE_ENABLED','true').lower() in ('1','true','yes')
        from .model import RULE_VERSION
        # Each frozen rule version keeps its own database: DIV-SR-1's books stay untouched in the original file.
        if RULE_VERSION!='DIV-SR-1':
            path=Path(path);path=path.with_name(f'{path.stem}-{RULE_VERSION.lower()}{path.suffix}')
        self.store=Store(path);self.clock=clock;self.stop_event=threading.Event()
        self.market=market or Market(self.stop_event);self.llm=llm;self.can_enter=can_enter
        self.jobs=queue.Queue(maxsize=20);self.thread=None;self.reviewer=None;self.backtester=None
        self.telegram=telegram;self.public_base=(env.get('PUBLIC_BASE_URL') or 'https://paperlab-production-919c.up.railway.app').rstrip('/')
        self.health={'status':'STARTING' if self.enabled else 'DISABLED','enabled':self.enabled,'heartbeat_ms':None,'last_scan_ms':None,'error':None}
        self.universe=list(UNIVERSE);self.next_scan=0;self.next_universe=0;self.next_funding={};self.quotes={}
        self.books=self.store.get('books') or {'baseline':paper.empty(),'council':paper.empty()}
        self.guard=threading.RLock()
        from .model import RULE_VERSION,RULES
        frozen=self.store.get('frozen_rules')
        if frozen and frozen!={'version':RULE_VERSION,'rules':RULES}:
            self.enabled=False;self.health.update(status='RULE_VERSION_MISMATCH',enabled=False,error='Saved rules differ; use a new desk version and preserve this book.')
        elif not frozen:self.store.set('frozen_rules',{'version':RULE_VERSION,'rules':RULES})
        self.constraints=self.store.get('universe',{})
        # A restart must not turn an interrupted review into an implicit approval
        # or replay a paid call. A new, fresh setup can be reviewed normally.
        for review in self.store.records('review',100):
            if review.get('status')=='queued':
                review.update(status='unavailable',decision='unavailable',reasons=['Review interrupted by restart; no council entry'])
                self.store.record('review',review['setup_id'],review['ts'],review)

    def now(self):return int(self.clock()*1000)

    def start(self):
        if not self.enabled:return
        self.thread=threading.Thread(target=self._run,name='divergence-scan',daemon=True);self.thread.start()
        self.reviewer=threading.Thread(target=self._review_loop,name='divergence-council',daemon=True);self.reviewer.start()
        self.backtester=threading.Thread(target=self._backtest_loop,name='divergence-backtest',daemon=True);self.backtester.start()

    def stop(self):
        self.stop_event.set()
        for thread in (self.thread,self.reviewer):
            if thread:thread.join(3)
        # An in-flight bounded model call may finish after shutdown starts; SQLite
        # remains available to its daemon until process exit so no partial review is lost.

    def _beat(self):self.health['heartbeat_ms']=self.now()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self._beat();self.cycle();self.health.update(status='RUNNING',error=None)
            except InterruptedError:break
            except Exception as exc:
                self.health.update(status='DEGRADED',error=type(exc).__name__)
            self._beat()
            if self.stop_event.wait(30):break
        self.health['status']='STOPPED'

    def cycle(self):
        from .model import analyze,HISTORY_DAYS
        now=self.now()
        if now>=self.next_universe:
            self.constraints=self.market.universe();self.store.set('universe',self.constraints)
            self.next_universe=now+300_000
        tg=self._tg()
        if tg is not None:tg.register_desk('divergence','Divergence Desk',self.approval_mode,self.set_approval_mode)
        # Forward stop/funding processing is independent of new signal generation.
        self._update_books()
        self._report_closed()
        self._pending_signals()
        if now<self.next_scan:return
        # Rescan at each completed 15m interval; do not delay 4h signals until the next hour.
        self.next_scan=(now//900_000+1)*900_000+5000
        scans=[]
        for symbol in self.universe:
            if self.stop_event.is_set():return
            self._beat()
            try:
                market=self.constraints.get(symbol,{})
                if not market.get('liquid'):
                    scans.append({'symbol':symbol,'ts':self.now(),'status':'ILLIQUID','error':None,'setups':[]});continue
                old=self.store.bars(symbol,now-HISTORY_DAYS*DAY)
                start=old[-1]['t'] if old else now-HISTORY_DAYS*DAY
                self.store.save_bars(symbol,self.market.bars(symbol,start,self.now()))
                bars=self.store.bars(symbol,now-HISTORY_DAYS*DAY)
                if not bars or self.now()-(bars[-1]['t']+900_000)>900_000:raise ValueError('Stale completed candle')
                result=analyze(bars,symbol,self.now())
                setups=result.get('setups',[])
                scans.append({'symbol':symbol,'ts':self.now(),'status':'OK','error':None,'setups':setups[-8:]})
                for signal in setups:
                    if signal.get('confirmed_at') is None or signal.get('status') not in (None,'CONFIRMED'):continue
                    if not 0<=self.now()-signal['confirmed_at']<=900_000:continue
                    if self.store.has('signal',signal['id']):continue
                    self.store.record('signal',signal['id'],signal['confirmed_at'],signal)
                    self._pending_signals()
            except InterruptedError:raise
            except Exception as exc:
                scans.append({'symbol':symbol,'ts':self.now(),'status':'DATA_ERROR','error':type(exc).__name__,'setups':[]})
            self.store.set('scans',scans)
        self.health['last_scan_ms']=self.now()
        self.store.set('last_scan_ms',self.health['last_scan_ms'])
        self.store.set('scans',scans)

    def _pending_signals(self):
        for signal in self.store.records('signal',50):
            if self.now()-signal['confirmed_at']>900_000:continue
            if not self.store.has('execution','baseline:'+signal['id']):
                quote=self.market.quote(signal['symbol']);self.quotes[signal['symbol']]=quote
                self._enter('baseline',signal,quote)
            if not self.store.has('review',signal['id']):self._queue_review(signal)

    def _enter(self,book_name,signal,quote,max_age=900_000,extra=None):
        now=self.now()
        with self.guard:
            book=self.books[book_name];paper.mark(book,self.quotes,now)
            reason='Global trading pause' if not self.can_enter() else None
            if not reason and not self.constraints.get(signal['symbol'],{}).get('liquid'):reason='Market not liquid'
            if not reason:reason=paper.open_position(book,signal,quote,self.constraints.get(signal['symbol'],{}).get('constraints',{}),now,max_age)
            self.store.execution(book_name+':'+signal['id'],now,{'book':book_name,'setup_id':signal['id'],'ts':now,'status':'SKIPPED' if reason else 'OPENED','reason':reason,**(extra or {})},self.books)
            return reason

    def _update_books(self):
        now=self.now()
        with self.guard:
            symbols=sorted({p['symbol'] for b in self.books.values() for p in b['open']})
        for symbol in symbols:
            self._beat()
            try:
                quote=self.market.quote(symbol);self.quotes[symbol]=quote
                with self.guard:
                    positions=[p for b in self.books.values() for p in b['open'] if p['symbol']==symbol]
                    since=min(p['last_bar'] for p in positions)
                    earliest=min(p['last_funding'] for p in positions)
                bars=self.market.bars(symbol,since,self.now(),'1m')
                # Fetch before every reconciliation, including possible exits;
                # a five-minute cache could otherwise omit a just-settled charge.
                self.store.save_funding(symbol,self.market.funding(symbol,earliest,self.now()+1))
                funding=self.store.funding(symbol,earliest)
                if self.now()-earliest>12*3_600_000 and not any(r[0]>earliest for r in funding):raise ValueError('Funding history incomplete')
                # Refuse catch-up without current funding coverage; never silently
                # treat an unavailable funding endpoint as zero cost.
                with self.guard:
                    for book in self.books.values():
                        for p in list(book['open']):
                            if p['symbol']==symbol:paper.update_position(book,p,bars,funding,quote,self.now())
                    self.store.set('books',self.books)
            except InterruptedError:raise
            except Exception as exc:
                self.store.record('data_error',symbol,now,{'symbol':symbol,'ts':now,'error':type(exc).__name__})
        with self.guard:
            for book in self.books.values():paper.mark(book,self.quotes,self.now())
            self.store.set('books',self.books)
        # Only the scanner thread may mutate portfolios. Review thread sends decisions via storage.
        for review in self.store.records('review',30):
            sid=review['setup_id']
            if review.get('decision')!='accept' or self.store.has('execution','council:'+sid):continue
            signal=review['setup'];symbol=signal['symbol']
            if self.approval_mode()=='manual' and self.telegram is not None:
                self._council_approval(sid,signal,review);continue
            if self.now()-signal['confirmed_at']>900_000:
                self.store.record('execution','council:'+sid,self.now(),{'book':'council','setup_id':sid,'status':'SKIPPED','reason':'Review completed after entry window','ts':self.now()});continue
            quote=self.market.quote(symbol);self.quotes[symbol]=quote;self._enter('council',signal,quote)

    # -- operator approval (Telegram) ------------------------------------------------------------------------------------
    APPROVAL_MS=3_600_000

    def _tg(self):
        try:return self.telegram() if callable(self.telegram) else self.telegram
        except Exception:return None

    def approval_mode(self):
        return 'manual' if self.store.get('approval_mode','auto')=='manual' else 'auto'   # default: no Telegram prompts

    def set_approval_mode(self,mode):
        if mode not in ('manual','auto'):raise ValueError('mode must be manual or auto')
        self.store.set('approval_mode',mode);return mode

    def link(self,setup_id):
        from urllib.parse import quote
        return f"{self.public_base}/public/competition/divergence?setup={quote(setup_id,safe='')}"

    def _approval_text(self,s,review):
        import html as H
        e=lambda v:H.escape(str(v))
        p1,p2=s.get('pivot1') or {},s.get('pivot2') or {}
        reasons=review.get('reasons') or []
        why=reasons[0] if isinstance(reasons,list) and reasons else (reasons if isinstance(reasons,str) else '')
        return (f"🧭 <b>Divergence Desk</b> · paper · needs your approval\n"
                f"<b>{e(s['side'].upper())} {e(s['symbol'].replace('USDT','/USDT'))}</b> · {e((s.get('divergence_type') or '').replace('_',' '))} "
                f"{e(s.get('divergence_tf'))} → {e(s.get('trigger_tf'))}\n"
                f"Swings {p1.get('price',0):,.6g} → {p2.get('price',0):,.6g} ({e(s.get('price_relation',''))}) · RSI {p1.get('rsi') or 0:.1f} → {p2.get('rsi') or 0:.1f} ({e(s.get('rsi_relation',''))})\n"
                f"Entry ≈ {s.get('entry_reference',0):,.6g} · stop {s['stop']:,.6g} · 2R target {s.get('target',0):,.6g} · risk 0.5% of the council book\n"
                f"Council: accepted{(' — '+e(why[:300])) if why else ''}\n"
                f"<i>Approve within 60 minutes. The entry uses a fresh quote and is refused if price has moved more than a quarter of the stop distance.</i>")

    def _approval_png(self,s):
        try:
            from app.live.trade_chart import render
            from .model import aggregate
            dur={'1h':3_600_000,'4h':14_400_000}.get(s.get('divergence_tf'),3_600_000)
            raw=self.store.bars(s['symbol'],s['confirmed_at']-90*dur)
            bars=[[b['t'],b['o'],b['h'],b['l'],b['c'],b['v']] for b in aggregate(raw,dur)]
            return render(bars,side=s['side'],entry=float(s['entry_reference']),stop=float(s['stop']),tps=[float(s['target'])],
                          entry_ts=int(s['confirmed_at']),title=s['symbol'].replace('USDT',' / USDT'),
                          subtitle='Binance perp · Divergence Desk',tf_label=s.get('divergence_tf') or '1h',
                          footer='paper decision · awaiting your approval')
        except Exception:
            return None

    def _council_approval(self,sid,signal,review):
        tg=self._tg();now=self.now();appr=self.store.get('approval:'+sid)
        skip=lambda why:self.store.record('execution','council:'+sid,now,{'book':'council','setup_id':sid,'status':'SKIPPED','reason':why,'ts':now,'approval':'telegram'})
        if appr is None:
            if now-signal['confirmed_at']>self.APPROVAL_MS:skip('Review finished after the approval window');return
            token=tg.request_approval('divergence',sid,self._approval_text(signal,review),self.link(sid),self._approval_png(signal)) if tg else None
            if not token:skip('Manual approval mode, but Telegram is not linked: no trade');return
            self.store.set('approval:'+sid,{'token':token,'ts':now,'setup_id':sid,'symbol':signal['symbol']});return
        token=appr['token'];st=tg.approval_status(token) if tg else None
        if st=='approved':
            quote=self.market.quote(signal['symbol']);self.quotes[signal['symbol']]=quote
            reason=self._enter('council',signal,quote,max_age=self.APPROVAL_MS,extra={'approval':'telegram'})
            if reason:tg.finish_approval(token,f"Approved, but not opened: {reason}.",'approved')
            else:
                p=next((x for x in self.books['council']['open'] if x['id']==sid),None)
                tg.finish_approval(token,f"✅ Paper trade opened at {p['entry']:,.6g} · stop {p['stop']:,.6g} · target {p['target']:,.6g}." if p else "✅ Paper trade opened.",'approved')
        elif st=='rejected':
            skip('Rejected by the operator in Telegram');tg.finish_approval(token,'❌ Rejected: no trade.','rejected')
        elif st in (None,'expired') or now-appr['ts']>self.APPROVAL_MS:
            skip('No approval within 60 minutes')
            if tg:tg.finish_approval(token,'⌛ No answer within an hour: no trade.','expired')

    def _report_closed(self):
        tg=self._tg()
        if tg is None:return
        for t in list(self.books.get('council',{}).get('trades',[]))[-20:]:
            appr=self.store.get('approval:'+str(t.get('id')))
            if not appr or appr.get('closed_sent'):continue
            tg.reply_approval(appr['token'],f"Closed ({t.get('reason')}) at {t.get('exit',0):,.6g}: <b>{t.get('net',0):+.2f} USD</b> ({t.get('r',0):+.2f}R)")
            appr['closed_sent']=True;self.store.set('approval:'+str(t.get('id')),appr)

    def _queue_review(self,signal):
        now=self.now();day=now//DAY
        budget=self.store.get('review_budget',{'day':day,'count':0})
        if budget['day']!=day:budget={'day':day,'count':0}
        if budget['count']>=12 or self.jobs.full():
            self.store.record('review',signal['id'],now,{'setup_id':signal['id'],'symbol':signal['symbol'],'ts':now,'setup':signal,'status':'unavailable','decision':'unavailable','reasons':['Divergence review limit reached; baseline continues'],'messages':[]});return
        budget['count']+=1;self.store.set('review_budget',budget)
        self.store.record('review',signal['id'],now,{'setup_id':signal['id'],'symbol':signal['symbol'],'ts':now,'setup':signal,'status':'queued','decision':'unavailable','messages':[]})
        self.jobs.put_nowait(signal)

    def _review_loop(self):
        from .review import ReviewCouncil
        council=ReviewCouncil(self.llm)
        while not self.stop_event.is_set():
            try:signal=self.jobs.get(timeout=1)
            except queue.Empty:continue
            now=self.now()
            try:out=council.review(signal,{'mode':'forward','market':self.constraints.get(signal['symbol'],{}),'risk_pct':.5,'reward_risk':2},now)
            except Exception as exc:out={'status':'unavailable','decision':'unavailable','messages':[],'reasons':[type(exc).__name__]}
            self.store.record('review',signal['id'],now,{**out,'setup_id':signal['id'],'symbol':signal['symbol'],'ts':now,'setup':signal})
            self.jobs.task_done()

    def summary(self):
        from .model import RULES,RULE_VERSION
        health={**self.health,'last_scan_ms':self.health.get('last_scan_ms') or self.store.get('last_scan_ms'),'llm':self.llm.health() if self.llm else {'configured':False}}
        health['heartbeat_age_s']=(self.now()-health['heartbeat_ms'])/1000 if health['heartbeat_ms'] else None
        if self.enabled and health['heartbeat_age_s'] is not None and health['heartbeat_age_s']>180:health['status']='STALE'
        scans=self.store.get('scans',[])
        errors=[s for s in scans if s.get('error')]
        if health['status']=='RUNNING' and errors:health.update(status='DEGRADED',error=f"{len(errors)} market scan(s) have data errors")
        path=Path(__file__).parent/'results'/'report.json'
        try:research=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'status':'PENDING','rows':[],'notes':['Historical study is being prepared. Forward paper books begin when this desk starts.']}
        except (ValueError,OSError):research={'status':'UNAVAILABLE','rows':[]}
        with self.guard:books=json.loads(json.dumps(self.books))
        if any(p.get('data_gap') for book in books.values() for p in book['open']):
            health.update(status='DEGRADED',error='Missing minute history; reconciling paper positions before further fills')
        for book in books.values():
            book['trades']=book['trades'][-100:];book['equity_curve']=book['equity_curve'][-2000:]
        setups={s['id']:{**s,'origin':'Historical chart scan'} for scan in scans for s in scan.get('setups',[])}
        setups.update({s['id']:{**s,'origin':'Prospective signal'} for s in self.store.records('signal',50)})
        return {'name':'Divergence Desk','approval_mode':self.approval_mode(),'rule_version':RULE_VERSION,'rules':RULES,'health':health,'universe':self.universe,'scans':scans,'setups':sorted(setups.values(),key=lambda s:s['divergence_at'],reverse=True),'reviews':self.store.records('review',25),'executions':self.store.records('execution',50),'books':books,'research':research,'execution':'PAPER_ONLY','read_only':True,'sources':'Binance USDT perpetual public candles and actual funding; RSS news for prospective reviews only'}

    # -- inspectable long-history backtest (read-only; BTC and ETH) -------------------------------------------------------
    BACKTEST_SYMBOLS=('BTCUSDT','ETHUSDT');BACKTEST_DAYS=730

    def _backtest_loop(self):
        """Backfill two years of completed 15m candles and funding for BTC and ETH, then replay every confirmed signal of
        the frozen rule (app/divergence/inspect.py). Recomputed every 6 hours; never touches the paper books."""
        while not self.stop_event.is_set():
            for symbol in self.BACKTEST_SYMBOLS:
                if self.stop_event.is_set():return
                try:self._backtest_symbol(symbol)
                except InterruptedError:return
                except Exception as exc:
                    old=self.store.get('backtest:'+symbol) or {}
                    self.store.set('backtest:'+symbol,{**old,'symbol':symbol,'status':old.get('status','ERROR'),'error':type(exc).__name__})
            if self.stop_event.wait(6*3600):return

    def _backtest_symbol(self,symbol):
        from .inspect import backtest_report
        now=self.now();start=(now-self.BACKTEST_DAYS*DAY)//DAY*DAY
        have=self.store.bars(symbol,start)
        if not have or have[0]['t']>start+DAY:
            self.store.save_bars(symbol,self.market.bars(symbol,start,have[0]['t'] if have else now))
        have=self.store.bars(symbol,start)
        if have and self.now()-have[-1]['t']>2*900_000:
            self.store.save_bars(symbol,self.market.bars(symbol,have[-1]['t'],self.now()));have=self.store.bars(symbol,start)
        fund=self.store.funding(symbol,start)
        if not fund or fund[0][0]>start+DAY:
            self.store.save_funding(symbol,self.market.funding(symbol,start,now))
        else:
            self.store.save_funding(symbol,self.market.funding(symbol,fund[-1][0]+1,now))
        report=backtest_report(have,self.store.funding(symbol,start),symbol,self.BACKTEST_DAYS)
        self.store.set('backtest:'+symbol,report)

    def backtest(self,symbol):
        if symbol not in self.BACKTEST_SYMBOLS:raise ValueError('Backtest is available for BTCUSDT and ETHUSDT')
        return {**(self.store.get('backtest:'+symbol) or {'status':'COMPUTING','symbol':symbol,'trades':[]}),'read_only':True}

    def chart(self,symbol,tf,setup_id='',around=0):
        """Read-only chart data: every stored candle of the last HISTORY_DAYS days on this timeframe (15m capped at
        3,000), RSI(14), EMA50, the major 4h support/resistance levels, and every setup the frozen rule finds in that history, each with its rule checks and,
        when confirmed, the replayed paper trade (next 15m open, fees and slippage; funding not included here)."""
        from .model import aggregate,indicators,ema_series,analyze,TREND_EMA,HISTORY_DAYS
        if symbol not in self.universe or tf not in ('15m','1h','4h'):raise ValueError('Unsupported symbol or timeframe')
        durations={'15m':900_000,'1h':3_600_000,'4h':14_400_000}
        if around:return self._chart_around(symbol,tf,int(around),durations[tf])
        raw=self.store.bars(symbol,self.now()-HISTORY_DAYS*DAY)
        bars=aggregate(raw,durations[tf])
        rsi,_,_=indicators(bars,durations[tf])
        ema=ema_series(bars,TREND_EMA,durations[tf]) if bars else []
        setups=self._chart_setups(symbol,raw)
        known={s['id'] for s in setups}
        setups=setups+[s for s in self.store.records('signal',100) if s['symbol']==symbol and s['id'] not in known]
        from .model import current_levels
        levels=current_levels(raw)
        selected=bars[-{'15m':3000,'1h':2400,'4h':600}[tf]:]
        times={b['t'] for b in selected}
        return {'symbol':symbol,'tf':tf,'source':'Binance USDT perpetuals','history_days':HISTORY_DAYS,'candles':selected,
                'rsi':[{'time':b['t'],'value':v} for b,v in zip(bars,rsi) if v is not None and b['t'] in times],
                'ema50':[{'time':b['t'],'value':v} for b,v in zip(bars,ema) if v is not None and b['t'] in times],
                'levels':levels,'setups':setups,'read_only':True}

    def _chart_around(self,symbol,tf,around,duration):
        """Candles around a past moment (for inspecting a backtest trade): about 350 bars before and 250 after, with
        RSI/EMA warmed on 60 days before it and the major 4h levels as they were known at that moment."""
        from .model import aggregate,indicators,ema_series,current_levels,TREND_EMA
        raw=[b for b in self.store.bars(symbol,around-60*DAY) if b['t']<=around+40*DAY]
        bars=aggregate(raw,duration)
        if not bars:raise ValueError('No stored history at that time')
        rsi,_,_=indicators(bars,duration);ema=ema_series(bars,TREND_EMA,duration)
        k=min(range(len(bars)),key=lambda i:abs(bars[i]['t']-around))
        lo,hi=max(0,k-350),min(len(bars),k+250)
        return {'symbol':symbol,'tf':tf,'source':'Binance USDT perpetuals','around':around,'candles':bars[lo:hi],
                'rsi':[{'time':b['t'],'value':v} for b,v in zip(bars[lo:hi],rsi[lo:hi]) if v is not None],
                'ema50':[{'time':b['t'],'value':v} for b,v in zip(bars[lo:hi],ema[lo:hi]) if v is not None],
                'levels':current_levels([b for b in raw if b['t']+900_000<=around]),'setups':[],'read_only':True}

    def _chart_setups(self,symbol,raw):
        """All setups in the stored history (cached per last candle), with the replayed outcome of confirmed ones."""
        from .model import analyze
        from .backtest import backtest
        if not raw:return []
        cache=getattr(self,'_chart_cache',None)
        if cache is None:cache=self._chart_cache={}
        hit=cache.get(symbol)
        if hit and hit[0]==raw[-1]['t']:return hit[1]
        setups=analyze(raw,symbol,raw[-1]['t']+900_000).get('setups',[])
        for s in setups:
            if s.get('status')!='CONFIRMED':continue
            out=backtest(raw,[s],[],start_equity=1000,start_ms=s['confirmed_at'])
            if out['trades']:
                t=out['trades'][0];risk=t['quantity']*abs(t['entry']-t['stop'])
                s['outcome']={'entry_t':t['entry_t'],'entry':t['entry'],'stop':t['stop'],'target':t['target'],'exit_t':t['exit_t'],
                              'exit':t['exit'],'reason':'open' if t['reason']=='period_end' else t['reason'],
                              'r':t['net']/risk if risk else None,'funding':'not included'}
            else:
                s['outcome']={'reason':out['skipped'][0]['reason'] if out['skipped'] else 'no fill'}
        cache[symbol]=(raw[-1]['t'],setups)
        return setups
