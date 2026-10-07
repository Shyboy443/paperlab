"""RiskManager.approve sizing, every rejection reason, halts, exchange-margin halt and cooldowns."""
from __future__ import annotations

import pytest

from app.core.portfolio import Portfolio
from app.core.risk import REJECT_CODES, RiskManager, reject_code
from app.core.types import ExchangePosition, Signal, TakeProfit
from tests.conftest import BTC_RULES, RULES, FixedClock, meta_for, settings_factory

SYM = "BTCUSDT"
PRICES = {SYM: 65000.0, "ETHUSDT": 3000.0}
ENTRY, STOP, TP = 65000.0, 64675.0, 65812.5  # 0.5% stop, 2.5R target
RISK_PCT = 0.04  # RISK_PER_TRADE_PCT default


def long_sig(entry: float = ENTRY, stop: float = STOP, tps: list[TakeProfit] | None = None, symbol: str = SYM,
             ts: int = 0, **meta) -> Signal:
    tps = [TakeProfit(TP, 1.0)] if tps is None else tps
    return Signal("S01", symbol, "entry", "long", ts, "1m", entry, stop, tps, meta=dict(meta))


def short_sig() -> Signal:
    return Signal("S01", SYM, "entry", "short", 0, "1m", ENTRY, 65325.0, [TakeProfit(64187.5, 1.0)])


@pytest.fixture
def settings(tmp_path):
    """Lab-wide notional caps relaxed for the unit rig.

    The gross / net caps are multiples of the WHOLE lab's equity (20 books in production). These tests run
    one or two wallets, so at the 4% risk default a single 0.5%-stop entry (8 x its own book) would trip the
    4 x net cap on rig size alone. The caps have their own dedicated tests below, which build their own
    Settings.
    """
    return settings_factory(data_dir=str(tmp_path / "data"), MAX_TOTAL_NOTIONAL_MULT=100,
                            MAX_NET_NOTIONAL_MULT=100)


def rig(tmp_path, clock, balance: float = 500.0, **env):
    """A standalone (RiskManager, Portfolio) pair with one funded book and the given env overrides."""
    env.setdefault("MAX_TOTAL_NOTIONAL_MULT", 100)
    env.setdefault("MAX_NET_NOTIONAL_MULT", 100)
    s = settings_factory(data_dir=str(tmp_path / "d"), **env)
    pf = Portfolio(s, RULES, clock)
    pf.fixed_slippage_bps = 2.0
    rm = RiskManager(s, pf, RULES, clock)
    rm.state.engine_running = True
    rm.roll_day(balance, "2026-01-01")
    pf.ensure_wallet("S01", balance)
    rm.set_halt_floor("S01", balance)
    return rm, pf


@pytest.fixture
def funded(risk, portfolio):
    portfolio.ensure_wallet("S01", 500.0)
    risk.set_halt_floor("S01", 500.0)
    return risk


class TestSizing:
    def test_valid_long_is_sized_from_4pct_of_the_wallet(self, funded):
        d = funded.approve(long_sig(), meta_for(), PRICES)
        assert d.approved and d.reason == "ok", d.reason
        expected_qty = BTC_RULES.round_qty_down(RISK_PCT * 500.0 * 1.0 / (ENTRY - STOP))
        assert d.qty == pytest.approx(expected_qty)
        assert d.notional == pytest.approx(d.qty * ENTRY)
        assert d.notional >= 100
        assert d.rr == pytest.approx(2.5)
        assert d.leverage == 15
        assert d.risk_usd == pytest.approx(d.qty * (ENTRY - STOP))
        assert d.margin == pytest.approx(d.notional / 15)

    def test_size_mult_scales_qty(self, funded):
        d = funded.approve(long_sig(), meta_for(size_mult=0.5), PRICES)
        assert d.approved
        assert d.qty == pytest.approx(BTC_RULES.round_qty_down(RISK_PCT * 500.0 * 0.5 / (ENTRY - STOP)))

    def test_margin_uses_the_virtual_leverage(self, funded):
        d15 = funded.approve(long_sig(), meta_for(leverage=15), PRICES)
        d25 = funded.approve(long_sig(), meta_for(leverage=25), PRICES)
        assert d15.approved and d25.approved
        assert d15.margin == pytest.approx(d15.notional / 15)
        assert d25.margin == pytest.approx(d25.notional / 25)
        assert d25.margin < d15.margin
        assert d25.leverage == 25

    def test_grid_leg_is_sized_from_margin_pct(self, funded):
        sig = long_sig(tps=[], margin_pct=0.08)
        d = funded.approve(sig, meta_for(min_rr=0), PRICES)
        assert d.approved, d.reason
        expected_qty = BTC_RULES.round_qty_down(0.08 * 500.0 * 15 / ENTRY)
        assert d.qty == pytest.approx(expected_qty)
        assert d.margin == pytest.approx(0.08 * 500.0, rel=0.02)
        assert d.rr is None


class TestRejections:
    def test_rr_below_min(self, funded):
        d = funded.approve(long_sig(tps=[TakeProfit(65500.0, 1.0)]), meta_for(), PRICES)
        assert not d.approved and d.reason.startswith("rr_below_min")

    def test_no_take_profit_when_min_rr_positive(self, funded):
        d = funded.approve(long_sig(tps=[]), meta_for(), PRICES)
        assert not d.approved and d.reason == "no_take_profit"

    def test_stop_too_tight(self, funded):
        d = funded.approve(long_sig(stop=64990.0, tps=[TakeProfit(65025.0, 1.0)]), meta_for(), PRICES)
        assert not d.approved and d.reason.startswith("stop_too_tight:")
        assert d.reason.endswith("bps<8"), d.reason
        assert reject_code(d.reason) == "stop_bps"

    def test_stop_wrong_side(self, funded):
        d = funded.approve(long_sig(stop=65100.0), meta_for(), PRICES)
        assert not d.approved and d.reason == "stop_wrong_side"

    def test_max_positions(self, funded, portfolio):
        meta = meta_for()
        d = funded.approve(long_sig(), meta, PRICES)
        assert d.approved
        portfolio.open_position(long_sig(), d, ENTRY, simulated=True)
        d2 = funded.approve(long_sig(symbol="ETHUSDT", entry=3000.0, stop=2985.0, tps=[TakeProfit(3037.5, 1.0)]),
                            meta, PRICES)
        assert not d2.approved and d2.reason == "max_positions"

    def test_strategy_disabled(self, funded):
        d = funded.approve(long_sig(), meta_for(enabled=False), PRICES)
        assert not d.approved and d.reason == "strategy_disabled"

    def test_killed(self, funded):
        funded.kill()
        d = funded.approve(long_sig(), meta_for(), PRICES)
        assert not d.approved and d.reason == "killed"
        funded.unkill()
        assert funded.approve(long_sig(), meta_for(), PRICES).approved

    def test_daily_halt(self, funded):
        assert funded.check_daily_halt(8800.0) is True
        d = funded.approve(long_sig(), meta_for(), PRICES)
        assert not d.approved and d.reason == "daily_halt"

    def test_spot_venue_refuses_shorts(self, funded):
        d = funded.approve(short_sig(), meta_for(), PRICES, venue_kind="spot")
        assert not d.approved and d.reason == "venue: no shorts"

    def test_spot_venue_clamps_leverage_to_1(self, funded):
        d = funded.approve(long_sig(), meta_for(leverage=15), PRICES, venue_kind="spot")
        assert d.approved, d.reason
        assert d.leverage == 1
        assert d.margin == pytest.approx(d.notional)
        assert any(c.name == "leverage_clamped" for c in d.checks)

    def test_strategy_cooldown(self, funded, clock):
        meta = meta_for()
        meta.cooldown_until[SYM] = clock.now + 60_000
        d = funded.approve(long_sig(), meta, PRICES)
        assert not d.approved and d.reason == "cooldown"
        clock.advance(60_001)
        assert funded.approve(long_sig(), meta, PRICES).approved

    def test_symbol_cooldown_expires_after_300s(self, funded, clock):
        funded.set_symbol_cooldown(SYM, 300_000)
        d = funded.approve(long_sig(), meta_for(), PRICES)
        assert not d.approved and d.reason == "symbol_cooldown"
        clock.advance(299_000)
        assert funded.approve(long_sig(), meta_for(), PRICES).reason == "symbol_cooldown"
        clock.advance(1_001)
        assert funded.approve(long_sig(), meta_for(), PRICES).approved

    def test_below_min_notional_with_a_tiny_wallet(self, funded, portfolio):
        """BTC's minimum is 50 USDT; a 3 USDT wallet cannot reach it even at full leverage."""
        portfolio.ensure_wallet("S02", 3.0)
        sig = long_sig()
        sig.strategy_id = "S02"
        d = funded.approve(sig, meta_for("S02"), PRICES)
        assert not d.approved and d.reason.startswith("below_min_notional")

    def test_one_min_notional_is_required(self, funded, portfolio):
        """Binance checks MIN_NOTIONAL (50 USDT on BTC) on the submitted order. At 4% risk on a 0.5%
        stop a 6 USDT wallet reaches only 45.50 USDT and is refused."""
        portfolio.ensure_wallet("S03", 6.0)
        sig = long_sig()
        sig.strategy_id = "S03"
        d = funded.approve(sig, meta_for("S03"), PRICES)
        assert not d.approved
        assert d.reason == "below_min_notional:45.50<50.00"
        assert reject_code(d.reason) == "min_notional"

    def test_an_order_above_one_minimum_is_not_refused_for_a_future_partial(self, funded, portfolio):
        """78 USDT clears BTC's 50 USDT minimum. It used to be refused because a hypothetical 50%
        partial exit (39 USDT) would not -- but exits are reduce-only, which Binance exempts."""
        portfolio.ensure_wallet("S03", 10.0)
        sig = long_sig()
        sig.strategy_id = "S03"
        d = funded.approve(sig, meta_for("S03"), PRICES)
        assert d.approved and d.notional == pytest.approx(78.0)

    def test_the_old_two_x_rule_is_an_explicit_opt_in(self, clock, tmp_path):
        s = settings_factory(data_dir=str(tmp_path / "d"), MIN_NOTIONAL_SAFETY_MULTIPLIER="2")
        pf = Portfolio(s, RULES, clock)
        rm = RiskManager(s, pf, RULES, clock)
        rm.state.engine_running = True
        rm.roll_day(10.0, "2026-01-01")
        pf.ensure_wallet("S03", 10.0)
        sig = long_sig()
        sig.strategy_id = "S03"
        d = rm.approve(sig, meta_for("S03"), PRICES)
        assert not d.approved and d.reason == "below_min_notional:78.00<100.00"

    def test_below_min_qty_has_its_own_reason(self, funded, portfolio):
        portfolio.ensure_wallet("S03", 0.5)
        sig = long_sig()
        sig.strategy_id = "S03"
        d = funded.approve(sig, meta_for("S03"), PRICES)
        assert not d.approved and d.reason.startswith("below_min_qty:")
        assert reject_code(d.reason) == "min_qty"

    def test_insufficient_virtual_margin(self, funded, portfolio):
        meta = meta_for(leverage=1, max_positions=2)
        d = funded.approve(long_sig(), meta, PRICES)
        assert d.approved and d.margin == pytest.approx(d.notional)
        portfolio.open_position(long_sig(), d, ENTRY, simulated=True)  # ~all of the wallet is now margin
        d2 = funded.approve(long_sig(symbol="ETHUSDT", entry=3000.0, stop=2985.0, tps=[TakeProfit(3037.5, 1.0)]),
                            meta, PRICES)
        assert not d2.approved and d2.reason.startswith("insufficient_virtual_margin")

    def test_gross_notional_cap(self, clock, tmp_path):
        s = settings_factory(data_dir=str(tmp_path / "d"), MAX_TOTAL_NOTIONAL_MULT=0.5)
        pf = Portfolio(s, RULES, clock)
        rm = RiskManager(s, pf, RULES, clock)
        rm.state.engine_running = True
        rm.roll_day(500.0, "2026-01-01")
        pf.ensure_wallet("S01", 500.0)
        d = rm.approve(long_sig(), meta_for(), PRICES)
        assert not d.approved and d.reason.startswith("gross_notional_cap")

    def test_net_notional_cap(self, clock, tmp_path):
        s = settings_factory(data_dir=str(tmp_path / "d"), MAX_NET_NOTIONAL_MULT=0.5)
        pf = Portfolio(s, RULES, clock)
        pf.fixed_slippage_bps = 2.0
        rm = RiskManager(s, pf, RULES, clock)
        rm.state.engine_running = True
        rm.roll_day(500.0, "2026-01-01")
        pf.ensure_wallet("S01", 500.0)
        d = rm.approve(long_sig(), meta_for(), PRICES)
        assert not d.approved and d.reason.startswith("net_notional_cap")

    def test_exchange_margin_cap_when_not_dry_run(self, funded):
        d = funded.approve(long_sig(), meta_for(), PRICES, venue_kind="futures", exchange_available=1.0,
                           exchange_leverage=15, dry_run=False)
        assert not d.approved and d.reason.startswith("exchange_margin_cap")
        ok = funded.approve(long_sig(), meta_for(), PRICES, venue_kind="futures", exchange_available=1000.0,
                            exchange_leverage=15, dry_run=False)
        assert ok.approved and any(c.name == "exchange_margin" for c in ok.checks)

    def test_engine_not_running(self, funded):
        funded.state.engine_running = False
        d = funded.approve(long_sig(), meta_for(), PRICES)
        assert not d.approved and d.reason == "engine_not_running"


class TestHalts:
    def test_daily_halt_at_minus_12pct(self, risk):
        assert risk.state.start_of_day_equity == 10_000.0
        assert risk.check_daily_halt(8801.0) is False
        assert risk.state.daily_halted is False
        assert risk.check_daily_halt(8800.0) is True
        assert risk.state.daily_halted is True
        assert risk.check_daily_halt(8000.0) is False, "already halted: not reported twice"

    def test_roll_day_is_idempotent_per_date(self, risk):
        assert risk.roll_day(9000.0, "2026-01-01") is False
        assert risk.state.start_of_day_equity == 10_000.0
        assert risk.roll_day(9000.0, "2026-01-02") is True
        assert risk.state.start_of_day_equity == 9000.0

    def test_strategy_halt_at_minus_25pct(self, risk):
        assert risk.set_halt_floor("S01", 500.0) == pytest.approx(375.0)
        assert risk.check_strategy_halt("S01", 376.0) is False
        assert risk.check_strategy_halt("S01", 375.0) is True
        assert "S01" in risk.state.halted_strategies
        assert risk.check_strategy_halt("S01", 100.0) is False, "already halted"
        new_floor = risk.clear_strategy_halt("S01", 400.0)
        assert new_floor == pytest.approx(300.0)
        assert "S01" not in risk.state.halted_strategies

    def test_halted_strategy_is_rejected(self, funded):
        funded.check_strategy_halt("S01", 375.0)
        d = funded.approve(long_sig(), meta_for(), PRICES)
        assert not d.approved and d.reason == "strategy_halted"

    def test_exchange_margin_halt_flags_only_the_endangered_symbol(self, funded):
        positions = {
            SYM: ExchangePosition(SYM, 0.01, 65000.0, 64700.0, 43.0, 0.0, 15, mark=65000.0),  # 0.46% from liq
            "ETHUSDT": ExchangePosition("ETHUSDT", 0.1, 3000.0, 2000.0, 20.0, 0.0, 15, mark=3000.0),  # 33% away
        }
        mmr = {SYM: 0.004, "ETHUSDT": 0.004}
        assert positions[SYM].liq_distance_pct() <= max(2 * 0.004, 0.005)
        newly = funded.check_exchange_margin(positions, mmr)
        assert newly == [SYM]
        assert funded.state.margin_halted == {SYM}
        d = funded.approve(long_sig(), meta_for(), PRICES)
        assert not d.approved and d.reason == "margin_halted"
        eth = funded.approve(long_sig(symbol="ETHUSDT", entry=3000.0, stop=2985.0, tps=[TakeProfit(3037.5, 1.0)]),
                             meta_for(), PRICES)
        assert eth.approved, eth.reason
        assert funded.check_exchange_margin(positions, mmr) == [], "not reported twice"
        funded.clear_margin_halt(SYM)
        assert funded.approve(long_sig(), meta_for(), PRICES).approved

    def test_snapshot_reports_halts(self, funded, clock):
        funded.set_symbol_cooldown(SYM, 1000)
        snap = funded.snapshot(PRICES)
        assert snap["killed"] is False and snap["daily_halted"] is False
        assert snap["start_of_day_equity"] == 10_000.0
        assert SYM in snap["symbol_cooldowns"]
        clock.advance(2000)
        assert SYM not in funded.snapshot(PRICES)["symbol_cooldowns"]


def test_fixed_clock_fixture():
    c = FixedClock(10)
    assert c() == 10 and c.advance(5) == 15 and c() == 15


class TestIsolatedWalletSizing:
    """Every strategy runs its own book: sizing reads THAT wallet's equity, never the paper total."""

    @pytest.fixture
    def two_books(self, risk, portfolio):
        portfolio.ensure_wallet("S01", 100.0)   # the default isolated book
        portfolio.ensure_wallet("S02", 200.0)   # one that was raised in the drawer
        risk.set_halt_floor("S01", 100.0)
        risk.set_halt_floor("S02", 200.0)
        return risk

    @staticmethod
    def _sig_for(sid: str) -> Signal:
        sig = long_sig()
        sig.strategy_id = sid
        return sig

    def test_the_same_signal_is_sized_from_each_strategys_own_wallet(self, two_books):
        small = two_books.approve(self._sig_for("S01"), meta_for("S01"), PRICES)
        big = two_books.approve(self._sig_for("S02"), meta_for("S02"), PRICES)
        assert small.approved and big.approved, (small.reason, big.reason)
        assert small.qty == pytest.approx(BTC_RULES.round_qty_down(RISK_PCT * 100.0 / (ENTRY - STOP)))
        assert big.qty == pytest.approx(BTC_RULES.round_qty_down(RISK_PCT * 200.0 / (ENTRY - STOP)))
        assert big.qty > small.qty
        assert big.risk_usd == pytest.approx(2 * small.risk_usd, rel=0.02)

    def test_sizing_ignores_the_other_books(self, two_books, portfolio):
        before = two_books.approve(self._sig_for("S01"), meta_for("S01"), PRICES).qty
        for sid in ("S03", "S04", "S05"):
            portfolio.ensure_wallet(sid, 100.0)  # paper total grows; S01's book does not
        after = two_books.approve(self._sig_for("S01"), meta_for("S01"), PRICES).qty
        assert after == pytest.approx(before)

    def test_a_loss_on_one_book_only_shrinks_that_books_next_trade(self, two_books, portfolio):
        before = two_books.approve(self._sig_for("S01"), meta_for("S01"), PRICES).qty
        other = two_books.approve(self._sig_for("S02"), meta_for("S02"), PRICES).qty
        portfolio.wallets["S01"].realized -= 50.0
        assert two_books.approve(self._sig_for("S01"), meta_for("S01"), PRICES).qty < before
        assert two_books.approve(self._sig_for("S02"), meta_for("S02"), PRICES).qty == pytest.approx(other)

    def test_halt_floor_is_75_on_a_100_book(self, two_books):
        assert two_books.set_halt_floor("S01", 100.0) == pytest.approx(75.0)
        assert two_books.check_strategy_halt("S01", 75.1) is False
        assert two_books.check_strategy_halt("S01", 74.9) is True
        assert two_books.state.halted_strategies == {"S01"}, "the other books keep trading"

    def test_a_zero_floor_never_halts(self, two_books):
        two_books.state.halt_floors["S02"] = 0.0
        assert two_books.check_strategy_halt("S02", 0.01) is False

    def test_min_notional_is_the_symbols_own_minimum(self, two_books, portfolio):
        """BTC needs one 50 USDT minimum per order: an 11 USDT book reaches 84.50 and trades, a
        6 USDT book reaches only 45.50 and is refused."""
        portfolio.ensure_wallet("S06", 11.0)
        assert two_books.approve(self._sig_for("S06"), meta_for("S06"), PRICES).approved
        portfolio.ensure_wallet("S05", 6.0)
        d = two_books.approve(self._sig_for("S05"), meta_for("S05"), PRICES)
        assert not d.approved and d.reason.startswith("below_min_notional")
        assert d.reason.endswith("<50.00")
        assert two_books.approve(self._sig_for("S01"), meta_for("S01"), PRICES).approved

    def test_the_caps_reject_they_never_clamp(self, clock, tmp_path):
        s = settings_factory(data_dir=str(tmp_path / "d"), MAX_TOTAL_NOTIONAL_MULT=1.0, balance=100)
        pf = Portfolio(s, RULES, clock)
        rm = RiskManager(s, pf, RULES, clock)
        rm.state.engine_running = True
        rm.roll_day(100.0, "2026-01-01")
        pf.ensure_wallet("S01", 100.0)
        d = rm.approve(long_sig(), meta_for(), PRICES)
        assert not d.approved and d.reason.startswith("gross_notional_cap")
        assert d.qty == 0.0, "a capped entry is refused outright, not shrunk to fit"


# ---- short reject codes for the Strategies grid ------------------------------------------------

class TestRejectCodes:
    """Every rejection the GUI shows must collapse to one of the documented short codes."""

    def test_min_notional(self, funded, portfolio):
        portfolio.ensure_wallet("S07", 6.0)
        sig = long_sig()
        sig.strategy_id = "S07"
        d = funded.approve(sig, meta_for("S07"), PRICES)
        assert d.reason.startswith("below_min_notional:")
        assert reject_code(d.reason) == "min_notional"

    def test_rr(self, funded):
        d = funded.approve(long_sig(tps=[TakeProfit(65500.0, 1.0)]), meta_for(), PRICES)
        assert d.reason.startswith("rr_below_min:")
        assert reject_code(d.reason) == "rr"
        assert reject_code(funded.approve(long_sig(tps=[]), meta_for(), PRICES).reason) == "rr"

    def test_stop_bps(self, funded):
        tight = funded.approve(long_sig(stop=64990.0, tps=[TakeProfit(65025.0, 1.0)]), meta_for(), PRICES)
        assert reject_code(tight.reason) == "stop_bps"
        wrong = funded.approve(long_sig(stop=65100.0), meta_for(), PRICES)
        assert reject_code(wrong.reason) == "stop_bps"

    def test_max_positions(self, funded, portfolio):
        meta = meta_for()
        d = funded.approve(long_sig(), meta, PRICES)
        portfolio.open_position(long_sig(), d, ENTRY, simulated=True)
        again = funded.approve(long_sig(symbol="ETHUSDT", entry=3000.0, stop=2985.0,
                                        tps=[TakeProfit(3037.5, 1.0)]), meta, PRICES)
        assert reject_code(again.reason) == "max_positions"

    def test_cooldown(self, funded, clock):
        meta = meta_for()
        meta.cooldown_until[SYM] = clock.now + 60_000
        assert reject_code(funded.approve(long_sig(), meta, PRICES).reason) == "cooldown"
        funded.set_symbol_cooldown("ETHUSDT", 300_000)
        eth = funded.approve(long_sig(symbol="ETHUSDT", entry=3000.0, stop=2985.0,
                                      tps=[TakeProfit(3037.5, 1.0)]), meta_for(), PRICES)
        assert reject_code(eth.reason) == "cooldown"

    def test_venue(self, funded):
        d = funded.approve(short_sig(), meta_for(), PRICES, venue_kind="spot")
        assert d.reason == "venue: no shorts"
        assert reject_code(d.reason) == "venue"
        assert reject_code(funded.approve(long_sig(), meta_for(supported=False), PRICES).reason) == "venue"

    def test_cap_gross(self, clock, tmp_path):
        rm, _ = rig(tmp_path, clock, MAX_TOTAL_NOTIONAL_MULT=0.5)
        d = rm.approve(long_sig(), meta_for(), PRICES)
        assert d.reason.startswith("gross_notional_cap:")
        assert reject_code(d.reason) == "cap_gross"

    def test_cap_net(self, clock, tmp_path):
        rm, _ = rig(tmp_path, clock, MAX_NET_NOTIONAL_MULT=0.5)
        d = rm.approve(long_sig(), meta_for(), PRICES)
        assert d.reason.startswith("net_notional_cap:")
        assert reject_code(d.reason) == "cap_net"

    def test_an_unknown_reason_keeps_its_head(self):
        assert reject_code("brand_new_gate:17<42") == "brand_new_gate"
        assert reject_code("brand_new_gate") == "brand_new_gate"
        assert reject_code("") == "unknown"

    def test_every_documented_code_is_reachable_from_the_table(self):
        for reason, code in REJECT_CODES.items():
            assert reject_code(reason) == code
            assert reject_code(f"{reason}:detail") == code


# ---- MIN_STOP_BPS replaces the old hard-coded 5 bps floor ---------------------------------------

SIX_BPS_STOP = ENTRY * (1 - 6 / 1e4)          # 64961.0
SIX_BPS_TP = ENTRY + 2.5 * (ENTRY - SIX_BPS_STOP)


class TestMinStopBps:
    def test_six_bps_is_refused_at_the_default_eight(self, funded):
        assert funded.settings.min_stop_bps == pytest.approx(8.0)
        d = funded.approve(long_sig(stop=SIX_BPS_STOP, tps=[TakeProfit(SIX_BPS_TP, 1.0)]), meta_for(), PRICES)
        assert not d.approved
        assert d.reason == "stop_too_tight:6.0bps<8"

    def test_six_bps_is_accepted_when_the_floor_is_five(self, clock, tmp_path):
        # The fee gate also refuses a 6 bps stop (it needs >= 32 bps at the default 25% ceiling), so it is
        # opened up here to keep this test about the min_stop_bps floor alone. TestFeePreCheck owns the fee.
        rm, _ = rig(tmp_path, clock, MIN_STOP_BPS=5, MAX_FEE_SHARE_OF_R=1.0)
        assert rm.settings.min_stop_bps == pytest.approx(5.0)
        d = rm.approve(long_sig(stop=SIX_BPS_STOP, tps=[TakeProfit(SIX_BPS_TP, 1.0)]), meta_for(), PRICES)
        assert d.approved, d.reason
        assert any(c.name == "stop_sane" for c in d.checks)

    def test_the_floor_is_read_from_settings_not_hard_coded(self, clock, tmp_path):
        rm, _ = rig(tmp_path, clock, MIN_STOP_BPS=30)
        d = rm.approve(long_sig(), meta_for(), PRICES)  # the 50 bps default stop still clears 30
        assert d.approved, d.reason
        narrow = ENTRY * (1 - 20 / 1e4)
        d2 = rm.approve(long_sig(stop=narrow, tps=[TakeProfit(ENTRY + 2.5 * (ENTRY - narrow), 1.0)]),
                        meta_for(), PRICES)
        assert not d2.approved and d2.reason == "stop_too_tight:20.0bps<30"


# ---- 4% risk per trade ---------------------------------------------------------------------------

class TestRiskPerTrade:
    def test_risk_usd_is_four_percent_of_the_wallet(self, funded):
        d = funded.approve(long_sig(), meta_for(), PRICES)
        assert d.approved, d.reason
        assert d.risk_usd == pytest.approx(RISK_PCT * 500.0, rel=0.01)

    def test_both_size_mults_scale_the_risk(self, funded):
        sig = long_sig()
        sig.size_mult = 1.5
        d = funded.approve(sig, meta_for(size_mult=0.5), PRICES)
        assert d.approved, d.reason
        assert d.risk_usd == pytest.approx(RISK_PCT * 500.0 * 0.5 * 1.5, rel=0.01)
        assert d.qty == pytest.approx(BTC_RULES.round_qty_down(RISK_PCT * 500.0 * 0.5 * 1.5 / (ENTRY - STOP)))

    def test_it_reads_the_strategys_own_wallet_not_the_lab_total(self, funded, portfolio):
        portfolio.ensure_wallet("S05", 50.0)
        mine = funded.approve(long_sig(), meta_for(), PRICES)
        sig = long_sig()
        sig.strategy_id = "S05"
        theirs = funded.approve(sig, meta_for("S05"), PRICES)
        assert mine.approved and theirs.approved, (mine.reason, theirs.reason)
        assert mine.risk_usd == pytest.approx(RISK_PCT * 500.0, rel=0.01)
        assert theirs.risk_usd == pytest.approx(RISK_PCT * 50.0, rel=0.02)
        assert portfolio.total_equity(PRICES) > 500.0, "the lab total is bigger and must not be used"


class TestFeePreCheck:
    """`fee_gt_r`: a round trip that eats more than 25% of R is refused before it is opened.

    With taker 0.05% both sides, 2 x fee x notional <= 0.25 x risk_usd reduces to
    stop_dist / price >= 8 x taker = 40 bps, independent of size — so this is a floor on stop WIDTH,
    not on account size, and it is the binding constraint for the tight-stop books.
    """

    def wide(self, tmp_path, clock, stop_bps: float):
        rm, _ = rig(tmp_path, clock)
        stop = ENTRY * (1 - stop_bps / 1e4)
        tp = ENTRY + (ENTRY - stop) * 2.5
        return rm, long_sig(stop=stop, tps=[TakeProfit(tp, 1.0)])

    @pytest.mark.parametrize("stop_bps,ok", [(20, False), (39, False), (41, True), (100, True)])
    def test_boundary_is_40_bps(self, tmp_path, clock, stop_bps, ok):
        rm, sig = self.wide(tmp_path, clock, stop_bps)
        d = rm.approve(sig, meta_for("S01"), PRICES)
        assert d.approved is ok, d.reason
        if not ok:
            assert reject_code(d.reason) == "fee_gt_r"

    def test_reason_carries_the_numbers(self, tmp_path, clock):
        rm, sig = self.wide(tmp_path, clock, 20)
        reason = rm.approve(sig, meta_for("S01"), PRICES).reason
        head, _, nums = reason.partition(":")
        assert head == "fee_gt_r"
        fee, _, budget = nums.partition(">")
        assert float(fee) > float(budget) > 0

    def test_code_is_registered_for_the_gui(self):
        assert REJECT_CODES["fee_gt_r"] == "fee_gt_r"

    def test_ceiling_is_configurable(self, tmp_path, clock):
        """A looser ceiling admits the same signal: the gate is the setting, not a hardcoded number."""
        stop = ENTRY * (1 - 20 / 1e4)
        sig = long_sig(stop=stop, tps=[TakeProfit(ENTRY + (ENTRY - stop) * 2.5, 1.0)])
        tight, _ = rig(tmp_path, clock)
        loose, _ = rig(tmp_path, clock, MAX_FEE_SHARE_OF_R=0.60)
        assert not tight.approve(sig, meta_for("S01"), PRICES).approved
        assert loose.approve(sig, meta_for("S01"), PRICES).approved

    def test_passing_trades_record_the_check(self, tmp_path, clock):
        rm, sig = self.wide(tmp_path, clock, 100)
        d = rm.approve(sig, meta_for("S01"), PRICES)
        assert d.approved and any(c.name == "fee_vs_r" and c.ok for c in d.checks)
