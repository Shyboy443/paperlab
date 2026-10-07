"""Bounded public-data smoke check, isolated from every existing trading book."""
import sys, pathlib, tempfile, time, json
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from app.autoresearch.service import ResearchService

def main():
    path=pathlib.Path(tempfile.mkdtemp(prefix='paperlab-research-check-'))/'research.db'
    service=ResearchService(path,env={'AUTORESEARCH_ENABLED':'true'});service.start()
    try:
        deadline=time.time()+110
        while time.time()<deadline:
            data=service.summary()
            if len(data.get('coverage',[]))==2 and data.get('studies'):break
            time.sleep(2)
        else:raise RuntimeError('Market/history study did not complete within the smoke-check budget')
        before=data['coverage'];n=len(data['studies'])
        # Kill only this smoke worker, then exercise its automatic restart against the same DB.
        service.proc.terminate();service.proc.join(3)
        deadline=time.time()+60
        while time.time()<deadline:
            data=service.summary()
            if service.restarts and data['health'].get('status')=='RUNNING' and len(data.get('studies',[]))>=n:break
            time.sleep(2)
        else:raise RuntimeError('Automatic worker recovery did not preserve evidence')
        report={'coverage_before_restart':before,'coverage_after_restart':data['coverage'],
                'studies_preserved':len(data['studies'])>=n,'restarts':service.restarts,'latest_result':data['studies'][0],
                'execution':data['execution'],'isolated_database':True}
        (pathlib.Path(__file__).resolve().parents[1]/'docs/AUTORESEARCH_SMOKE_QC.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k!='latest_result'},indent=2))
    finally:service.stop()

if __name__=='__main__':main()
