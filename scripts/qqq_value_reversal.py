"""Reproduce the frozen QQQ rule using Alpaca paper credentials and GET requests only."""
import argparse,asyncio,gzip,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.live.providers import Providers
from app.value_reversal.data import download
from app.value_reversal.research import evaluate

async def run(args):
    providers=Providers()
    if args.input.exists():
        with gzip.open(args.input,'rt',encoding='utf-8') as f:data=json.load(f)
    else:
        if not providers.keys('alpaca','testnet'):raise ValueError('Set ALPACA_PAPER_API_KEY and ALPACA_PAPER_API_SECRET')
        data=await download(providers.client('alpaca','testnet'),'2021-12-01T00:00:00Z','2026-01-01T00:00:00Z',
                            lambda n:print('Downloaded bars:',n,flush=True))
        args.input.parent.mkdir(parents=True,exist_ok=True)
        with gzip.open(args.input,'wt',encoding='utf-8') as f:json.dump(data,f)
    result=await asyncio.to_thread(evaluate,data,lambda s:print(s,flush=True))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(result['verdict'],result['reasons']);print('Results:',args.output)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,default=Path('data/qqq-value-reversal-input.json.gz'))
    p.add_argument('--output',type=Path,default=Path('research/qqq_value_reversal/results.json'))
    asyncio.run(run(p.parse_args()))
