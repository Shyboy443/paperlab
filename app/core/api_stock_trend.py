"""Private, Basic-auth + CSRF-protected stock-strategy controls."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from app.stock_trend.model import Blocked

router=APIRouter(prefix='/api/stock-trend')

def service(request):
    s=getattr(request.app.state,'stock_trend',None)
    if s is None:raise HTTPException(503,'Stock trend service is starting')
    return s

@router.get('')
async def status(request:Request):return service(request).summary()

@router.post('/start')
async def start(request:Request):
    try:
        b=await request.json()
        if not isinstance(b,dict):raise ValueError('JSON object required')
        return {'ok':True,'strategy':await service(request).arm(b.get('network','testnet'),b.get('allocation_usd',9.5),b.get('acknowledgement',''))}
    except (Blocked,ValueError,TypeError) as exc:
        return JSONResponse({'ok':False,'error':str(exc)},status_code=400)
    except Exception as exc:
        return JSONResponse({'ok':False,'error':service(request).providers.redact(f'{type(exc).__name__}: {exc}')},status_code=400)

@router.post('/pause')
async def pause(request:Request):
    try:return {'ok':True,'strategy':await service(request).pause()}
    except Exception as exc:return JSONResponse({'ok':False,'error':service(request).providers.redact(exc)},status_code=409)

@router.post('/paper-check')
async def paper_check(request:Request):
    try:return {'ok':True,'paper_check':await service(request).check_paper()}
    except Exception as exc:return JSONResponse({'ok':False,'error':service(request).providers.redact(exc)},status_code=409)

@router.post('/flatten')
async def flatten(request:Request):
    try:
        b=await request.json()
        return {'ok':True,'strategy':await service(request).flatten(b.get('acknowledgement',''))}
    except Exception as exc:return JSONResponse({'ok':False,'error':service(request).providers.redact(exc)},status_code=409)
