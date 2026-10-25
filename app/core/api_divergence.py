"""Divergence desk routes. Public ones are read-only; the one private route sets the operator approval mode."""
import asyncio
from fastapi import APIRouter,HTTPException,Request,Query

router=APIRouter(prefix='/api/public/divergence')

def service(request):
    svc=getattr(request.app.state,'divergence',None)
    if svc is None:raise HTTPException(503,'Divergence desk is starting')
    return svc

@router.get('')
async def summary(request:Request):return await asyncio.to_thread(service(request).summary)

@router.get('/chart')
async def chart(request:Request,symbol:str=Query('BTCUSDT',max_length=16),tf:str=Query('1h',max_length=3),setup_id:str=Query('',max_length=150),around:int=Query(0,ge=0,le=10**14)):
    try:return await asyncio.to_thread(service(request).chart,symbol,tf,setup_id,around)
    except ValueError as exc:raise HTTPException(400,str(exc)) from None

@router.get('/backtest')
async def backtest(request:Request,symbol:str=Query('BTCUSDT',max_length=16)):
    try:return await asyncio.to_thread(service(request).backtest,symbol)
    except ValueError as exc:raise HTTPException(400,str(exc)) from None


# PRIVATE (dashboard Basic auth + X-PaperLab header, app/main.py): the operator's trade-approval mode. Paper only.
private_router=APIRouter(prefix='/api/divergence')


@private_router.post('/settings')
async def settings(request:Request):
    body=await request.json()
    try:return {'ok':True,'approval_mode':service(request).set_approval_mode(str((body or {}).get('approval_mode','')).lower())}
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
