import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from app.core.display_prices import DisplayPrices, STALE_MS
from app.core.events import EventBus
from app.core.realtime import Realtime


def ticker(seq=1, kind="snapshot", **data):
    return {"topic": "tickers.XRPUSDT", "type": kind, "cs": seq, "ts": 100000 + seq,
            "data": {"symbol": "XRPUSDT", **data}}


@pytest.fixture
def feed():
    f = DisplayPrices(EventBus(), ["XRPUSDT"], clock=lambda: 100.1)
    f.connected = True
    assert f.ingest(ticker(bid1Price="1.5", ask1Price="1.6", markPrice="1.55", fundingRate="0.0001"))
    return f


def test_delta_merges_unchanged_fields_and_rejects_reordered_messages(feed):
    assert feed.ingest(ticker(2, "delta", bid1Price="1.52"))
    q = feed.snapshot()["quotes"]["XRPUSDT"]
    assert q["mid"] == pytest.approx(1.56) and q["ask"] == 1.6 and q["mark"] == 1.55
    assert not feed.ingest(ticker(1, "delta", bid1Price="1.0"))
    assert not feed.ingest(ticker(2, "delta", bid1Price="1.0"))
    assert feed.snapshot()["quotes"]["XRPUSDT"]["mid"] == q["mid"]


@pytest.mark.parametrize("changes", [{"bid1Price": "NaN"}, {"ask1Price": "inf"},
                                      {"ask1Price": "0"}, {"bid1Price": "-1"},
                                      {"bid1Price": "1.7"}, {"bid1Price": ""}, {"symbol": "BTCUSDT"}])
def test_bad_updates_do_not_contaminate_quote_or_refresh_age(feed, changes):
    before = copy.deepcopy(feed.snapshot())
    assert not feed.ingest(ticker(2, "delta", **changes))
    assert feed.snapshot() == before


def test_delayed_source_and_disconnect_are_stale_even_if_recently_received(feed):
    feed.clock = lambda: 120.0
    assert feed.ingest(ticker(3, "delta", bid1Price="1.51"))
    assert feed.snapshot()["quotes"]["XRPUSDT"]["stale"]
    feed.clock = lambda: 100.1
    feed.connected = False
    assert feed.snapshot()["quotes"]["XRPUSDT"]["stale"]


def test_reconnect_requires_new_snapshot_and_resets_exchange_sequence(feed):
    feed._ready.clear()
    assert not feed.ingest(ticker(9, "delta", bid1Price="1.51"))
    assert feed.snapshot()["quotes"]["XRPUSDT"]["stale"]
    assert feed.ingest(ticker(1, bid1Price="1.4", ask1Price="1.5"))
    q = feed.snapshot()["quotes"]["XRPUSDT"]
    assert q["mid"] == 1.45 and not q["stale"]
    assert "mark" not in q  # Not inherited from the old connection.


def test_age_increases_without_new_ticks(feed):
    feed.clock = lambda: 106.0
    assert feed.snapshot()["quotes"]["XRPUSDT"]["age_ms"] > STALE_MS
    assert feed.snapshot()["quotes"]["XRPUSDT"]["stale"]


async def test_full_snapshots_fan_out_without_replay_or_private_fields(feed, monkeypatch):
    import app.core.display_prices as module
    monkeypatch.setattr(module, "PUBLISH_S", 0.01)
    feed.bus.bind(asyncio.get_running_loop())
    sub = feed.bus.subscribe(public_only=True)
    task = asyncio.create_task(feed._publish())
    try:
        first = await asyncio.wait_for(sub.queue.get(), 1)
        second = await asyncio.wait_for(sub.queue.get(), 1)
        assert first[1] == second[1] == "prices"
        a, b = json.loads(first[2]), json.loads(second[2])
        assert b["seq"] > a["seq"] and b["quotes"] == a["quotes"]
        assert feed.bus.backlog(0, True) == []
        assert a["display_only"] is True and "equity" not in first[2]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        feed.bus.unsubscribe(sub)


def test_reconnect_greeting_includes_current_price_snapshot(feed):
    rt = Realtime()
    rt.display_prices = feed
    for greeting in (rt.public_greeting(), rt.greeting()):
        assert dict(greeting)["prices"]["quotes"]["XRPUSDT"]["mid"] == 1.55


async def test_public_snapshot_route_works_without_trading_engine(feed):
    from app.core.api_stream import display_price_snapshot
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(realtime=SimpleNamespace(display_prices=feed))))
    result = await display_price_snapshot(request)
    assert result["display_only"] and result["quotes"]
    request.app.state.realtime.display_prices = None
    assert not (await display_price_snapshot(request))["connected"]


def test_no_trading_fingerprint_changes():
    from app.competition import v6_config, v7_config
    assert v6_config.verify_freeze(v6_config.load_freeze()) == []
    assert v7_config.verify_freeze(v7_config.load_freeze()) == []
