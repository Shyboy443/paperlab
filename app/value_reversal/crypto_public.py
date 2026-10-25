"""Read-only access to separately versioned historical crypto research artifacts."""
import gzip,hashlib,json
from functools import lru_cache
from pathlib import Path
from .crypto_research import RULE

@lru_cache(maxsize=2)
def verified_report(path,mtime):
    with gzip.open(path,'rt') as f:report=json.load(f)
    expected={n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest()
              for n in ('crypto_data.py','crypto_indicator.py','crypto_research.py')}
    if report.get('rule')!=RULE or report.get('source_sha256')!=expected:
        raise ValueError('Crypto research identity mismatch; rebuild required')
    return report

def report_path(service):
    path=service.path.with_name('crypto-value-reversal-v1-report.json.gz')
    if not path.exists():path=Path(__file__).with_name('sources')/'crypto-research-report.json.gz'
    verified_report(str(path),path.stat().st_mtime_ns)
    return path

def replay(service,key,date):
    path=report_path(service);report=verified_report(str(path),path.stat().st_mtime_ns)
    selected=next((r for r in report['results'] if r['key']==key),None)
    if selected is None:raise ValueError('Unknown fixed crypto experiment')
    from datetime import datetime,timedelta
    from .crypto_data import NY
    cfg=selected['config'];day=datetime.strptime(date,'%Y-%m-%d').replace(tzinfo=NY,hour=18)
    start=int(day.timestamp()*1000);end=int((day+timedelta(days=1)).timestamp()*1000)
    file=service.path.with_name('crypto-value-reversal-replay.json.gz')
    if not file.exists():file=Path(__file__).with_name('sources')/'crypto-research-replay.json.gz'
    # Bounded artifact contains only sessions with arrows, not the full four-year dataset.
    rows=replay_cache(str(file),file.stat().st_mtime_ns).get(cfg['symbol']+'-'+str(cfg['minutes'])+'m',{}).get(date)
    if rows is None:raise ValueError('No archived signal session on this date')
    p=cfg['profile'];w=cfg['max_reversion_bars']
    candles=[{'bar':r['bar'],'low':r[p]['low'] if r[p] else None,'high':r[p]['high'] if r[p] else None,
              'poc':r[p]['poc'] if r[p] else None,'signal':r['signals'].get(f'{p}-{w}',{}).get('side',0)} for r in rows]
    signals=[s for r in report['results'] if r['key']==key for v in r['periods'].values()
             for s in v['net']['signals'] if start<=s['ts']<end]
    return {'key':key,'date':date,'minutes':cfg['minutes'],'symbol':cfg['symbol'],'rule':RULE,'candles':candles,'signals':signals,
            'session':'18:00 New York → next 18:00; dates label session start; all times New York'}

@lru_cache(maxsize=1)
def replay_cache(path,mtime):
    with gzip.open(path,'rt') as f:return json.load(f)
