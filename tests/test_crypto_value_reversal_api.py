import gzip,hashlib,json
from pathlib import Path
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.api_value_reversal import router
from app.value_reversal.crypto_research import RULE

def client(tmp_path,valid=True):
    folder=Path('app/value_reversal')
    report={'rule':RULE,'source_sha256':{n:hashlib.sha256((folder/n).read_bytes()).hexdigest()
                for n in ('crypto_data.py','crypto_indicator.py','crypto_research.py')},'results':[]}
    if not valid:report['source_sha256']['crypto_indicator.py']='bad'
    with gzip.open(tmp_path/'crypto-value-reversal-v1-report.json.gz','wt') as f:json.dump(report,f)
    app=FastAPI();app.state.value_reversal=SimpleNamespace(path=tmp_path/'qqq-value-reversal-v2.json',report=None)
    app.include_router(router)
    return TestClient(app)

def test_readonly_and_gzip_content_negotiation(tmp_path):
    c=client(tmp_path)
    r=c.get('/api/public/value-reversal/crypto',headers={'Accept-Encoding':'gzip'})
    assert r.status_code==200 and r.json()['rule']==RULE and r.headers['content-encoding']=='gzip'
    r=c.get('/api/public/value-reversal/crypto',headers={'Accept-Encoding':'identity'})
    assert r.status_code==200 and r.json()['rule']==RULE
    assert c.post('/api/public/value-reversal/crypto').status_code==405

def test_frozen_report_identity_enforced(tmp_path):
    c=client(tmp_path,False)
    assert c.get('/api/public/value-reversal/crypto').status_code==503

def test_replay_and_source_allowlists(tmp_path):
    c=client(tmp_path)
    assert c.get('/api/public/value-reversal/crypto/replay?key=NOT_A_MARKET&date=2024-01-01').status_code==400
    assert c.get('/api/public/value-reversal/source/key_vault.py').status_code==404
    assert c.get('/api/public/value-reversal/source/crypto_indicator.py').status_code==200

def test_audit_requires_matching_data(tmp_path):
    c=client(tmp_path)
    assert c.get('/api/public/value-reversal/arrow-audit').status_code==503

def test_actual_supplied_source_verification_is_bound_to_code(tmp_path):
    c=client(tmp_path)
    r=c.get('/api/public/value-reversal/source-verification')
    assert r.status_code==200
    p=r.json()
    assert p['byte_identical'] and p['level_and_signal_checks']==463584 and p['wallet_evaluations']==56
    assert len(p['results'])==8 and all(x['outside_bars']==10 for x in p['results'])
    import hashlib
    source=c.get('/api/public/value-reversal/source/user-provided.pine')
    assert source.status_code==200 and hashlib.sha256(source.content).hexdigest()==p['provided_source_sha256']

def test_supplied_verification_remains_read_only(tmp_path):
    c=client(tmp_path)
    assert c.post('/api/public/value-reversal/source-verification').status_code==405
