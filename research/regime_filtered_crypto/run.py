"""Freeze source/input hashes, run predetermined cases, never optimize on OOS."""
from __future__ import annotations
import argparse,hashlib,json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict,replace
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import pandas as pd
from model import Config,DailyData
from engine import Engine,MarketBook
from reuse import validated_reuse

HERE=Path(__file__).resolve().parent
PERIODS={'IS':('2019-01-01','2022-01-01'),'OOS':('2022-01-01','2026-01-01')}
SEEDS=[11,29,47,71,101]
RUNTIME_SOURCES={name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in ['run.py','engine.py','model.py']}

def metrics(history,starting,start,end):
    f=history.set_index(pd.to_datetime(history.timestamp,utc=True))
    daily=f.equity.resample('D',closed='right',label='left').last().dropna()
    r=daily.pct_change(fill_method=None);r.iloc[0]=daily.iloc[0]/starting-1
    month=daily.resample('ME').last().pct_change(fill_method=None)
    month.iloc[0]=daily.resample('ME').last().iloc[0]/starting-1
    values=history.equity.to_numpy();peaks=np.maximum.accumulate(np.r_[starting,values])[1:]
    dd=values/peaks-1;maximum=float(-dd.min());days=(pd.Timestamp(end)-pd.Timestamp(start)).days
    annual=(values[-1]/starting)**(365/days)-1 if values[-1]>0 else -1.
    vol=float(r.std(ddof=1));down=float(np.sqrt(np.mean(np.minimum(r.to_numpy(),0)**2)))
    m={'monthly_average_net_return':float(month.mean()),'annualized_return':float(annual),
       'total_return':float(values[-1]/starting-1),'end_equity':float(values[-1]),
       'sharpe':float(r.mean()/vol*np.sqrt(365)) if vol>0 else None,
       'sortino':float(r.mean()/down*np.sqrt(365)) if down>0 else None,
       'max_drawdown':maximum,'calmar':float(annual/maximum) if maximum>0 else None,
       'worst_month':month.idxmin().strftime('%Y-%m'),'worst_month_return':float(month.min()),
       'best_month':month.idxmax().strftime('%Y-%m'),'best_month_return':float(month.max()),
       'positive_month_fraction':float((month>0).mean()),'inactive_months':int((month==0).sum()),
       'annualized_daily_vol':vol*np.sqrt(365),'days':days}
    for k in ['fees','slippage','funding_cash','transfer_fees']:
        if k in history:m[k]=float(history[k].iloc[-1])
    for k in ['gross_exposure','max_momentum_weight']:
        if k in history:m['max_'+k]=float(history[k].max())
    if 'gross_exposure' in history:
        m['average_gross_exposure']=float(history.gross_exposure.mean())
        m['hours_above_gross_cap']=int((history.gross_exposure>1.5+1e-6).sum())
    return m,daily,month

def reconcile(result):
    o=result['orders'];h=result['history'];pnl=0.
    if len(o):
        mask=o.kind=='momentum';hedge=~mask
        pnl=-float(np.sum(o.loc[mask,'delta_contract_units']*o.loc[mask,'perp_reference']))
        pnl+=float(np.sum(o.loc[hedge,'delta_contract_units']*o.loc[hedge,'perp_reference']))
        pnl-=float(np.sum(o.loc[hedge,'delta_contract_units']*o.loc[hedge,'spot_reference_per_contract_unit']))
        for (s,k),g in o.groupby(['symbol','kind']):
            if abs(g.delta_contract_units.sum())>1e-6:raise AssertionError('Not flat '+s+' '+k)
    expected=result['config']['starting_equity']+pnl+h.funding_cash.iloc[-1]-h.fees.iloc[-1]-h.slippage.iloc[-1]-h.transfer_fees.iloc[-1]
    error=float(h.equity.iloc[-1]-expected)
    if abs(error)>1e-5:raise AssertionError('Independent ledger reconciliation failed: '+str(error))
    return error

def snapshot(root,out):
    files=[]
    for folder in ['daily','hourly','mark_hourly','funding','spot_daily','spot_hourly']:
        files.extend(sorted((root/folder).glob('*.csv')))
    files.extend(root/k for k in ['metadata.json','daily_manifest.json','intraday_manifest.json','spot_manifest.json','spot_metadata.json','btc_spot.csv'])
    def pin(p):return p.relative_to(root).as_posix(),hashlib.sha256(p.read_bytes()).hexdigest()
    with ThreadPoolExecutor(max_workers=8) as pool:inputs=dict(pool.map(pin,files))
    source={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob('*.py') if p.name!='report.py'}
    for name,value in RUNTIME_SOURCES.items():
        if source[name]!=value:raise RuntimeError('Source changed after import; restart before evaluation: '+name)
    source['PROTOCOL.md']=hashlib.sha256((HERE/'PROTOCOL.md').read_bytes()).hexdigest()
    source['requirements-lock.txt']=hashlib.sha256((HERE/'requirements-lock.txt').read_bytes()).hexdigest()
    core={'default':asdict(Config()),'periods':PERIODS,'lookbacks':[42,63,90],'breadths':[.5,.7],
          'seeds':SEEDS,'source_sha256':source,'input_sha256':inputs,
          'selection_sha256':hashlib.sha256((out/'required_months.json').read_bytes()).hexdigest()}
    digest=hashlib.sha256(json.dumps(core,sort_keys=True).encode()).hexdigest()[:16]
    path=out/('run_'+digest);path.mkdir(exist_ok=True)
    pinfile=path/'pin.json'
    if not pinfile.exists():pinfile.write_text(json.dumps({**core,'fingerprint':digest,'frozen_at':datetime.now(timezone.utc).isoformat()},indent=2))
    return path

def benchmark(root,start,end,btc=True):
    dates=pd.date_range(start,pd.Timestamp(end)-pd.Timedelta(days=1),tz='UTC')
    equity=np.full(len(dates),100000.)
    if btc:
        f=pd.read_csv(root/'btc_spot.csv');f.index=pd.to_datetime(f.timestamp,unit='ms',utc=True)
        f=f.reindex(dates)
        if f.close.isna().any():raise RuntimeError('Missing BTC benchmark bars')
        qty=100000/(f.open.iloc[0]*(1+.0004+.0002))
        equity=qty*f.close.to_numpy();equity[-1]*=1-.0004-.0002
    # Daily bars timestamped at the following midnight, matching strategy curve.
    return pd.DataFrame({'timestamp':dates+pd.Timedelta(days=1),'equity':equity})

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,default=HERE/'output')
    p.add_argument('--smoke',action='store_true',help='One IS run for execution/data diagnostics; no parameter tuning')
    p.add_argument('--reuse-from',type=Path,help='Reuse independently verified identical books; validate frozen inputs, engine and metric code first')
    a=p.parse_args();daily=DailyData(a.data);book=MarketBook(daily,a.output/'required_months.json')
    a.output.mkdir(exist_ok=True);(a.output/'data_quality.json').write_text(json.dumps(book.quality,indent=2))
    path=snapshot(a.data,a.output);print('Frozen run',path.name,flush=True)
    reusable=validated_reuse(a.reuse_from,path,HERE) if a.reuse_from else {}
    rows=[];all_monthly=[]
    cases={};default=Config()
    main_cases={'combined':default,'combined_no_regime':replace(default,regime=False),
                'momentum_regime':replace(default,harvest=False),'momentum_no_regime':replace(default,harvest=False,regime=False),
                'funding_only':replace(default,momentum=False)}
    def case(name,period,cfg):
        start,end=PERIODS[period];key=(period,json.dumps(asdict(cfg),sort_keys=True))
        tag=name+'_'+period;destination=path/tag
        if key in cases:
            m,di,mo,reference=cases[key]
        else:
            if not destination.exists() and tag in reusable:
                import shutil
                shutil.copytree(reusable[tag],destination)
                print('Reused hash-validated identical book',tag,flush=True)
            destination.mkdir(exist_ok=True);saved=destination/'metrics.json'
            if saved.exists():
                m=json.loads(saved.read_text());di=pd.read_csv(destination/'daily_equity.csv',index_col=0).iloc[:,0]
                mo=pd.read_csv(destination/'monthly_returns.csv',index_col=0).iloc[:,0]
                di.index=pd.to_datetime(di.index,utc=True);mo.index=pd.to_datetime(mo.index,utc=True)
            else:
                result=Engine(book,cfg).run(start,end);error=reconcile(result)
                m,di,mo=metrics(result['history'],cfg.starting_equity,start,end)
                m.update(kill_time=result['kill_time'],kill_reason=result['kill_reason'],terminal_proxies=result['terminal_proxies'],
                         cap_actions=result['cap_actions'],ledger_error=error,order_count=len(result['orders']),config=asdict(cfg))
                for k in ['history','orders','trades']:result[k].to_csv(destination/(k+'.csv.gz'),index=False,compression='gzip')
                di.to_csv(destination/'daily_equity.csv');mo.to_csv(destination/'monthly_returns.csv')
                saved.write_text(json.dumps(m,indent=2))
            reference=tag;cases[key]=(m,di,mo,reference)
        row={k:v for k,v in m.items() if k!='config'};row.update(case=name,period=period,reference=reference)
        rows.append(row)
        for d,v in mo.items():all_monthly.append({'case':name,'period':period,'month':d.strftime('%Y-%m'),'return':float(v)})
        print('Completed',name,period,'orders',m.get('order_count'),flush=True)
        return m
    if a.smoke:
        case('diagnostic_combined','IS',default);book.close();print('Smoke execution complete',path,flush=True);return
    for name,cfg in main_cases.items():
        for period in PERIODS:
            case(name,period,cfg)
            case(name+'_gross',period,replace(cfg,fee=0,slippage=0,transfer_fee=0))
    for period in PERIODS:case('combined_without_funding',period,replace(default,funding=False))
    for l in [42,63,90]:
        for b in [.5,.7]:
            for period in PERIODS:case(f'sensitivity_L{l}_B{int(b*100)}',period,replace(default,lookback=l,breadth=b))
    for seed in SEEDS:
        for period in PERIODS:case('seed_'+str(seed),period,replace(default,seed=seed))
    for btc,name in [(True,'BTC_buy_hold'),(False,'USDT_cash')]:
        for period,(start,end) in PERIODS.items():
            history=benchmark(a.data,start,end,btc);m,di,mo=metrics(history,100000,start,end)
            dest=path/(name+'_'+period);dest.mkdir(exist_ok=True);history.to_csv(dest/'history.csv',index=False)
            di.to_csv(dest/'daily_equity.csv');mo.to_csv(dest/'monthly_returns.csv');m['drawdown_resolution']='daily'
            (dest/'metrics.json').write_text(json.dumps(m,indent=2));rows.append({**m,'case':name,'period':period,'reference':name+'_'+period})
            for d,v in mo.items():all_monthly.append({'case':name,'period':period,'month':d.strftime('%Y-%m'),'return':float(v)})
    summary=pd.DataFrame(rows);summary.to_csv(path/'metrics.csv',index=False)
    pd.DataFrame(all_monthly).to_csv(path/'monthly_returns.csv',index=False)
    def get(name):return summary[(summary.case==name)&(summary.period=='OOS')].iloc[0]
    m=get('combined');u=get('combined_no_regime');mr=get('momentum_regime');mu=get('momentum_no_regime')
    positives=sum(get('seed_'+str(s)).total_return>0 for s in SEEDS)
    acceptance={'monthly_mean_positive':bool(m.monthly_average_net_return>0),'max_drawdown_below_25_percent':bool(m.max_drawdown<.25),
       'regime_improves_combined_monthly_mean':bool(m.monthly_average_net_return>u.monthly_average_net_return),
       'regime_improves_pure_momentum_monthly_mean':bool(mr.monthly_average_net_return>mu.monthly_average_net_return),
       'positive_seed_count':int(positives),'at_least_four_positive_seeds':bool(positives>=4),
       'regime_improves_pure_momentum_return_and_drawdown':bool(mr.total_return>mu.total_return and mr.max_drawdown<mu.max_drawdown),
       'regime_improves_combined_return_and_drawdown':bool(m.total_return>u.total_return and m.max_drawdown<u.max_drawdown),
       'OOS_sensitivity_positive_count':int(((summary.period=='OOS')&summary.case.str.startswith('sensitivity_')&(summary.total_return>0)).sum()),
       'seed_interpretation':'same market sample, 5 distinct execution-cost perturbations; not independent evidence',
       'evaluation_integrity':'2022-2024 retrospective; 2025 added; no OOS tuning'}
    acceptance['passed']=all(acceptance[k] for k in ['monthly_mean_positive','max_drawdown_below_25_percent','at_least_four_positive_seeds',
        'regime_improves_combined_monthly_mean'])
    (path/'acceptance.json').write_text(json.dumps(acceptance,indent=2));(a.output/'latest_run.txt').write_text(path.name)
    book.close()
    print('COMPLETE',path,flush=True);print(json.dumps(acceptance,indent=2),flush=True)

if __name__=='__main__':main()
