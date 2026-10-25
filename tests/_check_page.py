from fastapi.testclient import TestClient
from app.main import create_app
a = create_app()
c = TestClient(a)
r = c.get('/public/value-reversal')
print('status:', r.status_code, '| bytes:', len(r.content))
print('contains live-signals section:', b'live-signals' in r.content)
print('contains refreshLive fn:', b'function refreshLive' in r.content)
print('contains 5s interval:', b'setInterval(refreshLive,5000)' in r.content)
