from __future__ import annotations
import multiprocessing as mp
import os, threading, time
from .store import Store, snapshot

class ResearchService:
    def __init__(self,path,env=None):
        e=os.environ if env is None else env
        self.enabled=e.get('AUTORESEARCH_ENABLED','false').lower() in ('true','1','yes')
        self.cfg={'db':str(path),'max_bots':max(1,min(12,int(e.get('AUTORESEARCH_MAX_PAPER_BOTS','8'))))}
        self.stopping=threading.Event();self.proc=None;self.stop_event=None;self.thread=None;self.restarts=0
        s=Store(path);s.close()
    def start(self):
        if self.enabled:
            self._spawn();self.thread=threading.Thread(target=self._supervise,name='autoresearch-supervisor',daemon=True);self.thread.start()
    def _spawn(self):
        from .worker import worker_main
        ctx=mp.get_context('spawn');self.stop_event=ctx.Event()
        self.proc=ctx.Process(target=worker_main,args=(self.cfg,self.stop_event),name='market-research',daemon=True)
        self.proc.start();self.started=time.time()
    def _supervise(self):
        backoff=10
        while not self.stopping.wait(10):
            data=self.summary();age=data['health'].get('heartbeat_age_s')
            hung=(age is None or age>240) and time.time()-self.started>240
            if self.proc is not None and (not self.proc.is_alive() or hung):
                if self.proc.is_alive():self.proc.terminate();self.proc.join(3)
                if self.stopping.wait(backoff):return
                self.restarts+=1;self._spawn();backoff=min(300,backoff*2)
            elif time.time()-self.started>600:backoff=10
    def stop(self):
        self.stopping.set()
        if self.stop_event:self.stop_event.set()
        if self.proc:
            self.proc.join(4)
            if self.proc.is_alive():self.proc.terminate();self.proc.join(2)
        if self.thread:self.thread.join(2)
    def summary(self):
        try:data=snapshot(self.cfg['db'])
        except Exception:
            data={'health':{'status':'STATE_STORE_ERROR'},'bots':[],'markets':[],'studies':[],'execution':'PAPER_ONLY','read_only':True}
        health=data['health'];hb=health.get('heartbeat_ms')
        health['enabled']=self.enabled;health['restarts']=self.restarts
        health['heartbeat_age_s']=round(max(0,time.time()-hb/1000),1) if hb else None
        if not self.enabled:health['status']='DISABLED'
        elif self.proc is not None and not self.proc.is_alive():health['status']='RESTARTING'
        elif hb and health['heartbeat_age_s']>180:health['status']='STALE'
        return data
