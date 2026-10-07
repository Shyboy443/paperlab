"""Retrospective validation of the deployed $9.50 five-stock execution rule.

Selection uses completed closes; orders use the next observed open. Reuses
the production planner. Never selects the default using the reported results.
"""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from app.stock_trend.model import RULE, Blocked, plan

def simulate(data,start,end,lookback=63,seed=None,sell_fee=.01):
    dates=data['dates'];names=data['stocks'];opening=data['opening'];close=data['close']
    marked=pd.DataFrame(opening).ffill().fillna(0).to_numpy()
    ma=pd.Series(data['index']).rolling(200).mean().to_numpy()
    first=int(np.searchsorted(dates,start));last=min(int(np.searchsorted(dates,end,side='right'))-2,len(dates)-2)
    qty=np.zeros(len(names));cash=9.50;rng=np.random.default_rng(seed)
    records=[];orders=[];chosen=None;month=None;paused=False
    for t in range(first,last):
        m=str(dates[t])[:7]
        members=np.flatnonzero(data['current'][t])
        if m!=month:
            # Starting mid-month must use the same frozen selection as a
            # continuously monitored strategy, not a later starting-day rank.
            asof=next(i for i in range(t+1) if str(dates[i])[:7]==m)
            scores=close[asof,members]/close[asof-lookback,members]-1
            if asof<lookback or len(members)!=20 or not np.isfinite(scores).all():
                raise ValueError('Incomplete selection history: '+str(dates[t]))
            chosen=sorted(members,key=lambda j:(-float(close[asof,j]/close[asof-lookback,j]-1),str(names[j])))[:5]
            month=m
        price=marked[t+1];future=marked[t+2];before=float(cash+qty@price)
        regime='LONG' if data['index'][t]>ma[t] else 'CASH'
        sig={'regime':regime,'universe':[str(names[j]) for j in members],
             'selected_stocks':[str(names[j]) for j in chosen]}
        positions=[{'symbol':str(names[j]),'qty':str(qty[j])} for j in np.flatnonzero(qty>0)]
        required=set(chosen)|set(np.flatnonzero(qty>0))
        quoted={str(names[j]):{'bp':float(price[j]),'ap':float(price[j])}
                for j in required if np.isfinite(opening[t+1,j]) and opening[t+1,j]>0}
        cost=0.;turnover=0.;rejected=0;skipped=0
        try:
            basket={'orders':[],'skipped':[]} if paused else plan(sig,min(9.5,before),positions,quoted,cash)
        except Blocked:
            # Production pauses and retains shares on a preflight failure.
            # No future CASH exit or automatic rearm is assumed in research.
            basket={'orders':[],'skipped':[]};rejected=1;paused=True
        for order in basket['orders']:
            j=int(np.flatnonzero(names==order['symbol'])[0])
            # Conservative execution proxy, not a claim of actual Alpaca fees.
            extra=float(rng.uniform(0,.0004)) if seed is not None else 0.
            size=float(order.get('notional',0)) if order['side']=='buy' else float(order['qty'])*price[j]
            impact=float(np.nan_to_num(data['sigma'][t,j],nan=.03))*np.sqrt(size/max(float(np.nan_to_num(data['adv'][t,j],nan=1e7)),1e6))
            rate=.0006+extra+impact
            if order['side']=='sell':
                units=float(order['qty']);fee=min(size,sell_fee);loss=size*rate+fee
                cash+=size-loss;qty[j]=max(0.,qty[j]-units)
            else:
                if cash+1e-10<size:rejected+=1;continue
                units=size/(price[j]*(1+rate));loss=size-units*price[j]
                cash-=size;qty[j]+=units
            cost+=loss;turnover+=size
            orders.append({'execution_date':str(dates[t+1]),'decision_date':str(dates[t]),
                           'symbol':order['symbol'],'side':order['side'],'reference_notional':size,'cost_usd':loss})
        skipped=len(basket['skipped'])
        after=float(cash+qty@future);pnl=float(qty@(future-price))
        if cash< -1e-8 or abs(after-(before+pnl-cost))>1e-8:
            raise AssertionError('Self-financing ledger failed')
        records.append({'date':str(dates[t+2]),'decision_date':str(dates[t]),'equity':after,
                        'net_return':after/before-1,'cost_usd':cost,'price_pnl':pnl,'cash':cash,
                        'regime':regime,'turnover':turnover/before,'blocked_days':rejected,
                        'paused':paused,
                        'skipped_small_adjustments':skipped,'unquoted_held':int(sum(not np.isfinite(opening[t+1,j]) for j in np.flatnonzero(qty>0)))})
    frame=pd.DataFrame(records)
    residual=frame.equity.iloc[-1]-(9.5+frame.price_pnl.sum()-frame.cost_usd.sum())
    if abs(residual)>1e-7:raise AssertionError('Full ledger failed')
    return frame,pd.DataFrame(orders)

def metrics(frame,rf):
    r=pd.Series(frame.net_return.to_numpy(),index=pd.to_datetime(frame.date))
    monthly=(1+r).groupby(r.index.to_period('M')).prod()-1
    eq=(1+r).cumprod();peaks=np.maximum.accumulate(np.r_[1.,eq.to_numpy()])[1:]
    excess=r-rf.reindex(r.index).fillna(0);sd=float(excess.std())
    return {'monthly_average_net':float(monthly.mean()),'annualized_return':float(eq.iloc[-1]**(252/len(r))-1),
            'sharpe_over_tbill':float(excess.mean()/sd*np.sqrt(252)) if sd else None,
            'max_drawdown':float(-(eq.to_numpy()/peaks-1).min()),
            'worst_month':str(monthly.idxmin()),'worst_month_return':float(monthly.min()),
            'positive_month_fraction':float((monthly>0).mean()),'ending_equity_usd':float(frame.equity.iloc[-1]),
            'total_proxy_cost_usd':float(frame.cost_usd.sum()),'blocked_days':int(frame.blocked_days.sum()),
            'paused_days':int(frame.paused.sum()),
            'unquoted_held_days':int((frame.unquoted_held>0).sum())},monthly

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=ROOT.parent/'stock_trend/data/n20_arrays.npz')
    p.add_argument('--output',type=Path,default=Path(__file__).parent/'output');a=p.parse_args()
    data=dict(np.load(a.data,allow_pickle=False));a.output.mkdir(parents=True,exist_ok=True)
    pin={'rule':RULE,'data_sha256':hashlib.sha256(a.data.read_bytes()).hexdigest(),
         'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         'planner_sha256':hashlib.sha256((ROOT/'app/stock_trend/model.py').read_bytes()).hexdigest(),
         'default':{'stocks':5,'lookback':63,'starting_usd':9.5,'exposure_cap_usd':9.5,'sell_fee_proxy_usd':.01},
         'seeds':[11,29,47,71,101],'lookbacks':[42,63,90],
         'limitations':['Retrospective reuse of previously examined OOS history; not a fresh holdout.',
         'Missing delisted stocks; observed historical liquidity sample, not complete S&P 500.',
         'Adjusted historical units approximate fractional shares, dividends and splits.',
         '6bps per executed side plus square-root impact; 1c per sell is a stress proxy, not historical broker fees.',
         'No interest paid on cash; portfolio marked at final observation, not liquidated.',
         'Missing executable opens block the complete basket; held positions marked at last observed open.']}
    (a.output/'pin.json').write_text(json.dumps(pin,indent=2))
    rf=pd.Series(data['rf'],index=pd.to_datetime(data['dates']));rows=[];months=[];curves=[]
    for period,start,end in [('IS','2001-01-01','2020-12-31'),('OOS','2021-01-01','2025-12-31')]:
        for lb in pin['lookbacks']:
            for seed in [None,*pin['seeds']]:
                frame,orders=simulate(data,start,end,lb,seed)
                result,monthly=metrics(frame,rf);case=f'{period}_L{lb}_seed{seed}'
                rows.append({'case':case,'period':period,'lookback':lb,'seed':seed,**result})
                if lb==63 and seed is None:
                    frame.to_csv(a.output/f'{period}_daily.csv',index=False);orders.to_csv(a.output/f'{period}_orders.csv',index=False)
                    curves.append((period,frame))
                    months.extend({'period':period,'month':str(m),'net_return':float(v)} for m,v in monthly.items())
                print(case,round(result['monthly_average_net']*100,3),'monthly',flush=True)
    # Explicit small-account fee sensitivity rather than pretending commissions
    # and regulatory fee rounding are irrelevant to a $9.50 portfolio.
    for fee in [0.,.03]:
        f,_=simulate(data,'2021-01-01','2025-12-31',sell_fee=fee);m,_=metrics(f,rf)
        rows.append({'case':f'OOS_sell_fee_{fee}','period':'OOS','lookback':63,'seed':None,**m})
    pd.DataFrame(rows).to_csv(a.output/'metrics.csv',index=False)
    pd.DataFrame(months).to_csv(a.output/'monthly_returns.csv',index=False)
    fig,ax=plt.subplots(2,1,figsize=(11,7),sharex=False)
    for period,f in curves:
        dt=pd.to_datetime(f.date);eq=f.equity/9.5;peak=np.maximum.accumulate(np.r_[1.,eq.to_numpy()])[1:]
        ax[0].plot(dt,eq,label=period+' (independent $9.50 book)');ax[1].plot(dt,(eq/peak-1)*100,label=period)
    ax[0].set_ylabel('Equity / starting equity');ax[1].set_ylabel('Drawdown %')
    for axis in ax:axis.legend();axis.grid(alpha=.2)
    fig.suptitle('Five-stock / SMA200 / monthly 63-session winners / costed $9.50 books')
    fig.tight_layout();fig.savefig(a.output/'equity_drawdown.png',dpi=170);plt.close(fig)
    default=next(r for r in rows if r['case']=='OOS_L63_seedNone')
    seeds=[r for r in rows if r['period']=='OOS' and r.get('lookback')==63 and r.get('seed') is not None]
    verdict={**default,'positive_execution_seeds':sum(r['ending_equity_usd']>9.5 for r in seeds),
             'status':'RETROSPECTIVE ONLY — new variant, no prospective performance evidence'}
    (a.output/'verdict.json').write_text(json.dumps(verdict,indent=2));print(json.dumps(verdict,indent=2))

if __name__=='__main__':main()
