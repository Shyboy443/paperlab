"""Reproduce the divergence desk's frozen-rule historical study from Binance.

Run from repo root: python -m scripts.run_divergence_research --days 120 --end 2026-10-10
Raw public candles/funding are cached under data/divergence-research. No broker keys.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import threading
from datetime import datetime,timezone
from pathlib import Path
from app.divergence.market import Market,UNIVERSE,DAY


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--days',type=int,default=120)
    parser.add_argument('--end',default='2026-10-10')
    parser.add_argument('--symbols',default=','.join(UNIVERSE))
    parser.add_argument('--download-only',action='store_true')
    args=parser.parse_args()
    end=int(datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc).timestamp()*1000)
    start=end-args.days*DAY
    cache=Path('data/divergence-research');cache.mkdir(parents=True,exist_ok=True)
    client=Market(threading.Event());sources=[]
    for symbol in args.symbols.split(','):
        if symbol not in UNIVERSE:raise ValueError('Symbol outside frozen universe')
        path=cache/f'{symbol}-{start}-{end}.json.gz'
        if path.exists():
            with gzip.open(path,'rt',encoding='utf-8') as f:data=json.load(f)
        else:
            data={'symbol':symbol,'start':start,'end':end,'candles':client.bars(symbol,start,end),'funding':client.funding(symbol,start,end)}
            with gzip.open(path,'wt',encoding='utf-8') as f:json.dump(data,f,separators=(',',':'))
        candles=data['candles'];funding=data['funding']
        expected=(end-start)//900_000
        if len(candles)!=expected or any(b['t']!=start+i*900_000 for i,b in enumerate(candles)):
            raise ValueError(f'{symbol}: incomplete 15-minute history; study stopped')
        if not funding or funding[0][0]-start>8*3600000+60000 or end-funding[-1][0]>8*3600000+60000 or any(funding[i][0]-funding[i-1][0]>8*3600000+60000 for i in range(1,len(funding))) or any(r[2]<=0 for r in funding):
            raise ValueError(f'{symbol}: incomplete funding/mark history; study stopped')
        sources.append((path,data))
        print(json.dumps({'symbol':symbol,'bars':len(data['candles']),'funding':len(data['funding']),'downloaded':str(path)}),flush=True)
    if args.download_only:return
    from app.divergence.model import RULES,RULE_VERSION,TYPES,generate_signals
    from app.divergence.backtest import backtest
    split=start+int(args.days*.7)*DAY
    rows=[];curves=[];trades=[]
    for path,data in sources:
        signals=generate_signals(data['candles'],data['symbol'])
        # Each divergence type is its own independent book per symbol/timeframe/period (DIV-SR-2).
        for tf in ('1h','4h'):
            for kind in TYPES:
                selected=[s for s in signals if s['divergence_tf']==tf and s['divergence_type']==kind]
                for period,a,b in [('development',start,split),('holdout',split+900_000,end)]:
                    result=backtest(data['candles'],selected,data['funding'],start_equity=1000,start_ms=a,end_ms=b)
                    result['metrics']['funding_net']=result['metrics'].get('funding')
                    rows.append({'symbol':data['symbol'],'timeframe':tf,'type':kind,'period':period,'start':a,'end':b,'metrics':result['metrics']})
                    curves.append({'symbol':data['symbol'],'timeframe':tf,'type':kind,'period':period,'equity':result.get('equity',[]),'monthly':result.get('monthly',[])})
                    trades.append({'symbol':data['symbol'],'timeframe':tf,'type':kind,'period':period,'trades':result.get('trades',[])})
        print(json.dumps({'symbol':data['symbol'],'signals':len(signals),'backtest':'complete'}),flush=True)
    by_type=[]
    for kind in TYPES:
        for tf in ('1h','4h'):
            for period in ('development','holdout'):
                ts=[t for g in trades if g['type']==kind and g['timeframe']==tf and g['period']==period for t in g['trades']]
                r=[t['net']/1000*100 for t in ts]
                wins=sum(t['net'] for t in ts if t['net']>0);losses=-sum(t['net'] for t in ts if t['net']<0)
                rr=[(t['exit']-t['entry'])/(t['entry']-t['stop']) for t in ts if t['entry']!=t['stop']]
                by_type.append({'type':kind,'timeframe':tf,'period':period,'trades':len(ts),'books':len({g['symbol'] for g in trades if g['type']==kind and g['timeframe']==tf and g['period']==period and g['trades']}),
                                'win_rate_pct':sum(t['net']>0 for t in ts)/len(ts)*100 if ts else None,
                                'avg_trade_return_pct':sum(r)/len(r) if r else None,'net_usd':sum(t['net'] for t in ts),
                                'profit_factor':wins/losses if losses else None,
                                'avg_r_after_slippage':sum(rr)/len(rr) if rr else None})
    report={'status':'COMPLETE','by_type':by_type,'asof':end,'start':start,'end':end,'split':split,'rule_version':RULE_VERSION,'rules':RULES,'rows':rows,'notes':['DIV-SR-2: swing-structure pivots (5 left / 3 right), RSI matched within 2 bars, clean price and RSI lines, four divergence types each backtested as its own book; regular types need support/resistance, hidden types need the EMA50 trend, every entry needs a volume-confirmed lower-timeframe structure break.','Preliminary fixed-rule study on a current ten-symbol liquid universe; selection/survivorship bias remains. No claim of profitability or qualification.','Each symbol/timeframe is an independent $1,000 paper book. Returns are not a combined portfolio.','Chronological 70/30 development/holdout split with a 15-minute boundary embargo; rules were not tuned on either subset. Warm-up precedes eligible entries.','Historical replay contains only the mechanical rule. AI debate/news filtering begins prospectively; historical news and invented agent votes are excluded.','Taker fees 0.04% and adverse slippage 0.02% per side plus actual Binance funding. Stops win candles touching both stop and target.','Forward entries use fresh observed quotes after confirmation/review. Historical fills use the next 15-minute open; execution latency differs.','Forward partial entry-minute stop touches are conservatively counted as losses; partial-minute targets require an observed quote. Funding with ambiguous intrabar exit ordering charges debits and withholds credits.'],'data_manifest':[{'symbol':d['symbol'],'file':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bars':len(d['candles']),'funding_records':len(d['funding']),'coverage_verified':True} for p,d in sources]}
    out=Path('app/divergence/results');out.mkdir(parents=True,exist_ok=True)
    (out/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    # Per-book curves and trades are large (two years of 15m equity); keep them with the cached data, not in the app.
    with gzip.open(cache/f'detail-{start}-{end}.json.gz','wt',encoding='utf-8') as f:json.dump({'curves':curves,'trades':trades},f,allow_nan=False)
    print(json.dumps({'report':str(out/'report.json'),'rows':len(rows)}),flush=True)

if __name__=='__main__':main()
