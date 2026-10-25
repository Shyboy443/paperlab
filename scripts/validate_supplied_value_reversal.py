"""Rerun supplied-code defaults, refusing stale or different-source feature caches.

This verifies an identical source, not an automatic translator for arbitrary Pine.
No historical reports, forward books, credentials or broker settings are overwritten.
"""
import argparse,gzip,hashlib,inspect,json,sys
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.value_reversal.crypto_research import Config,features,simulate,PERIODS,stamp
from app.value_reversal.model import Config as QConfig,aggregate,backtest
from app.value_reversal.research import clean

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def identical(a,b,path='result'):
    if isinstance(a,dict):
        if set(a)!=set(b):raise AssertionError('Different fields: '+path)
        for k,v in a.items():identical(v,b[k],path+'.'+k)
    elif isinstance(a,list):
        if len(a)!=len(b):raise AssertionError('Different length: '+path)
        for i,(x,y) in enumerate(zip(a,b)):identical(x,y,path+'.'+str(i))
    elif isinstance(a,float):
        if b is None or abs(a-b)>1e-7:raise AssertionError('Numerical mismatch: '+path)
    elif a!=b:raise AssertionError('Mismatch: '+path)

def run(source,data_root,output):
    stored=Path('app/value_reversal/sources/value-area-reversion-official.pine')
    if source.read_bytes()!=stored.read_bytes():
        raise ValueError('Supplied code differs; existing translated-feature cache must not be reused')
    report=json.loads(Path('research/crypto_value_reversal/results.json').read_text())
    for n,digest in report['source_sha256'].items():
        if sha(Path('app/value_reversal')/n)!=digest:raise ValueError('Changed research code: '+n)
    provenance=report['profile_cache_provenance']
    if hashlib.sha256(inspect.getsource(features).encode()).hexdigest()!=provenance['unchanged_features_function_sha256']:
        raise ValueError('Changed profile generator')
    cache_identity={(r['symbol'],r['minutes']):r['sha256'] for r in provenance['verified_cached_files']}
    with gzip.open(data_root/'input.json.gz','rt') as f:data=json.load(f)
    verified={'provided_source_sha256':sha(source),'published_source_sha256':sha(stored),'byte_identical':True,
              'source_lines':len(source.read_text(encoding='utf-8-sig').splitlines()),
              'source_type':'indicator; no strategy orders, stop or exit implementation',
              'source_defaults':{'rows':60,'value_area_pct':70,'max_outside_bars':10,'previous_signals':True,
                                 'developing_signals':False,'anchor':'18:00 America/New_York'},
              'scope':'Actual supplied Pine re-executed in source parity; complete histories rerun using hash-verified identical translated signal features.',
              'limitations':['Source parity checks are on QQQ 2024–2025 and selected crypto sessions spanning both DST changes, not all four years of crypto candles.',
                             'QQQ is the approved regular-hours ETF adaptation; crypto uses 24/7 NY18 sessions.',
                             'PnL uses the disclosed next-open, excursion-stop, opposite-value-area-target execution model; these are not orders defined by the Pine indicator.',
                             '2024–2025 is a retrospective comparison, not an unseen holdout. No setting is promoted from this grid.'],
              'execution_assumptions':{'initial_usd':10000,'risk_pct':.5,'notional_cap_pct':100,'drawdown_halt_pct':20,
                  'crypto_fee_bps_per_side':4,'crypto_slippage_bps_per_side':2,'crypto_funding':'actual historical',
                  'qqq_fee_bps_per_side':1,'qqq_slippage_bps_per_side':2,'stress_cost_multiplier':2},
              'checked_at':datetime.now(timezone.utc).isoformat(),'results':[],'wallet_evaluations':0}
    for symbol in data['symbols']:
        for minutes in (15,30):
            path=data_root/f'{symbol}-{minutes}m-features.json.gz'
            if sha(path)!=cache_identity[symbol,minutes]:raise ValueError('Changed feature cache')
            with gzip.open(path,'rt') as f:rows=json.load(f)
            cfg=Config(symbol,minutes,'previous',10)
            old=next(r for r in report['results'] if r['key']==cfg.key)
            result={'key':cfg.key,'market':symbol,'minutes':minutes,'profile':'previous','outside_bars':10,'periods':{}}
            for label,(start,end) in PERIODS.items():
                result['periods'][label]={}
                for kind,c in [('net',cfg),('without_funding',replace(cfg,funding=False)),
                               ('gross',replace(cfg,fee_bps=0,slip_bps=0,funding=False)),('stress',replace(cfg,fee_bps=8,slip_bps=4))]:
                    n=simulate(rows,c,data['symbols'][symbol]['funding'],stamp(start),stamp(end))
                    identical(n,old['periods'][label][kind]);verified['wallet_evaluations']+=1
                    result['periods'][label][kind]={k:v for k,v in n.items() if k not in ('curve','signals','recent_trades')}
            verified['results'].append(result);print('Re-tested supplied defaults',cfg.key,flush=True)
    q=json.loads(Path('research/qqq_value_reversal/results.json').read_text())
    with gzip.open('data/qqq-value-reversal-input.json.gz','rt') as f:qd=json.load(f)
    for n,digest in q['source_sha256'].items():
        if sha(Path('app/value_reversal')/n)!=digest:raise ValueError('Changed frozen QQQ code')
    for minutes in (15,30):
        cfg=QConfig(minutes=minutes,max_reversion_bars=10);groups=aggregate(qd['bars'],qd['sessions'],minutes)
        old=next(r for r in q['results'] if r['key']==cfg.key)
        result={'key':cfg.key,'market':'QQQ','minutes':minutes,'profile':'previous','outside_bars':10,'periods':{}}
        for label,(a,z) in PERIODS.items():
            start,end=stamp(a),stamp(z)
            chosen=[g for g in groups if g['close_ms']<start][-1:]+[g for g in groups if start<=g['open_ms']<end]
            if any(not g['complete'] for g in chosen):raise ValueError('Incomplete QQQ history')
            result['periods'][label]={}
            for kind,c in [('net',cfg),('stress',replace(cfg,fee_bps=2,slip_bps=4))]:
                n=clean(backtest(chosen,c,start,end));identical(n,old['periods'][label][kind]);verified['wallet_evaluations']+=1
                result['periods'][label][kind]={k:v for k,v in n.items() if k not in ('curve','signals','recent_trades')}
        verified['results'].append(result);print('Re-tested supplied defaults',cfg.key,flush=True)
    verified['matches_existing_results']=True
    verified['crypto_code_sha256']=report['source_sha256']
    verified['qqq_code_sha256']=q['source_sha256']
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(verified,indent=2,allow_nan=False),encoding='utf-8')
    return verified

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('app/value_reversal/sources/user-provided.pine'))
    p.add_argument('--data',type=Path,default=Path('data/crypto_value_reversal'))
    p.add_argument('--output',type=Path,default=Path('data/user-source-retest.json'))
    a=p.parse_args();r=run(a.source,a.data,a.output);print('Matched all',r['wallet_evaluations'],'historical cost/period wallets',flush=True)
