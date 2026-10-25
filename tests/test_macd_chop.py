"""The choppy-market filter's forward paper test (docs/MACD_CHOP_TEST.md): the CHOP measure, the filter going flat at a
choppy cross, and the service's fixed start, both books and restart safety (no network)."""
import math

from app.divergence.store import Store
from app.macdtrio import chop_test, sim

H4 = sim.H4
T0 = 1_790_006_400_000 // H4 * H4


def wave(n, t0=T0, amp=30.0, noise=0.0):
    out = []
    for i in range(n):
        c = 100 + amp * math.sin(i / 12) + noise * ((-1) ** i)
        o = out[-1]["c"] if out else c
        out.append({"t": t0 + i * H4, "o": o, "h": max(o, c) * 1.002, "l": min(o, c) * 0.998, "c": c, "v": 1.0})
    return out


def test_choppiness_is_low_in_a_trend_and_high_in_a_zigzag():
    up = [{"t": i, "o": 100 + i, "h": 101 + i, "l": 100 + i, "c": 101 + i} for i in range(30)]
    zz = [{"t": i, "o": 100, "h": 101, "l": 99, "c": 100 + (1 if i % 2 else -1)} for i in range(30)]
    assert sim.choppiness(up)[-1] < 10 and sim.choppiness(zz)[-1] > 90 and sim.choppiness(up)[5] is None


def test_the_filter_goes_flat_at_a_choppy_cross_and_otherwise_matches_control():
    bars = wave(500)
    ctrl = sim.simulate(bars, (), bars[100]["t"], size=0.5)
    never = sim.simulate(bars, (), bars[100]["t"], size=0.5, chop_len=14, chop_max=101)      # never choppy = control
    always = sim.simulate(bars, (), bars[100]["t"], size=0.5, chop_len=14, chop_max=-1)      # always choppy = flat
    assert never["metrics"] == ctrl["metrics"] and ctrl["metrics"]["trades"] > 0
    assert always["metrics"]["trades"] == 0 and always["metrics"]["net_return_pct"] == 0


class FakeClient:
    def funding(self, symbol, start, end):
        return []


class FakeMarket:
    def __init__(self, path, bars):
        self.store, self.bars, self.client = Store(path), bars, FakeClient()
        self.calls = 0

    def klines(self, symbol, start, end):
        self.calls += 1
        return [b for b in self.bars if start <= b["t"] and b["t"] + H4 <= end]


def test_the_service_starts_at_the_next_4h_boundary_runs_both_books_and_survives_a_restart(tmp_path):
    bars = wave(1000, t0=T0 - 800 * H4)
    clock = {"now": (T0 + 1000) / 1000}
    market = FakeMarket(tmp_path / "t.db", bars)
    svc = chop_test.ChopPaperTest(market, env={"MACD_CHOP_TEST_ENABLED": "true"}, clock=lambda: clock["now"])
    assert svc.start_ms == T0 + H4                                          # the first 4h boundary after creation
    clock["now"] = (T0 + 150 * H4 + 60_000) / 1000
    out = svc.cycle()
    assert set(out["coins"]) == {"STXUSDT", "DASHUSDT", "XLMUSDT"} and set(out["combined"]) == {"control", "filter"}
    assert out["coins"]["STXUSDT"]["control"]["metrics"]["trades"] > 0 and out["crosses"] > 0 and out["paper_only"]
    again = chop_test.ChopPaperTest(market, env={"MACD_CHOP_TEST_ENABLED": "true"}, clock=lambda: clock["now"])
    assert again.start_ms == svc.start_ms and again.cycle()["combined"] == out["combined"]          # restart: same books
    assert svc.summary()["evaluate_on"] == "2026-12-31"


def test_switched_off_it_does_nothing(tmp_path):
    svc = chop_test.ChopPaperTest(FakeMarket(tmp_path / "t.db", []), env={"MACD_CHOP_TEST_ENABLED": "false"})
    assert svc.start_ms is None and svc.health["status"] == "DISABLED"
