"""Run the preregistered 3x3 sensitivity grid and paired funding counterfactuals."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from model import Config, DailyData
from engine import Engine, HourlyData

PERIODS={'IS':('2019-01-01','2022-01-01'),'OOS':('2022-01-01','2025-01-01')}
HERE=Path(__file__).resolve().parent


def statistics(result,start,end):
    h=result['history']
    starting=result['config']['starting_equity']
    values=h.equity.to_numpy()
    frame=h.copy()
    frame.index=pd.to_datetime(frame.timestamp,utc=True)
    daily=frame.equity.resample('D',closed='right',label='left').last().dropna()
    returns=daily.pct_change(fill_method=None)
    returns.iloc[0]=daily.iloc[0]/starting-1
    monthly=daily.resample('ME').last().pct_change(fill_method=None)
    monthly.iloc[0]=daily.resample('ME').last().iloc[0]/starting-1
    days=(pd.Timestamp(end)-pd.Timestamp(start)).days
    annual=(values[-1]/starting)**(365/days)-1 if values[-1]>0 else -1
    peaks=np.maximum.accumulate(np.r_[starting,values])[1:]
    dd=values/peaks-1
    stdev=returns.std(ddof=1)
    downside=np.sqrt(np.mean(np.minimum(returns.to_numpy(),0)**2))
    trades=result['trades']
    metrics={'monthly_average_net_return':float(monthly.mean()),'annualized_return':float(annual),
             'total_net_return':float(values[-1]/starting-1),
             'sharpe':float(returns.mean()/stdev*np.sqrt(365)) if stdev>0 else None,
             'sortino':float(returns.mean()/downside*np.sqrt(365)) if downside>0 else None,
             'max_drawdown':float(-dd.min()),'calmar':float(annual/-dd.min()) if dd.min()<0 else None,
             'win_rate':float((trades.net>0).mean()) if len(trades) else None,
             'average_holding_days':float(trades.holding_days.mean()) if len(trades) else None,
             'closed_round_trips':len(trades),'end_equity':float(values[-1]),
             'funding_net_cost':float(h.cumulative_funding_cost.iloc[-1]),
             'fees':float(h.cumulative_fees.iloc[-1]),'slippage_cost':float(h.cumulative_slippage_cost.iloc[-1]),
             'worst_month':str(monthly.idxmin().date()),'worst_month_return':float(monthly.min()),
             'realized_annual_vol':float(stdev*np.sqrt(365)),
             'average_gross_exposure':float(h.gross_exposure.mean()),
             'max_gross_exposure':float(h.gross_exposure.max()),
             'max_position_weight':float(h.max_position_weight.max()),
             'first_trade':result['first_trade'],'risk_cap_actions':result['cap_actions'],
             'terminal_settlement_proxies':int((trades.exit_reason=='archival_terminal_settlement_proxy').sum()) if len(trades) else 0,
             'inactive_months':int((monthly==0).sum())}
    if len(trades):
        difference=values[-1]-starting-trades.net.sum()
        if abs(difference)>1e-5:
            raise AssertionError(f'Ledger conservation failed: {difference}')
    if result['config']['mode']!='buy_hold':
        if metrics['max_position_weight']>result['config']['position_cap']+1e-7:
            raise AssertionError('Position cap violation')
        if metrics['max_gross_exposure']>result['config']['gross_cap']+1e-7:
            raise AssertionError('Gross exposure violation')
    return metrics,daily,monthly


def snapshot(data_root,output):
    source={name:hashlib.sha256((HERE/name).read_bytes()).hexdigest()
            for name in ('download.py','model.py','engine.py','run.py','clean_daily.py')}
    inputs={name:hashlib.sha256((data_root/name).read_bytes()).hexdigest()
            for name in ('metadata.json','daily_manifest.json','intraday_manifest.json')}
    core={'default':asdict(Config()),'lookbacks':[30,63,126],'universe_sizes':[40,50,60],
          'periods':PERIODS,'sources':source,'input_manifests':inputs,
          'policy':{'selection':'63/N50 predetermined; no choosing on OOS',
                    'success':'At least 5/9 positive OOS total returns WITH funding, separately per mode',
                    'universe':'past 30d quote-volume; age from first real observed trade of each contract lifecycle; minimum 40',
                    'chop':'rank full cross-section, then remove >2x median vol without filling the slots',
                    'vol_target':'min(5% equity, 40% / trailing annual asset vol), cap takes priority',
                    'rebalances':'Monday 00 UTC; 2-rank move since last allocation (first eligible allocation allowed)',
                    'mandatory_exits':'stops, loss of universe eligibility and risk caps override the rank deadband',
                    'daily_features':'all lagged one complete UTC day',
                    'stop':'prior 20d high/low +/- 2 prior ATR14; ratchet prevents loosening; hourly triggers',
                    'funding_order':'existing inventory charged before boundary rebalances',
                    'funding_marks':'hourly historical opening mark price at settlement; rates and intervals actual',
                    'terminal_prices':'last real observed hourly price; terminal settlement is a disclosed proxy',
                    'archive_repairs':'REST or daily exchange archives verify internal holes; responses/hashes retained',
                    'quiet_hours':'REST-confirmed zero-trade hours mark inventory but prohibit execution; stops/risk exits await trading',
                    'buy_hold':'initial point-in-time universe at first eligible Monday per period; no replacements; 1x',
                    'tsm':'sign of 63d return, same vol/chop/stops/caps, weekly resize; rank deadband inapplicable',
                    'statistics':'365-day annualization, daily Sharpe/Sortino at zero risk-free, hourly max drawdown',
                    'periods':'independent 100000 USDT books; flat at boundaries with closing costs'}}
    digest=hashlib.sha256(json.dumps(core,sort_keys=True).encode()).hexdigest()[:16]
    output.mkdir(parents=True,exist_ok=True)
    (output/'protocol.json').write_text(json.dumps({**core,'fingerprint':digest,
                          'frozen_at':datetime.now(timezone.utc).isoformat()},indent=2))
    return digest


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--data',type=Path,required=True)
    p.add_argument('--output',type=Path,default=HERE/'output')
    p.add_argument('--main-only',action='store_true')
    args=p.parse_args()
    daily=DailyData(args.data)
    data=HourlyData(daily,args.output/'required_months.json')
    (args.output/'data_quality.json').write_text(json.dumps(data.quality,indent=2))
    fingerprint=snapshot(args.data,args.output)
    path=args.output/('run_'+fingerprint)
    path.mkdir(exist_ok=True)
    summary=[]
    grid=[(n,size,mode) for n in (30,63,126) for size in (40,50,60) for mode in ('long_only','long_short')]
    if args.main_only:
        grid=[(63,50,mode) for mode in ('long_only','long_short')]
    grid += [(63,50,'buy_hold'),(63,50,'tsm')]
    cases=[(stage,start,end,n,size,mode,funding) for stage,(start,end) in PERIODS.items()
           for n,size,mode in grid for funding in (True,False)]
    for count,(stage,start,end,n,size,mode,funding) in enumerate(cases,1):
        name=f'{stage}_{mode}_L{n}_N{size}_'+('funding' if funding else 'no_funding')
        destination=path/name
        cached=destination.with_suffix('.json')
        if cached.exists():
            row=json.loads(cached.read_text())
        else:
            cfg=Config(lookback=n,universe_size=size,mode=mode,with_funding=funding)
            result=Engine(data,cfg).run(start,end)
            stats,curve,months=statistics(result,start,end)
            row={'case':name,'stage':stage,'mode':mode,'lookback':n,'universe_size':size,'funding':funding,**stats}
            cached.write_text(json.dumps(row,indent=2,allow_nan=False))
            curve.rename('equity').to_csv(destination.with_suffix('.daily.csv'))
            months.rename('net_return').to_csv(destination.with_suffix('.monthly.csv'))
            result['trades'].to_csv(destination.with_suffix('.trades.csv'),index=False)
            if n==63 and size==50:
                result['history'].to_csv(destination.with_suffix('.hourly.csv.gz'),index=False,compression='gzip')
                result['orders'].to_csv(destination.with_suffix('.orders.csv.gz'),index=False,compression='gzip')
                destination.with_suffix('.universes.json').write_text(json.dumps(result['universes']))
        summary.append(row)
        pd.DataFrame(summary).to_csv(path/'metrics.csv',index=False)
        print(f'{count}/{len(cases)} {name}: net={row["total_net_return"]:.2%}, Sharpe={row["sharpe"]}',flush=True)
    robustness=[]
    for stage in PERIODS:
        for mode in ('long_only','long_short'):
            rows=[r for r in summary if r['stage']==stage and r['mode']==mode and r['funding']]
            positive=sum(r['total_net_return']>0 for r in rows)
            robustness.append({'stage':stage,'mode':mode,'positive_configurations':positive,'tested':len(rows),
                               'passed_5_of_9':len(rows)==9 and positive>=5})
    (path/'robustness.json').write_text(json.dumps(robustness,indent=2))
    (args.output/'LATEST_RUN.txt').write_text(path.name)
    print('BACKTEST COMPLETE: '+str(path),flush=True)


if __name__=='__main__':
    main()
