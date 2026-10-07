"""Remove padded zero-trade archive rows from an existing daily snapshot.

The downloader already performs this check on fresh runs. This migration is
idempotent and retains all raw archive files for audit.
"""
import csv
import hashlib
import json
from pathlib import Path
import sys

root=Path(sys.argv[1])
metadata={}
audit=[]
for path in sorted((root/'daily').glob('*.csv')):
    with path.open() as f:
        reader=csv.DictReader(f)
        header=reader.fieldnames
        original=list(reader)
    clean=[r for r in original if float(r['volume'])>0 and float(r['quote_volume'])>0]
    if not clean:
        continue
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=header)
        writer.writeheader()
        writer.writerows(clean)
    symbol=path.stem
    metadata[symbol]={'first_observed_ms':int(clean[0]['timestamp']),'last_observed_ms':int(clean[-1]['timestamp']),
                      'days':len(clean),'daily_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    if len(clean)!=len(original):
        audit.append({'symbol':symbol,'removed_zero_trade_days':len(original)-len(clean),
                      'old_last_ms':int(original[-1]['timestamp']),'last_real_trade_day_ms':int(clean[-1]['timestamp'])})
(root/'metadata.json').write_text(json.dumps(metadata,indent=2))
(root/'dummy_candle_audit.json').write_text(json.dumps(audit,indent=2))
print(json.dumps({'contracts':len(metadata),'affected_contracts':len(audit),'removed_dummy_days':sum(r['removed_zero_trade_days'] for r in audit)}))
