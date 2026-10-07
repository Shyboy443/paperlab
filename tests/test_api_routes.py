"""Smoke-test every read-only API route against a booted engine.

These routes are thin wrappers, but several do their imports inside the function body, so a module split
can silently break one without any other test noticing — that is exactly how `/api/tape` shipped broken
(`_fill_row` had moved to `app.core.payloads`). Calling each route once catches that class of bug.

The routes are invoked directly rather than through starlette's TestClient, which would pull in httpx as
a dependency the app itself does not need.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import api
from app.core.engine import EngineError

EXPORT_TABLES = ["fills", "signals", "orders", "equity", "events", "strategy_daily", "analysis_notes"]


def req(engine):
    """The only thing the route bodies use a Request for is `request.app.state`."""
    state = SimpleNamespace(engine=engine, settings=engine.settings, boot_error=None)
    return SimpleNamespace(app=SimpleNamespace(state=state))


async def drain(response) -> str:
    return "".join([chunk if isinstance(chunk, str) else chunk.decode()
                    async for chunk in response.body_iterator])


class TestReadRoutes:
    async def test_health(self, engine):
        body = await api.health(req(engine))
        assert body["ok"] is True and body["strategies"] == len(engine.strategies)

    async def test_state(self, engine):
        body = await api.state(req(engine), curves=0)
        assert body["epoch"] == engine.epoch and len(body["strategies"]) == len(engine.strategies)
        assert "journal" in body and "wallets" in body

    async def test_state_carries_the_exit_mix(self, engine):
        """The Overview reads the hold experiment off /api/state, so the block must always be present."""
        x = (await api.state(req(engine), curves=0))["exit_kinds"]
        assert {"kinds", "total", "time_share", "target_share", "fee_gt_r_today"} <= set(x)
        assert x["total"] == sum(v["n"] for v in x["kinds"].values())

    async def test_state_with_curves(self, engine):
        assert "equity_curves" in await api.state(req(engine), curves=1)

    async def test_strategies_list(self, engine):
        assert len((await api.strategies(req(engine)))["strategies"]) == len(engine.strategies)

    async def test_strategy_detail(self, engine):
        body = await api.strategy_detail(req(engine), "s01")
        assert body["id"] == "S01"
        assert {"scoreboard", "trades", "lives", "rejects_today", "notes"} <= set(body)

    async def test_strategy_trades(self, engine):
        body = await api.strategy_trades(req(engine), "S01", skip=0, limit=5)
        assert body["strategy_id"] == "S01" and "summary" in body and isinstance(body["trades"], list)

    async def test_analysis(self, engine):
        body = await api.analysis(req(engine), strategy=None, from_=None, to=None)
        assert {"daily", "totals", "rejects", "notes"} <= set(body)

    async def test_analysis_for_one_strategy(self, engine):
        assert (await api.analysis(req(engine), strategy="s01", from_=None, to=None))["strategy"] == "S01"

    async def test_equity_series(self, engine):
        assert "points" in await api.equity(req(engine), series="TOTAL", since_ts=0, points=10)

    async def test_candles(self, engine):
        body = await api.candles(req(engine), symbol="btcusdt", tf="1m", limit=5)
        assert body["symbol"] == "BTCUSDT"

    @pytest.mark.parametrize("limit", [10, 500])
    async def test_tape(self, engine, limit):
        """The regression that shipped: /api/tape 500'd because it imported a moved helper."""
        body = await api.tape(req(engine), limit=limit)
        assert set(body) == {"fills", "signals", "orders", "events"}
        for row in body["fills"]:
            assert {"strategy_id", "symbol", "side", "qty", "price", "kind", "pnl", "life"} <= set(row)

    async def test_notes_roundtrip(self, engine):
        posted = await api.post_note(req(engine), {"strategy_id": "S01", "text": "route smoke note"})
        assert posted["ok"]
        texts = [n["text"] for n in (await api.get_notes(req(engine), strategy="S01", limit=20))["notes"]]
        assert "route smoke note" in texts

    async def test_post_note_requires_text(self, engine):
        with pytest.raises(EngineError):
            await api.post_note(req(engine), {"strategy_id": "S01", "text": "  "})


class TestExport:
    @pytest.mark.parametrize("table", EXPORT_TABLES)
    async def test_every_table_streams_a_header(self, engine, table):
        resp = await api.export_csv(req(engine), table=table)
        disposition = resp.headers["content-disposition"]
        assert f"paperlab_{table}_{engine.epoch}_" in disposition
        assert (await drain(resp)).splitlines()[0].count(",") >= 1

    async def test_unknown_table_is_refused(self, engine):
        with pytest.raises(EngineError):
            await api.export_csv(req(engine), table="secrets")


class TestNotFound:
    async def test_unknown_strategy_detail(self, engine):
        with pytest.raises(EngineError):
            await api.strategy_detail(req(engine), "S99")

    async def test_unknown_strategy_trades(self, engine):
        with pytest.raises(EngineError):
            await api.strategy_trades(req(engine), "S99", skip=0, limit=10)
