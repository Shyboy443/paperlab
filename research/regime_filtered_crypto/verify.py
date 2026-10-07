"""Independent source/input, order-ledger and summary reconciliation."""
from __future__ import annotations
import argparse,hashlib,json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        while chunk:=f.read(1024*1024):h.update(chunk)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--raw',action='store_true',help='Also hash every referenced public ZIP, gzip and API response')
    a=p.parse_args();pin=json.loads((a.run/'pin.json').read_text());here=Path(__file__).parent
    checks=[(here/name,value) for name,value in pin['source_sha256'].items()]
    checks.extend((a.data/name,value) for name,value in pin['input_sha256'].items())
    if a.raw:
        for name in ['daily_manifest.json','intraday_manifest.json']:
            m=json.loads((a.data/name).read_text())
            for row in m['archives']:checks.append((a.data/'raw'/row['key'],row['sha256']))
            for kind in ['api_daily','api_funding','api_mark','api_trade']:
                for row in m.get(kind,[]):checks.append((a.data/row['path'],row['sha256']))
        for row in json.loads((a.data/'spot_manifest.json').read_text())['files']:checks.append((a.data/row['path'],row['sha256']))
    checks=dict(checks)
    def check(item):
        path,expected=item
        if sha(path)!=expected:raise AssertionError('SHA-256 mismatch '+str(path))
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(check,checks.items()))
    summary=pd.read_csv(a.run/'metrics.csv');references=summary.reference.unique();errors={}
    for reference in references:
        path=a.run/reference
        if not (path/'orders.csv.gz').exists():continue
        o=pd.read_csv(path/'orders.csv.gz');h=pd.read_csv(path/'history.csv.gz');m=json.loads((path/'metrics.json').read_text())
        config=m['config'];mom=o.kind=='momentum';hedge=~mom
        pnl=-float((o.loc[mom,'delta_contract_units']*o.loc[mom,'perp_reference']).sum())
        pnl+=float((o.loc[hedge,'delta_contract_units']*(o.loc[hedge,'perp_reference']-o.loc[hedge,'spot_reference_per_contract_unit'])).sum())
        expected=config['starting_equity']+pnl+h.funding_cash.iloc[-1]-o.fee.sum()-o.slippage.sum()-h.transfer_fees.iloc[-1]
        error=float(h.equity.iloc[-1]-expected);errors[reference]=error
        if abs(error)>1e-5:raise AssertionError('Ledger failure '+reference+': '+str(error))
        if abs(o.fee.sum()-h.fees.iloc[-1])>1e-6:raise AssertionError('Fee ledger mismatch')
        if abs(o.slippage.sum()-h.slippage.iloc[-1])>1e-6:raise AssertionError('Slippage ledger mismatch')
        for _,g in o.groupby(['symbol','kind']):
            if abs(g.delta_contract_units.sum())>1e-6:raise AssertionError('Unclosed inventory '+reference)
        if len(o):
            base=o.delta_contract_units.abs()*o.perp_reference
            base.loc[hedge]+=o.loc[hedge,'delta_contract_units'].abs()*o.loc[hedge,'spot_reference_per_contract_unit']
            if not np.allclose(o.fee,base*config['fee'],atol=1e-8):raise AssertionError('Wrong per-leg fee')
            if (o.adverse_slippage_rate<config['slippage']-1e-12).any():raise AssertionError('Missing required slippage')
        peaks=np.maximum.accumulate(np.r_[config['starting_equity'],h.equity.to_numpy()])[1:]
        dd=float(-(h.equity.to_numpy()/peaks-1).min())
        if abs(dd-m['max_drawdown'])>1e-9:raise AssertionError('Drawdown mismatch')
        if m['kill_time']:
            t=int((pd.Timestamp(m['kill_time']).timestamp()*1000-1546300800000)//3600000)
            if ((o.hour>t)&(o.delta_contract_units>1e-8)).any():raise AssertionError('Restarted after permanent kill')
    report={'passed':True,'hashed_files':len(checks),'reconciled_unique_books':len(errors),
            'max_ledger_error_usdt':max(map(abs,errors.values()),default=0.),'ledger_errors':errors,
            'raw_sources_verified':a.raw,'fingerprint':pin['fingerprint']}
    (a.run/'verification.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='ledger_errors'},indent=2))

if __name__=='__main__':main()
