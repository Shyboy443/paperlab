"""Reproduce the fixed 24-configuration crypto study using public archives only."""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.value_reversal.crypto_data import load
from app.value_reversal.crypto_research import evaluate
parser=argparse.ArgumentParser()
parser.add_argument('--data',default='data/crypto_value_reversal')
parser.add_argument('--output',default='research/crypto_value_reversal/results.json')
args=parser.parse_args();root=Path(args.data)
result=evaluate(load(root),root)
output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps(result,separators=(',',':')))
print(json.dumps({'verdict':result['verdict'],'results':[{'key':r['key'],**{k:r['periods']['comparison']['net'][k] for k in ('return_pct','signal_count','trades','max_dd_pct')}} for r in result['results']]},indent=2),flush=True)
