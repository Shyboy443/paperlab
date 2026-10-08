"""V16 CONDITIONAL: the study's market condition, the CHOP-only entry gate, the field, the freeze, the service switch
and champion names that never move an existing bot. No network."""
from __future__ import annotations

from types import SimpleNamespace

from app.competition import v16_config as v16

DAY = v16.DAY


def series(fn, n=80, start=20_000):
    return {start + i: fn(i) for i in range(n)}


def test_condition_matches_the_study_rule():
    rising = series(lambda i: 100 + i)
    falling = series(lambda i: 200 - i)
    flat = series(lambda i: 100 + (3 if i % 2 else -3))
    h = (20_000 + 80) * DAY + 3_600_000                     # an hour into the day after the last close
    assert v16.condition_at(rising, h) == "BULL" and v16.condition_at(falling, h) == "BEAR"
    assert v16.condition_at(flat, h) == "CHOP"
    assert v16.condition_at(series(lambda i: 100, n=20), h) is None          # not enough history: never CHOP
    # the decision only uses COMPLETED days: today's close is not read
    assert v16.condition_at({**rising, 20_080: 1.0}, h) == "BULL"


def test_the_gate_drops_entries_outside_chop_and_keeps_exits():
    class Base:
        def on_candle(self, c, ctx):
            return [SimpleNamespace(kind="entry", meta={}), SimpleNamespace(kind="exit", meta={})]
    state = {"now": "BULL"}
    cls = v16.conditional_class(Base, "CHOP", lambda t: state["now"])
    c = SimpleNamespace(close_time=3_600_000 - 1)
    assert [s.kind for s in cls().on_candle(c, None)] == ["exit"]
    state["now"] = "CHOP"
    out = cls().on_candle(c, None)
    assert [s.kind for s in out] == ["entry", "exit"] and out[0].meta["market_condition"] == "CHOP"
    state["now"] = None
    assert [s.kind for s in cls().on_candle(c, None)] == ["exit"]


def test_field_freeze_and_identity():
    assert [s.key for s in v16.field_plan()] == ["V16-BTC-V6.6-CHOP", "V16-UNI-V6.1-CHOP"]
    assert all(s.symbol in ("BTCUSDT", "UNIUSDT") and s.timeframe == "1h" for s in v16.field_plan())
    man = v16.load_freeze()
    assert v16.verify_freeze(man) == [] and v16.experiment_identity(man)[0].startswith("v16x-")
    assert man["starting_balance"] == 1000.0 and set(man["rules"]) == {"BTCUSDT", "UNIUSDT"}


def test_service_runs_by_default_and_has_an_off_switch(tmp_path):
    from app.core.programs import program_retired
    from app.live.v6_service import V6ForwardService
    assert not program_retired("v16")
    assert V6ForwardService(str(tmp_path / "x.db"), env={}, program="V16").enabled
    assert not V6ForwardService(str(tmp_path / "x.db"), env={"V16_FORWARD_ENABLED": "false"}, program="V16").enabled


def test_v16_never_moves_an_existing_champion():
    from app.core.bot_names import assign
    base = [("v6", "V6.6-XRP-1H"), ("v8", "V8.3-ETH-5M"), ("v6", "V6.2-DOGE-1H")]
    before = assign(base)
    after = assign(base + [("v16", s.control_key) for s in v16.field_plan()])
    assert all(after[k] == v for k, v in before.items()) and before[("v6", "V6.6-XRP-1H")] == "Lux"
