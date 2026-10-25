"""Small, read-only dashboard projections; complete evidence stays downloadable."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from functools import lru_cache
from .crypto_public import report_path, verified_report

HEAVY = {'curve', 'signals', 'recent_trades', 'monthly', 'ledger', 'trades_ledger', 'fills'}

def wallet_view(wallet, detail=False, crypto=False):
    result = {k: v for k, v in wallet.items() if not isinstance(v, list)}
    if detail:
        for key in ('curve', 'monthly'):
            if key in wallet: result[key] = wallet[key]
        result['recent_trades'] = wallet.get('recent_trades', [])[-50:]
        dates = set()
        for signal in wallet.get('signals', []):
            date = datetime.fromtimestamp(signal['ts'] / 1000, ZoneInfo('America/New_York'))
            if crypto and date.hour < 18: date -= timedelta(days=1)
            dates.add(date.strftime('%Y-%m-%d'))
        result['session_dates'] = sorted(dates, reverse=True)
    return result

def report_view(report, selected=None, crypto=False):
    out = {k: v for k, v in report.items() if k != 'results'}
    out['results'] = []
    for row in report['results']:
        detail = selected is None or selected == row['key']
        item = {k: v for k, v in row.items() if k != 'periods'}
        item['periods'] = {}
        for period, value in row['periods'].items():
            item['periods'][period] = {k: wallet_view(v, detail and (crypto or k == 'net'), crypto) if isinstance(v, dict) and 'return_pct' in v else v
                                       for k, v in value.items()}
        out['results'].append(item)
    out['dashboard_projection'] = True
    return out

@lru_cache(maxsize=24)
def crypto_view_bytes(path, mtime, key):
    report = verified_report(path, mtime)
    if not any(r['key'] == key for r in report['results']): raise ValueError('Unknown fixed crypto experiment')
    return json.dumps(report_view(report, key, True), allow_nan=False, separators=(',', ':')).encode()

def crypto_view(service, key):
    path = report_path(service)
    return crypto_view_bytes(str(path), path.stat().st_mtime_ns, key)

def summary_view(service, key):
    out = service.summary()
    if out['report']:
        if not any(r['key'] == key for r in out['report']['results']): raise ValueError('Unknown frozen experiment')
        out['report'] = report_view(out['report'], key)
    out['books'] = [{k:v for k,v in book.items() if k not in ('curve','replay','signals')} for book in out['books']]
    return json.dumps(out, allow_nan=False, separators=(',', ':')).encode()
