"""Lives: what happens when an isolated book hits its -25% floor.

The survival upgrade turns a strategy halt into a *death*: the book is flattened, the dead life is banked
into `realized_all_lives` and written to disk with its `life` number, and a fresh life is started at
STRATEGY_STARTING_BALANCE so the bake-off keeps 20 runners. Two things stop a death loop:
`MAX_LIVES_PER_DAY` and `AUTO_RESPAWN_HALTED=false`.

These tests drive the REAL engine path (`engine_dispatch.check_halts` -> `end_life`), plus the pure
`Portfolio.replay` / `Portfolio.respawn` bookkeeping underneath it.
"""
from __future__ import annotations

import pytest

from app.core import engine_dispatch
from app.core.portfolio import Portfolio
from app.core.storage import Storage
from app.core.types import Fill, RiskDecision, Signal, TakeProfit
from tests.conftest import RULES, T0, FixedClock, boot_engine, force_equity, settings_factory

SYM = "BTCUSDT"
BALANCE = 100.0
FLOOR = 75.0       # 1 - STRATEGY_HALT_PCT (0.25) x 100
DEAD_EQUITY = 74.0


# ---- helpers ------------------------------------------------------------------------------------

def ref_price(e) -> float:
    return e.last.get(SYM) or 65000.0


def open_one(e, sid: str = "S01", qty: float = 0.001):
    """Put one small virtual position on the book the way the dispatcher does (fill recorded + stamped)."""
    price = ref_price(e)
    sig = Signal(sid, SYM, "entry", "long", e.clock(), "1m", price, price * 0.99,
                 [TakeProfit(price * 1.03, 1.0)], reason="test entry")
    dec = RiskDecision(True, "ok", qty=qty, notional=qty * price, margin=qty * price / 15, leverage=15,
                       risk_usd=qty * price * 0.01, rr=3.0)
    pos, fill = e.portfolio.open_position(sig, dec, price, simulated=True, ts=e.clock())
    engine_dispatch.stamp_wallet(e, fill)
    e.storage.record_fill(fill, snapshot=pos)
    return pos


async def kill_the_book(e, sid: str = "S01", equity: float = DEAD_EQUITY, with_position: bool = True):
    """Drive `sid` under its halt floor and run the real halt path."""
    if with_position:
        open_one(e, sid)
    force_equity(e.portfolio, sid, equity, e.prices())
    assert e.portfolio.wallet_equity(sid, e.prices()) == pytest.approx(equity)
    await engine_dispatch.check_halts(e, sid)


def notes_of(e, sid: str, kind: str | None = None) -> list[dict]:
    rows = e.storage.notes(e.epoch, sid, limit=200)
    return [r for r in rows if kind is None or r["kind"] == kind]


def events_of(e, sid: str, kind: str) -> list[dict]:
    return [ev for ev in e.storage.events_recent(200)
            if ev["kind"] == kind and ev.get("strategy_id") == sid]


def net_at_death(e, sid: str) -> float:
    """The dead life's net, read back from the last fill's stamped wallet equity (allocation was 100)."""
    fills = e.storage.fills_for(e.epoch, sid)
    assert fills, "the halt must have written at least one fill"
    return fills[-1].wallet_equity_after - BALANCE


# ---- 1. a death banks the life and respawns the book --------------------------------------------

class TestRespawnOnDeath:
    async def test_the_book_is_flattened_banked_and_reborn(self, engine):
        e, sid = engine, "S01"
        before = e.portfolio.wallets[sid]
        assert before.life == 1 and before.realized_all_lives == 0.0
        await kill_the_book(e, sid)

        assert e.portfolio.positions_of(sid) == [], "the dead life is flattened first"
        w = e.portfolio.wallets[sid]
        assert w.life == 2
        assert w.allocation == pytest.approx(BALANCE)
        assert (w.realized, w.fees, w.funding) == (0.0, 0.0, 0.0)
        assert w.equity() == pytest.approx(BALANCE), "the new life starts on a clean 100 book"
        assert w.net() == 0.0

    async def test_the_strategy_is_armed_again_with_a_fresh_floor(self, engine):
        e, sid = engine, "S01"
        await kill_the_book(e, sid)
        assert e.meta[sid].enabled is True
        assert e.meta[sid].halted is False
        assert sid not in e.risk.state.halted_strategies
        assert e.board.is_enabled(sid) is True
        assert e.risk.state.halt_floors[sid] == pytest.approx(FLOOR)

    async def test_both_journal_lines_are_written(self, engine):
        e, sid = engine, "S01"
        await kill_the_book(e, sid)
        ended = notes_of(e, sid, "life_ended")
        respawned = notes_of(e, sid, "life_respawned")
        assert len(ended) == 1, "exactly one obituary"
        assert len(respawned) == 1
        assert "life_ended" in ended[0]["text"]
        assert "life=2" in respawned[0]["text"]
        assert events_of(e, sid, "life_respawned"), "and a machine-readable event"
        assert events_of(e, sid, "strategy_halt"), "the halt itself is still recorded"

    async def test_realized_all_lives_is_the_dead_lifes_net(self, engine):
        e, sid = engine, "S01"
        await kill_the_book(e, sid)
        w = e.portfolio.wallets[sid]
        assert w.realized_all_lives == pytest.approx(net_at_death(e, sid))
        assert w.realized_all_lives < -25.0, "the book died at least 25% down"
        assert w.net_all_lives() == pytest.approx(w.realized_all_lives), "the new life has done nothing yet"

    async def test_the_lives_counter_starts_at_one_for_today(self, engine):
        e, sid = engine, "S01"
        await kill_the_book(e, sid)
        w = e.portfolio.wallets[sid]
        assert w.lives_today == 1
        assert w.lives_day == e.today()

    async def test_the_life_is_persisted_for_the_next_boot(self, engine):
        e, sid = engine, "S01"
        await kill_the_book(e, sid)
        row = e.storage.strategy_states()[sid]
        assert row["life"] == 2
        assert row["lives_today"] == 1
        assert row["lives_day"] == e.today()
        assert row["realized_all_lives"] == pytest.approx(e.portfolio.wallets[sid].realized_all_lives)
        assert row["enabled"] is True and row["halted"] is False

    async def test_only_that_book_is_touched(self, engine):
        e = engine
        await kill_the_book(e, "S01")
        for sid in ("S02", "S06", "S20"):
            other = e.portfolio.wallets[sid]
            assert other.life == 1
            assert other.allocation == pytest.approx(BALANCE)
            assert other.realized_all_lives == 0.0
            assert e.meta[sid].halted is False
        assert e.risk.state.halted_strategies == set()

    async def test_the_new_life_stamps_its_fills(self, engine):
        e, sid = engine, "S01"
        await kill_the_book(e, sid)
        open_one(e, sid)
        fills = e.storage.fills_for(e.epoch, sid)
        assert {f.life for f in fills} == {1, 2}
        assert e.storage.fills_for(e.epoch, sid, life=2)[-1].life == 2
        assert all(f.life == 1 for f in e.storage.fills_for(e.epoch, sid, life=1))

    async def test_a_second_death_is_life_three(self, engine):
        e, sid = engine, "S01"
        await kill_the_book(e, sid)
        first_banked = e.portfolio.wallets[sid].realized_all_lives
        await kill_the_book(e, sid)
        w = e.portfolio.wallets[sid]
        assert w.life == 3 and w.lives_today == 2
        assert w.realized_all_lives < first_banked, "both dead lives are banked"


# ---- 2. the daily cap stops a death loop ---------------------------------------------------------

class TestDailyCap:
    async def test_the_ninth_death_of_the_day_stays_halted(self, engine):
        e, sid = engine, "S01"
        cap = e.settings.max_lives_per_day
        assert cap == 8
        w = e.portfolio.wallets[sid]
        w.lives_today, w.lives_day, w.life = cap, e.today(), cap
        await kill_the_book(e, sid)

        assert e.meta[sid].halted is True
        assert e.meta[sid].enabled is False
        assert sid in e.risk.state.halted_strategies
        assert e.portfolio.wallets[sid].life == cap, "no new life was started"
        assert e.portfolio.wallets[sid].lives_today == cap
        assert notes_of(e, sid, "life_ended"), "the death is still recorded"
        assert notes_of(e, sid, "life_respawned") == []
        capped = events_of(e, sid, "respawn_capped")
        assert len(capped) == 1
        assert capped[0]["payload"] == {"lives_today": cap, "cap": cap}
        assert events_of(e, sid, "life_respawned") == []

    async def test_the_book_keeps_its_dead_equity_when_capped(self, engine):
        e, sid = engine, "S01"
        w = e.portfolio.wallets[sid]
        w.lives_today, w.lives_day = e.settings.max_lives_per_day, e.today()
        await kill_the_book(e, sid)
        assert e.portfolio.wallet_equity(sid, e.prices()) < FLOOR, "not reset to 100"
        assert e.portfolio.wallets[sid].realized_all_lives == 0.0, "nothing was banked: the life is not over"

    async def test_the_counter_resets_on_a_new_day(self, engine):
        e, sid = engine, "S01"
        w = e.portfolio.wallets[sid]
        w.lives_today, w.lives_day = e.settings.max_lives_per_day, "2000-01-01"  # yesterday's tally
        await kill_the_book(e, sid)
        w = e.portfolio.wallets[sid]
        assert w.life == 2, "a new UTC day gets a fresh allowance"
        assert w.lives_today == 1
        assert w.lives_day == e.today()
        assert e.meta[sid].halted is False
        assert notes_of(e, sid, "life_respawned")
        assert events_of(e, sid, "respawn_capped") == []

    async def test_one_below_the_cap_still_respawns(self, engine):
        e, sid = engine, "S01"
        w = e.portfolio.wallets[sid]
        w.lives_today, w.lives_day = e.settings.max_lives_per_day - 1, e.today()
        await kill_the_book(e, sid)
        assert e.portfolio.wallets[sid].lives_today == e.settings.max_lives_per_day
        assert e.meta[sid].halted is False

    def test_the_cap_is_configurable(self, tmp_path):
        s = settings_factory(data_dir=str(tmp_path / "d"), MAX_LIVES_PER_DAY=3)
        assert s.max_lives_per_day == 3
        assert settings_factory(data_dir=str(tmp_path / "d")).max_lives_per_day == 8


# ---- 3. AUTO_RESPAWN_HALTED=false keeps the manual behaviour -------------------------------------

class TestAutoRespawnOff:
    @pytest.fixture
    async def manual_engine(self, tmp_path, monkeypatch):
        s = settings_factory(data_dir=str(tmp_path / "data"), SYMBOLS="BTCUSDT",
                             AUTO_RESPAWN_HALTED="false")
        storage = Storage(str(tmp_path / "manual.db"))
        e = await boot_engine(s, storage, monkeypatch, clock=FixedClock())
        yield e
        await e.stop()
        storage.close()

    async def test_the_default_is_on(self, tmp_path):
        assert settings_factory(data_dir=str(tmp_path / "d")).auto_respawn_halted is True

    async def test_a_halted_book_stays_halted(self, manual_engine):
        e, sid = manual_engine, "S01"
        assert e.settings.auto_respawn_halted is False
        await kill_the_book(e, sid)

        assert e.meta[sid].halted is True
        assert e.meta[sid].enabled is False
        assert sid in e.risk.state.halted_strategies
        assert e.portfolio.positions_of(sid) == [], "it is still flattened"
        w = e.portfolio.wallets[sid]
        assert w.life == 1 and w.lives_today == 0
        assert w.realized_all_lives == 0.0
        assert e.portfolio.wallet_equity(sid, e.prices()) < FLOOR

    async def test_no_respawn_note_or_event(self, manual_engine):
        e, sid = manual_engine, "S01"
        await kill_the_book(e, sid)
        assert notes_of(e, sid, "life_ended"), "the death is journalled either way"
        assert notes_of(e, sid, "life_respawned") == []
        assert events_of(e, sid, "life_respawned") == []
        assert events_of(e, sid, "respawn_capped") == [], "it was not the cap that stopped it"
        halt_notes = [n["text"] for n in notes_of(e, sid, "halt")]
        assert any("AUTO_RESPAWN_HALTED=false" in t for t in halt_notes)


# ---- 4. replay ignores the dead lives -------------------------------------------------------------

REF = 65000.0


def ledger_fill(kind: str, qty: float, price: float, *, ts: int, life: int, pnl: float = 0.0,
                fee: float = 0.0, is_open: bool = False, pid: str = "p1", sid: str = "S01") -> Fill:
    return Fill(id=f"{pid}-{kind}-{ts}", ts=ts, epoch=1, strategy_id=sid, symbol=SYM, position_id=pid,
                side="BUY" if is_open else "SELL", qty=qty, price=price, fee=fee, slippage_bps=2.0,
                kind=kind, realized_pnl=pnl, simulated=True,
                meta={"stop": REF * 0.99} if is_open else {}, ref_price=price, leverage=15,
                position_side="long", is_open=is_open, life=life)


def two_lives_ledger() -> list[Fill]:
    """Life 1 lost 30, life 2 made 12 -- both on disk, as a real respawned book looks."""
    return [
        ledger_fill("entry", 0.01, REF, ts=T0, life=1, fee=0.26, is_open=True, pid="dead"),
        ledger_fill("stop", 0.01, REF * 0.95, ts=T0 + 60_000, life=1, pnl=-30.0, fee=0.25, pid="dead"),
        ledger_fill("entry", 0.01, REF, ts=T0 + 120_000, life=2, fee=0.26, is_open=True, pid="alive"),
        ledger_fill("tp", 0.01, REF * 1.02, ts=T0 + 180_000, life=2, pnl=12.0, fee=0.27, pid="alive"),
    ]


class TestReplayIgnoresDeadLives:
    @staticmethod
    def _replayed(settings, clock, life: int) -> Portfolio:
        pf = Portfolio(settings, RULES, clock)
        pf.replay(two_lives_ledger(), [], {"S01": BALANCE}, {"S01": life})
        return pf

    def test_only_the_current_lifes_fills_shape_the_wallet(self, settings, clock):
        pf = self._replayed(settings, clock, life=2)
        w = pf.wallets["S01"]
        assert w.life == 2
        assert w.realized == pytest.approx(12.0), "the -30 of life 1 is history, not equity"
        assert w.fees == pytest.approx(0.26 + 0.27)
        assert w.equity() == pytest.approx(BALANCE + 12.0 - 0.53)
        assert w.net() == pytest.approx(12.0 - 0.53)

    def test_only_the_current_lifes_trades_are_listed(self, settings, clock):
        pf = self._replayed(settings, clock, life=2)
        assert [t.position_id for t in pf.closed_trades] == ["alive"]
        assert pf.stats("S01", T0)["trades_total"] == 1
        assert pf.stats("S01", T0)["net"] > 0

    def test_replaying_as_life_one_sees_the_dead_life_instead(self, settings, clock):
        pf = self._replayed(settings, clock, life=1)
        w = pf.wallets["S01"]
        assert w.life == 1
        assert w.realized == pytest.approx(-30.0)
        assert [t.position_id for t in pf.closed_trades] == ["dead"]

    def test_no_lives_map_means_everything_is_life_one(self, settings, clock):
        pf = Portfolio(settings, RULES, clock)
        pf.replay(two_lives_ledger(), [], {"S01": BALANCE})
        assert pf.wallets["S01"].realized == pytest.approx(-30.0)
        assert [t.position_id for t in pf.closed_trades] == ["dead"]

    async def test_a_restart_after_a_death_reopens_on_the_live_book(self, engine, tmp_path, monkeypatch):
        """End to end: die, respawn, reboot -- the new life's 100 book is what comes back."""
        e, sid = engine, "S01"
        await kill_the_book(e, sid)
        banked = e.portfolio.wallets[sid].realized_all_lives
        await e.stop()
        again = await boot_engine(e.settings, e.storage, monkeypatch, clock=FixedClock())
        try:
            w = again.portfolio.wallets[sid]
            assert w.life == 2
            assert w.realized_all_lives == pytest.approx(banked)
            assert w.lives_today == 1
            assert w.equity() == pytest.approx(BALANCE), "life 1's loss does not follow the new life"
            assert again.meta[sid].halted is False and again.meta[sid].enabled is True
        finally:
            await again.stop()


# ---- 5. Portfolio.respawn on its own ---------------------------------------------------------------

class TestPortfolioRespawn:
    def test_it_banks_resets_and_counts(self, portfolio):
        w = portfolio.ensure_wallet("S01", BALANCE)
        w.realized, w.fees, w.funding = -20.0, 3.0, -1.0
        assert w.net() == pytest.approx(-24.0)
        out = portfolio.respawn("S01", BALANCE, "2026-09-20")
        assert out is portfolio.wallets["S01"]
        assert out.life == 2
        assert out.realized_all_lives == pytest.approx(-24.0)
        assert (out.realized, out.fees, out.funding) == (0.0, 0.0, 0.0)
        assert out.allocation == pytest.approx(BALANCE)
        assert out.lives_today == 1 and out.lives_day == "2026-09-20"

    def test_the_daily_counter_rolls_over(self, portfolio):
        portfolio.ensure_wallet("S01", BALANCE)
        portfolio.respawn("S01", BALANCE, "2026-09-20")
        portfolio.respawn("S01", BALANCE, "2026-09-20")
        assert portfolio.wallets["S01"].lives_today == 2
        portfolio.respawn("S01", BALANCE, "2026-09-21")
        assert portfolio.wallets["S01"].lives_today == 1
        assert portfolio.wallets["S01"].life == 4

    def test_it_drops_only_that_strategys_closed_trades(self, portfolio):
        from app.core.portfolio import ClosedTrade

        for sid in ("S01", "S02"):
            portfolio.ensure_wallet(sid, BALANCE)
            portfolio.closed_trades.append(ClosedTrade(
                f"{sid}-t", sid, SYM, "long", 0.01, REF, REF * 1.01, T0, T0 + 1000, 6.5, 0.5, 6.0, 1.0, "tp"))
        portfolio.respawn("S01", BALANCE, "2026-09-20")
        assert [t.strategy_id for t in portfolio.closed_trades] == ["S02"]
        assert portfolio.wallets["S02"].life == 1, "no other book is touched"

    def test_banking_accumulates_across_lives(self, portfolio):
        w = portfolio.ensure_wallet("S01", BALANCE)
        w.realized = -26.0
        portfolio.respawn("S01", BALANCE, "2026-09-20")
        portfolio.wallets["S01"].realized = -30.0
        portfolio.respawn("S01", BALANCE, "2026-09-20")
        w = portfolio.wallets["S01"]
        assert w.life == 3
        assert w.realized_all_lives == pytest.approx(-56.0)
        assert w.net_all_lives() == pytest.approx(-56.0)
