"""Virtual book maths: fills, fees, exits (TP / trail / BE / liq / time), R multiples, replay and reset."""
from __future__ import annotations

import pytest

from app.core.portfolio import Portfolio
from app.core.positions import ExitEngine
from app.core.types import RiskDecision, Signal, TakeProfit, TrailSpec, VirtualPosition
from app.execution.config import BINANCE_USDM
from tests.conftest import BTC_RULES, RULES, T0, settings_factory

SYM = "BTCUSDT"
REF = 65000.0
STOP = 64675.0            # 325 below the reference price
TP1 = REF + 2.5 * 325     # 65812.5
BPS = 2.0                 # the portfolio fixture pins slippage at 2 bps
FEE = BINANCE_USDM.taker_rate            # the venue schedule, not a local constant


def decision(qty: float = 0.01, leverage: int = 15) -> RiskDecision:
    return RiskDecision(True, "ok", qty=qty, notional=qty * REF, margin=qty * REF / leverage, leverage=leverage,
                        risk_usd=qty * (REF - STOP), rr=2.5)


def entry_signal(side: str = "long", stop: float = STOP, tps: list[TakeProfit] | None = None,
                 trail: TrailSpec | None = None, be_at_r: float | None = None, max_hold_s: int | None = None,
                 sid: str = "S01", leg: str | None = None) -> Signal:
    tps = [TakeProfit(TP1, 0.5)] if tps is None else tps
    return Signal(sid, SYM, "entry", side, T0, "1m", REF, stop, tps, trail=trail, be_at_r=be_at_r,
                  max_hold_s=max_hold_s, leg=leg, reason="test", id="sig-1")


def open_long(portfolio: Portfolio, qty: float = 0.01, **kw) -> tuple[VirtualPosition, object]:
    portfolio.ensure_wallet(kw.get("sid", "S01"), 500.0)
    return portfolio.open_position(entry_signal(**kw), decision(qty), REF, simulated=True)


@pytest.fixture
def exits() -> ExitEngine:
    return ExitEngine(RULES, atr_provider=lambda s, tf, n: 100.0)


# ---- fills, fees and slippage ----------------------------------------------------------------

class TestFillMaths:
    def test_long_entry_fill_is_ref_plus_slippage_and_pays_taker_fee(self, portfolio):
        pos, fill = open_long(portfolio)
        assert fill.side == "BUY" and fill.kind == "entry" and fill.is_open
        assert fill.price == pytest.approx(REF * (1 + BPS / 1e4))
        assert fill.slippage_bps == BPS
        assert fill.fee == pytest.approx(FEE * 0.01 * fill.price)
        assert pos.entry_price == fill.price
        assert pos.margin == pytest.approx(0.01 * fill.price / 15)
        assert pos.initial_risk_usd == pytest.approx(0.01 * (fill.price - STOP))
        w = portfolio.wallets["S01"]
        assert w.fees == pytest.approx(fill.fee) and w.realized == 0.0
        assert portfolio.margin_used("S01") == pytest.approx(pos.margin)

    def test_short_entry_fill_is_ref_minus_slippage(self, portfolio):
        portfolio.ensure_wallet("S01", 500.0)
        sig = entry_signal("short", stop=REF + 325, tps=[TakeProfit(REF - 812.5, 1.0)])
        pos, fill = portfolio.open_position(sig, decision(), REF, simulated=True)
        assert fill.side == "SELL"
        assert fill.price == pytest.approx(REF * (1 - BPS / 1e4))
        assert pos.side == "short" and pos.signed_qty == pytest.approx(-0.01)

    def test_close_long_sells_below_ref_and_books_pnl_and_fee(self, portfolio):
        pos, entry = open_long(portfolio)
        exit_ref = 66000.0
        fill = portfolio.close_position(pos.id, 1.0, exit_ref, "manual", simulated=True, reason="test close")
        assert fill.side == "SELL" and fill.kind == "manual" and not fill.is_open
        assert fill.price == pytest.approx(exit_ref * (1 - BPS / 1e4))
        assert fill.fee == pytest.approx(FEE * 0.01 * fill.price)
        assert fill.realized_pnl == pytest.approx((fill.price - entry.price) * 0.01)
        w = portfolio.wallets["S01"]
        assert w.realized == pytest.approx(fill.realized_pnl)
        assert w.fees == pytest.approx(entry.fee + fill.fee)
        assert w.equity() == pytest.approx(500.0 + fill.realized_pnl - entry.fee - fill.fee)
        assert pos.id not in portfolio.positions
        assert len(portfolio.closed_trades) == 1

    def test_close_short_buys_above_ref(self, portfolio):
        portfolio.ensure_wallet("S01", 500.0)
        sig = entry_signal("short", stop=REF + 325, tps=[TakeProfit(REF - 812.5, 1.0)])
        pos, entry = portfolio.open_position(sig, decision(), REF, simulated=True)
        fill = portfolio.close_position(pos.id, 1.0, 64000.0, "tp", simulated=True)
        assert fill.side == "BUY"
        assert fill.price == pytest.approx(64000.0 * (1 + BPS / 1e4))
        assert fill.realized_pnl == pytest.approx((entry.price - fill.price) * 0.01)
        assert fill.realized_pnl > 0

    def test_random_slippage_stays_inside_the_configured_band(self, settings, clock):
        pf = Portfolio(settings, RULES, clock)
        for _ in range(50):
            assert settings.slippage_bps_min <= pf.slippage_bps() <= settings.slippage_bps_max

    def test_maker_flag_uses_the_maker_fee(self, portfolio, settings):
        portfolio.ensure_wallet("S01", 500.0)
        sig = entry_signal()
        sig.meta["maker"] = True
        _, fill = portfolio.open_position(sig, decision(), REF, simulated=True)
        assert fill.fee == pytest.approx(settings.maker_fee * 0.01 * fill.price)


# ---- exits ----------------------------------------------------------------------------------------

class TestExitEngine:
    def test_partial_tp_then_atr_trail_that_never_falls(self, portfolio, exits):
        trail = TrailSpec("atr", "1m", 1.5, 14, activate_after_tp1=True)
        pos, _ = open_long(portfolio, trail=trail)
        # below TP1: nothing happens and the trail is not armed yet
        assert exits.on_price(pos, 65500.0, None, T0 + 1000) == []
        assert pos.stop == STOP and pos.tp1_done is False
        # TP1 (2.5R) hit -> close 50% of the ORIGINAL qty, arm the trail
        intents = exits.on_price(pos, 65900.0, None, T0 + 2000)
        assert len(intents) == 1 and intents[0].kind == "tp" and intents[0].fraction == pytest.approx(0.5)
        assert pos.tp1_done is True and pos.take_profits == []
        assert pos.stop == pytest.approx(65900.0 - 1.5 * 100.0) and pos.meta["stop_kind"] == "trail"
        fill = portfolio.close_position(pos.id, intents[0].fraction, intents[0].ref_price, "tp", simulated=True)
        assert fill.qty == pytest.approx(0.005) and pos.qty == pytest.approx(0.005)
        # trailing stop rises with price ...
        exits.on_price(pos, 66200.0, None, T0 + 3000)
        assert pos.stop == pytest.approx(66200.0 - 150.0)
        # ... and never falls back when price retraces
        exits.on_price(pos, 66000.0, None, T0 + 4000)
        assert pos.stop == pytest.approx(66050.0)
        assert pos.extreme_price == 66200.0
        # stop hit closes the rest with kind "trail" at the CURRENT price
        intents = exits.on_price(pos, 66040.0, None, T0 + 5000)
        assert len(intents) == 1 and intents[0].kind == "trail" and intents[0].fraction == 1.0
        assert intents[0].ref_price == 66040.0
        portfolio.close_position(pos.id, 1.0, intents[0].ref_price, intents[0].kind, simulated=True)
        assert pos.id not in portfolio.positions
        trade = portfolio.closed_trades[-1]
        assert trade.exit_kind == "trail" and trade.qty == pytest.approx(0.01)
        assert trade.net > 0

    def test_s01_shaped_runner_survives_tp1_and_trails(self, portfolio, exits):
        """The tuning pack's runner: BE at 0.8R, 40% off at TP1 (2.0R), the rest rides an ATR trail."""
        trail = TrailSpec("atr", "1m", 1.2, 14, activate_after_tp1=True)
        pos, _ = open_long(portfolio, tps=[TakeProfit(REF + 2.0 * 325, 0.40)], trail=trail, be_at_r=0.8)
        risk_per_unit = pos.initial_risk_usd / pos.qty_initial
        # 1. break-even arms at 0.8R, before TP1, and the trail is still asleep
        assert exits.on_price(pos, pos.entry_price + 0.8 * risk_per_unit, None, T0 + 1000) == []
        assert pos.be_done is True and pos.tp1_done is False
        assert pos.stop >= pos.entry_price and pos.meta["stop_kind"] == "be"
        be_stop = pos.stop
        # 2. TP1 closes 40% of the ORIGINAL qty -- the runner is NOT closed with it
        tp_price = pos.entry_price + 2.1 * risk_per_unit
        intents = exits.on_price(pos, tp_price, None, T0 + 2000)
        assert len(intents) == 1 and intents[0].kind == "tp"
        assert intents[0].fraction == pytest.approx(0.40)
        portfolio.close_position(pos.id, intents[0].fraction, intents[0].ref_price, "tp", simulated=True)
        assert pos.id in portfolio.positions, "the remainder stays open"
        assert pos.qty == pytest.approx(0.6 * pos.qty_initial)
        assert pos.tp1_done is True and pos.take_profits == []
        # 3. the trail is live now and follows price up, never down
        assert pos.trail is not None
        exits.on_price(pos, tp_price + 500.0, None, T0 + 3000)
        assert pos.meta["stop_kind"] == "trail"
        assert pos.stop == pytest.approx(tp_price + 500.0 - 1.2 * 100.0)
        assert pos.stop > be_stop > STOP
        higher = pos.stop
        exits.on_price(pos, tp_price + 200.0, None, T0 + 4000)
        assert pos.stop == pytest.approx(higher), "a trailing stop never retreats"
        assert pos.extreme_price == pytest.approx(tp_price + 500.0)

    def test_last_tp_without_runner_closes_everything(self, portfolio, exits):
        pos, _ = open_long(portfolio, tps=[TakeProfit(TP1, 0.5)], trail=None)
        intents = exits.on_price(pos, 66000.0, None, T0 + 1000)
        assert len(intents) == 1 and intents[0].kind == "tp" and intents[0].fraction == 1.0

    def test_break_even_moves_the_stop_at_be_at_r(self, portfolio, exits):
        pos, _ = open_long(portfolio, tps=[TakeProfit(REF + 10 * 325, 1.0)], be_at_r=1.0)
        per_unit_risk = pos.initial_risk_usd / pos.qty_initial
        exits.on_price(pos, pos.entry_price + 0.9 * per_unit_risk, None, T0 + 1000)
        assert pos.stop == STOP and pos.be_done is False
        exits.on_price(pos, pos.entry_price + 1.0 * per_unit_risk, None, T0 + 2000)
        assert pos.be_done is True
        assert pos.stop == pytest.approx(pos.entry_price * (1 + exits.fee_buffer_bps / 1e4))
        assert pos.meta["stop_kind"] == "be"
        intents = exits.on_price(pos, pos.stop - 1.0, None, T0 + 3000)
        assert intents and intents[0].kind == "be"

    def test_paper_liquidation_when_mark_crosses_liq_price(self, portfolio, exits):
        pos, _ = open_long(portfolio)
        liq = pos.liq_price(BTC_RULES.maint_margin_rate)
        assert liq == pytest.approx(pos.entry_price * (1 - (1 / 15 - BTC_RULES.maint_margin_rate)))
        assert liq < STOP, "at 15x the paper liquidation sits below the stop"
        assert exits.on_price(pos, pos.entry_price, liq * 1.001, T0 + 1000) == []
        intents = exits.on_price(pos, pos.entry_price, liq * 0.999, T0 + 2000)
        assert len(intents) == 1 and intents[0].kind == "liq" and intents[0].fraction == 1.0
        assert intents[0].ref_price == pytest.approx(liq)

    def test_short_paper_liquidation_is_above_entry(self, portfolio, exits):
        portfolio.ensure_wallet("S01", 500.0)
        sig = entry_signal("short", stop=REF + 325, tps=[TakeProfit(REF - 812.5, 1.0)])
        pos, _ = portfolio.open_position(sig, decision(), REF, simulated=True)
        liq = pos.liq_price(exits.mmr(SYM))
        assert liq > pos.entry_price
        intents = exits.on_price(pos, pos.entry_price, liq * 1.001, T0 + 1000)
        assert intents and intents[0].kind == "liq"

    def test_time_exit_at_max_hold_deadline(self, portfolio, exits):
        pos, _ = open_long(portfolio, max_hold_s=60)
        assert pos.max_hold_deadline == T0 + 60_000
        assert exits.on_price(pos, pos.entry_price, None, T0 + 59_999) == []
        intents = exits.on_price(pos, pos.entry_price, None, T0 + 60_000)
        assert len(intents) == 1 and intents[0].kind == "time" and intents[0].fraction == 1.0

    def test_stop_hit_fills_at_current_price_not_the_stop(self, portfolio, exits):
        pos, _ = open_long(portfolio)
        gap = STOP - 200.0
        intents = exits.on_price(pos, gap, None, T0 + 1000)
        assert intents[0].kind == "stop" and intents[0].ref_price == gap

    def test_apply_update_only_tightens_unless_allow_worse(self, portfolio, exits):
        from app.core.types import ExitUpdate
        pos, _ = open_long(portfolio)
        assert exits.apply_update(pos, ExitUpdate(stop=STOP - 100), 65000.0) == []
        assert pos.stop == STOP
        exits.apply_update(pos, ExitUpdate(stop=STOP + 100), 65000.0)
        assert pos.stop == STOP + 100
        exits.apply_update(pos, ExitUpdate(stop=STOP - 100, allow_worse=True), 65000.0)
        assert pos.stop == STOP - 100
        intents = exits.apply_update(pos, ExitUpdate(close=True, reason="regime"), 65000.0)
        assert intents and intents[0].kind == "manual" and intents[0].fraction == 1.0


# ---- close_position rounding ----------------------------------------------------------------

class TestCloseRounding:
    def test_partial_close_rounds_to_the_lot_step(self, portfolio):
        pos, _ = open_long(portfolio, qty=0.01)
        fill = portfolio.close_position(pos.id, 0.33, REF, "tp", simulated=True)
        assert fill.qty == pytest.approx(0.0033)
        assert pos.qty == 0.0067
        assert pos.margin == pytest.approx(0.0067 * pos.entry_price / 15)
        assert pos.id in portfolio.positions

    def test_remainder_below_min_notional_stays_open(self, portfolio):
        """A partial exit is reduce-only, which Binance exempts from MIN_NOTIONAL (-4164), and the
        remainder is a position, not an order. Neither has to clear $50 -- only the lot size."""
        pos, _ = open_long(portfolio, qty=0.001)  # $65 position: each half is ~$32 < $50
        fill = portfolio.close_position(pos.id, 0.5, REF, "tp", simulated=True)
        assert fill.qty == pytest.approx(0.0005)
        assert pos.id in portfolio.positions and pos.qty == pytest.approx(0.0005)

    def test_remainder_below_one_lot_closes_everything(self, portfolio):
        pos, _ = open_long(portfolio, qty=0.0003)
        fill = portfolio.close_position(pos.id, 0.9, REF, "tp", simulated=True)
        assert fill.qty == pytest.approx(0.0003), "a sub-lot remainder could never be closed later"
        assert pos.id not in portfolio.positions

    def test_conservative_multiplier_restores_the_old_rule(self, tmp_path, clock):
        """The old behaviour survives as an explicit PaperLab preference, not as an exchange rule."""
        from tests.conftest import settings_factory
        pf = Portfolio(settings_factory(data_dir=str(tmp_path / "d"),
                                        MIN_NOTIONAL_SAFETY_MULTIPLIER="2"), RULES, clock)
        pf.fixed_slippage_bps = 2.0
        pos, _ = open_long(pf, qty=0.001)
        fill = pf.close_position(pos.id, 0.5, REF, "tp", simulated=True)
        assert fill.qty == pytest.approx(0.001)
        assert pos.id not in pf.positions


# ---- R multiples and stats ------------------------------------------------------------------

class TestStats:
    def test_closed_trade_r_multiple_is_net_over_initial_risk(self, portfolio):
        pos, entry = open_long(portfolio)
        fill = portfolio.close_position(pos.id, 1.0, 66000.0, "tp", simulated=True)
        t = portfolio.closed_trades[-1]
        net = fill.realized_pnl - entry.fee - fill.fee
        assert t.pnl == pytest.approx(fill.realized_pnl)
        assert t.fees == pytest.approx(entry.fee + fill.fee)
        assert t.net == pytest.approx(net)
        assert t.r_multiple == pytest.approx(net / (0.01 * (entry.price - STOP)))
        assert t.exit_price == pytest.approx(fill.price)
        assert t.hold_s == 0 and t.exit_kind == "tp" and t.simulated

    def test_stats_win_rate_profit_factor_and_avg_r(self, portfolio):
        pos, _ = open_long(portfolio)
        portfolio.close_position(pos.id, 1.0, 66000.0, "tp", simulated=True)
        pos, _ = open_long(portfolio)
        portfolio.close_position(pos.id, 1.0, 64600.0, "stop", simulated=True)
        win, loss = portfolio.closed_trades
        assert win.net > 0 > loss.net
        st = portfolio.stats("S01", T0)
        assert st["trades_total"] == 2 and st["trades_today"] == 2
        assert st["win_rate"] == pytest.approx(0.5)
        assert st["profit_factor"] == pytest.approx(win.net / -loss.net)
        assert st["avg_r"] == pytest.approx((win.r_multiple + loss.r_multiple) / 2)
        assert st["net"] == pytest.approx(win.net + loss.net)
        assert portfolio.stats("S99", T0)["trades_total"] == 0
        assert portfolio.stats(None, T0)["trades_total"] == 2

    def test_only_wins_gives_capped_profit_factor(self, portfolio):
        pos, _ = open_long(portfolio)
        portfolio.close_position(pos.id, 1.0, 66000.0, "tp", simulated=True)
        assert portfolio.stats("S01", T0)["profit_factor"] == 999.0


# ---- replay determinism -----------------------------------------------------------------------

def _wallet_dict(pf: Portfolio) -> dict:
    return {sid: (w.allocation, w.realized, w.fees, w.funding) for sid, w in pf.wallets.items()}


class TestReplay:
    def test_replay_rebuilds_wallets_closed_trades_and_open_positions(self, portfolio, settings, clock):
        fills = []
        p1, f = open_long(portfolio, sid="S01")
        fills.append(f)
        fills.append(portfolio.close_position(p1.id, 0.5, 65900.0, "tp", simulated=True))
        clock.advance(60_000)
        fills.append(portfolio.close_position(p1.id, 1.0, 66100.0, "trail", simulated=True))
        p2, f = open_long(portfolio, sid="S02", leg="g1")
        fills.append(f)
        clock.advance(60_000)
        fills.append(portfolio.close_position(p2.id, 1.0, 64500.0, "stop", simulated=True))
        p3, f = open_long(portfolio, sid="S01")  # stays open -> restored from its snapshot
        fills.append(f)
        fills.extend(portfolio.apply_funding(SYM, 0.0001, 65000.0))
        snapshots = list(portfolio.positions.values())
        allocations = {sid: w.allocation for sid, w in portfolio.wallets.items()}

        fresh = Portfolio(settings, RULES, clock)
        fresh.replay(fills, snapshots, allocations)

        assert set(fresh.wallets) == set(portfolio.wallets)
        for sid, (alloc, realized, fees, funding) in _wallet_dict(portfolio).items():
            w = fresh.wallets[sid]
            assert (w.allocation, w.realized, w.fees, w.funding) == pytest.approx((alloc, realized, fees, funding))
        assert len(fresh.closed_trades) == len(portfolio.closed_trades) == 2
        for a, b in zip(portfolio.closed_trades, fresh.closed_trades):
            da, db = a.to_dict(), b.to_dict()
            assert set(da) == set(db)
            for key, val in da.items():
                if isinstance(val, float):
                    assert db[key] == pytest.approx(val), key
                else:
                    assert db[key] == val, key
        assert list(fresh.positions) == [p3.id]
        assert fresh.positions[p3.id].qty == p3.qty and fresh.positions[p3.id].entry_price == p3.entry_price
        assert fresh.total_equity({SYM: REF}) == pytest.approx(portfolio.total_equity({SYM: REF}))

    def test_replay_is_idempotent(self, portfolio, settings, clock):
        p, f = open_long(portfolio)
        fills = [f, portfolio.close_position(p.id, 1.0, 66000.0, "tp", simulated=True)]
        allocations = {"S01": 500.0}
        a = Portfolio(settings, RULES, clock)
        a.replay(fills, [], allocations)
        a.replay(fills, [], allocations)
        assert len(a.closed_trades) == 1
        assert _wallet_dict(a) == pytest.approx(_wallet_dict(portfolio))


# ---- funding and reset -------------------------------------------------------------------------

class TestFundingAndReset:
    def test_longs_pay_positive_funding_and_shorts_receive_it(self, portfolio):
        long_pos, _ = open_long(portfolio, sid="S01")
        portfolio.ensure_wallet("S02", 500.0)
        sig = entry_signal("short", stop=REF + 325, tps=[TakeProfit(REF - 812.5, 1.0)], sid="S02")
        short_pos, _ = portfolio.open_position(sig, decision(), REF, simulated=True)
        fills = portfolio.apply_funding(SYM, 0.0001, 65000.0)
        assert len(fills) == 2
        by_sid = {f.strategy_id: f for f in fills}
        assert by_sid["S01"].realized_pnl == pytest.approx(-0.0001 * long_pos.notional(65000.0))
        assert by_sid["S02"].realized_pnl == pytest.approx(+0.0001 * short_pos.notional(65000.0))
        assert portfolio.wallets["S01"].funding < 0 < portfolio.wallets["S02"].funding
        assert all(f.kind == "funding" and f.qty == 0.0 and f.meta.get("paper") for f in fills)
        assert portfolio.apply_funding("ETHUSDT", 0.0001, 3000.0) == []

    def test_negative_rate_pays_longs(self, portfolio):
        open_long(portfolio)
        (fill,) = portfolio.apply_funding(SYM, -0.0002, 65000.0)
        assert fill.realized_pnl > 0 and portfolio.wallets["S01"].funding > 0

    def test_reset_clears_positions_and_reallocates(self, portfolio):
        p, _ = open_long(portfolio)
        portfolio.close_position(p.id, 0.5, 66000.0, "tp", simulated=True)
        assert portfolio.positions and portfolio.wallets["S01"].realized != 0
        portfolio.reset(2, {"S01": 1000.0, "S02": 250.0})
        assert portfolio.epoch == 2
        assert portfolio.positions == {} and len(portfolio.closed_trades) == 0
        assert set(portfolio.wallets) == {"S01", "S02"}
        assert portfolio.wallets["S01"].allocation == 1000.0 and portfolio.wallets["S01"].realized == 0.0
        assert portfolio.wallets["S01"].fees == 0.0 and portfolio.wallets["S02"].allocation == 250.0
        assert portfolio.total_equity({SYM: REF}) == pytest.approx(1250.0)


def test_settings_factory_defaults(tmp_path):
    s = settings_factory(data_dir=str(tmp_path / "d"))
    assert s.taker_fee == 0.0005 and s.maker_fee == 0.0002 and s.fee_source == "binance_usdm"
    assert (s.slippage_bps_min, s.slippage_bps_max) == (1.0, 3.0)
