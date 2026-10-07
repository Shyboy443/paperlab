import json,math,threading
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.autoresearch import model
from app.autoresearch.client import Client, RateLimit
from app.autoresearch.model import HOUR, DAY, Rule, closed_bars, features, signal, replay, study
from app.autoresearch.store import Store, snapshot
from app.autoresearch.service import ResearchService
from app.autoresearch.worker import paper_tick, cooldown
from app.core.api_autoresearch import router

def series(n=2160):
    out=[]
    for i in range(n):
        o=100+i*.003+math.sin(i*.13)*3;c=100+(i+1)*.003+math.sin((i+1)*.13)*3
        out.append([i*HOUR,o,max(o,c)+.2,min(o,c)-.2,c,200])
    return out

def test_forming_malformed_and_duplicate_bars_are_filtered():
    rows=series(4)+[[4*HOUR,1,2,0,1,5],[2*HOUR,10,12,9,11,4],[HOUR+1,1,2,1,1,4]]
    result=closed_bars(rows,3*HOUR+1)
    assert [b[0] for b in result]==[0,HOUR,2*HOUR]
    assert result[-1][4]==11

def test_context_is_causal_and_incomplete_groups_are_not_used():
    bars=series(100);f=features(bars)
    assert all(x['hf'] is None for x in f[:3])
    assert f[3]['context_close']==4*HOUR
    assert f[4]['context_close']==4*HOUR
    assert features(bars[:40])==f[:40]
    missing=features(bars[:3]+bars[4:8])
    assert missing[2]['hf'] is None

def test_spot_never_shorts():
    bars=series(500);fs=features(bars)
    for rule in model.RULES:
        for i in range(240,len(bars)):
            s=signal(bars,fs,i,rule,'spot')
            assert s is None or s['side']==1

def test_holdout_cannot_influence_rule_selection():
    bars=series();other=[b[:] for b in bars]
    for b in other[int(len(bars)*.8):]:b[1:5]=[x*1.8 for x in b[1:5]]
    a=study(bars,'perp');b=study(other,'perp')
    assert a['rule_key']==b['rule_key'] and a['train']==b['train'] and a['validation']==b['validation']
    assert a['split']['train'][1]==a['split']['validation'][0]
    assert a['split']['validation'][1]==a['split']['holdout'][0]

def test_data_gap_and_short_history_reject():
    assert study(series(400),'spot')['state']=='INSUFFICIENT_HISTORY'
    b=series();del b[1000]
    assert study(b,'spot')['state']=='DATA_GAP'

def test_stop_has_priority_and_gaps_fill_through():
    p={'side':1,'stop':95,'target':110,'held':1}
    assert model.exit_price(p,[0,90,115,85,100,1],24)==(90,'stop')
    p={'side':-1,'stop':105,'target':90,'held':1}
    assert model.exit_price(p,[0,111,115,85,100,1],24)==(111,'stop')

def test_funding_and_costs_reduce_returns():
    bars=series();fs=features(bars);rule=Rule('pullback',20,2.5)
    normal=replay(bars,fs,rule,'perp',240,len(bars))
    stress=replay(bars,fs,rule,'perp',240,len(bars),multiplier=2)
    assert stress['net']<=normal['net']
    p=model.entry({'side':1,'distance':2},100,100,.001,.001);p['funding']=1
    assert model.settle(p,100,.001,.001)<-1

def test_minimum_notional_and_quantity_steps_are_enforced():
    sig={'side':1,'distance':2}
    assert model.entry(sig,100,100,.001,.001,{'min_notional':100}) is None
    p=model.entry(sig,100,1000,.001,.001,{'step':.3,'min_qty':.3,'min_notional':10})
    assert p is not None and p['qty']==pytest.approx(2.4)
    assert model.entry(sig,100,1000,.001,.001,{'min_qty':100}) is None
    ticked=model.entry(sig,100.15,1000,.001,0,{'tick':.5})
    assert ticked['stop']==98 and ticked['target']==104
    assert ticked['qty']*ticked['distance']<=5.000001

def test_round_robin_persistence_and_no_failed_admissions(tmp_path):
    path=tmp_path/'ar.db';s=Store(path);now=100*DAY
    rows=[{'symbol':x,'volume':v,'spread':1,'price':100,'liquid':True} for x,v in [('BTCUSDT',1e8),('ETHUSDT',1e7)]]
    s.markets('spot',rows,now);assert s.next_market(now)['symbol']=='BTCUSDT'
    s.record_study('spot:BTCUSDT',{'state':'REJECTED'},now)
    assert s.next_market(now)['symbol']=='ETHUSDT'
    assert not s.admit('spot:ETHUSDT',{'state':'REJECTED'},now)
    s.close();s=Store(path);assert s.next_market(now)['symbol']=='ETHUSDT';s.close()

def qualified():return {'state':'HISTORICALLY_QUALIFIED','rule':Rule('breakout',20,1.5).data(),'rule_key':'test'}

def test_paper_admission_cap_and_stored_rule_is_immutable(tmp_path):
    s=Store(tmp_path/'a.db');assert s.admit('spot:AUSDT',qualified(),100*DAY,max_bots=1)
    assert not s.admit('perp:BUSDT',qualified(),100*DAY,max_bots=1)
    assert not s.admit('spot:AUSDT',qualified(),101*DAY,max_bots=1)
    assert len(s.paper_bots())==1;s.close()

def test_no_historical_catchup_entry_and_atomic_exit(tmp_path,monkeypatch):
    s=Store(tmp_path/'a.db');now=100*DAY+HOUR+600_000
    s.markets('spot',[{'symbol':'AUSDT','volume':1e8,'spread':1,'price':100,'liquid':True}],now)
    s.admit('spot:AUSDT',qualified(),now-HOUR);b=s.paper_bots()[0]
    bars=[[now//HOUR*HOUR-HOUR,100,101,99,100,1]]
    class Fake:
        def history(self,*args):return bars
        def quote(self,*args):return {'bid':100,'ask':100.01,'ts':now}
    monkeypatch.setattr('app.autoresearch.worker.signal',lambda *a:{'side':1,'distance':2,'signal_ts':bars[-1][0]+HOUR})
    paper_tick(s,Fake(),b,now);assert s.paper_bots()[0]['position'] is None
    p=model.entry({'side':1,'distance':2},100,100,.001,.0003);p['opened_ts']=now-2*HOUR
    b=s.paper_bots()[0];b['position']=json.dumps(p);s.update_bot(b)
    class Stop(Fake):
        def quote(self,*args):return {'bid':90,'ask':90.01,'ts':now}
    paper_tick(s,Stop(),b,now)
    assert s.db.execute('SELECT COUNT(*) FROM paper_trades').fetchone()[0]==1
    assert s.paper_bots()[0]['position'] is None
    paper_tick(s,Stop(),s.paper_bots()[0],now)
    assert s.db.execute('SELECT COUNT(*) FROM paper_trades').fetchone()[0]==1;s.close()

def test_public_client_cannot_access_orders_or_authenticated_endpoints():
    c=Client(threading.Event(),opener=lambda *a,**k:pytest.fail('Network should not be reached'))
    for endpoint in ['order','account','listenKey','../order']:
        with pytest.raises(ValueError):c.get('perp',endpoint)
    with pytest.raises(ValueError):c.get('spot','fundingRate')

def test_offline_service_and_readonly_public_route(tmp_path):
    service=ResearchService(tmp_path/'a.db',env={});service.start();assert service.proc is None
    app=FastAPI();app.state.research=service;app.include_router(router)
    with TestClient(app) as client:
        data=client.get('/api/public/research').json()
        assert data['health']['status']=='DISABLED' and data['execution']=='PAPER_ONLY'
        assert client.post('/api/public/research').status_code==405
    service.stop()

def test_rate_limit_cooldown_honors_stop_without_restart(tmp_path):
    s=Store(tmp_path/'a.db');stop=threading.Event();stop.set()
    assert cooldown(s,stop,300)
    assert s.get('status')=='RATE_LIMITED' and s.get('heartbeat_ms')>0;s.close()
