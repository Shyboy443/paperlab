"""Independently reconcile exported returns, closed ledgers and frozen inputs."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=HERE/'output')
    args=parser.parse_args()
    protocol=json.loads((args.output/'protocol.json').read_text())
    for name,expected in protocol['sources'].items():
        assert digest(HERE/name)==expected, 'Changed trading source: '+name
    for name,expected in protocol['input_manifests'].items():
        assert digest(args.data/name)==expected, 'Changed input manifest: '+name
    path=args.output/(args.output/'LATEST_RUN.txt').read_text().strip()
    assert path.name=='run_'+protocol['fingerprint']
    metrics=pd.read_csv(path/'metrics.csv')
    assert len(metrics)==80 and metrics.case.nunique()==80
    differences=[]
    for row in metrics.itertuples(index=False):
        daily=pd.read_csv(path/(row.case+'.daily.csv'),index_col=0).iloc[:,0]
        monthly=pd.read_csv(path/(row.case+'.monthly.csv'),index_col=0).iloc[:,0]
        trades=pd.read_csv(path/(row.case+'.trades.csv'))
        assert len(monthly)==36
        assert len(daily)==1096
        assert abs(daily.iloc[-1]/100000-1-row.total_net_return)<1e-10
        assert abs(monthly.mean()-row.monthly_average_net_return)<1e-10
        assert abs(np.prod(1+monthly)-daily.iloc[-1]/100000)<1e-10
        error=abs(trades.net.sum()-(row.end_equity-100000))
        assert error<1e-5, row.case+' ledger mismatch'
        assert abs(trades.funding_cost.sum()-row.funding_net_cost)<1e-5
        assert abs(trades.fees.sum()-row.fees)<1e-5
        assert abs(trades.slippage_cost.sum()-row.slippage_cost)<1e-5
        if not row.funding:
            assert row.funding_net_cost==0
        if row.mode!='buy_hold':
            assert row.max_position_weight<=.0500001
            assert row.max_gross_exposure<=2.0000001
        differences.append(error)
    quality=json.loads((args.output/'data_quality.json').read_text())
    assert sum(r['expected_rate_gaps'] for r in quality)==0
    assert sum(r['missing_event_marks'] for r in quality)==0
    metadata=json.loads((args.data/'metadata.json').read_text())
    for symbol,entry in metadata.items():
        assert digest(args.data/'daily'/(symbol+'.csv'))==entry['daily_sha256']
    manifests=[json.loads((args.data/name).read_text()) for name in ('daily_manifest.json','intraday_manifest.json')]
    responses=[]
    raw={}
    for manifest in manifests:
        raw.update({item['key']:item['sha256'] for item in manifest['archives']})
        for key in ('api_daily','api_funding','api_mark','api_trade'):
            for item in manifest.get(key,[]):
                p=args.data/item['path']
                assert digest(p)==item['sha256'], str(p)
                payload=json.loads(p.read_text())
                if 'archive' in payload:
                    raw[payload['archive']['key']]=payload['archive']['sha256']
                responses.append(item)
    for key,expected in raw.items():
        assert digest(args.data/'raw'/key)==expected, 'Changed exchange archive: '+key
    robustness=json.loads((path/'robustness.json').read_text())
    for result in robustness:
        subset=metrics[(metrics.stage==result['stage'])&(metrics['mode']==result['mode'])&metrics.funding]
        assert len(subset)==9
        positive=int((subset.total_net_return>0).sum())
        assert positive==result['positive_configurations']
        assert result['passed_5_of_9']==(positive>=5)
    audit={'status':'PASS','fingerprint':protocol['fingerprint'],'cases':len(metrics),
           'months_per_case':36,'days_per_case':1096,'max_ledger_residual_usdt':max(differences),
           'daily_contract_files_checked':len(metadata),'selected_intraday_contracts':len(quality),
           'source_archives_sha256_checked':len(raw),'api_repair_files_sha256_checked':len(responses),
           'missing_funding_events':0,'missing_settlement_marks':0,
           'robustness':robustness,'note':'Integrity PASS is independent of strategy profitability.'}
    (args.output/'verification.json').write_text(json.dumps(audit,indent=2))
    print(json.dumps(audit,indent=2))


if __name__=='__main__':
    main()
