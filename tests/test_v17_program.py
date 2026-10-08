"""V17 SWING paper program: the daily-bar store (only final bars, fetched on demand at the day's turn), the studied
classes reading it, the field, the freeze, the service switch and champion names. No network."""
from __future__ import annotations

from types import SimpleNamespace

from app.competition import v17_config as v17

DAY = v17.DAY


def kline(day: int, close: float) -> list:
    return [day * DAY, close - 1, close + 2, close - 2, close, 10, 1000]


class FakeBybit:
    """Daily klines up to (and including the forming) day of `now`, recording each fetch."""

    def __init__(self, clock: dict, closes: dict[int, float]):
        self.clock, self.closes, self.calls = clock, closes, []

    def __call__(self, symbol: str, start: int, end: int) -> list:
        self.calls.append((symbol, start, end))
        today = self.clock["now"] // DAY
        return [kline(d, self.closes[d]) for d in sorted(self.closes) if start <= d * DAY < end and d <= today]


def test_only_final_bars_count_and_the_just_closed_day_is_fetched_on_demand():
    clock = {"now": 100 * DAY + 23 * 3_600_000}              # day 100, 23:00 UTC
    bb = FakeBybit(clock, {d: 100.0 + d for d in range(0, 102)})
    store = v17.DailyBars(bb, now=lambda: clock["now"])
    store.load("BTCUSDT", 50)
    assert [b.open_time // DAY for b in store.completed("BTCUSDT", clock["now"])][-1] == 99   # day 100 is forming
    assert store.count("BTCUSDT") == 50
    clock["now"] = 101 * DAY + 60_000                         # 00:01 the next day: day 100 has ended
    calls = len(bb.calls)
    bars = store.completed("BTCUSDT", 101 * DAY)              # the 00:00 decision
    assert bars[-1].open_time == 100 * DAY and bars[-1].close == 200.0 and len(bb.calls) == calls + 1
    store.completed("BTCUSDT", 101 * DAY)                     # already final: no second fetch
    assert len(bb.calls) == calls + 1


def test_a_bar_fetched_while_forming_is_never_used():
    clock = {"now": 100 * DAY + 3_600_000}
    store = v17.DailyBars(FakeBybit(clock, {100: 1.0, 99: 2.0}), now=lambda: clock["now"])
    store.merge("ETHUSDT", [kline(100, 1.0), kline(99, 2.0)], clock["now"])
    assert [b.open_time // DAY for b in store.completed("ETHUSDT", 102 * DAY)] == [99]


def test_live_classes_read_the_store():
    clock = {"now": 300 * DAY + 3_600_000}
    store = v17.DailyBars(FakeBybit(clock, {d: 100.0 + d for d in range(300)}), now=lambda: clock["now"])
    store.load("BTCUSDT", 299)
    for sid in v17.FAMILIES:
        cls = v17.live_class(sid, store)
        c = SimpleNamespace(symbol="BTCUSDT", close_time=300 * DAY - 1)
        days = cls.days(cls.__new__(cls), None, c)
        assert days[-1].open_time == 299 * DAY and all(d.close_time < 300 * DAY for d in days), sid
    if "V18.2" in v17.family_classes():
        cls = v17.live_class("V18.2", store)
        assert cls.regime(300 * DAY) == "long"                # a rising BTC is above its 50-day mean


def test_field_and_service_switch(tmp_path):
    from app.core.programs import program_retired
    from app.live.v6_service import V6ForwardService
    plan = v17.field_plan()
    assert len(plan) == len(v17.FAMILIES) * len(v17.COINS)
    assert all(s.timeframe == "4h" and s.key.startswith("V17-") for s in plan)
    assert len({s.key for s in plan}) == len(plan)
    assert not program_retired("v17")
    assert V6ForwardService(str(tmp_path / "x.db"), env={}, program="V17").enabled
    assert not V6ForwardService(str(tmp_path / "x.db"), env={"V17_FORWARD_ENABLED": "false"}, program="V17").enabled


def test_freeze_matches():
    man = v17.load_freeze()
    assert v17.verify_freeze(man) == [] and v17.experiment_identity(man)[0].startswith("v17x-")
    assert man["field"]["families"] == list(v17.FAMILIES) and man["starting_balance"] == 1000.0


def test_v17_never_moves_an_existing_champion():
    from app.core.bot_names import assign
    from app.competition import v16_config as v16
    base = [("v6", "V6.6-XRP-1H"), ("v8", "V8.3-ETH-5M"), ("v6", "V6.2-DOGE-1H")] + \
           [("v16", s.control_key) for s in v16.field_plan()]
    before = assign(base)
    after = assign(base + [("v17", s.control_key) for s in v17.field_plan()])
    assert all(after[k] == v for k, v in before.items())
    assert len({after[("v17", s.control_key)] for s in v17.field_plan()}) == len(v17.field_plan())
