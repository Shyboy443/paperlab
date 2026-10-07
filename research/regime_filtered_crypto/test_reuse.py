import hashlib,json
import pytest
from reuse import validated_reuse


@pytest.fixture
def frozen(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new';here=tmp_path/'code'
    for p in [old,new,here]:p.mkdir()
    source=b'def metrics(x):\n    return x + 1\n'
    (here/'run.py').write_bytes(source);(old/'source_run.py').write_bytes(source)
    pin={'fingerprint':'old','input_sha256':{'price.csv':'abc'},'selection_sha256':'sel',
         'default':{'lookback':63},'periods':{'OOS':['2022','2026']},'seeds':[11],
         'breadths':[.5,.7],'source_sha256':{'run.py':hashlib.sha256(source).hexdigest(),
                                          'engine.py':'engine-v1'}}
    (old/'pin.json').write_text(json.dumps(pin));pin['fingerprint']='new'
    (new/'pin.json').write_text(json.dumps(pin))
    (old/'verification.json').write_text(json.dumps({'passed':True,'fingerprint':'old','ledger_errors':{'combined_OOS':0}}))
    book=old/'combined_OOS';book.mkdir();(book/'metrics.json').write_text(json.dumps({'config':{'breadth':.5}}))
    return old,new,here


def test_identical_book_reuse(frozen):
    old,new,here=frozen
    assert validated_reuse(old,new,here)=={'combined_OOS':old/'combined_OOS'}


@pytest.mark.parametrize('field,key,value',[('input_sha256','price.csv','changed'),
                                          ('source_sha256','engine.py','changed')])
def test_changed_data_or_engine_blocks_reuse(frozen,field,key,value):
    old,new,here=frozen;pin=json.loads((new/'pin.json').read_text());pin[field][key]=value
    (new/'pin.json').write_text(json.dumps(pin))
    with pytest.raises(AssertionError):validated_reuse(old,new,here)


def test_changed_calculation_blocks_reuse(frozen):
    old,new,here=frozen;(here/'run.py').write_text('def metrics(x):\n    return x + 2\n')
    with pytest.raises(AssertionError,match='calculations'):validated_reuse(old,new,here)


def test_unreconciled_book_blocks_reuse(frozen):
    old,new,here=frozen
    (old/'verification.json').write_text(json.dumps({'passed':True,'fingerprint':'old','ledger_errors':{}}))
    with pytest.raises(AssertionError,match='reconciled'):validated_reuse(old,new,here)
