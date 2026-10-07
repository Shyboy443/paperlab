"""Realtime stream: the SSE bus, the state delta publisher, the storage write hooks and the routes.

The contract that matters:
* a client is PUSHED changes; nothing here depends on the browser asking again;
* the public stream carries only events published as public;
* a state delta applies only on top of the snapshot it was computed from (seq chain), so a client
  that missed one can tell, instead of silently drifting.
"""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from app.core import events as events_mod
from app.core.events import EventBus, last_event_id
from app.core.realtime import Realtime, StatePublisher
from app.core.storage import Storage
from app.core.types import Fill
from tests.conftest import settings_factory


def parse(frame: str) -> dict:
    out: dict = {}
    for line in frame.strip().splitlines():
        if line.startswith(":"):
            out["comment"] = line[1:].strip()
            continue
        k, _, v = line.partition(": ")
        out[k] = v
    if "data" in out:
        out["data"] = json.loads(out["data"])
    return out


async def frames(gen, n: int, timeout: float = 2.0) -> list[dict]:
    got = []
    for _ in range(n):
        got.append(parse(await asyncio.wait_for(gen.__anext__(), timeout)))
    return got


class TestBus:
    async def test_live_events_reach_a_subscriber(self):
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        gen = bus.stream(None, public_only=False)
        head = await frames(gen, 2)                      # retry + hello
        assert head[0]["retry"] == "3000" and head[1]["event"] == "hello"
        bus.publish("fill", {"symbol": "SOLUSDT"})
        f = (await frames(gen, 1))[0]
        assert f["event"] == "fill" and f["data"]["symbol"] == "SOLUSDT" and int(f["id"]) >= 1
        await gen.aclose()
        assert bus.clients == 0

    async def test_publish_from_a_worker_thread(self):
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        gen = bus.stream(None, public_only=False)
        await frames(gen, 2)
        t = threading.Thread(target=lambda: bus.publish("jev", {"action": "SKIP"}, public=True))
        t.start()
        t.join()
        f = (await frames(gen, 1))[0]
        assert f["event"] == "jev" and f["data"]["action"] == "SKIP"
        await gen.aclose()

    async def test_public_stream_never_sees_private_events(self):
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        gen = bus.stream(None, public_only=True)
        await frames(gen, 2)
        bus.publish("fill", {"secret_ish": 1})             # private by default
        bus.publish("state", {"seq": 1}, replay=False)
        bus.publish("competition", {"kind": "arena"}, public=True)
        f = (await frames(gen, 1))[0]
        assert f["event"] == "competition"
        await gen.aclose()

    async def test_reconnect_replays_the_backlog_after_last_id(self):
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        a = bus.publish("note", {"n": 1})
        bus.publish("state", {"seq": 9}, replay=False)     # never replayed
        bus.publish("note", {"n": 2})
        gen = bus.stream(a, public_only=False)
        got = await frames(gen, 3)                          # retry, note 2, hello
        assert got[1]["event"] == "note" and got[1]["data"]["n"] == 2
        assert got[2]["event"] == "hello" and got[2]["data"]["resumed"] is True
        await gen.aclose()

    async def test_heartbeat_keeps_an_idle_stream_open(self, monkeypatch):
        monkeypatch.setattr(events_mod, "HEARTBEAT_S", 0.05)
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        gen = bus.stream(None, public_only=True)
        await frames(gen, 2)
        assert (await frames(gen, 1))[0]["comment"] == "keep-alive"
        await gen.aclose()

    async def test_greeting_follows_hello_without_an_id(self):
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        gen = bus.stream(None, public_only=False, greeting=lambda: [("state", {"full": True, "seq": 4})])
        got = await frames(gen, 3)
        assert got[2]["event"] == "state" and got[2]["data"]["full"] is True and "id" not in got[2]
        await gen.aclose()

    def test_last_event_id_parsing(self):
        assert last_event_id("42") == 42
        assert last_event_id("") is None and last_event_id(None) is None and last_event_id("x") is None


class TestStateDeltas:
    def test_first_tick_is_complete_then_only_changes(self):
        box = {"ts": 1, "equity": {"total": 100.0}, "prices": {"SOL": 1.0},
               "strategies": [{"id": "S01", "equity": 20.0}, {"id": "S02", "equity": 20.0}]}
        pub = StatePublisher(lambda: json.loads(json.dumps(box)))
        d1 = pub.tick()
        assert d1["seq"] == 1 and d1["base"] == 0
        assert set(d1["set"]) == {"equity", "prices"} and set(d1["rows"]) == {"S01", "S02"}
        assert d1["order"] == ["S01", "S02"]
        box["ts"] = 2                                    # ts alone is not a change
        assert pub.tick() is None
        box["prices"] = {"SOL": 1.1}
        box["strategies"][1]["equity"] = 20.5
        d2 = pub.tick()
        assert d2["seq"] == 2 and d2["base"] == 1
        assert set(d2["set"]) == {"prices"} and set(d2["rows"]) == {"S02"} and d2["order"] is None

    def test_removed_blocks_and_rows_are_reported(self):
        box = {"ts": 1, "a": 1, "b": 2, "strategies": [{"id": "S01"}, {"id": "S02"}]}
        pub = StatePublisher(lambda: dict(box))
        pub.tick()
        box.pop("b")
        box["strategies"] = [{"id": "S01"}]
        d = pub.tick()
        assert d["unset"] == ["b"] and d["order"] == ["S01"]

    def test_full_snapshot_matches_the_seq(self):
        pub = StatePublisher(lambda: {"ts": 1, "x": 1, "strategies": []})
        pub.tick()
        full = pub.full()
        assert full["full"] is True and full["seq"] == 1 and full["state"]["x"] == 1


class TestStorageHooks:
    def test_fills_signals_and_notes_are_announced_after_the_write(self, tmp_path):
        st = Storage(str(tmp_path / "t.db"))
        seen: list[tuple[str, dict]] = []

        def listener(kind, data):
            # the row must already be readable when the stream hears about it
            if kind == "note":
                assert any(n["id"] == data["id"] for n in st.notes(1, limit=5))
            seen.append((kind, data))

        st.on_write = listener
        st.insert_note(1, "config", "hello")
        f = Fill(id="f1", ts=1, epoch=1, strategy_id="S01", symbol="SOLUSDT", position_id="p1",
                 side="buy", qty=1.0, price=100.0, fee=0.05, slippage_bps=1.0, kind="entry",
                 realized_pnl=0.0, simulated=True)
        st.record_fill(f)
        assert [k for k, _ in seen] == ["note", "fill"]
        assert seen[1][1]["symbol"] == "SOLUSDT" and seen[1][1]["kind"] == "entry"
        st.close()

    def test_a_broken_listener_cannot_fail_a_write(self, tmp_path):
        st = Storage(str(tmp_path / "t.db"))
        st.on_write = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
        assert st.insert_note(1, "config", "still stored") > 0
        st.close()


class TestRealtime:
    def test_public_health_carries_no_engine_or_feed_detail(self):
        rt = Realtime()
        rt.engine = SimpleNamespace(state="running", paused_reason=None, feed=None)
        pub = rt.health_payload(private=False)
        assert "engine" not in pub and "feed" not in pub and pub["stream"]["status"] == "LIVE"
        priv = rt.health_payload(private=True)
        assert priv["engine"]["state"] == "running"

    async def test_competition_watch_publishes_only_changes(self, tmp_path):
        from app.competition.service import CompetitionService
        st = Storage(str(tmp_path / "t.db"))
        svc = CompetitionService(settings_factory(data_dir=str(tmp_path)), st, {})
        rt = Realtime()
        rt.competition = svc
        rt.bus.bind(asyncio.get_running_loop())
        first = rt.competition_summary()
        assert first["season"]["status"] == "idle"
        st.close()


class TestRoutes:
    def test_private_stream_requires_auth(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from app.core import engine_boot
        from app.main import create_app
        from tests.conftest import FakeFeed
        monkeypatch.setattr(engine_boot, "MarketFeed", FakeFeed)
        app = create_app(settings_factory(data_dir=str(tmp_path / "data"), SYMBOLS="BTCUSDT"))
        with TestClient(app) as c:
            assert c.get("/api/stream").status_code == 401
            assert c.post("/api/public/stream").status_code in (401, 403, 405)

    async def test_private_route_greets_with_a_full_state_snapshot(self, engine):
        from app.core import api_stream
        rt = Realtime()
        rt.attach(engine=engine)
        rt.bus.bind(asyncio.get_running_loop())
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(realtime=rt)), headers={})
        resp = await api_stream.private_stream(request, last_id="")
        assert resp.media_type == "text/event-stream"
        it = resp.body_iterator
        got = [parse(await asyncio.wait_for(it.__anext__(), 2)) for _ in range(3)]
        assert got[1]["event"] == "hello" and got[1]["data"]["public"] is False
        assert got[2]["event"] == "state" and got[2]["data"]["full"] is True
        assert got[2]["data"]["state"]["epoch"] == engine.epoch
        await it.aclose()

    async def test_public_route_greets_with_health_only(self, engine):
        from app.core import api_stream
        rt = Realtime()
        rt.attach(engine=engine)
        rt.bus.bind(asyncio.get_running_loop())
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(realtime=rt)), headers={})
        resp = await api_stream.public_stream(request, last_id="")
        it = resp.body_iterator
        got = [parse(await asyncio.wait_for(it.__anext__(), 2)) for _ in range(3)]
        assert got[1]["data"]["public"] is True
        assert got[2]["event"] == "health" and "engine" not in got[2]["data"]
        await it.aclose()


# ---- WebSocket transport (primary) ------------------------------------------------------------------

@pytest.fixture
def wsclient(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.core import engine_boot
    from app.main import create_app
    from tests.conftest import FakeFeed
    monkeypatch.setattr(engine_boot, "MarketFeed", FakeFeed)
    app = create_app(settings_factory(data_dir=str(tmp_path / "data"), SYMBOLS="BTCUSDT"))
    with TestClient(app) as c:
        yield c


def _basic(pw: str = "test-pw") -> str:
    import base64
    return "Basic " + base64.b64encode(f"admin:{pw}".encode()).decode()


class TestWebSocket:
    def test_public_socket_greets_and_carries_only_public_events(self, wsclient):
        with wsclient.websocket_connect("/api/public/ws") as ws:
            ws.send_json({"type": "hello"})
            hello = ws.receive_json()
            assert hello["event"] == "hello" and hello["data"]["transport"] == "websocket" and hello["data"]["public"]
            health = ws.receive_json()
            assert health["event"] == "health" and "engine" not in health["data"]
            bus = wsclient.app.state.realtime.bus
            bus.publish("fill", {"secret_ish": 1})                       # private: must never arrive
            bus.publish("competition", {"kind": "arena"}, public=True)
            msg = ws.receive_json()
            assert msg["event"] == "competition" and isinstance(msg["id"], int) and msg["data"]["kind"] == "arena"

    def test_private_socket_without_the_password_is_refused(self, wsclient):
        from starlette.websockets import WebSocketDisconnect
        with wsclient.websocket_connect("/api/ws") as ws:
            ws.send_json({"type": "hello", "authorization": _basic("wrong")})
            assert ws.receive_json()["event"] == "unauthorized"
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4401

    def test_private_socket_with_the_password_gets_state(self, wsclient):
        # The transport's job: authenticate, then hello, then the per-client greeting, then events.
        # (The snapshot's CONTENT is tested against a booted engine in TestRoutes.)
        rt = wsclient.app.state.realtime
        rt.greeting = lambda: [("state", {"full": True, "seq": 7, "state": {"epoch": 3}}), ("health", {"ok": 1})]
        with wsclient.websocket_connect("/api/ws") as ws:
            ws.send_json({"type": "hello", "authorization": _basic()})
            hello = ws.receive_json()
            assert hello["event"] == "hello" and hello["data"]["public"] is False
            first = ws.receive_json()
            assert first["event"] == "state" and first["data"]["full"] is True and "id" not in first
            assert first["data"]["state"]["epoch"] == 3
            assert ws.receive_json()["event"] == "health"
            rt.bus.publish("fill", {"symbol": "BTCUSDT"})
            msg = ws.receive_json()
            while msg["event"] in ("state", "health", "ping"):      # producers may interleave
                msg = ws.receive_json()
            assert msg["event"] == "fill" and msg["data"]["symbol"] == "BTCUSDT"

    def test_a_cross_origin_browser_cannot_open_the_private_socket(self, wsclient):
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with wsclient.websocket_connect("/api/ws", headers={"origin": "https://evil.example"}) as ws:
                ws.receive_json()

    def test_resume_replays_what_was_missed(self, wsclient):
        bus = wsclient.app.state.realtime.bus
        a = bus.publish("competition", {"n": 1}, public=True)
        bus.publish("competition", {"n": 2}, public=True)
        with wsclient.websocket_connect("/api/public/ws") as ws:
            ws.send_json({"type": "hello", "last_id": a})
            first = ws.receive_json()
            assert first["event"] == "competition" and first["data"]["n"] == 2
            hello = ws.receive_json()
            assert hello["event"] == "hello" and hello["data"]["resumed"] is True

    def test_a_silent_client_is_dropped(self, wsclient, monkeypatch):
        from starlette.websockets import WebSocketDisconnect
        from app.core import api_stream
        monkeypatch.setattr(api_stream, "HELLO_TIMEOUT_S", 0.05)
        with wsclient.websocket_connect("/api/public/ws") as ws:
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4400
