"""V5 runtime focus: BAKEOFF_ENABLED=false stops the frozen V1 bake-off -- every book boots disarmed, nothing
respawns, open PAPER positions close on boot (dry run only; a live router is never touched) -- while the engine
itself (feed, RiskManager, kill switch) keeps running. The default stays on, so nothing else changes."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import engine_boot
from app.core.storage import Storage
from tests.conftest import FixedClock, boot_engine, settings_factory


def test_default_is_unchanged(tmp_path):
    assert settings_factory(data_dir=str(tmp_path / "d")).bakeoff_enabled is True
    assert settings_factory(data_dir=str(tmp_path / "d"), BAKEOFF_ENABLED="false").bakeoff_enabled is False


@pytest.mark.asyncio
async def test_stopped_bakeoff_boots_disarmed_and_never_respawns(tmp_path, monkeypatch):
    s = settings_factory(data_dir=str(tmp_path / "data"), SYMBOLS="BTCUSDT", BAKEOFF_ENABLED="false")
    storage = Storage(str(tmp_path / "paperlab.db"))
    e = await boot_engine(s, storage, monkeypatch, clock=FixedClock())
    try:
        assert e.meta and not any(m.enabled for m in e.meta.values())
        assert e.boot_report["bakeoff"]["stopped"] is True
        assert e.boot_report["bakeoff"]["books_disarmed"] == len(e.meta)
        assert e.state in ("running", "paused")                 # the engine itself keeps running
        sid = next(iter(e.meta))
        e.meta[sid].halted = True
        assert await engine_boot.respawn_halted(e) == []        # a stopped bake-off never re-arms a book
        assert not e.meta[sid].enabled
    finally:
        await e.stop()
        storage.close()


@pytest.mark.asyncio
async def test_paper_positions_close_only_in_dry_run(monkeypatch):
    closed = []

    async def fake_close(e, pos, fraction, price, kind, reason):
        closed.append((pos.symbol, fraction, kind, reason))

    monkeypatch.setattr(engine_boot.engine_dispatch, "close_position", fake_close)

    def fake_engine(dry_run):
        pos = SimpleNamespace(symbol="SOLUSDT", entry_price=100.0)
        return SimpleNamespace(settings=SimpleNamespace(bakeoff_enabled=False), router=SimpleNamespace(dry_run=dry_run),
                               portfolio=SimpleNamespace(positions={"p1": pos}), last={"SOLUSDT": 101.0},
                               meta={"S01": SimpleNamespace(enabled=False)}, boot_report={},
                               event=lambda *a, **k: None)
    assert await engine_boot.stop_bakeoff(fake_engine(True)) == 1
    assert closed == [("SOLUSDT", 1.0, "halt", "bake-off stopped (BAKEOFF_ENABLED=false)")]
    closed.clear()
    assert await engine_boot.stop_bakeoff(fake_engine(False)) == 0 and closed == []   # a live router: never
