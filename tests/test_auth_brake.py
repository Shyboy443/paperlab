"""Dashboard auth middleware: brute-force brake (429 after too many wrong passwords) and security headers."""
import asyncio
import base64

from app.main import AUTH_MAX_FAILS, BasicAuthMiddleware


async def ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def call(mw, pw=None, path="/api/state", ip="1.2.3.4"):
    headers = [(b"x-forwarded-for", ip.encode())]
    if pw is not None:
        headers.append((b"authorization", b"Basic " + base64.b64encode(f"admin:{pw}".encode())))
    sent = []

    async def send(m):
        sent.append(m)
    asyncio.run(mw({"type": "http", "path": path, "method": "GET", "headers": headers}, None, send))
    start = sent[0]
    return start["status"], dict(start["headers"])


def test_wrong_passwords_lock_the_address_not_the_others():
    mw = BasicAuthMiddleware(ok_app, "right-password", False)
    assert call(mw, "right-password")[0] == 200
    for _ in range(AUTH_MAX_FAILS):
        assert call(mw, "guess")[0] == 401
    assert call(mw, "right-password")[0] == 429                      # locked, even with the right one
    assert call(mw, "right-password", ip="5.6.7.8")[0] == 200         # another address is not
    assert call(mw, None, path="/api/public/home")[0] == 200          # public pages stay open


def test_a_missing_password_is_not_counted_and_headers_are_set():
    mw = BasicAuthMiddleware(ok_app, "right-password", False)
    for _ in range(AUTH_MAX_FAILS + 5):
        assert call(mw, None)[0] == 401
    status, headers = call(mw, "right-password")
    assert status == 200 and headers[b"x-frame-options"] == b"DENY" and b"max-age" in headers[b"strict-transport-security"]
