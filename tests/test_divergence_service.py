"""Accounting and runtime safety: no external network or model completions."""
import json
import pytest
from app.divergence import paper
from app.divergence.service import DivergenceService
from app.divergence.backtest import backtest
from app.divergence.model import BASE

T=1_800_000_000_000

def signal(now=T,side='long'):
    return {'id':'signal-'+side,'symbol':'BTCUSDT','side':side,'divergence_tf':'1h','trigger_tf':'15m','confirmed_at':now,'divergence_at':now-3600000,'entry_reference':100.,'stop':98. if side=='long' else 102.,'target':104. if side=='long' else 96.}

def quote(now=T,price=100):return {'bid':price,'ask':price,'ts':now}
def bar(t=T,o=100,h=101,l=99,c=100):return {'t':t,'o':o,'h':h,'l':l,'c':c,'v':100}

@pytest.mark.parametrize('side',['long','short'])
def test_forward_stop_risk_includes_fees_and_slippage_and_2r(side):
    b=paper.empty();s=signal(side=side)
    assert paper.open_position(b,s,quote(),{},T) is None
    p=b['open'][0]
    assert abs(p['target']-p['entry'])==pytest.approx(2*abs(p['entry']-p['stop']))
    now=T+120000
    stopbar=bar(T,h=103,l=97)
    t=paper.update_position(b,p,[stopbar],[],quote(now),now)
    assert t['reason']=='stop'
    assert -t['net']<=5.0000001
    assert b['cash']==pytest.approx(1000+t['net'])

def test_partial_entry_minute_no_unearned_target_and_conservative_stop():
    b=paper.empty();now=T+15000
    paper.open_position(b,signal(),quote(now),{},now)
    p=b['open'][0]
    assert paper.update_position(b,p,[bar(T,h=110)],[],quote(T+60001),T+60001) is None
    assert p in b['open']
    b=paper.empty();paper.open_position(b,signal(),quote(now),{},now);p=b['open'][0]
    t=paper.update_position(b,p,[bar(T,h=110,l=95)],[],quote(T+60001),T+60001)
    assert t['reason']=='entry_minute_ambiguous_stop' and t['net']<0

def test_gap_is_not_silently_walked_and_stale_quote_rejected():
    b=paper.empty();paper.open_position(b,signal(),quote(),{},T);p=b['open'][0]
    assert paper.update_position(b,p,[bar(T+60000,l=90)],[],quote(T+180000),T+180000) is None
    assert p['data_gap'] and p['last_bar']==T-60000
    with pytest.raises(ValueError,match='Stale'):
        paper.update_position(b,p,[],[],quote(T),T+180000)

def test_funding_charged_once_survives_json_and_books_reconcile():
    b=paper.empty();paper.open_position(b,signal(),quote(),{},T);p=b['open'][0]
    funds=[(T+1,.001,100)]
    paper.update_position(b,p,[bar()],funds,quote(T+60001),T+60001)
    after=b['cash'];b=json.loads(json.dumps(b));p=b['open'][0]
    paper.update_position(b,p,[],funds,quote(T+60002),T+60002)
    assert b['cash']==after and b['funding']<0
    trade=paper.update_position(b,p,[bar(T+60000,h=110)],funds,quote(T+120001),T+120001)
    assert b['cash']==pytest.approx(1000+trade['net'])

def test_expired_and_duplicate_symbol_cannot_open():
    b=paper.empty()
    assert 'expired' in paper.open_position(b,signal(),quote(T+900001),{},T+900001)
    assert paper.open_position(b,signal(),quote(),{},T) is None
    assert 'already' in paper.open_position(b,signal(),quote(),{},T)

def test_service_frozen_restart_and_interrupted_reviews(tmp_path):
    path=tmp_path/'desk.db';d=DivergenceService(path,env={'DIVERGENCE_ENABLED':'false'},clock=lambda:T/1000)
    d.constraints={'BTCUSDT':{'liquid':True,'constraints':{}}}
    d._enter('baseline',signal(),quote())
    d.store.record('review','signal-long',T,{'setup_id':'signal-long','ts':T,'status':'queued'})
    d.store.close()
    d=DivergenceService(path,env={'DIVERGENCE_ENABLED':'false'},clock=lambda:T/1000)
    assert len(d.books['baseline']['open'])==1
    assert d.store.has('execution','baseline:signal-long')
    assert d.store.records('review')[0]['status']=='unavailable'
    frozen=d.store.get('frozen_rules');frozen['rules']['reward_risk']=3;d.store.set('frozen_rules',frozen);d.store.close()
    d=DivergenceService(path,env={'DIVERGENCE_ENABLED':'true'})
    assert not d.enabled and d.health['status']=='RULE_VERSION_MISMATCH'
    assert len(d.books['baseline']['open'])==1
    d.store.close()

def test_backtest_boundary_plus_one_funding_and_missing_mark_flag():
    rows=[bar(T),bar(T+BASE,h=110)]
    s=signal()
    result=backtest(rows,[s],[(T+BASE+1,.001,100)])
    assert result['metrics']['funding']<0 and result['metrics']['ambiguous_funding_events']==1
    assert result['metrics']['funding_verified']
    missing=backtest(rows,[s],[(T+BASE+1,.001,0)])
    assert not missing['metrics']['funding_verified']

def test_public_route_is_get_only_and_validates_symbols(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core.api_divergence import router
    app=FastAPI();app.include_router(router)
    app.state.divergence=DivergenceService(tmp_path/'api.db',env={'DIVERGENCE_ENABLED':'false'})
    with TestClient(app) as c:
        assert c.get('/api/public/divergence').json()['execution']=='PAPER_ONLY'
        assert c.post('/api/public/divergence').status_code==405
        assert c.get('/api/public/divergence/chart?symbol=WRONG').status_code==400
    app.state.divergence.store.close()
