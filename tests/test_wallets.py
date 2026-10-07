"""The isolated-book contract: 20 wallets of STRATEGY_STARTING_BALANCE, all armed, never sharing cash.

Covers boot on an empty DB, reset_paper, wallet isolation, the per-wallet halt floor, the rebase of a
database written by the old shared-pool model, and the set_allocation guards.
"""
from __future__ import annotations

import json

import pytest

from app.core import engine_boot
from app.core.engine import EngineError
from app.core.engine_boot import wallet_model_marker
from app.core.storage import Storage
from app.core.types import Fill, VirtualPosition
from app.strategies.registry import STRATEGY_IDS
from tests.conftest import FixedClock, boot_engine, settings_factory

BALANCE = 100.0
N = len(STRATEGY_IDS)  # 20
PAPER_TOTAL = BALANCE * N  # 2000


def assert_isolated_books(e, balance: float = BALANCE) -> None:
    """Every registered strategy: its own wallet at `balance`, armed, halt floor 75% of ITS book."""
    assert set(e.portfolio.wallets) == set(STRATEGY_IDS)
    for sid in STRATEGY_IDS:
        w = e.portfolio.wallets[sid]
        assert w.allocation == pytest.approx(balance), sid
        assert e.meta[sid].enabled is True, sid
        assert e.meta[sid].size_mult == pytest.approx(1.0), sid
        assert e.meta[sid].halted is False, sid
        assert e.risk.state.halt_floors[sid] == pytest.approx(0.75 * balance), sid
    assert e.portfolio.total_equity(e.prices()) == pytest.approx(balance * N)
    assert e.settings.paper_total(N) == pytest.approx(balance * N)
    assert e.risk.state.halted_strategies == set()


class TestFirstBoot:
    async def test_every_strategy_gets_its_own_100_usdt_book(self, engine):
        assert engine.boot_report["first_boot"] is True
        assert engine.boot_report["needs_rebase"] is False
        assert len(engine.strategies) == N
        assert_isolated_books(engine)
        assert engine.portfolio.total_equity(engine.prices()) == pytest.approx(PAPER_TOTAL)

    async def test_engine_is_running_not_paused(self, engine):
        assert engine.state == "running"
        assert engine.paused_reason is None
        assert engine.risk.state.engine_running is True

    async def test_wallet_model_marker_and_first_boot_event(self, engine):
        assert engine.storage.get_meta("wallet_model") == wallet_model_marker(BALANCE) == "isolated:100"
        kinds = [ev["kind"] for ev in engine.storage.events_recent(50)]
        assert "first_boot" in kinds
        payload = next(ev["payload"] for ev in engine.storage.events_recent(50) if ev["kind"] == "first_boot")
        assert payload["balance_each"] == BALANCE
        assert payload["paper_total"] == PAPER_TOTAL
        assert len(payload["enabled"]) == N

    async def test_boot_persists_every_strategy_state(self, engine):
        rows = engine.storage.strategy_states()
        assert len(rows) == N
        assert all(r["enabled"] for r in rows.values())
        assert all(r["allocation"] == pytest.approx(BALANCE) for r in rows.values())
        assert all(r["halt_floor"] == pytest.approx(75.0) for r in rows.values())

    async def test_a_custom_balance_scales_every_book(self, tmp_path, monkeypatch):
        s = settings_factory(data_dir=str(tmp_path / "d"), SYMBOLS="BTCUSDT", balance=250)
        storage = Storage(":memory:")
        e = await boot_engine(s, storage, monkeypatch, clock=FixedClock())
        try:
            assert_isolated_books(e, 250.0)
            assert e.portfolio.total_equity(e.prices()) == pytest.approx(250.0 * N)
            assert storage.get_meta("wallet_model") == "isolated:250"
        finally:
            await e.stop()
            storage.close()


class TestResetPaper:
    async def test_reset_rebuilds_the_same_isolated_books_in_a_new_epoch(self, engine):
        old_epoch = engine.epoch
        engine.portfolio.wallets["S01"].realized -= 12.0
        await engine.set_enabled("S02", False)
        out = await engine.reset_paper(confirm=True)
        assert out["ok"] and out["epoch"] == old_epoch + 1 and out["enabled"] == N
        assert engine.epoch == old_epoch + 1
        assert engine.portfolio.epoch == engine.epoch
        assert_isolated_books(engine)
        assert engine.storage.get_meta("wallet_model") == wallet_model_marker(BALANCE)

    async def test_reset_needs_confirm(self, engine):
        with pytest.raises(EngineError):
            await engine.reset_paper(confirm=False)
        assert engine.epoch == 1

    async def test_previous_epoch_rows_are_untouched(self, engine):
        old_epoch = engine.epoch
        fill = Fill(id="keepme", ts=engine.clock(), epoch=old_epoch, strategy_id="S01", symbol="BTCUSDT",
                    position_id="p-old", side="BUY", qty=0.01, price=65000.0, fee=0.26, slippage_bps=2.0,
                    kind="entry", realized_pnl=0.0, simulated=True, is_open=True)
        engine.storage.insert_fill(fill)
        before = dict(engine.storage.conn.execute("SELECT * FROM fills WHERE id='keepme'").fetchone())
        await engine.reset_paper(confirm=True)
        after = dict(engine.storage.conn.execute("SELECT * FROM fills WHERE id='keepme'").fetchone())
        assert after == before
        assert [f.id for f in engine.storage.fills_since(old_epoch)] == ["keepme"]
        assert engine.storage.fills_since(engine.epoch) == []


class TestIsolation:
    async def test_a_loss_on_one_book_leaves_every_other_book_alone(self, engine):
        prices = engine.prices()
        before_s06 = engine.portfolio.wallet_equity("S06", prices)
        before_total = engine.portfolio.total_equity(prices)
        engine.portfolio.wallets["S01"].fees += 7.5
        engine.portfolio.wallets["S01"].realized -= 2.5
        assert engine.portfolio.wallet_equity("S01", prices) == pytest.approx(90.0)
        assert engine.portfolio.wallet_equity("S06", prices) == pytest.approx(before_s06)
        assert engine.portfolio.total_equity(prices) == pytest.approx(before_total - 10.0)
        assert all(engine.portfolio.wallet_equity(sid, prices) == pytest.approx(BALANCE)
                   for sid in STRATEGY_IDS if sid != "S01")

    async def test_disarming_a_strategy_moves_no_capital(self, engine):
        out = await engine.set_enabled("S03", False)
        assert out["allocation"] == pytest.approx(BALANCE)
        assert engine.portfolio.wallets["S03"].allocation == pytest.approx(BALANCE)
        assert engine.portfolio.total_equity(engine.prices()) == pytest.approx(PAPER_TOTAL)
        assert all(w.allocation == pytest.approx(BALANCE) for w in engine.portfolio.wallets.values())


class TestHaltFloor:
    async def test_floor_is_75_on_a_100_book_not_75pct_of_the_paper_total(self, engine):
        assert engine.risk.state.halt_floors["S01"] == pytest.approx(75.0)
        assert engine.risk.state.halt_floors["S01"] != pytest.approx(0.75 * PAPER_TOTAL)
        assert engine.risk.check_strategy_halt("S01", 75.1) is False
        assert "S01" not in engine.risk.state.halted_strategies
        assert engine.risk.check_strategy_halt("S01", 74.9) is True
        assert "S01" in engine.risk.state.halted_strategies
        assert engine.risk.state.halted_strategies == {"S01"}, "one book halting must not halt the others"


# ---- migration from the old shared-pool model -------------------------------------------------

OLD_ALLOCATION = 500.0


def _write_old_model_db(path, clock: FixedClock) -> tuple[Storage, dict]:
    """A DB as the previous capital model left it: 500-per-slice, only S01+S06 armed, no wallet_model."""
    st = Storage(str(path))
    for sid in STRATEGY_IDS:
        enabled = 1 if sid in ("S01", "S06") else 0
        st.upsert_strategy_state(sid, enabled=enabled, allocation=OLD_ALLOCATION, leverage=15,
                                 size_mult=0.5, params={}, halted=0, halt_floor=0.75 * OLD_ALLOCATION)
    pos = VirtualPosition(id="oldpos", strategy_id="S01", symbol="BTCUSDT", side="long", qty=0.01,
                          qty_initial=0.01, entry_price=64000.0, entry_ts=clock() - 600_000, leverage=15,
                          margin=64000.0 * 0.01 / 15, stop=0.0, take_profits=[], trail=None, be_at_r=0.0,
                          max_hold_deadline=None, initial_risk_usd=20.0,
                          extreme_price=64000.0, tf="1m")
    st.upsert_position(pos, 1)
    entry = Fill(id="oldfill", ts=clock() - 600_000, epoch=1, strategy_id="S01", symbol="BTCUSDT",
                 position_id="oldpos", side="BUY", qty=0.01, price=64000.0, fee=0.256, slippage_bps=2.0,
                 kind="entry", realized_pnl=0.0, simulated=True, meta={"stop": 63800.0}, ref_price=64000.0,
                 leverage=15, position_side="long", is_open=True)
    st.insert_fill(entry)
    row = dict(st.conn.execute("SELECT * FROM fills WHERE id='oldfill'").fetchone())
    assert st.get_meta("wallet_model") is None
    return st, row


@pytest.fixture
async def rebased(tmp_path, monkeypatch):
    clock = FixedClock()
    storage, old_fill_row = _write_old_model_db(tmp_path / "old.db", clock)
    s = settings_factory(data_dir=str(tmp_path / "d"), SYMBOLS="BTCUSDT")
    e = await boot_engine(s, storage, monkeypatch, clock=clock)
    yield e, old_fill_row
    await e.stop()
    storage.close()


class TestRebaseOfAnOlderVolume:
    async def test_rebase_fires_and_bumps_the_epoch(self, rebased):
        e, _ = rebased
        assert e.boot_report["first_boot"] is False
        assert e.boot_report["needs_rebase"] is True
        assert e.boot_report["rebased"] == {"from_epoch": 1, "to_epoch": 2, "closed_positions": 1,
                                            "balance_each": BALANCE}
        assert e.epoch == 2 and e.storage.epoch() == 2
        assert e.storage.get_meta("wallet_model") == wallet_model_marker(BALANCE)

    async def test_open_position_is_flattened_into_the_old_epoch(self, rebased):
        e, _ = rebased
        rebase_fills = [f for f in e.storage.fills_since(1) if f.kind == "wallet_rebase"]
        assert len(rebase_fills) == 1
        f = rebase_fills[0]
        assert f.epoch == 1, "the closing fill belongs to the run it closes"
        assert f.position_id == "oldpos" and f.is_open is False
        assert e.storage.fills_since(2) == []
        assert e.portfolio.positions == {}
        assert e.storage.open_positions(1) == [] and e.storage.open_positions(2) == []

    async def test_old_fills_are_not_rewritten(self, rebased):
        e, old_fill_row = rebased
        after = dict(e.storage.conn.execute("SELECT * FROM fills WHERE id='oldfill'").fetchone())
        assert after == old_fill_row

    async def test_every_wallet_is_rebased_and_all_20_are_armed(self, rebased):
        e, _ = rebased
        assert_isolated_books(e)
        assert e.portfolio.total_equity(e.prices()) == pytest.approx(PAPER_TOTAL)
        rows = e.storage.strategy_states()
        assert all(r["enabled"] for r in rows.values())
        assert all(r["allocation"] == pytest.approx(BALANCE) for r in rows.values())

    async def test_a_wallet_rebased_event_is_written(self, rebased):
        e, _ = rebased
        ev = next((x for x in e.storage.events_recent(100) if x["kind"] == "wallet_rebased"), None)
        assert ev is not None
        assert ev["payload"]["from_epoch"] == 1 and ev["payload"]["epoch"] == 2
        assert ev["payload"]["closed_positions"] == 1
        assert ev["payload"]["paper_total"] == PAPER_TOTAL
        assert ev["epoch"] == 2

    async def test_second_boot_on_the_rebased_db_does_not_rebase_again(self, tmp_path, monkeypatch):
        clock = FixedClock()
        storage, _ = _write_old_model_db(tmp_path / "old2.db", clock)
        s = settings_factory(data_dir=str(tmp_path / "d"), SYMBOLS="BTCUSDT")
        first = await boot_engine(s, storage, monkeypatch, clock=clock)
        await first.stop()
        second = await boot_engine(s, storage, monkeypatch, clock=clock)
        try:
            assert second.boot_report["needs_rebase"] is False
            assert second.epoch == 2
            assert_isolated_books(second)
        finally:
            await second.stop()
            storage.close()

    def test_marker_changes_with_the_balance(self):
        assert wallet_model_marker(100) == "isolated:100"
        assert wallet_model_marker(250.5) == "isolated:250.5"
        assert wallet_model_marker(100) != wallet_model_marker(500)


class TestSetAllocation:
    async def test_below_10_is_rejected(self, engine):
        with pytest.raises(EngineError) as exc:
            await engine.set_allocation("S01", allocation=5)
        assert exc.value.status == 400
        assert engine.portfolio.wallets["S01"].allocation == pytest.approx(BALANCE)

    async def test_above_10000_is_rejected(self, engine):
        with pytest.raises(EngineError) as exc:
            await engine.set_allocation("S01", allocation=20_000)
        assert exc.value.status == 400
        assert engine.portfolio.wallets["S01"].allocation == pytest.approx(BALANCE)

    async def test_250_is_accepted_and_rebases_only_that_halt_floor(self, engine):
        await engine.set_allocation("S01", allocation=250)
        assert engine.portfolio.wallets["S01"].allocation == pytest.approx(250.0)
        assert engine.risk.state.halt_floors["S01"] == pytest.approx(187.5)
        assert engine.risk.state.halt_floors["S02"] == pytest.approx(75.0)
        assert engine.portfolio.wallets["S02"].allocation == pytest.approx(BALANCE)
        assert engine.storage.strategy_states()["S01"]["allocation"] == pytest.approx(250.0)

    async def test_409_while_the_strategy_holds_a_position(self, engine):
        pos = VirtualPosition(id="p1", strategy_id="S01", symbol="BTCUSDT", side="long", qty=0.01,
                              qty_initial=0.01, entry_price=65000.0, entry_ts=engine.clock(), leverage=15,
                              margin=43.0, stop=64000.0, take_profits=[], trail=None, be_at_r=0.0,
                              max_hold_deadline=None, initial_risk_usd=10.0,
                              extreme_price=65000.0, tf="1m")
        engine.portfolio.positions[pos.id] = pos
        with pytest.raises(EngineError) as exc:
            await engine.set_allocation("S01", allocation=250)
        assert exc.value.status == 409
        with pytest.raises(EngineError):
            await engine.set_allocation("S01", leverage=10)
        # size_mult alone is allowed with a position open
        await engine.set_allocation("S01", size_mult=0.5)
        assert engine.meta["S01"].size_mult == pytest.approx(0.5)
        engine.portfolio.positions.pop(pos.id)

    async def test_unknown_strategy_is_404(self, engine):
        with pytest.raises(EngineError) as exc:
            await engine.set_allocation("S99", allocation=100)
        assert exc.value.status == 404


class TestRegistryDefaults:
    def test_first_boot_state_arms_everything_at_full_size(self):
        from app.strategies.registry import first_boot_state

        state = first_boot_state(STRATEGY_IDS)
        assert len(state) == N
        assert all(v == {"enabled": True, "size_mult": 1.0} for v in state.values())

    def test_paper_total_is_balance_times_strategies(self, settings):
        assert settings.strategy_starting_balance == pytest.approx(BALANCE)
        assert settings.paper_total(N) == pytest.approx(PAPER_TOTAL)
        assert settings.paper_total(0) == 0.0

    def test_no_shared_pool_settings_remain(self):
        from app.config import Settings

        fields = set(Settings.__dataclass_fields__)
        assert "starting_equity" not in fields and "auto_allocate" not in fields
        assert "strategy_starting_balance" in fields
        assert not hasattr(engine_boot, "rebalance_allocations")

    def test_balance_range_is_enforced(self, tmp_path):
        from app.config import ConfigError

        with pytest.raises(ConfigError):
            settings_factory(data_dir=str(tmp_path / "d"), balance=1)
        assert settings_factory(data_dir=str(tmp_path / "d"), balance=10).strategy_starting_balance == 10

    def test_wallet_model_marker_is_stored_as_json_safe_text(self, engine):
        assert json.loads(json.dumps(engine.storage.get_meta("wallet_model"))) == "isolated:100"


# ---- params: class defaults win, operator edits survive ----------------------------------------

class TestParamsCustomOverlay:
    """A new tuning pack must land on the next boot, but a slider the operator moved must not be lost.

    `strategy_state.params_json` is the full snapshot of what was running; `params_custom` names the keys
    the operator actually edited. Boot starts from the CLASS defaults and overlays only those keys.
    """

    @staticmethod
    def _db_with_stored_params(path, params: dict, custom: list[str]) -> Storage:
        st = Storage(str(path))
        st.set_meta("wallet_model", wallet_model_marker(BALANCE))  # not a rebase: an ordinary restart
        st.upsert_strategy_state("S01", enabled=1, allocation=BALANCE, leverage=15, size_mult=1.0,
                                 params=params, params_custom=custom, halted=0,
                                 halt_floor=0.75 * BALANCE, life=1, lives_today=0, lives_day="",
                                 realized_all_lives=0.0)
        return st

    async def test_a_stale_default_is_replaced_and_an_edited_key_is_kept(self, tmp_path, monkeypatch):
        from app.strategies.s01_ema_cross import EmaCrossMomentum

        defaults = EmaCrossMomentum.Params()
        assert defaults.fast == 5, "the tuning pack's new default"
        storage = self._db_with_stored_params(tmp_path / "params.db",
                                              {"fast": 9, "slow": 21, "tp1_r": 2.5}, ["slow"])
        s = settings_factory(data_dir=str(tmp_path / "d"), SYMBOLS="BTCUSDT")
        e = await boot_engine(s, storage, monkeypatch, clock=FixedClock())
        try:
            p = e.strategies["S01"].params
            assert p.fast == defaults.fast, "fast was never edited: it follows the new class default"
            assert p.tp1_r == pytest.approx(defaults.tp1_r), "same for an un-edited float"
            assert p.slow == 21, "slow IS in params_custom: the operator's value survives"
            assert e.params_custom["S01"] == {"slow"}
        finally:
            await e.stop()
            storage.close()

    async def test_no_params_custom_means_every_key_follows_the_class(self, tmp_path, monkeypatch):
        from app.strategies.s01_ema_cross import EmaCrossMomentum

        defaults = EmaCrossMomentum.Params()
        storage = self._db_with_stored_params(tmp_path / "params2.db", {"fast": 9, "slow": 21}, [])
        s = settings_factory(data_dir=str(tmp_path / "d"), SYMBOLS="BTCUSDT")
        e = await boot_engine(s, storage, monkeypatch, clock=FixedClock())
        try:
            p = e.strategies["S01"].params
            assert (p.fast, p.slow) == (defaults.fast, defaults.slow)
            assert e.params_custom["S01"] == set()
        finally:
            await e.stop()
            storage.close()

    async def test_set_params_records_the_edited_keys_and_they_survive_a_restart(self, tmp_path, monkeypatch):
        from app.strategies.s01_ema_cross import EmaCrossMomentum

        storage = Storage(str(tmp_path / "params3.db"))
        s = settings_factory(data_dir=str(tmp_path / "d"), SYMBOLS="BTCUSDT")
        first = await boot_engine(s, storage, monkeypatch, clock=FixedClock())
        try:
            await first.set_params("S01", {"slow": 21})
            assert first.params_custom["S01"] == {"slow"}
            assert storage.strategy_states()["S01"]["params_custom"] == ["slow"]
        finally:
            await first.stop()
        second = await boot_engine(s, storage, monkeypatch, clock=FixedClock())
        try:
            assert second.strategies["S01"].params.slow == 21
            assert second.strategies["S01"].params.fast == EmaCrossMomentum.Params().fast
            assert second.params_custom["S01"] == {"slow"}
        finally:
            await second.stop()
            storage.close()
