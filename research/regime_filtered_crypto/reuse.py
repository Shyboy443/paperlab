"""Reuse invariant cases only, with frozen input, engine and calculation checks."""
from __future__ import annotations
import ast,hashlib,json
from pathlib import Path


def calculations(source):
    wanted={'metrics','reconcile','benchmark'}
    return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(source).body
            if isinstance(n,ast.FunctionDef) and n.name in wanted}


def validated_reuse(previous:Path,current:Path,here:Path):
    old=json.loads((previous/'pin.json').read_text());new=json.loads((current/'pin.json').read_text())
    verified=json.loads((previous/'verification.json').read_text())
    if not verified['passed'] or verified['fingerprint']!=old['fingerprint']:
        raise AssertionError('Previous run lacks matching independent verification')
    if old['input_sha256']!=new['input_sha256'] or old['selection_sha256']!=new['selection_sha256']:
        raise AssertionError('Changed frozen data; reuse forbidden')
    if old['default']!=new['default'] or old['periods']!=new['periods'] or old['seeds']!=new['seeds']:
        raise AssertionError('Changed strategy, periods or seeds; reuse forbidden')
    # Only the runner's grid/acceptance and documented protocol may change.
    for name,digest in old['source_sha256'].items():
        if name in {'run.py','PROTOCOL.md'}:continue
        if new['source_sha256'].get(name)!=digest:
            raise AssertionError('Changed execution or data code; reuse forbidden: '+name)
    source=(previous/'source_run.py').read_bytes()
    if hashlib.sha256(source).hexdigest()!=old['source_sha256']['run.py']:
        raise AssertionError('Original calculation source does not match old pin')
    if calculations(source)!=calculations((here/'run.py').read_bytes()):
        raise AssertionError('Changed return/accounting calculations; reuse forbidden')
    reusable={}
    for folder in previous.iterdir():
        if not folder.is_dir() or not (folder/'metrics.json').exists():continue
        metrics=json.loads((folder/'metrics.json').read_text())
        if 'config' not in metrics:continue
        if folder.name.startswith('sensitivity_') and metrics['config']['breadth'] not in new['breadths']:
            continue
        if folder.name not in verified['ledger_errors']:
            raise AssertionError('Book was not reconciled previously: '+folder.name)
        reusable[folder.name]=folder
    record={'previous_fingerprint':old['fingerprint'],'new_fingerprint':new['fingerprint'],
            'checks':['identical input hashes','identical engine/data sources','identical calculation AST',
                      'identical default/periods/seeds','previous independent book reconciliation'],
            'reused_cases':sorted(reusable),'note':'Only user-requested 70% breadth cells are new; no selection by OOS performance.'}
    (current/'reuse_provenance.json').write_text(json.dumps(record,indent=2))
    return reusable
