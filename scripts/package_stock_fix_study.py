"""Package the bounded experiment without modifying Railway application files."""
import base64
import hashlib
import json
from pathlib import Path
import zlib

root = Path(__file__).resolve().parents[1]
files = {'app.live.stock_engine': 'app/live/stock_engine.py',
         'app.live.fix_candidate': 'app/live/alpaca_market.py',
         'app.strategies.v9.fix_candidate': 'app/strategies/v9/stocks.py'}
sources = {module: (root / file).read_text(encoding='utf-8') for module, file in files.items()}
sources['study'] = (root / 'scripts/v9_fix_validation.py').read_text(encoding='utf-8')
payload = json.dumps(sources)
digest = hashlib.sha256(payload.encode()).hexdigest()
bootstrap = f'''import json,types,sys
data=json.loads({payload!r})
for name,source in data.items():
    if name != 'study':
        module=types.ModuleType(name)
        module.__file__=name
        sys.modules[name]=module
        exec(compile(source,name,'exec'),module.__dict__)
SOURCE_SHA256={digest!r}
exec(compile(data['study'],'stock-fix-study','exec'))
'''
encoded = base64.b64encode(zlib.compress(bootstrap.encode(), 9)).decode()
assert len(encoded) < 25000, 'Payload exceeds the bounded command transport limit'
out = root / 'docs/stock-bot-audit'
(out / 'stock-fix-bootstrap.b64').write_text(encoded)
(out / 'stock-fix-study-source-hash.txt').write_text(digest)
print(f'Frozen source {digest}; compressed payload {len(encoded)} bytes')
