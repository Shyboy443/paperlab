import gzip,hashlib,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.api_value_reversal import router
from app.value_reversal.public_view import report_view
from app.value_reversal.crypto_research import RULE

@pytest.fixture
def report():
    wallet={'return_pct':12.3,'trades':1,'curve':[[1,100],[2,112.3]],'monthly':[{'month':'2025-01','net_pct':12.3}],
            'signals':[{'ts':1735772400000,'side':1}],'recent_trades':[{'net':12.3,'entry_ts':1}], 'entry_audit':{'entered':1}}
    return {'rule':RULE,'source_sha256':{n:hashlib.sha256((Path('app/value_reversal')/n).read_bytes()).hexdigest()
            for n in ('crypto_data.py','crypto_indicator.py','crypto_research.py')},
            'results':[{'key':key,'periods':{'comparison':{'net':wallet,'stress':wallet}}} for key in ('A','B')]}

def test_projection_preserves_metrics_and_selected_evidence(report):
    projected=report_view(report,'A',True)
    for old,row in zip(report['results'],projected['results']):
        for mode in ('net','stress'):
            before=old['periods']['comparison'][mode];after=row['periods']['comparison'][mode]
            assert after['return_pct']==before['return_pct'] and after['trades']==1
            assert after['entry_audit']==before['entry_audit']
            assert ('curve' in after)==(row['key']=='A')
            if row['key']=='A':
                assert after['curve']==before['curve'] and after['monthly']==before['monthly']
                # 18:00 NY belongs to the new session; retain the same complete date picker.
                assert after['session_dates']==['2025-01-01']
    assert report['results'][0]['periods']['comparison']['net']['signals']

def test_small_crypto_api_retains_full_download_and_rejects_writes(tmp_path,report):
    with gzip.open(tmp_path/'crypto-value-reversal-v1-report.json.gz','wt') as f:json.dump(report,f)
    app=FastAPI();app.state.value_reversal=SimpleNamespace(path=tmp_path/'qqq.json');app.include_router(router)
    c=TestClient(app);r=c.get('/api/public/value-reversal/crypto/view?key=A')
    assert r.status_code==200 and r.json()['dashboard_projection']
    assert c.get('/api/public/value-reversal/crypto/view?key=unknown').status_code==400
    full=c.get('/api/public/value-reversal/crypto').json()
    assert full==report
    assert c.post('/api/public/value-reversal/crypto/view?key=A').status_code==405
    report['source_sha256']['crypto_indicator.py']='bad'
    with gzip.open(tmp_path/'crypto-value-reversal-v1-report.json.gz','wt') as f:json.dump(report,f)
    assert c.get('/api/public/value-reversal/crypto/view?key=A').status_code==400
