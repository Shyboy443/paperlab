"""The cost analyzer (the competition pages' "Cost analyzer" view) is operator-only, streams OpenRouter's answer, holds
one slot at a time and never lets the key out. The old /lab address redirects to the competition page."""
from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.ai.analyzer import ERROR_MARK, LEASE_S, Analyzer, AnalyzeInputError, AnalyzeRequest
from tests.conftest import settings_factory

KEY = "sk-or-v1-" + "a1b2c3d4" * 6
AUTH = ("admin", "test-pw")
POST = {"X-PaperLab": "1"}
BODY = {"fileName": "trades.csv", "content": "ts,side,net,fee\n1,long,0.5,0.02\n", "notes": "taker entries"}


def sse(*events: object, done: bool = True) -> list[bytes]:
    out = [b": OPENROUTER PROCESSING\n", b"\n"]
    for e in events:
        out += [b"data: " + json.dumps(e).encode() + b"\n", b"\n"]
    return out + ([b"data: [DONE]\n"] if done else [])


def delta(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


class Recorder:
    def __init__(self, status: int = 200, lines: list[bytes] | None = None, exc: Exception | None = None):
        self.status, self.lines, self.exc, self.calls = status, lines or [], exc, []

    def __call__(self, url, body, headers, timeout_s):
        self.calls.append((url, json.loads(body), dict(headers)))
        if self.exc:
            raise self.exc
        return self.status, iter(self.lines)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from app.main import create_app
    return create_app(settings_factory(data_dir=str(tmp_path / "d")))


def test_old_lab_addresses_redirect_to_the_competition_page(app):
    c = TestClient(app)
    for path, target in (("/lab", "/public/competition"), ("/lab/bots/v6--V6.6-XRP-1H", "/public/competition/bots"),
                         ("/lab/programs", "/public/competition/programs"), ("/lab/analyze", "/public/competition/analyzer")):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == target, path
        assert "www-authenticate" not in r.headers
    for path in ("/public/competition/programs", "/public/competition/analyzer"):     # client-side views of one shell
        assert c.get(path).status_code == 200


def test_analyzer_routes_need_the_password_and_the_csrf_header(app):
    c = TestClient(app)
    assert c.get("/api/analyzer/status").status_code == 401
    assert c.post("/api/analyzer", json=BODY, headers=POST).status_code == 401
    assert c.post("/api/analyzer", json=BODY, auth=AUTH).status_code == 403          # no X-PaperLab


def test_not_configured_without_the_key(app):
    c = TestClient(app)
    assert c.get("/api/analyzer/status", auth=AUTH).json()["configured"] is False
    r = c.post("/api/analyzer", json=BODY, headers=POST, auth=AUTH)
    assert r.status_code == 503 and "OPENROUTER_API_KEY" in r.json()["error"]


def test_streams_the_answer_and_sends_the_prompt_server_side(app, caplog):
    rec = Recorder(lines=sse(delta("## Summary\n"), delta("- 1 trade"), {"choices": [{"delta": {}}]}))
    app.state.analyzer = Analyzer(KEY, model="anthropic/claude-opus-5.5", stream=rec)
    c = TestClient(app)
    with caplog.at_level(logging.DEBUG):
        r = c.post("/api/analyzer", json={**BODY, "botContext": "name=Vector net=+3%"}, headers=POST, auth=AUTH)
    assert r.status_code == 200 and r.text == "## Summary\n- 1 trade"
    assert r.headers["x-analyzer-model"] == "anthropic/claude-opus-5.5"
    url, body, headers = rec.calls[0]
    assert url.startswith("https://openrouter.ai/") and body["stream"] is True
    assert body["model"] == "anthropic/claude-opus-5.5"
    assert body["messages"][0]["role"] == "system" and "## Testable changes" in body["messages"][0]["content"]
    user = body["messages"][1]["content"]
    assert "name=Vector" in user and "Operator notes: taker entries" in user and "1,long,0.5,0.02" in user
    assert headers["Authorization"] == "Bearer " + KEY                 # only on the server-to-OpenRouter request
    assert KEY not in r.text and KEY not in caplog.text and "1,long,0.5" not in caplog.text
    assert c.get("/api/analyzer/status", auth=AUTH).json()["busy"] is False      # slot released


@pytest.mark.parametrize("status,needle", [(402, "credits"), (429, "busy"), (404, "ANALYZER_MODEL"), (500, "500")])
def test_upstream_errors_become_a_sanitized_error_line(app, caplog, status, needle):
    leak = json.dumps({"error": {"message": f"bad key Bearer {KEY}"}}).encode()
    app.state.analyzer = Analyzer(KEY, stream=Recorder(status=status, lines=[leak]))
    with caplog.at_level(logging.WARNING):
        r = TestClient(app).post("/api/analyzer", json=BODY, headers=POST, auth=AUTH)
    assert r.status_code == 200 and r.text.strip().startswith(ERROR_MARK) and needle in r.text
    assert KEY not in r.text and KEY not in caplog.text


def test_mid_stream_error_and_network_failure(app):
    app.state.analyzer = Analyzer(KEY, stream=Recorder(lines=sse(delta("part"), {"error": {"message": "overloaded"}})))
    r = TestClient(app).post("/api/analyzer", json=BODY, headers=POST, auth=AUTH)
    assert r.text.startswith("part") and ERROR_MARK in r.text
    app.state.analyzer = Analyzer(KEY, stream=Recorder(exc=OSError(f"connect failed {KEY}")))
    r = TestClient(app).post("/api/analyzer", json=BODY, headers=POST, auth=AUTH)
    assert ERROR_MARK in r.text and KEY not in r.text
    app.state.analyzer = Analyzer(KEY, stream=Recorder(lines=sse()))
    assert "no answer" in TestClient(app).post("/api/analyzer", json=BODY, headers=POST, auth=AUTH).text


def test_bad_input_is_rejected_before_any_upstream_call(app):
    rec = Recorder(lines=sse(delta("x")))
    app.state.analyzer = Analyzer(KEY, stream=rec)
    c = TestClient(app)
    for bad in ({**BODY, "content": ""}, {**BODY, "content": "x" * 250_001}, {"content": "x"}, {**BODY, "notes": 5}):
        assert c.post("/api/analyzer", json=bad, headers=POST, auth=AUTH).status_code == 400
    assert c.post("/api/analyzer", content=b"not json", headers=POST, auth=AUTH).status_code == 400
    assert rec.calls == []
    with pytest.raises(AnalyzeInputError):
        AnalyzeRequest.parse([1, 2])


def test_one_analysis_at_a_time_and_the_lease_expires():
    now = [0.0]
    a = Analyzer(KEY, stream=Recorder(lines=sse(delta("ok"))), clock=lambda: now[0])
    assert a.acquire() and not a.acquire()
    now[0] += LEASE_S + 1                        # an abandoned stream frees the slot by itself
    assert a.acquire()
    assert "".join(a.run(AnalyzeRequest("f", "c"))) == "ok" and not a.busy


def test_busy_slot_answers_429(app):
    a = Analyzer(KEY, stream=Recorder(lines=sse(delta("ok"))))
    app.state.analyzer = a
    assert a.acquire()
    r = TestClient(app).post("/api/analyzer", json=BODY, headers=POST, auth=AUTH)
    assert r.status_code == 429


def test_the_key_never_shows_in_repr_or_state():
    a = Analyzer(KEY)
    assert KEY not in repr(a) and KEY not in str(vars(a)) and KEY not in json.dumps(a.status())
    assert Analyzer.from_env({"OPENROUTER_API_KEY": KEY}).model == "anthropic/claude-opus-5.5"
    assert Analyzer.from_env({"ANALYZER_MODEL": "anthropic/claude-sonnet-5.5"}).model == "anthropic/claude-sonnet-5.5"
