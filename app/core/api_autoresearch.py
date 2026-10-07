from fastapi import APIRouter,Request

router=APIRouter(prefix='/api/public/research')

@router.get('')
def research(request:Request):
    service=getattr(request.app.state,'research',None)
    return service.summary() if service else {'health':{'status':'DISABLED','enabled':False},'read_only':True,'execution':'PAPER_ONLY'}
