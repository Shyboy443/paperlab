"""Fixed protocol evaluation; no grid winner selection and no future-data signals."""
import hashlib, json, math
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from .model import Book, Config, RULE, aggregate, backtest, metrics
from .data import parse_ts

def reduce_curve(curve):
    days={}
    for ts,eq in curve:days[datetime.fromtimestamp(ts/1000,timezone.utc).strftime('%Y-%m-%d')]=[ts,eq]
    return list(days.values())

def clean(book):
    s=metrics(book);s['curve']=reduce_curve(book.curve);s.pop('signals');s.pop('levels')
    s['recent_trades']=book.trades # small and auditably complete, rather than only winners
    return s

def benchmark(groups,start,end,cfg):
    bars=[b for g in groups for b in g['bars'] if start<=b[0]<end]
    b=Book(cfg)
    if not bars:return clean(b)
    first=bars[0];price=b.execution_price(first[1],1)
    qty=math.floor(cfg.initial/(price*(1+cfg.fee_bps/10000)))
    b.cash-=price*qty*cfg.fee_bps/10000
    b.position={'entry':price,'side':1,'qty':qty,'entry_ts':first[0],'entry_fee':price*qty*cfg.fee_bps/10000}
    b.total_fees=price*qty*cfg.fee_bps/10000
    for bar in bars:b.mark(bar[0]+15*60000,bar[4])
    # Benchmark is not subject to the strategy's 20% kill switch.
    b.halted=False;b.exit(bars[-1][4],bars[-1][0]+15*60000,bars[-1][0]+15*60000,'end_of_test')
    return clean(b)

def evaluate(data,progress=None):
    data_sha=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    periods={'development':('2022-01-01T00:00:00Z','2024-01-01T00:00:00Z'),
             'comparison':('2024-01-01T00:00:00Z','2026-01-01T00:00:00Z')}
    out={'rule':RULE,'source_video':'https://www.youtube.com/watch?v=dUczefIYKIU','symbol':'QQQ',
         'data_sha256':data_sha,'source_sha256':{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
             for name in ('model.py','data.py','research.py')},'periods':periods,'results':[],'benchmarks':{},
         'scope':'QQQ ETF adaptation; retrospective comparison, not a validated futures strategy',
         'limitations':['Volume-at-price and absorption are approximations from OHLCV, not order flow.',
            'QQQ regular sessions differ from Nasdaq futures sessions and contract economics.',
            'Historical candles are not evidence of executable intrabar prices.',
            'Fee/slippage allowances and zero intraday borrow fee are assumptions.',
            'No new unseen holdout claim; prospective delayed-feed evidence is required.']}
    for minutes in (15,30):
        groups=aggregate(data['bars'],data['sessions'],minutes)
        incomplete=[g['date'] for g in groups if not g['complete']]
        for kind in ('previous','developing'):
            cfg=Config(minutes,kind);row={'key':cfg.key,'incomplete_sessions':incomplete,'periods':{}}
            for label,(start,end) in periods.items():
                a,z=parse_ts(start),parse_ts(end)
                # One preceding complete session is enough; no simulated inventory crosses the boundary.
                warm=[g for g in groups if g['close_ms']<a][-1:]
                chosen=warm+[g for g in groups if a<=g['open_ms']<z]
                if any(not g['complete'] for g in chosen):
                    raise ValueError('Missing regular-session bars; refusing optimistic partial backtest: '+
                                     ','.join(g['date'] for g in chosen if not g['complete'])[:200])
                base=backtest(chosen,cfg,a,z);stress=backtest(chosen,replace(cfg,fee_bps=2,slip_bps=4),a,z)
                row['periods'][label]={'net':clean(base),'stress':clean(stress)}
                if progress:progress(cfg.key+' '+label)
            out['results'].append(row)
        if minutes==15:
            for label,(start,end) in periods.items():
                out['benchmarks'][label]=benchmark(groups,parse_ts(start),parse_ts(end),Config())
    primary=out['results'][0]['periods']['comparison']
    p,stress=primary['net'],primary['stress']
    reasons=[]
    if p['net']<=0:reasons.append('Nonpositive net comparison return')
    if stress['net']<=0:reasons.append('Loses with doubled cost allowance')
    if p['max_dd_pct']>=20:reasons.append('20% drawdown threshold reached')
    if p['trades']<30:reasons.append('Fewer than 30 completed comparison trades')
    out['verdict']='FAIL' if reasons else 'RETROSPECTIVE_CHECKS_PASS_UNPROVEN'
    out['reasons']=reasons;out['primary']='QQQ-15m-previous'
    return out
