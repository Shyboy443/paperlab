"""Read-only research and prospective delayed-feed paper evidence."""
import asyncio,gzip,json,hashlib
from dataclasses import asdict
from pathlib import Path
from fastapi import APIRouter,Request,HTTPException,Query
from fastapi.responses import FileResponse, Response
router=APIRouter(prefix='/api/public/value-reversal')

@router.get('/source-verification')
async def supplied_source_verification():
    root=Path(__file__).resolve().parents[1]/'value_reversal'
    path=root/'sources'/'user-source-verification.json'
    try:
        proof=json.loads(path.read_text(encoding='utf-8'))
        if hashlib.sha256((root/'sources'/'user-provided.pine').read_bytes()).hexdigest()!=proof['provided_source_sha256']:
            raise ValueError('Supplied source changed')
        if hashlib.sha256((root/'sources'/'value-area-reversion-official.pine').read_bytes()).hexdigest()!=proof['published_source_sha256']:
            raise ValueError('Published source changed')
        for group,names in [('crypto_code_sha256',('crypto_data.py','crypto_indicator.py','crypto_research.py')),
                            ('qqq_code_sha256',('model.py','data.py','research.py','indicator.py'))]:
            for name in names:
                if hashlib.sha256((root/name).read_bytes()).hexdigest()!=proof[group][name]:raise ValueError('Research source changed')
        return proof
    except (OSError,ValueError,KeyError):raise HTTPException(503,'Supplied-source verification is not yet ready')

def _read_gzip_json(path):
    with gzip.open(path,'rt') as f:return json.load(f)

@router.get('/crypto')
async def crypto_report(request:Request):
    service=getattr(request.app.state,'value_reversal',None)
    if service is None:raise HTTPException(503,'Paper service is starting')
    from app.value_reversal.crypto_public import report_path
    try:path=await asyncio.to_thread(report_path,service)
    except FileNotFoundError:raise HTTPException(503,'Crypto historical report is not yet available')
    except (ValueError,OSError,KeyError):raise HTTPException(503,'Crypto report requires validation')
    if 'gzip' in request.headers.get('accept-encoding',''):
        return FileResponse(path,media_type='application/json',headers={'Content-Encoding':'gzip','Vary':'Accept-Encoding'})
    return await asyncio.to_thread(_read_gzip_json,path)

@router.get('/crypto/view')
async def crypto_dashboard(request:Request,key:str=Query('ETHUSDT-15m-previous-10bars',max_length=80)):
    service=getattr(request.app.state,'value_reversal',None)
    if service is None:raise HTTPException(503,'Paper service is starting')
    from app.value_reversal.public_view import crypto_view
    try:return Response(await asyncio.to_thread(crypto_view,service,key),media_type='application/json')
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    except (FileNotFoundError,OSError,KeyError):raise HTTPException(503,'Crypto historical report is not yet available')

@router.get('/crypto/replay')
async def crypto_replay(request:Request,key:str=Query(max_length=80),date:str=Query(max_length=10)):
    service=getattr(request.app.state,'value_reversal',None)
    if service is None:raise HTTPException(503,'Paper service is starting')
    from app.value_reversal.crypto_public import replay
    try:return await asyncio.to_thread(replay,service,key,date)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    except FileNotFoundError:raise HTTPException(503,'Crypto historical replay is not yet available')

@router.get('/arrow-audit')
async def arrow_audit(request:Request):
    service=getattr(request.app.state,'value_reversal',None)
    path=Path(__file__).resolve().parents[1]/'value_reversal'/'sources'/'qqq-arrow-audit.json'
    audit=json.loads(path.read_text())
    if service is None or not service.report or service.report.get('data_sha256')!=audit['data_sha256']:
        raise HTTPException(503,'Arrow audit is awaiting the matching historical dataset')
    return audit

@router.get('')
async def summary(request:Request,compact:bool=False,key:str=Query('QQQ-15m-previous-5bars',max_length=80)):
    service=getattr(request.app.state,'value_reversal',None)
    if service is None:raise HTTPException(503,'QQQ paper service is starting')
    if compact:
        from app.value_reversal.public_view import summary_view
        try:return Response(await asyncio.to_thread(summary_view,service,key),media_type='application/json')
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    return await asyncio.to_thread(service.summary)

@router.get('/replay')
async def replay(request:Request,key:str=Query(max_length=80),date:str=Query(max_length=10)):
    service=getattr(request.app.state,'value_reversal',None)
    if service is None:raise HTTPException(503,'QQQ paper service is starting')
    try:return await asyncio.to_thread(service.replay_historical,key,date)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    except FileNotFoundError:raise HTTPException(503,'Historical cache is not yet ready')

@router.get('/live-signals')
async def live_signals(request:Request,limit:int=Query(20,ge=1,le=100)):
    """Flat list of the most recent LIVE signals across all 8 forward books, newest first.

    A "live" signal is one observed since the service started (observed_ms >= service.started).
    Each entry includes the book key, the closed-bar timestamp, the side, the published value-area
    levels, the stop and target, the outside-bar count, the directional volume, the reclaim volume,
    and whether the execution model was eligible to take it (would be a fill at the next open).
    The most recent book with a live signal is also returned with its last 60 replay bars so the
    dashboard can render a candle chart without a second request.
    """
    service=getattr(request.app.state,'value_reversal',None)
    if service is None:raise HTTPException(503,'QQQ paper service is starting')
    out=await asyncio.to_thread(_collect_live_signals,service,limit)
    return out

def _collect_live_signals(service,limit):
    started=service.started or 0
    entries=[]
    focus_key=None;focus_ts=0
    for book in service.books.values():
        for sig in book.signals:
            observed=int(sig.get('observed_ms') or 0)
            if started and observed<started:continue
            entries.append({'key':book.cfg.key,'config':asdict(book.cfg),'ts':int(sig['ts']),
                           'observed_ms':observed,'side':int(sig['side']),
                           'stop':float(sig['stop']),'target':float(sig['target']),
                           'value_low':float(sig['value_low']),'value_high':float(sig['value_high']),
                           'outside_bars':int(sig['outside_bars']),
                           'last_directional_volume':float(sig['last_directional_volume'] or 0.0),
                           'reclaim_volume':float(sig['reclaim_volume'] or 0.0),
                           'entry_eligible':bool(sig.get('entry_eligible'))})
            if observed>focus_ts:focus_key,focus_ts=book.cfg.key,observed
    entries.sort(key=lambda e:e['observed_ms'],reverse=True)
    entries=entries[:limit]
    focus_replay=None;focus_levels=None
    if focus_key and focus_key in service.books:
        b=service.books[focus_key]
        focus_replay=b.replay[-60:] if b.replay else []
        focus_levels=asdict(b.cfg) if b.latest_profile is None else b.latest_profile
    return {'status':service.status,'heartbeat':service.heartbeat,'forward_start_ms':service.started,
            'book_count':len(service.books),'signal_count':len(entries),
            'focus_key':focus_key,'focus_replay':focus_replay,'focus_levels':focus_levels,
            'signals':entries}

@router.get('/source/indicator.py')
async def python_source():
    return FileResponse(Path(__file__).resolve().parents[1]/'value_reversal'/'indicator.py',media_type='text/plain')

@router.get('/source/original.pine')
async def pine_source():
    return FileResponse(Path(__file__).resolve().parents[1]/'value_reversal'/'sources'/'value-area-reversion-official.pine',media_type='text/plain')

@router.get('/source/{name}')
async def crypto_source(name:str):
    if name=='user-provided.pine':
        return FileResponse(Path(__file__).resolve().parents[1]/'value_reversal'/'sources'/name,media_type='text/plain')
    if name not in ('crypto_indicator.py','crypto_research.py','crypto_data.py'):
        raise HTTPException(404,'Unknown public source')
    return FileResponse(Path(__file__).resolve().parents[1]/'value_reversal'/name,media_type='text/plain')
