"""HTF buffer stability, cache corrections, signal slots and order reservations."""
from collections import deque
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.core.types import Candle, Signal
from app.strategies.v11.scan import Cand
from app.strategies.v14 import htf
from tests.test_v14 import Ctx, T, T0, UP


def test_htf_indicators_do_not_move_until_a_new_htf_candle_closes():
    ctx = Ctx({"A": UP})
    ctx.bars["A"] = deque(ctx.bars["A"], maxlen=600)
    before = htf.htf_view(ctx, "A", 100, T)
    last = ctx.bars["A"][-1]
    ctx.bars["A"].append(replace(last, open_time=T + 1, close_time=T + htf.HOUR,
                                  open=130, high=200, low=120, close=190))
    after = htf.htf_view(ctx, "A", 190, T + htf.HOUR)
    assert after["h4_close_ms"] == before["h4_close_ms"]
    assert after["d1_close_ms"] == before["d1_close_ms"]
    assert after["h4_slope_atr"] == before["h4_slope_atr"]
    assert after["d1_slope_atr"] == before["d1_slope_atr"]


def test_same_completed_history_has_same_view_regardless_of_extra_old_bars():
    full = Ctx({"A": UP})
    limited = Ctx({"A": UP})
    limited.bars["A"] = limited.bars["A"][-480:]
    assert htf.htf_view(full, "A", 100, T) == htf.htf_view(limited, "A", 100, T)


def test_cached_view_detects_in_place_corruption():
    ctx = Ctx({"A": UP})
    assert htf.htf_view(ctx, "A", 100, T)
    ctx.bars["A"][-1].low = 1e8
    assert htf.htf_view(ctx, "A", 100, T) is None


def test_cached_rejection_recovers_when_middle_bar_is_repaired():
    ctx = Ctx({"A": UP})
    original = ctx.bars["A"][-200]
    ctx.bars["A"][-200] = replace(original, low=1e8)
    assert htf.htf_view(ctx, "A", 100, T) is None
    ctx.bars["A"][-200] = original
    assert htf.htf_view(ctx, "A", 100, T)


def test_returned_view_cannot_mutate_cached_trend():
    ctx = Ctx({"A": UP})
    view = htf.htf_view(ctx, "A", 100, T)
    view["h4"] = -1
    assert htf.htf_view(ctx, "A", 100, T)["h4"] == 1


def test_old_invalid_bar_outside_required_window_does_not_block_valid_current_data():
    ctx = Ctx({"A": UP})
    ctx.bars["A"][0] = replace(ctx.bars["A"][0], low=1e8)
    assert htf.htf_view(ctx, "A", 100, T)


def scanner(max_signals=1, failed="A", capacity=None):
    class Base:
        timeframes = ("15m",)
        signal_tf = "15m"
        anchor = "BTC"

        def __init__(self):
            self.params = SimpleNamespace(max_signals=max_signals, min_score=0)

        def bound(self, c):
            return c.tf == "15m"

        def scan(self, ctx, t):
            return [Cand(s, "long", 99, 1, score, {}, "setup") for s, score in (("A", 0.9), ("B", 0.8))]

        def _signal(self, ctx, x, t):
            return None if x.symbol == failed else Signal(self.id, x.symbol, "entry", x.side, t, "15m", 100)

        def on_candle(self, c, ctx):
            found = sorted(self.scan(ctx, c.close_time), key=lambda x: -x.score)
            return [sig for x in found[:self.params.max_signals]
                    if (sig := self._signal(ctx, x, c.close_time)) is not None]
    if capacity is not None:
        Base.max_positions = capacity
    return htf.htf_scanner(Base, "V14.2", "test")()


def test_invalid_highest_ranked_candidate_does_not_consume_scanner_slot():
    ctx = Ctx({"A": UP, "B": UP}, {"A": 100, "B": 100, "BTC": 100})
    out = scanner().on_candle(ctx.candles("BTC", "15m")[-1], ctx)
    assert [s.symbol for s in out] == ["B"]


def test_held_coin_is_filtered_before_ranking():
    ctx = Ctx({"A": UP, "B": UP}, {"A": 100, "B": 100})
    ctx.positions_of = lambda sid: [SimpleNamespace(symbol="A")]
    assert [c.symbol for c in scanner().scan(ctx, T)] == ["B"]


def snapback(monkeypatch, *, failed="A", max_signals=1):
    from app.strategies.v13 import snapback as original
    monkeypatch.setattr(original, "stretch_atr", lambda cs: (-5 if cs[-1].symbol == "A" else -4, 1, 100))
    class Base:
        timeframes = ("5m",)
        signal_tf = "5m"
        anchor = "BTC"
        universe = ("A", "B", "BTC")

        def __init__(self):
            self.params = SimpleNamespace(max_signals=max_signals, threshold=3)
            self.resting = {}

        def current(self, ctx, sym, tf, t, need):
            return ctx.candles(sym, tf)

        def limit_signal(self, ctx, c, st, atr, vwap, t):
            if c.symbol == failed:
                return None
            return Signal(self.id, c.symbol, "entry", "long", t, "5m", 100,
                          meta={"expire_ms": T + 900_000})
    return htf.htf_snapback(Base, "V14.4", "test")()


def test_invalid_highest_stretch_does_not_consume_limit_order_slot(monkeypatch):
    ctx = Ctx({"A": UP, "B": UP}, {"A": 100, "B": 100, "BTC": 100})
    out = snapback(monkeypatch).on_candle(ctx.candles("BTC", "5m")[-1], ctx)
    assert [s.symbol for s in out] == ["B"]


def test_cancelled_order_releases_its_strategy_reservation(monkeypatch):
    bot = snapback(monkeypatch, failed=None)
    bot.resting["A"] = T + 900_000
    ctx = Ctx({"A": UP, "B": UP}, {"A": 100, "B": 100, "BTC": 100})
    ctx._v14_pending_symbols = frozenset()  # engine confirms old order was cancelled
    out = bot.on_candle(ctx.candles("BTC", "5m")[-1], ctx)
    assert [s.symbol for s in out] == ["A"]


def test_actual_pending_order_remains_reserved(monkeypatch):
    bot = snapback(monkeypatch, failed=None)
    ctx = Ctx({"A": UP, "B": UP}, {"A": 100, "B": 100, "BTC": 100})
    ctx._v14_pending_symbols = frozenset({"A"})
    out = bot.on_candle(ctx.candles("BTC", "5m")[-1], ctx)
    assert [s.symbol for s in out] == ["B"]


@pytest.mark.parametrize("values", [(2, 1, .3), (1, float("nan"), .3), (1, 1, float("nan"))])
def test_invalid_regime_values_fail_closed(values):
    h4, d1, slope = values
    assert not htf.allows({"h4": h4, "d1": d1, "h4_slope_atr": slope}, "long", "breakout")


def test_resume_checks_the_entire_experiment_for_every_arm():
    from scripts.v14_htf_revision_study import reusable_rows
    jobs = [("V14.2", mode, None) for mode in ("BASE", "OLD", "NEW")]
    legacy = [{"sid": sid, "mode": mode, "coin": coin, "revision": "same"} for sid, mode, coin in jobs]
    assert reusable_rows(legacy, jobs, "current") == []
    wrong = [{**row, "input_signature": "changed-data-or-engine"} for row in legacy]
    assert reusable_rows(wrong, jobs, "current") == []
    matching = [{**row, "input_signature": "current"} for row in legacy]
    assert reusable_rows(matching, jobs, "current") == matching
    with pytest.raises(ValueError, match="Duplicate"):
        reusable_rows(matching + matching[:1], jobs, "current")


def test_single_family_study_uses_a_separate_checkpoint():
    from scripts.v14_htf_revision_study import output_path
    paths = [output_path(sid).with_suffix(".partial.json") for sid in (None, "V14.1", "V14.2")]
    assert len(set(paths)) == 3


def test_replay_discards_results_if_inputs_changed_before_start(monkeypatch):
    from scripts import v14_htf_revision_study as study
    monkeypatch.setattr(study, "study_signature", lambda: "edited-source")
    with pytest.raises(RuntimeError, match="changed before"):
        study.run(("V14.2", "NEW", None, "original-source"))


def test_limit_engine_publishes_pending_and_filled_reservations():
    from app.live.v14_engine import HTFLimitEngine
    from tests.test_v14_fill_context import replay
    seen = []
    def inspect(eng):
        seen.append(eng.ctx._v14_pending_symbols)
    eng, result = replay(HTFLimitEngine, "V14.4", after_queue=inspect)
    assert seen == [frozenset({"A"})]
    assert any(f.kind == "entry" for f in result.fills)
    assert eng.ctx._v14_pending_symbols == frozenset()


@pytest.mark.parametrize("family", ["scanner", "snapback"])
def test_zero_signal_budget_never_emits_an_order(monkeypatch, family):
    ctx = Ctx({"A": UP, "B": UP}, {"A": 100, "B": 100, "BTC": 100})
    bot = scanner(0) if family == "scanner" else snapback(monkeypatch, max_signals=0)
    tf = "15m" if family == "scanner" else "5m"
    assert bot.on_candle(ctx.candles("BTC", tf)[-1], ctx) == []


def test_held_snapback_coin_cannot_displace_a_free_coin(monkeypatch):
    ctx = Ctx({"A": UP, "B": UP}, {"A": 100, "B": 100, "BTC": 100})
    ctx.positions_of = lambda sid: [SimpleNamespace(symbol="A")]
    bot = snapback(monkeypatch, failed=None)
    assert [s.symbol for s in bot.on_candle(ctx.candles("BTC", "5m")[-1], ctx)] == ["B"]


@pytest.mark.parametrize("occupied_kind", ["held", "pending"])
def test_scanner_reserves_free_slots_for_the_highest_ranked_setups(occupied_kind):
    ctx = Ctx({"A": UP, "B": UP}, {"A": 100, "B": 100, "BTC": 100})
    if occupied_kind == "held":
        ctx.positions_of = lambda sid: [SimpleNamespace(symbol="C")]
    else:
        ctx._v14_pending_symbols = frozenset({"C"})
    bot = scanner(max_signals=2, failed=None, capacity=2)
    out = bot.on_candle(ctx.candles("BTC", "15m")[-1], ctx)
    assert [s.symbol for s in out] == ["A"]  # lower-ranked B cannot fill the last slot first


def test_full_scanner_book_does_not_queue_more_entries():
    ctx = Ctx({"A": UP, "B": UP}, {"A": 100, "B": 100, "BTC": 100})
    ctx.positions_of = lambda sid: [SimpleNamespace(symbol="C"), SimpleNamespace(symbol="D")]
    bot = scanner(max_signals=2, failed=None, capacity=2)
    assert bot.on_candle(ctx.candles("BTC", "15m")[-1], ctx) == []


def test_resting_orders_reserve_limit_book_capacity(monkeypatch):
    bot = snapback(monkeypatch, failed=None, max_signals=4)
    bot.max_positions = 2
    ctx = Ctx({"A": UP, "B": UP}, {"A": 100, "B": 100, "BTC": 100})
    ctx._v14_pending_symbols = frozenset({"C"})
    assert [s.symbol for s in bot.on_candle(ctx.candles("BTC", "5m")[-1], ctx)] == ["A"]
