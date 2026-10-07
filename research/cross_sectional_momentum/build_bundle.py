"""Package research code, final results and public-data provenance, without keys."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

HERE=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,required=True)
    args=parser.parse_args()
    output=HERE/'output'
    latest=(output/'LATEST_RUN.txt').read_text().strip()
    audit=json.loads((output/'verification.json').read_text())
    assert audit['status']=='PASS' and latest=='run_'+audit['fingerprint']
    files={p:p.name for p in HERE.iterdir() if p.is_file() and p.suffix in ('.py','.md','.txt','.ini')}
    for p in output.rglob('*'):
        if not p.is_file():
            continue
        relative=p.relative_to(output)
        if relative.parts[0].startswith('run_') and relative.parts[0]!=latest:
            continue
        if relative.parts[0]=='tmp' or p.name.endswith('-log.txt'):
            continue
        files[p]='output/'+relative.as_posix()
    for name in ('catalog.json','metadata.json','daily_manifest.json','intraday_manifest.json','dummy_candle_audit.json'):
        p=args.data/name
        if p.exists():
            files[p]='data_audit/'+name
    for folder in ('api_daily','api_funding','api_mark','api_trade','rest_2019'):
        for p in (args.data/folder).rglob('*'):
            if p.is_file():
                files[p]='data_audit/'+p.relative_to(args.data).as_posix()
    hashes={name:hashlib.sha256(p.read_bytes()).hexdigest() for p,name in files.items()}
    destination=HERE/'Crypto-Perpetual-Momentum.zip'
    with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p,name in sorted(files.items(),key=lambda item:item[1]):
            z.write(p,'cross_sectional_momentum/'+name)
        z.writestr('cross_sectional_momentum/BUNDLE_MANIFEST.json',json.dumps({'run':latest,'files_sha256':hashes},indent=2))
    with zipfile.ZipFile(destination) as z:
        assert z.testzip() is None
        for name,expected in hashes.items():
            assert hashlib.sha256(z.read('cross_sectional_momentum/'+name)).hexdigest()==expected
    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
    (HERE/'Crypto-Perpetual-Momentum.zip.sha256').write_text(digest+'  '+destination.name+'\n')
    print(json.dumps({'bundle':str(destination),'bytes':destination.stat().st_size,'files':len(files),
                      'sha256':digest,'run':latest},indent=2))


if __name__=='__main__':
    main()
