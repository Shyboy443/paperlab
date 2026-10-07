"""PaperLab controls, persistence, exit management and access boundaries."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.api_video_breakout import router
from app.main import BasicAuthMiddleware
from app.video_breakout.engine import STEP
from app.video_breakout.service import VideoBreakoutService
from tests.test_video_breakout import bar


class Feed:
    def __init__(self):
        self.i = 42
        self.rows = [bar(i) for i in range(42)] + [bar(42, closed=False)]
        self.bad = False

    def __call__(self, symbol):
        if self.bad:
            raise OSError("Price feed unavailable")
        return self.i * STEP + 5000, self.rows

    def breakout(self):
        self.i = 43
        self.rows = [bar(i) for i in range(42)] + [bar(42, close=102), bar(43, close=110, closed=False)]

    def breakdown(self):
        self.i = 44
        self.rows = [bar(i) for i in range(42)] + [bar(42, close=102), bar(43, close=98), bar(44, close=90, closed=False)]


@pytest.fixture
async def svc(tmp_path):
    feed = Feed()
    s = VideoBreakoutService(tmp_path / "book.json", feed=feed)
    yield s, feed
    await s.close()


async def test_operator_start_uses_configuration_and_waits_for_next_close(svc):
    s, feed = svc
    out = await s.arm({"balance": 200, "allocation": .5, "fee_bps": 5})
    assert out["enabled"] and out["config"]["initial_cash"] == 200
    assert not s.bot.fills
    feed.breakout()
    await s.tick()
    assert len(s.bot.fills) == 1 and s.bot.cash == pytest.approx(100)
    await s.tick()
    assert len(s.bot.fills) == 1


async def test_pause_suppresses_new_buys_without_destroying_wallet(svc):
    s, feed = svc
    await s.arm({})
    await s.pause()
    feed.breakout()
    await s.tick()
    assert s.summary()["status"] == "PAUSED" and not s.bot.fills
    assert s.bot.signals[-1]["suppressed"] == "New entries paused"


async def test_pause_keeps_automatic_exit_management(svc):
    s, feed = svc
    await s.arm({})
    feed.breakout()
    await s.tick()
    await s.pause()
    assert s.summary()["status"] == "MANAGING_POSITION"
    feed.breakdown()
    await s.tick()
    assert s.bot.qty == 0 and len(s.bot.trades) == 1
    assert s.bot.trades[0]["exit_reason"] == "3-day breakdown"


async def test_manual_close_uses_fresh_quote_costs_and_pauses(svc):
    s, feed = svc
    await s.arm({})
    feed.breakout()
    await s.tick()
    qty = s.bot.qty
    out = await s.flatten()
    assert not out["enabled"] and out["qty"] == 0
    assert s.bot.cash == pytest.approx(qty * 110 * .9998 * .999)
    assert s.bot.trades[-1]["exit_reason"] == "manual"


async def test_failed_manual_close_preserves_position_and_disables_entries(svc):
    s, feed = svc
    await s.arm({})
    feed.breakout()
    await s.tick()
    feed.bad = True
    with pytest.raises(OSError):
        await s.flatten()
    assert s.bot.qty > 0 and not s.enabled and s.blocked
    persisted = json.loads(s.path.read_text())
    assert not persisted["enabled"] and persisted["book"]["qty"] > 0


async def test_restart_restores_wallet_and_does_not_repeat_fill(svc):
    s, feed = svc
    await s.arm({})
    feed.breakout()
    await s.tick()
    await s.close()
    restored = VideoBreakoutService(s.path, feed=feed)
    try:
        assert restored.enabled and restored.bot.qty == s.bot.qty
        await restored.tick()
        assert len(restored.bot.fills) == 1
    finally:
        await restored.close()


async def test_reset_archives_history_and_requires_paused_flat_book(svc):
    s, feed = svc
    await s.arm({})
    with pytest.raises(ValueError, match="Pause"):
        await s.reset()
    feed.breakout()
    await s.tick()
    await s.pause()
    with pytest.raises(ValueError, match="close"):
        await s.reset()
    await s.flatten()
    await s.reset()
    archives = list((s.path.parent / "video-breakout-history").glob("*.json"))
    assert len(archives) == 1
    assert len(json.loads(archives[0].read_text())["book"]["trades"]) == 1
    assert not s.bot.fills and s.bot.cash == 1000
    await s.arm({"balance": 300})
    assert s.bot.config.initial_cash == 300


async def test_configuration_cannot_rebase_existing_trades(svc):
    s, feed = svc
    await s.arm({})
    feed.breakout()
    await s.tick()
    await s.flatten()
    with pytest.raises(ValueError, match="trade history"):
        await s.arm({"balance": 9000})


async def test_network_error_blocks_and_explicit_resume_preserves_position(svc):
    s, feed = svc
    await s.arm({})
    feed.breakout()
    await s.tick()
    qty = s.bot.qty
    feed.bad = True
    await s.tick()
    assert s.blocked and not s.enabled and s.bot.qty == qty
    feed.bad = False
    feed.breakdown()
    await s.arm({})
    assert not s.blocked and s.enabled and s.bot.qty == qty
    assert len(s.bot.fills) == 1  # No retrospective liquidation during resume.


async def test_kill_gate_blocks_operator_start(svc):
    s, feed = svc
    s.can_enter = lambda: False
    with pytest.raises(ValueError, match="kill switch"):
        await s.arm({})
    assert not s.summary()["can_start"]


async def test_lab_kill_closes_breakout_book(svc):
    from app.core.api import kill
    s, feed = svc
    await s.arm({})
    feed.breakout()
    await s.tick()
    class Engine:
        async def kill(self):
            return {"closed_positions": 0}
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=Engine(), video_breakout=s)))
    out = await kill(req)
    assert out["video_breakout"]["qty"] == 0
    assert s.bot.trades[-1]["exit_reason"] == "lab kill switch"
    assert not s.enabled


async def test_api_requires_password_and_csrf_header(svc):
    s, feed = svc
    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware, password="test-pw", allow_no_auth=False)
    app.include_router(router)
    app.state.video_breakout = s
    with TestClient(app) as client:
        assert client.get("/api/video-breakout").status_code == 401
        assert client.post("/api/video-breakout/start", json={}).status_code == 401
        client.auth = ("admin", "test-pw")
        assert client.post("/api/video-breakout/start", json={}).status_code == 403
        assert client.get("/api/video-breakout").json()["mode"] == "PAPER"
        assert client.post("/api/video-breakout/start", json={}, headers={"X-PaperLab": "1"}).json()["enabled"]
        assert client.post("/api/video-breakout/pause", json={}, headers={"X-PaperLab": "1"}).json()["status"] == "PAUSED"
        assert client.post("/api/video-breakout/start", json=[], headers={"X-PaperLab": "1"}).status_code == 400


async def test_page_and_navigation_exist_on_full_app(tmp_path):
    from app.main import create_app
    from tests.conftest import settings_factory
    app = create_app(settings_factory(data_dir=str(tmp_path)))
    # No context manager: check routes without booting exchange workers.
    client = TestClient(app)
    page = client.get("/public/video-breakout")
    assert page.status_code == 200 and 'breakout-competition.js' in page.text
    assert page.url.path == '/' and page.url.params['p'] == 'video'
    assert 'bot=BTC-4H-BREAKOUT' in client.get("/").text
    assert 'bot=BTC-4H-BREAKOUT' in client.get("/public/competition").text
    assert client.get("/api/video-breakout").status_code == 401
    assert client.get("/static/breakout-competition.js").status_code == 200


async def test_competition_preserves_forward_wallet_and_costs(svc):
    from app.video_breakout.competition import KEY, payload, candles
    from app.core.roster_view import build
    s, feed = svc
    await s.arm({})
    feed.breakout()
    await s.tick()
    qty, fees = s.bot.qty, s.summary()['total_fees']
    p = payload(s)
    row = p['leaderboard'][0]
    assert row['key'] == KEY and row['fees'] == fees
    assert row['equity_now'] == pytest.approx(s.bot.cash + qty * 110)
    assert row['open_positions'][0]['qty'] == qty
    assert row['open_positions'][0]['stop'] is None
    out = build(SimpleNamespace(video_breakout=s))
    assert len(out['scanners']) == 1 and out['kpis']['bots_total'] == 1
    from app.core.bot_names import NAMES
    assert out['names']['video|' + KEY]['name'] in NAMES          # every bot carries a champion name
    assert out['scanners'][0]['live'] and '_st' not in out['scanners'][0]
    assert out['stream'][0]['kind'] == 'open'
    assert len(candles(s)['candles']) == 42
    feed.breakdown()
    await s.tick()
    p = payload(s)
    assert p['trades'][0]['net'] == s.bot.trades[-1]['pnl']
    assert p['trades'][0]['exit_kind'] == '3-day breakdown'
    out = build(SimpleNamespace(video_breakout=s))
    assert out['kpis']['all_trades'] == 1
    assert out['ideas'][0]['bots'][0]['net'] == s.bot.trades[-1]['pnl']


async def test_public_competition_contract_and_private_control_boundary(svc):
    from app.core.api_public import router as public_router
    from app.video_breakout.competition import KEY
    s, feed = svc
    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware, password='test-pw', allow_no_auth=False)
    app.include_router(router)
    app.include_router(public_router)
    app.state.video_breakout = s
    await s.arm({})
    with TestClient(app) as client:
        assert client.get('/api/public/competition/video').status_code == 200
        data = client.get('/api/public/competition/video/bot/' + KEY).json()
        assert data['read_only'] and data['leaderboard'][0]['key'] == KEY
        assert not any(k in json.dumps(data) for k in ['book.json', 'journal', 'password', 'provider'])
        assert client.get('/api/public/competition/video/candles?tf=4h').json()['candles']
        assert client.get('/api/public/competition/video/candles?tf=1h').status_code == 400
        assert client.get('/api/public/competition/video/bot/no-such-bot').status_code == 404
        assert client.post('/api/video-breakout/pause', json={}).status_code == 401
        assert s.enabled
        from app.core.roster_view import invalidate
        invalidate()
        cached = client.get('/api/public/competition/roster').json()
        assert cached['scanners'][0]['start_equity'] == 1000
        client.auth = ('admin', 'test-pw')
        assert client.post('/api/video-breakout/pause', json={}, headers={'X-PaperLab': '1'}).status_code == 200
        assert client.get('/api/public/competition/roster').json()['scanners'][0]['status'] == 'PAUSED'
        changed = client.post('/api/video-breakout/start', json={'balance': 400}, headers={'X-PaperLab': '1'})
        assert changed.status_code == 200
        assert client.get('/api/public/competition/roster').json()['scanners'][0]['start_equity'] == 400


async def test_full_app_lifecycle_registers_and_restores_service(tmp_path, monkeypatch):
    from app.main import create_app
    from app.core.engine import Engine
    from tests.conftest import settings_factory
    async def no_exchange_boot(self):
        pass
    feed = Feed()
    async def local_feed(self):
        return feed("BTCUSDT")
    monkeypatch.setattr(Engine, "boot", no_exchange_boot)
    monkeypatch.setattr(VideoBreakoutService, "read_feed", local_feed)
    settings = settings_factory(data_dir=str(tmp_path))
    app = create_app(settings)
    with TestClient(app) as client:
        client.auth = ("admin", "test-pw")
        started = client.post("/api/video-breakout/start", json={}, headers={"X-PaperLab": "1"})
        assert started.status_code == 200 and started.json()["enabled"]
    with TestClient(create_app(settings)) as client:
        client.auth = ("admin", "test-pw")
        assert client.get("/api/video-breakout").json()["enabled"]
