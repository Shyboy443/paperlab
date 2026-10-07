"""Inspect/apply only uptime settings on the already-linked PaperLab production service."""
import json, pathlib, urllib.request, sys
SERVICE='2d1e8b26-a1a8-4773-9bd5-3c97e897e49c'
ENVIRONMENT='294106ca-59ec-4f2a-98f5-b53404fbb30e'

def query(document,variables=None):
    cfg=json.loads((pathlib.Path.home()/'.railway/config.json').read_text())
    token=cfg['user'].get('token') or cfg['user'].get('accessToken')
    if not token:raise RuntimeError('Existing Railway CLI login is required')
    req=urllib.request.Request('https://backboard.railway.app/graphql/v2',
        data=json.dumps({'query':document,'variables':variables or {}}).encode(),
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':'PaperLab-Railway-Config/1'},method='POST')
    with urllib.request.urlopen(req,timeout=30) as res:result=json.load(res)
    if result.get('errors'):raise RuntimeError('; '.join(x.get('message','GraphQL error') for x in result['errors']))
    return result['data']

if __name__=='__main__':
    fields=query('query { __type(name: "ServiceInstanceUpdateInput") { inputFields { name } } }')
    allowed={x['name'] for x in fields['__type']['inputFields']}
    desired={'restartPolicyType':'ALWAYS','sleepApplication':False,'healthcheckPath':'/api/health','healthcheckTimeout':120}
    assert set(desired)<=allowed,'Uptime fields unavailable in current API'
    variables={'serviceId':SERVICE,'environmentId':ENVIRONMENT}
    read='query($serviceId:String!,$environmentId:String!){serviceInstance(serviceId:$serviceId,environmentId:$environmentId){serviceName restartPolicyType sleepApplication healthcheckPath healthcheckTimeout}}'
    before=query(read,variables)['serviceInstance'];assert before['serviceName']=='paperlab'
    if '--apply' in sys.argv:
        query('mutation($serviceId:String!,$environmentId:String!,$input:ServiceInstanceUpdateInput!){serviceInstanceUpdate(serviceId:$serviceId,environmentId:$environmentId,input:$input)}',{**variables,'input':desired})
    after=query(read,variables)['serviceInstance']
    print(json.dumps({'before':before,'after':after,'desired':desired},indent=2))
