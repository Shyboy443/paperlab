"""V13 SNAPBACK: the resting post-only limit entry engine (fill on a trade-through at the limit and the maker fee,
post-only rejection, expiry, slots, the fill bar walked from the fill), the stretch measure, the strategy's limit
signals, the freeze and the wiring. No network."""
from __future__ import annotations

import math

import pytest

from app.competition import v13_config as v13
from app.core.types import Candle, MarketRules
from app.live.v13_engine import LimitEntryEngineV13
from app.strategies.v11.scan import ScanV11
from app.strategies.v13.snapback import DAY_BARS, SnapbackV13, stretch_atr
from tests.conftest import T0

MINUTE = 60_000
DAY = 86_400_000
D0 = (T0 // DAY + 1) * DAY
ANCHOR = "BTCUSDT"


# -- a scripted strategy: one limit order at a chosen minute -----------------------------------------------------------
def scripted(orders: dict[int, list[tuple[str, str, float, float, float, int]]], max_pos: int = 4):
    """orders: decision minute -> [(symbol, side, limit, stop, tp, expiry minutes)]."""

    class Scripted(ScanV11):
        id = "T13"
        name = "scripted limits"
        family = "TEST"
        signal_tf, ctx_fast, ctx_slow = "1m", "1m", "1m"
        max_positions = max_pos

        def on_candle(self, c, ctx):
            if c.tf != "1m" or c.symbol != ANCHOR:
                return []
            m = (c.open_time - D0) // MINUTE
            out = []
            for sym, side, limit, stop, tp, exp in orders.get(m, []):
                s = self.make_entry(symbol=sym, side=side, ts=c.close_time, tf="1m", price=limit, stop=stop,
                                    tps_price=[(tp, 1.0)], max_hold_s=3600,
                                    meta={"limit": limit, "expire_ms": c.close_time + 1 + MINUTE + exp * MINUTE})
                s.id = f"T13:{sym}:{m}"
                out.append(s)
            return out
    return Scripted.for_universe(["AAAUSDT", "BBBUSDT", ANCHOR])


def run(prices: dict[str, dict[int, tuple[float, float, float, float]]], orders, minutes: int = 40, max_pos: int = 4,
        flat: float = 100.0):
    syms = ["AAAUSDT", "BBBUSDT", ANCHOR]
    rules = {s: MarketRules(s, 0.0001, 0.001, 0.001, 5.0) for s in syms}
    eng = LimitEntryEngineV13(v13.settings_v13(), syms, rules=rules, seed=7, execution=v13.EXECUTION_V13,
                              sizing=None, leverage_policy="needed", max_risk_pct=None, maker_tp=True)

    def tape():
        for m in range(minutes):
            t = D0 + m * MINUTE
            for s in syms:
                o, h, lo, c = prices.get(s, {}).get(m, (flat, flat + 0.05, flat - 0.05, flat) if s != ANCHOR else
                                                     (50_000.0, 50_010.0, 49_990.0, 50_000.0))
                yield Candle(s, "1m", t, o, h, lo, c, 10.0, t + MINUTE - 1)
    res = eng.run(scripted(orders, max_pos), tape(), since_ms=D0, leverage=v13.LEVERAGE_CEILING, signal_tf="1m")
    return eng, res


LONG = ("AAAUSDT", "long", 99.0, 98.0, 100.0, 5)       # buy limit 99, stop 98, target 100, live 5 minutes


class TestLimitEngine:
    def test_a_limit_fills_only_on_a_trade_through_at_its_price_and_the_maker_fee(self):
        # decided at minute 10's close: on the book from minute 12 (the decision instant + the 60 s window)
        px = {"AAAUSDT": {12: (100.0, 100.1, 99.5, 99.8), 13: (99.8, 99.9, 98.9, 99.1),     # 98.9 < 99 x (1 - 0.5 bp)
                          14: (99.1, 99.6, 99.0, 99.5), 15: (99.5, 100.2, 99.4, 100.1), 16: (100.1, 100.2, 100.0, 100.1)}}
        eng, res = run(px, {10: [LONG]})
        assert len(res.trades) == 1
        t = res.trades[0]
        assert t.entry_price == pytest.approx(99.0) and t.exit_kind == "tp" and t.exit_price == pytest.approx(100.0)
        entry = next(f for f in res.fills if f.kind == "entry")
        assert entry.price == pytest.approx(99.0) and entry.meta["liquidity_role"] == "MAKER"
        assert entry.fee == pytest.approx(99.0 * entry.qty * 0.0002)                       # Bybit maker 0.020%
        assert t.net > 0 and t.r_multiple == pytest.approx((1.0 - t.fees / t.qty) / 1.0, rel=0.05)

    def test_a_touch_is_not_a_fill(self):
        px = {"AAAUSDT": {13: (99.8, 99.9, 99.0, 99.1)}}                                    # low == the limit
        eng, res = run(px, {10: [LONG]})
        assert res.trades == [] and res.rejects.get("limit_expired") == 1

    def test_post_only_rejects_a_limit_that_would_cross_when_placed(self):
        px = {"AAAUSDT": {12: (98.5, 98.6, 98.0, 98.2)}}                                    # already below 99
        eng, res = run(px, {10: [LONG]})
        assert res.trades == [] and res.rejects.get("post_only_would_cross") == 1

    def test_an_unfilled_limit_is_cancelled_at_expiry(self):
        px = {"AAAUSDT": {20: (99.8, 99.9, 98.5, 98.7)}}                                    # dips long after expiry
        eng, res = run(px, {10: [LONG]})
        assert res.trades == [] and res.rejects.get("limit_expired") == 1

    def test_a_stop_on_the_way_down_in_the_fill_bar_is_hit(self):
        px = {"AAAUSDT": {13: (99.8, 99.9, 97.5, 97.8)}}
        eng, res = run(px, {10: [LONG]})
        t = res.trades[0]
        assert t.exit_kind == "stop" and t.entry_price == pytest.approx(99.0) and t.r_multiple < -1.0

    def test_the_fill_bar_is_walked_from_the_fill_dip_first_then_the_rise(self):
        # one bar: opens 99.8, dips to 98.9 (the limit fills at 99), then rises to 100.3 (the target): profit, same bar
        px = {"AAAUSDT": {13: (99.8, 100.3, 98.9, 100.1)}}
        eng, res = run(px, {10: [LONG]})
        t = res.trades[0]
        assert t.entry_price == pytest.approx(99.0) and t.exit_kind == "tp" and t.exit_price == pytest.approx(100.0)
        # the same bar with a dip through the stop as well: the stop (adverse first), never the target
        px = {"AAAUSDT": {13: (99.8, 100.3, 97.9, 100.1)}}
        eng, res = run(px, {10: [LONG]})
        assert res.trades[0].exit_kind == "stop"

    def test_a_short_limit_mirrors(self):
        short = ("AAAUSDT", "short", 101.0, 102.0, 100.0, 5)
        px = {"AAAUSDT": {13: (100.2, 101.1, 100.1, 100.9), 14: (100.9, 101.0, 99.8, 99.9), 15: (99.9, 100.0, 99.9, 99.95)}}
        eng, res = run(px, {10: [short]})
        t = res.trades[0]
        assert t.side == "short" and t.entry_price == pytest.approx(101.0) and t.exit_kind == "tp"

    def test_slots_are_checked_when_a_limit_fills(self):
        both = [LONG, ("BBBUSDT", "long", 99.0, 98.0, 100.0, 5)]
        px = {s: {13: (99.8, 99.9, 98.9, 99.1)} for s in ("AAAUSDT", "BBBUSDT")}
        eng, res = run(px, {10: both}, max_pos=1)
        opened = [f for f in res.fills if f.kind == "entry"]
        assert len(opened) == 1 and res.rejects.get("limit_slot_taken") == 1


def test_the_end_of_a_replay_closes_an_alt_at_its_own_price_not_with_btcs_bar():
    # filled at 99, then held; the tape ends on a WIDE BTC bar (the anchor is always last): the shared engine priced the
    # alt's exit against that bar and could print absurd (even negative) prices
    px = {"AAAUSDT": {13: (99.8, 99.9, 98.9, 99.1), **{m: (99.4, 99.5, 99.3, 99.4) for m in range(14, 30)}},
          ANCHOR: {29: (50_000.0, 52_000.0, 48_000.0, 50_000.0)}}
    eng, res = run(px, {10: [LONG]}, minutes=30)
    t = res.trades[0]
    assert t.exit_kind == "end_of_run" and abs(t.exit_price - 99.4) < 0.05


def five_min(prices: list[float], vol: float = 10.0) -> list[Candle]:
    out = []
    for i, p in enumerate(prices):
        prev = prices[i - 1] if i else p
        out.append(Candle("AAAUSDT", "5m", D0 + i * 300_000, prev, max(p, prev) * 1.001, min(p, prev) * 0.999, p, vol,
                          D0 + (i + 1) * 300_000 - 1))
    return out


class TestStretch:
    def test_stretch_matches_its_definition(self):
        prices = [100 * (1 + 0.002 * math.sin(i / 7)) for i in range(DAY_BARS + 40)]
        cs = five_min(prices)
        st, atr, vwap = stretch_atr(cs)
        win = cs[-DAY_BARS:]
        vw = sum((c.high + c.low + c.close) / 3 * c.volume for c in win) / sum(c.volume for c in win)
        r = [math.log(b.close / a.close) for a, b in zip(cs[-DAY_BARS - 1:-1], win)]
        mu = sum(r) / len(r)
        sd = math.sqrt(sum((x - mu) ** 2 for x in r) / len(r))
        assert vwap == pytest.approx(vw) and st == pytest.approx(math.log(cs[-1].close / vw) / (sd * math.sqrt(DAY_BARS)))
        assert atr == pytest.approx(sum(max(c.high, p.close) - min(c.low, p.close) for p, c in zip(cs[-15:-1], cs[-14:])) / 14)

    def test_needs_a_full_day(self):
        assert stretch_atr(five_min([100.0] * DAY_BARS)) is None


class TestStrategy:
    def test_a_stretched_coin_gets_one_resting_limit_beyond_the_close(self):
        base = [100 * (1 + 0.002 * math.sin(i / 7)) for i in range(DAY_BARS + 10)]
        prices = base + [base[-1] * 0.997 ** (j + 1) for j in range(12)]          # a 1-hour selloff of ~3.5%
        cs = five_min(prices)

        class Ctx:
            def candles(self, sym, tf):
                return cs if sym == "AAAUSDT" else []
        strat = SnapbackV13.for_universe(["AAAUSDT", ANCHOR])()
        anchor = Candle(ANCHOR, "5m", cs[-1].open_time, 1, 1, 1, 1, 1, cs[-1].close_time)
        sigs = strat.on_candle(anchor, Ctx())
        assert len(sigs) == 1
        s = sigs[0]
        st, atr, _ = stretch_atr(cs)
        p = strat.params
        assert s.side == "long" and st <= -p.threshold
        assert s.entry_price == pytest.approx(prices[-1] - p.offset_atr * atr) and s.meta["limit"] == s.entry_price
        assert s.stop == pytest.approx(s.entry_price * (1 - max(p.min_stop_pct, p.stop_atr * atr / s.entry_price)))
        assert s.take_profits[0].price == pytest.approx(s.entry_price + p.target_r * (s.entry_price - s.stop))
        assert s.meta["expire_ms"] == anchor.close_time + MINUTE + int(p.expiry_min * MINUTE)
        assert strat.on_candle(anchor, Ctx()) == []                                         # one order per coin

    def test_a_coin_near_its_vwap_is_left_alone(self):
        cs = five_min([100 * (1 + 0.002 * math.sin(i / 7)) for i in range(DAY_BARS + 11)])

        class Ctx:
            def candles(self, sym, tf):
                return cs if sym == "AAAUSDT" else []
        strat = SnapbackV13.for_universe(["AAAUSDT", ANCHOR])()
        anchor = Candle(ANCHOR, "5m", cs[-1].open_time, 1, 1, 1, 1, 1, cs[-1].close_time)
        assert strat.on_candle(anchor, Ctx()) == []


class TestFieldAndWiring:
    def test_one_control_bot_deciding_every_5_minutes_never_eliminated(self):
        plan = v13.field_plan()
        assert [s.key for s in plan] == ["V13.1-SNAP"] and plan[0].timeframe == "5m" and plan[0].symbol == ""
        assert v13.should_eliminate({"net_now": -15.0, "trades": 0}, 10 * DAY) is None
        status, d = v13.bot_status({"trades": 160, "net_now": 1.0, "profit_factor": 1.3, "max_dd": 0.1}, 15 * DAY)
        assert status == "QUALIFIED"
        assert v13.bot_status({"trades": 60, "net_now": 1.0, "profit_factor": 1.3, "max_dd": 0.1}, 15 * DAY)[0] == "ACTIVE"
        assert v13.bot_status({"trades": 160, "net_now": 1.0, "profit_factor": 1.3, "max_dd": 0.1}, 3 * DAY)[0] == "ACTIVE"

    def test_the_identity_pins_the_engine_and_the_v11_objects_it_uses(self):
        code = v13.code_fingerprints()
        for m in ("app.live.v13_engine", "app.strategies.v13.snapback", "app.live.v13_worker", "app.live.scan_engine",
                  "app.live.scan_market", "app.strategies.v11.scan.ScanV11", "app.live.v11_worker.ScanGateV11"):
            assert m in code
        assert "app.strategies.v11.scan" not in code and "app.live.v11_worker" not in code   # V11-only edits don't restart V13

    def test_service_names_roster_and_routes(self, tmp_path):
        from app.core import bot_names, roster_view
        from app.core.api_public import router
        from app.live.v6_service import V6ForwardService
        svc = V6ForwardService(str(tmp_path / "v13.db"), env={"V13_FORWARD_ENABLED": "true"}, program="V13")
        assert svc.retired and not svc.enabled and svc.cfg["jev"] is False  # retired 2026-10-08 (app/core/programs.py): never starts
        names = bot_names.assign([("v13", "V13.1-SNAP"), ("v11", "V11.1-SCAN")])
        bounce = names[("v13", "V13.1-SNAP")]                   # a champion, like every bot
        assert bounce in bot_names.NAMES
        d = bot_names.describe("v13", {"key": "V13.1-SNAP", "strategy_id": "V13.1", "coin": "ALL"}, names)
        assert d["name"] == bounce and d["where"] == "29 coins"
        assert "v13" in roster_view.PINNED and roster_view.PROGRAMS["v13"] == "V13 Snapback"
        methods = {m for r in router.routes if getattr(r, "path", "").endswith("/v13") for m in r.methods}
        assert methods and methods <= {"GET", "HEAD"}

    def test_the_settings_message_describes_snapback(self):
        from app.live.telegram import settings_text
        assert "V13 Snapback" in settings_text() and "LIMIT order" in settings_text()


def test_the_running_v13_is_the_frozen_manifest_with_the_study_choice():
    man = v13.load_freeze()
    assert man and v13.verify_freeze(man) == []
    assert man["study_verdict"] == "FAIL"
    p = SnapbackV13.Params()
    assert {k: getattr(p, k) for k in ("threshold", "offset_atr", "target_r", "max_hold_min")} == man["study_selected"]
    eid, _ = v13.experiment_identity(man)
    assert eid.startswith("v13x-")
