"""Trade grouping, the summary stats, the live scoreboard, the idempotent daily rollup and the journal."""
from __future__ import annotations

import pytest

from app.core import analytics, engine_dispatch
from app.core.analytics import (build_trades, day_bounds, day_key, note, note_reject, recompute_daily,
                                scoreboard, summarise)
from app.core.types import Fill, VirtualPosition
from tests.conftest import T0

SYM = "BTCUSDT"
PID = "pos-1"


def fill(kind: str, qty: float, price: float, *, ts: int, pnl: float = 0.0, fee: float = 0.0,
         is_open: bool = False, pid: str = PID, sid: str = "S01", stop: float = 0.0,
         side: str = "SELL", reason: str = "") -> Fill:
    return Fill(id=f"{pid}-{kind}-{ts}", ts=ts, epoch=1, strategy_id=sid, symbol=SYM, position_id=pid,
                side=side, qty=qty, price=price, fee=fee, slippage_bps=2.0, kind=kind, realized_pnl=pnl,
                simulated=True, meta={"stop": stop} if is_open else {"reason": reason},
                ref_price=price, leverage=15, position_side="long", is_open=is_open,
                wallet_equity_after=100.0 + pnl - fee)


def open_pos(pid: str = PID, qty: float = 1.0, sid: str = "S01") -> VirtualPosition:
    return VirtualPosition(id=pid, strategy_id=sid, symbol=SYM, side="long", qty=qty, qty_initial=qty,
                           entry_price=100.0, entry_ts=T0, leverage=15, margin=6.7, stop=90.0,
                           take_profits=[], trail=None, be_at_r=0.0, max_hold_deadline=None,
                           initial_risk_usd=10.0, extreme_price=100.0, tf="1m")


ENTRY = fill("entry", 1.0, 100.0, ts=T0, fee=0.04, is_open=True, stop=90.0, side="BUY")
EXIT1 = fill("tp", 0.5, 110.0, ts=T0 + 120_000, pnl=5.0, fee=0.022)
EXIT2 = fill("trail", 0.5, 120.0, ts=T0 + 300_000, pnl=10.0, fee=0.024)


class TestBuildTrades:
    def test_an_entry_and_two_partial_exits_become_one_trade(self):
        trades = build_trades([ENTRY, EXIT1, EXIT2])
        assert len(trades) == 1
        t = trades[0]
        assert t["position_id"] == PID and t["strategy_id"] == "S01" and t["symbol"] == SYM
        assert t["open"] is False
        assert t["qty"] == pytest.approx(1.0) and t["qty_closed"] == pytest.approx(1.0)
        assert t["entry_price"] == pytest.approx(100.0)
        assert t["exit_price"] == pytest.approx(115.0)  # qty-weighted 110/120
        assert t["pnl"] == pytest.approx(15.0)
        assert t["fees"] == pytest.approx(0.086)
        assert t["net"] == pytest.approx(14.914)
        assert t["risk_usd"] == pytest.approx(10.0)  # qty x |entry - stop|
        assert t["r"] == pytest.approx(1.4914)
        assert t["entry_ts"] == T0 and t["exit_ts"] == T0 + 300_000
        assert t["hold_s"] == 300
        assert t["exit_kind"] == "trail"
        assert [x["kind"] for x in t["exits"]] == ["tp", "trail"]
        assert [x["qty"] for x in t["exits"]] == [0.5, 0.5]

    def test_exits_are_ordered_by_time_even_if_the_fills_are_not(self):
        t = build_trades([EXIT2, ENTRY, EXIT1])[0]
        assert [x["ts"] for x in t["exits"]] == [T0 + 120_000, T0 + 300_000]
        assert t["exit_kind"] == "trail"

    def test_an_entry_with_no_exit_is_open(self):
        trades = build_trades([ENTRY], [open_pos()])
        assert len(trades) == 1
        t = trades[0]
        assert t["open"] is True
        assert t["exit_ts"] is None and t["exit_price"] is None
        assert t["qty_closed"] == 0.0 and t["hold_s"] == 0
        assert t["net"] == pytest.approx(-0.04)  # the entry fee is already paid
        assert summarise(trades)["trades_total"] == 0, "an open trade is not a result yet"

    def test_a_partially_closed_position_is_still_open(self):
        trades = build_trades([ENTRY, EXIT1], [open_pos(qty=0.5)])
        t = trades[0]
        assert t["open"] is True
        assert t["qty_closed"] == pytest.approx(0.5)
        assert t["exit_price"] == pytest.approx(110.0)

    def test_funding_fills_do_not_create_trades(self):
        fund = fill("funding", 0.0, 100.0, ts=T0 + 60_000, pnl=-0.12, pid="pos-funded")
        trades = build_trades([ENTRY, EXIT1, EXIT2, fund])
        assert [t["position_id"] for t in trades] == [PID]

    def test_an_exit_without_its_entry_is_skipped(self):
        assert build_trades([EXIT1]) == []

    def test_trades_are_sorted_oldest_first(self):
        second = [fill("entry", 1.0, 200.0, ts=T0 + 600_000, is_open=True, stop=190.0, pid="pos-2", side="BUY"),
                  fill("stop", 1.0, 190.0, ts=T0 + 900_000, pnl=-10.0, pid="pos-2")]
        trades = build_trades([*second, ENTRY, EXIT1, EXIT2])
        assert [t["position_id"] for t in trades] == [PID, "pos-2"]

    def test_r_is_none_without_a_stop_on_the_entry(self):
        entry = fill("entry", 1.0, 100.0, ts=T0, is_open=True, stop=0.0, pid="pos-3", side="BUY")
        ex = fill("manual", 1.0, 101.0, ts=T0 + 1000, pnl=1.0, pid="pos-3")
        t = build_trades([entry, ex])[0]
        assert t["r"] is None and t["risk_usd"] == 0.0


def _trade(net: float, r: float | None, hold_s: int = 60, ts: int = T0 + 1000) -> dict:
    return {"open": False, "exit_ts": ts, "net": net, "r": r, "hold_s": hold_s}


class TestSummarise:
    def test_win_rate_profit_factor_and_avg_r(self):
        s = summarise([_trade(10.0, 1.0), _trade(-5.0, -0.5), _trade(20.0, 2.0), _trade(-5.0, -0.5)])
        assert s["trades_total"] == 4 and s["wins"] == 2 and s["losses"] == 2
        assert s["win_rate"] == pytest.approx(0.5)
        assert s["gross_profit"] == pytest.approx(30.0) and s["gross_loss"] == pytest.approx(10.0)
        assert s["profit_factor"] == pytest.approx(3.0)
        assert s["avg_r"] == pytest.approx(0.5)
        assert s["expectancy"] == pytest.approx(5.0)
        assert s["net"] == pytest.approx(20.0)
        assert s["time_in_market_s"] == 240

    def test_a_breakeven_trade_counts_as_a_loss(self):
        s = summarise([_trade(0.0, 0.0), _trade(1.0, 0.1)])
        assert s["wins"] == 1 and s["losses"] == 1 and s["win_rate"] == pytest.approx(0.5)

    def test_no_losses_means_a_capped_profit_factor(self):
        assert summarise([_trade(10.0, 1.0)])["profit_factor"] == 999.0

    def test_no_trades_is_all_none(self):
        s = summarise([])
        assert s["trades_total"] == 0
        assert s["win_rate"] is None and s["profit_factor"] is None
        assert s["avg_r"] is None and s["expectancy"] is None

    def test_only_losses_gives_a_zero_profit_factor(self):
        assert summarise([_trade(-3.0, -0.3)])["profit_factor"] == pytest.approx(0.0)

    def test_open_trades_are_excluded(self):
        s = summarise([{"open": True, "exit_ts": None, "net": 99.0, "r": 9.0, "hold_s": 10}, _trade(1.0, 1.0)])
        assert s["trades_total"] == 1 and s["net"] == pytest.approx(1.0)

    def test_round_trip_from_build_trades(self):
        s = summarise(build_trades([ENTRY, EXIT1, EXIT2]))
        assert s["trades_total"] == 1 and s["wins"] == 1
        assert s["net"] == pytest.approx(14.914)
        assert s["avg_r"] == pytest.approx(1.4914)


class TestDayKeys:
    def test_day_key_and_bounds_round_trip(self):
        day = day_key(T0)
        start, end = day_bounds(day)
        assert start == T0  # T0 is UTC-midnight aligned
        assert end - start == 86_400_000
        assert day_key(end - 1) == day
        assert day_key(end) != day


SCOREBOARD_KEYS = {
    "allocation", "equity", "realized", "fees", "funding", "upnl", "trades_total", "trades_open",
    "trades_today", "wins", "losses", "win_rate", "profit_factor", "avg_r", "expectancy", "max_dd_pct",
    "time_in_market_s", "last_fill_ts", "last_signal", "margin_used", "gross_notional",
    "life", "lives_today", "realized_all_lives", "rejects_today",
}


def _record_trade(e, sid: str = "S01", *, net_pnl: float = 5.0, ts: int | None = None) -> None:
    """Persist one closed round trip for `sid` in the engine's current epoch."""
    ts = e.clock() if ts is None else ts
    entry = fill("entry", 1.0, 100.0, ts=ts, fee=0.04, is_open=True, stop=90.0, sid=sid, side="BUY",
                 pid=f"{sid}-{ts}")
    ex = fill("tp", 1.0, 100.0 + net_pnl, ts=ts + 60_000, pnl=net_pnl, fee=0.04, sid=sid, pid=f"{sid}-{ts}")
    for f in (entry, ex):
        f.epoch = e.epoch
        e.storage.insert_fill(f)


class TestScoreboard:
    async def test_returns_every_documented_key(self, engine):
        sb = scoreboard(engine, "S01")
        assert set(sb) == SCOREBOARD_KEYS
        assert sb["allocation"] == pytest.approx(100.0)
        assert sb["equity"] == pytest.approx(100.0)
        assert sb["trades_total"] == 0 and sb["trades_open"] == 0
        assert sb["win_rate"] is None and sb["last_fill_ts"] is None
        assert sb["max_dd_pct"] == pytest.approx(0.0)

    async def test_it_reads_only_that_strategys_book(self, engine):
        _record_trade(engine, "S01", net_pnl=5.0)
        _record_trade(engine, "S02", net_pnl=-3.0)
        s01 = scoreboard(engine, "S01")
        s02 = scoreboard(engine, "S02")
        assert s01["trades_total"] == 1 and s01["wins"] == 1 and s01["losses"] == 0
        assert s02["trades_total"] == 1 and s02["wins"] == 0 and s02["losses"] == 1
        assert s01["win_rate"] == pytest.approx(1.0) and s02["win_rate"] == pytest.approx(0.0)
        assert s01["trades_today"] == 1

    async def test_it_accepts_prebuilt_trades(self, engine):
        trades = build_trades([ENTRY, EXIT1, EXIT2])
        sb = scoreboard(engine, "S01", trades)
        assert sb["trades_total"] == 1 and sb["avg_r"] == pytest.approx(1.4914)


class TestRecomputeDaily:
    async def test_it_writes_one_row_per_strategy(self, engine):
        day = day_key(engine.clock())
        written = recompute_daily(engine, day)
        assert written == len(engine.strategies)
        rows = engine.storage.strategy_daily(engine.epoch)
        assert len(rows) == len(engine.strategies)
        assert {r["day_utc"] for r in rows} == {day}

    async def test_it_is_idempotent(self, engine):
        _record_trade(engine, "S01", net_pnl=5.0, ts=engine.clock() + 60_000)
        _record_trade(engine, "S01", net_pnl=-2.0, ts=engine.clock() + 600_000)
        day = day_key(engine.clock())

        def s01_row() -> dict:
            row = next(r for r in engine.storage.strategy_daily(engine.epoch, "S01", day, day))
            row.pop("updated_ts")
            return row

        recompute_daily(engine, day, ["S01"])
        first = s01_row()
        recompute_daily(engine, day, ["S01"])
        second = s01_row()
        assert second == first
        assert first["trades"] == 2 and first["wins"] == 1 and first["losses"] == 1
        assert first["realized"] == pytest.approx(3.0)
        assert first["fees"] == pytest.approx(0.16)
        assert first["win_rate"] == pytest.approx(0.5)
        assert len(engine.storage.strategy_daily(engine.epoch, "S01")) == 1, "upsert, not append"

    async def test_a_day_without_trades_is_an_empty_row_not_a_crash(self, engine):
        day = day_key(engine.clock() - 86_400_000)
        recompute_daily(engine, day, ["S04"])
        row = engine.storage.strategy_daily(engine.epoch, "S04", day, day)[0]
        assert row["trades"] == 0 and row["realized"] == pytest.approx(0.0)
        assert row["win_rate"] is None
        assert row["reject_reasons"] == {}

    async def test_trades_are_attributed_to_the_day_they_closed(self, engine):
        today = day_key(engine.clock())
        yesterday = day_key(engine.clock() - 86_400_000)
        _record_trade(engine, "S05", net_pnl=4.0, ts=engine.clock() + 3_600_000)
        recompute_daily(engine, today, ["S05"])
        recompute_daily(engine, yesterday, ["S05"])
        assert engine.storage.strategy_daily(engine.epoch, "S05", today, today)[0]["trades"] == 1
        assert engine.storage.strategy_daily(engine.epoch, "S05", yesterday, yesterday)[0]["trades"] == 0


def _reject_notes(e, sid: str | None = None) -> list[dict]:
    return [n for n in e.storage.notes(e.epoch, sid, 500) if n["kind"] == "reject"]


class TestJournal:
    async def test_note_appends_one_line(self, engine):
        note(engine, "manual", "hello lab", "S01", "BTCUSDT")
        rows = engine.storage.notes(engine.epoch, "S01", 10)
        assert rows[0]["text"] == "hello lab"
        assert rows[0]["kind"] == "manual" and rows[0]["author"] == "system"
        assert rows[0]["symbol"] == "BTCUSDT"

    async def test_note_reject_is_throttled_to_one_line_per_reason_per_hour(self, engine, ):
        clock = engine.clock
        note_reject(engine, "S01", "BTCUSDT", "below_min_notional:78<100")
        assert len(_reject_notes(engine, "S01")) == 1
        note_reject(engine, "S01", "BTCUSDT", "below_min_notional:64<100")
        note_reject(engine, "S01", "ETHUSDT", "below_min_notional:12<40")
        assert len(_reject_notes(engine, "S01")) == 1, "same reason inside the hour: one line only"

        note_reject(engine, "S01", "BTCUSDT", "max_positions")
        assert len(_reject_notes(engine, "S01")) == 2, "a different reason is written immediately"

        clock.advance(3_600_001)
        note_reject(engine, "S01", "BTCUSDT", "below_min_notional:70<100")
        assert len(_reject_notes(engine, "S01")) == 3, "the throttle expires after an hour"

    async def test_the_throttle_is_per_strategy(self, engine):
        note_reject(engine, "S01", "BTCUSDT", "rr_below_min:1.2<2.5")
        note_reject(engine, "S02", "BTCUSDT", "rr_below_min:1.1<2.5")
        assert len(_reject_notes(engine, "S01")) == 1
        assert len(_reject_notes(engine, "S02")) == 1

    async def test_rejects_stay_countable_in_the_signals_table(self, engine):
        """The journal is throttled; `signals` keeps every reject, which is what the counts read."""
        assert analytics.REJECT_NOTE_THROTTLE_MS == 3_600_000
        counts = engine.storage.reject_reason_counts(engine.epoch, "S01", engine.day_start_ms())
        assert isinstance(counts, dict)

    async def test_a_note_failure_never_raises(self, engine, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(engine.storage, "insert_note", boom)
        note(engine, "manual", "still fine")  # must swallow

    async def test_note_fill_writes_open_and_close_lines(self, engine):
        analytics.note_fill(engine, ENTRY, opened=True)
        analytics.note_fill(engine, EXIT2, opened=False)
        rows = engine.storage.notes(engine.epoch, "S01", 10)
        kinds = [r["kind"] for r in rows]
        assert "open" in kinds and "close" in kinds
        assert any("S01 open BTCUSDT long" in r["text"] for r in rows)
        assert any("pnl=+10.00" in r["text"] for r in rows)


# ---- lives on the scoreboard and in the daily rollup --------------------------------------------

class TestScoreboardLives:
    async def test_a_fresh_book_is_life_one_with_nothing_banked(self, engine):
        sb = scoreboard(engine, "S01")
        assert sb["life"] == 1
        assert sb["lives_today"] == 0
        assert sb["realized_all_lives"] == pytest.approx(0.0)
        assert sb["rejects_today"] == {}

    async def test_it_reports_the_wallets_life_bookkeeping(self, engine):
        w = engine.portfolio.wallets["S01"]
        w.realized = -4.0
        engine.portfolio.respawn("S01", 100.0, engine.today())
        sb = scoreboard(engine, "S01")
        assert sb["life"] == 2
        assert sb["lives_today"] == 1
        assert sb["realized_all_lives"] == pytest.approx(-4.0), "dead lives plus the current one"
        assert sb["realized"] == pytest.approx(0.0), "the realized column is the CURRENT life only"

    async def test_realized_all_lives_adds_the_running_life(self, engine):
        engine.portfolio.wallets["S01"].realized = -6.0
        engine.portfolio.respawn("S01", 100.0, engine.today())
        engine.portfolio.wallets["S01"].realized = 2.5
        assert scoreboard(engine, "S01")["realized_all_lives"] == pytest.approx(-3.5)

    async def test_only_the_current_lifes_fills_are_counted(self, engine):
        _record_trade(engine, "S01", net_pnl=5.0)
        assert scoreboard(engine, "S01")["trades_total"] == 1
        engine.portfolio.respawn("S01", 100.0, engine.today())
        sb = scoreboard(engine, "S01")
        assert sb["life"] == 2
        assert sb["trades_total"] == 0, "life 1's trades stay on disk but off the new life's record"

    async def test_last_signal_carries_a_short_reject_code(self, engine):
        from app.core.types import Signal, TakeProfit

        sig = Signal("S01", SYM, "entry", "long", engine.clock(), "1m", 100.0, 99.0,
                     [TakeProfit(103.0, 1.0)], id="sig-x")
        engine.board.post(sig, "rejected", "below_min_notional:78.00<100.00", ts=engine.clock())
        last = scoreboard(engine, "S01")["last_signal"]
        assert last["status"] == "rejected"
        assert last["code"] == "min_notional"
        engine.board.post(sig, "approved", "", ts=engine.clock())
        assert scoreboard(engine, "S01")["last_signal"]["code"] is None, "only rejects carry a code"

    async def test_rejects_today_counts_by_reason(self, engine):
        from app.core.types import Signal, TakeProfit

        for i, reason in enumerate(("below_min_notional:78.00<100.00", "below_min_notional:60.00<100.00",
                                    "rr_below_min:1.2<2.5")):
            sig = Signal("S01", SYM, "entry", "long", engine.clock(), "1m", 100.0, 99.0,
                         [TakeProfit(103.0, 1.0)], id=f"r{i}")
            engine.storage.insert_signal(sig, "rejected", reason, engine.epoch, None, life=1)
        # the breakdown uses the SHORT reject codes, the same vocabulary the row pill shows
        assert scoreboard(engine, "S01")["rejects_today"] == {"min_notional": 2, "rr": 1}


class TestRecomputeDailyLives:
    async def test_the_daily_row_carries_the_life(self, engine):
        _record_trade(engine, "S01", net_pnl=4.0)
        recompute_daily(engine, strategy_ids=["S01"])
        row = engine.storage.strategy_daily(engine.epoch, "S01")[0]
        assert row["life"] == 1
        assert row["trades"] == 1

    async def test_a_same_day_respawn_keeps_the_dead_lifes_daily_row(self, engine):
        """The graveyard must survive a same-day death: `life` is part of the strategy_daily key, so the
        reborn book gets its own row instead of overwriting the one it died in."""
        _record_trade(engine, "S01", net_pnl=4.0)
        recompute_daily(engine, strategy_ids=["S01"])
        assert engine.storage.strategy_daily(engine.epoch, "S01")[0]["trades"] == 1
        engine.portfolio.respawn("S01", 100.0, engine.today())
        recompute_daily(engine, strategy_ids=["S01"])
        rows = {r["life"]: r for r in engine.storage.strategy_daily(engine.epoch, "S01")}
        assert sorted(rows) == [1, 2], "both lives must keep a daily row"
        assert rows[1]["trades"] == 1, "the dead life kept its trades"
        assert rows[2]["trades"] == 0, "the new life starts clean"
        assert [r["life"] for r in analytics.lives_timeline(engine, "S01")] == [1, 2]

    async def test_lives_timeline_lists_every_life(self, engine):
        _record_trade(engine, "S01", net_pnl=4.0)
        recompute_daily(engine, strategy_ids=["S01"])
        timeline = analytics.lives_timeline(engine, "S01")
        assert [r["life"] for r in timeline] == [1]
        assert timeline[0]["current"] is True
        assert timeline[0]["trades"] == 1
        assert "equity" in timeline[0]

    async def test_lives_timeline_marks_only_the_current_life(self, engine):
        engine.portfolio.respawn("S01", 100.0, engine.today())
        timeline = analytics.lives_timeline(engine, "S01")
        assert timeline[-1]["life"] == 2 and timeline[-1]["current"] is True
        assert all(r["current"] is False for r in timeline[:-1])


class TestCheckStarved:
    @staticmethod
    def _idle_signals(e, sid: str, n: int, status: str = "shadow") -> None:
        from app.core.types import Signal, TakeProfit

        for i in range(n):
            sig = Signal(sid, SYM, "entry", "long", e.clock() - 60_000, "1m", 100.0, 99.0,
                         [TakeProfit(103.0, 1.0)], id=f"{sid}-idle-{i}")
            e.storage.insert_signal(sig, status, "strategy off", e.epoch, None, life=1)

    @staticmethod
    def _age_the_engine(e) -> None:
        e.boot_ms = e.clock() - 4 * 3_600_000  # the window is measured on the engine clock

    async def test_a_book_with_no_approved_entry_in_three_hours_is_flagged(self, engine):
        self._age_the_engine(engine)
        self._idle_signals(engine, "S01", 25)
        engine_dispatch.check_starved(engine)
        starved = [n for n in engine.storage.notes(engine.epoch, "S01", limit=50) if n["kind"] == "starved"]
        assert len(starved) == 1
        assert "0 approved entries in 3h" in starved[0]["text"]
        assert any(ev["kind"] == "starved" for ev in engine.storage.events_recent(200))

    async def test_it_is_not_repeated_inside_the_same_window(self, engine):
        self._age_the_engine(engine)
        self._idle_signals(engine, "S01", 25)
        engine_dispatch.check_starved(engine)
        engine_dispatch.check_starved(engine)
        starved = [n for n in engine.storage.notes(engine.epoch, "S01", limit=50) if n["kind"] == "starved"]
        assert len(starved) == 1

    async def test_a_book_that_traded_is_not_starved(self, engine):
        from app.core.types import Signal, TakeProfit

        self._age_the_engine(engine)
        self._idle_signals(engine, "S01", 25)
        sig = Signal("S01", SYM, "entry", "long", engine.clock() - 60_000, "1m", 100.0, 99.0,
                     [TakeProfit(103.0, 1.0)], id="S01-ok")
        engine.storage.insert_signal(sig, "approved", "", engine.epoch, None, life=1)
        engine_dispatch.check_starved(engine)
        assert [n for n in engine.storage.notes(engine.epoch, "S01", limit=50) if n["kind"] == "starved"] == []

    async def test_too_few_idle_signals_is_not_starvation(self, engine):
        self._age_the_engine(engine)
        self._idle_signals(engine, "S01", engine_dispatch.STARVED_MIN_IDLE_SIGNALS)
        engine_dispatch.check_starved(engine)
        assert [n for n in engine.storage.notes(engine.epoch, "S01", limit=50) if n["kind"] == "starved"] == []

    async def test_a_young_engine_is_never_starved(self, engine):
        self._idle_signals(engine, "S01", 40)
        engine_dispatch.check_starved(engine)  # boot_ms is "now": the 3h window has not elapsed
        assert [n for n in engine.storage.notes(engine.epoch, "S01", limit=50) if n["kind"] == "starved"] == []
