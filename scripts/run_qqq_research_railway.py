"""Run the read-only QQQ data download/evaluation on the existing Railway host.

Credentials stay in the encrypted Railway vault. Only new QQQ research files are written.
Does not redeploy, reconfigure, connect to an account endpoint, or send an order.
"""
import base64,io,os,subprocess,zipfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
buf=io.BytesIO()
with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
    for name in ('__init__.py','model.py','data.py','research.py','indicator.py','sources/value-area-reversion-official.pine'):
        z.writestr('qqq_temp/'+name,(root/'app/value_reversal'/name).read_bytes())
payload=base64.b64encode(buf.getvalue()).decode()
source='''import asyncio,base64,gzip,io,json,os,pathlib,sys,tempfile,zipfile
work=pathlib.Path(tempfile.mkdtemp(prefix='paperlab-qqq-'))
zipfile.ZipFile(io.BytesIO(base64.b64decode(PAYLOAD))).extractall(work)
sys.path.insert(0,str(work))
from app.live.providers import Providers
from app.live.key_vault import KeyVault,VAULT_FILE
from qqq_temp.data import download
from qqq_temp.research import evaluate
async def run():
 providers=Providers(vault=KeyVault(pathlib.Path('/data')/VAULT_FILE,os.environ['DASHBOARD_PASSWORD']))
 cache=pathlib.Path('/data/qqq-value-reversal-input.json.gz')
 if cache.exists():
  with gzip.open(cache,'rt') as f:data=json.load(f)
 else:
  if not providers.keys('alpaca','testnet'):raise RuntimeError('Alpaca paper data credentials required')
  data=await download(providers.client('alpaca','testnet'),'2021-12-01T00:00:00Z','2026-01-01T00:00:00Z',lambda n:print('Downloaded',n,'QQQ bars',flush=True))
  with gzip.open(cache,'wt') as f:json.dump(data,f)
 result=await asyncio.to_thread(evaluate,data,lambda s:print('Evaluated',s,flush=True))
 output=pathlib.Path('/data/qqq-value-reversal-v2-report.json')
 temporary=output.with_suffix('.json.tmp');temporary.write_text(json.dumps(result));os.replace(temporary,output)
 print(json.dumps({'verdict':result['verdict'],'reasons':result['reasons'],'results':[{'key':x['key'],'periods':{k:{'monthly_avg_net_pct':v['net']['monthly_avg_net_pct'],'return_pct':v['net']['return_pct'],'max_dd_pct':v['net']['max_dd_pct'],'trades':v['net']['trades'],'stress_return_pct':v['stress']['return_pct']} for k,v in x['periods'].items()}} for x in result['results']]}),flush=True)
asyncio.run(run())
'''.replace('PAYLOAD',repr(payload))
encoded=base64.b64encode(source.encode()).decode()
command="python -c 'exec(__import__(\"base64\").b64decode(\""+encoded+"\"))'"
cli=Path(os.environ['APPDATA'])/'npm/node_modules/@railway/cli/bin/railway.exe'
raise SystemExit(subprocess.call([str(cli),'ssh','--project','10add7d7-f7f5-40be-9197-04afa241aa82',
 '--service','paperlab','--environment','production','--',command]))
