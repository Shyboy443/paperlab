"""Package actual-source verification and separately labelled default-input retests."""
import hashlib,json
from pathlib import Path
root=Path('app/value_reversal');output=root/'sources'/'user-source-verification.json'
v=json.loads(Path('data/user-source-retest.json').read_text(encoding='utf-8'))
source_sha=hashlib.sha256((root/'sources'/'user-provided.pine').read_bytes()).hexdigest()
if v['provided_source_sha256']!=source_sha:raise ValueError('Source changed after retest')
if not v['matches_existing_results'] or v['wallet_evaluations']!=56:raise ValueError('Incomplete wallet retest')
v['pine_checks']={}
for name,file in [('crypto','data/user-crypto-pine-verified.json'),('qqq','data/user-qqq-pine-verified.json')]:
    p=json.loads(Path(file).read_text(encoding='utf-8'))
    if p['source_sha256']!=source_sha or any(j['mismatches'] for j in p['jobs']):raise ValueError('Pine source check failed')
    v['pine_checks'][name]=p
v['level_and_signal_checks']=sum(j['level_and_signal_checks'] for p in v['pine_checks'].values() for j in p['jobs'])
for name,file in [('crypto_code_sha256','research/crypto_value_reversal/results.json'),('qqq_code_sha256','research/qqq_value_reversal/results.json')]:
    v[name]=json.loads(Path(file).read_text(encoding='utf-8'))['source_sha256']
    for file,digest in v[name].items():
        if hashlib.sha256((root/file).read_bytes()).hexdigest()!=digest:raise ValueError('Backtest implementation changed')
v['default_verdicts']={}
for r in v['results']:
    p=r['periods']['comparison'];reason=[]
    if p['net']['return_pct']<=0:reason.append('Nonpositive comparison net return')
    if p['stress']['return_pct']<=0:reason.append('Loses under doubled costs')
    if p['net']['max_dd_pct']>=20:reason.append('20% drawdown limit reached')
    if p['net']['trades']<30:reason.append('Fewer than 30 comparison trades')
    v['default_verdicts'][r['key']]={'verdict':'FAIL' if reason else 'RETROSPECTIVE_CHECKS_PASS_UNPROVEN','reasons':reason}
output.write_text(json.dumps(v,indent=2,allow_nan=False),encoding='utf-8')
print('Packaged',v['level_and_signal_checks'],'actual supplied-Pine checks and',v['wallet_evaluations'],'wallet retests')
for r in v['results']:
    p=r['periods']['comparison'];n=p['net']
    print(r['key'],'arrows',n['signal_count'],'trades',n['trades'],'net',round(n['return_pct'],4),'stress',round(p['stress']['return_pct'],4))
