"""V14 HTF: the higher-timeframe rule (trends from 1h candles, the bias, what it lets through), the wrappers around
the copied strategies (filtering BEFORE ranking), the field and its originals, the freeze and the wiring. No network."""
from __future__ import annotations

import pytest

from app.competition import v14_config as v14
from app.core.types import Candle
from app.strategies.v11.scan import Cand
from app.strategies.v14 import htf

HOUR = 3_600_000
T0 = 1_790_000_000_000 // HOUR * HOUR


class Ctx:
    def __init__(self, series: dict[str, list[float]], tf_last: dict[str, float] | None = None):
        self.s = series
        self.last = tf_last or {}

    def candles(self, sym, tf):
        if tf == "1h":
            return [Candle(sym, "1h", T0 + i * HOUR, p, p, p, p, 1.0, T0 + (i + 1) * HOUR - 1)
                    for i, p in enumerate(self.s.get(sym, []))]
        p = self.last.get(sym)
        return [Candle(sym, tf, T0, p, p, p, p, 1.0, T0 + 1)] if p else []


UP = [100 + 0.05 * i for i in range(400)]
DOWN = [120 - 0.05 * i for i in range(400)]
FLAT = [100.0] * 400                                                     # no trend at all


class TestRule:
    def test_trends_and_bias(self):
        assert htf.htf_view(Ctx({"A": UP}), "A", UP[-1]) == {"h4": 1, "d1": 1, "bias": 2}
        assert htf.htf_view(Ctx({"A": DOWN}), "A", DOWN[-1]) == {"h4": -1, "d1": -1, "bias": -2}
        assert htf.htf_view(Ctx({"A": FLAT}), "A", 100.0)["bias"] == 0
        assert htf.htf_view(Ctx({"A": UP[:250]}), "A", UP[249]) is None             # < 300 hourly candles: no view
        v = htf.htf_view(Ctx({"A": UP}), "A", UP[-1] - 6.0)                          # a deep dip in a daily uptrend
        assert v["d1"] in (0, 1) and v["h4"] in (0, -1)

    def test_what_the_bias_allows(self):
        assert htf.allows({"bias": 2}, "long") and htf.allows({"bias": 1}, "long") and not htf.allows({"bias": 0}, "long")
        assert htf.allows({"bias": -1}, "short") and not htf.allows({"bias": 1}, "short")
        assert not htf.allows(None, "long") and not htf.allows(None, "short")

    def test_the_ema(self):
        e = htf.ema([1.0, 2.0, 3.0], 3)
        assert e[0] == 1.0 and e[1] == pytest.approx(1.5) and e[2] == pytest.approx(2.25)


class TestWrappers:
    def test_a_single_coin_entry_against_the_higher_timeframes_is_dropped(self):
        from app.core.types import Signal

        class Base:
            timeframes = ("5m",)
            signal_tf = "5m"

            def __init__(self, side):
                self.side = side

            def on_candle(self, c, ctx):
                return [Signal("V8.3", "AUSDT", "entry", self.side, T0, "5m", 100.0, stop=99.0)]
        cls = htf.htf_single(Base, "V14.1", "test")
        assert cls.id == "V14.1" and "1h" in cls.timeframes
        up = Ctx({"AUSDT": UP}, {"AUSDT": UP[-1]})
        assert len(cls("long").on_candle(None, up)) == 1 and cls("short").on_candle(None, up) == []
        down = Ctx({"AUSDT": DOWN}, {"AUSDT": DOWN[-1]})
        assert cls("long").on_candle(None, down) == [] and len(cls("short").on_candle(None, down)) == 1

    def test_a_scanner_filters_before_ranking(self):
        class Base:
            timeframes = ("15m", "1h")
            signal_tf = "15m"

            def scan(self, ctx, t):
                return [Cand("AUSDT", "long", 99.0, 1.0, 0.9, {}, "best, against the trend"),
                        Cand("BUSDT", "long", 99.0, 1.0, 0.5, {}, "weaker, with the trend")]
        cls = htf.htf_scanner(Base, "V14.3", "test")
        ctx = Ctx({"AUSDT": DOWN, "BUSDT": UP}, {"AUSDT": DOWN[-1], "BUSDT": UP[-1]})
        out = cls().scan(ctx, T0)
        assert [x.symbol for x in out] == ["BUSDT"] and out[0].factors["htf"] == 1.0

    def test_the_built_classes_are_the_originals_plus_the_rule(self):
        from app.strategies.v8.arena import load_v8_scalpers
        from app.strategies.v13.snapback import SnapbackV13
        for sid in htf.SOURCES:
            cls = htf.build(sid, v14.UNIVERSE)
            assert cls.id == sid and "1h" in cls.timeframes and cls.htf_rule and cls.signal_tf == htf.SIGNAL_TF[sid]
            base = htf.build(sid, v14.UNIVERSE, htf=False)
            assert base.Params() == cls.Params()                                   # the copied strategy's own settings
        assert htf.build("V14.1").Params() == load_v8_scalpers()["V8.3"].Params()
        assert htf.build("V14.4", v14.UNIVERSE).Params() == SnapbackV13.Params()
        assert htf.build("V14.2", v14.UNIVERSE).exits == "LADDER"                   # the V11 25/50/25 ladder


class TestField:
    def test_nine_bots_each_with_a_running_original(self):
        plan = v14.field_plan()
        assert [s.key for s in plan] == ["V14.1-ETH-5M", "V14.1-SOL-5M", "V14.1-XRP-5M", "V14.1-DOGE-5M",
                                         "V14.1-ARB-5M", "V14.1-ENA-5M", "V14.2-SCAN", "V14.3-SCAN", "V14.4-SNAP"]
        assert v14.original_key(plan[4]) == ("v8", "V8.3-ARB-5M")
        assert [v14.original_key(s) for s in plan[6:]] == [("v11", "V11.1-SCAN"), ("v11", "V11.2-SCAN"),
                                                           ("v13", "V13.1-SNAP")]
        assert plan[0].symbol == "ETHUSDT" and not plan[0].multi and plan[0].timeframe == "5m"
        assert plan[6].symbol == "" and plan[6].multi and plan[6].timeframe == "15m"
        assert v14.should_eliminate({"net_now": -15.0}, 30 * 86_400_000) is None

    def test_the_identity_pins_every_copied_object(self):
        code = v14.code_fingerprints()
        for k in ("app.strategies.v14.htf", "app.live.v14_worker", "app.live.v8_engine", "app.live.v13_engine",
                  "app.strategies.v11.ladder", "app.strategies.v13.snapback", "app.strategies.v8.arena.VwapSnapV8",
                  "app.strategies.v8.arena.SnapParams", "app.strategies.v11.scan.RsPullbackV11"):
            assert k in code
        assert "app.strategies.v8.arena" not in code                          # a V8.1 edit does not restart V14

    def test_the_running_v14_is_the_frozen_manifest(self):
        man = v14.load_freeze()
        assert man and v14.verify_freeze(man) == [] and set(man["study_verdicts"]) == set(htf.SOURCES)
        assert v14.experiment_identity(man)[0].startswith("v14x-")


class TestWiring:
    def test_service_names_roster_route_and_settings(self, tmp_path):
        from app.core import bot_names, roster_view
        from app.core.api_public import router
        from app.live.telegram import settings_text
        from app.live.v6_service import V6ForwardService
        svc = V6ForwardService(str(tmp_path / "v14.db"), env={"V14_FORWARD_ENABLED": "true"}, program="V14")
        assert svc.enabled and svc.cfg["jev"] is False
        ctl = [("v8", "V8.3-ARB-5M"), ("v11", "V11.2-SCAN"), ("v14", "V14.1-ARB-5M"), ("v14", "V14.3-SCAN")]
        names = bot_names.assign(ctl)
        assert names[("v14", "V14.1-ARB-5M")] == names[("v8", "V8.3-ARB-5M")] + " HTF"
        assert names[("v14", "V14.3-SCAN")] == names[("v11", "V11.2-SCAN")] + " HTF"
        assert "v14" in roster_view.PINNED and roster_view.PROGRAMS["v14"] == "V14 HTF"
        methods = {m for r in router.routes if getattr(r, "path", "").endswith("/v14") for m in r.methods}
        assert methods and methods <= {"GET", "HEAD"}
        assert "V14 HTF" in settings_text() and "4-hour and daily trend" in settings_text()
