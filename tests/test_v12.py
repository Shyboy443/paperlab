"""V12 BIZZY: beebots' Bizzy Bee ported -- the freeze, the field, the Larry Williams day breakout through the engine (one
trade a day, full size 2x, stop at the day's open, out at the UTC close, coin preference), the Jev twin's GO / WAIT, and
the program's wiring. No network."""
from __future__ import annotations

import pytest

from app.competition import v12_config as v12
from app.core.types import Candle, MarketRules, Signal
from app.live import v6_gates as gates
from app.strategies.v12.bizzy import ANCHOR, PREFERENCE, TRADE_COINS, BizzyV12, bizzy_engine
from tests.conftest import T0

MINUTE = 60_000
HOUR = 60 * MINUTE
DAY = 24 * HOUR
SYMS = list(PREFERENCE)                        # BTC (the anchor) is delivered last each minute below
D0 = (T0 // DAY + 1) * DAY                     # a UTC midnight: day A = D0, day B = D0 + 1 day, day C = D0 + 2 days


def _price(sym: str, t: int) -> tuple[float, float, float, float]:
    """(open, high, low, close) of `sym`'s 1m bar starting at t.

    day A: every coin flat at 100, one hour touching 102 / 98 (yesterday's range = 4 for day B)
    day B: ETH and SOL close at 102.6 at minute 300 (through 100 + 0.5 x 4 = 102), hold 103, then 104 from minute 600
    day C: ETH opens at 103, breaks out at minute 100 (106), falls through the day's open at minute 200 (stop), then
           breaks out again at minute 300 -- which must NOT be traded (one trade a day)"""
    day, m = (t - D0) // DAY, ((t - D0) % DAY) // MINUTE
    if day == 0:
        if m == 600:
            return 100.0, 102.0, 98.0, 100.0
        return 100.0, 100.05, 99.95, 100.0
    if day == 1:
        if sym in ("ETHUSDT", "SOLUSDT") and m >= 300:
            p = 102.6 if m == 300 else 103.0 if m < 600 else 104.0
            return p, p + 0.05, p - 0.05, p
        return 100.0, 100.05, 99.95, 100.0
    if sym == "ETHUSDT":
        if m < 100:
            return 103.0, 103.05, 102.95, 103.0
        if m < 200:
            return 106.0, 106.05, 105.95, 106.0
        if m == 200:                                       # falls through the open (103) INSIDE this bar
            return 106.0, 106.05, 100.95, 101.0
        if m < 300:
            return 101.0, 101.05, 100.95, 101.0
        return 108.0, 108.05, 107.95, 108.0
    return 103.0 if sym == "SOLUSDT" else 100.0, 103.05 if sym == "SOLUSDT" else 100.05, \
        102.95 if sym == "SOLUSDT" else 99.95, 103.0 if sym == "SOLUSDT" else 100.0


def _tape(days: int = 3):
    order = sorted(s for s in SYMS if s != ANCHOR) + [ANCHOR]
    for t in range(D0, D0 + days * DAY, MINUTE):
        for s in order:
            o, h, lo, c = _price(s, t)
            yield Candle(s, "1m", t, o, h, lo, c, 10.0, t + MINUTE - 1)


def _run(days: int = 3, since: int = D0 + DAY):
    rules = {s: MarketRules(s, 0.0001, 0.001, 0.001, 5.0) for s in SYMS}
    eng = bizzy_engine()(v12.settings_v12(), SYMS, rules=rules, seed=7, execution=v12.EXECUTION_V12, sizing=None,
                         leverage_policy="needed", max_risk_pct=None)
    res = eng.run(BizzyV12.for_universe(SYMS), _tape(days), since_ms=since, leverage=v12.LEVERAGE_CEILING,
                  signal_tf="1m", only_symbol=None)
    return eng, res


class TestFreezeAndField:
    def test_the_running_v12_is_the_frozen_manifest(self):
        man = v12.load_freeze()
        assert man is not None and v12.verify_freeze(man) == []
        assert man["field"] == ["V12.1-DAY", "V12.1-DAY+JEV"]
        assert man["profile"]["leverage_ceiling"] == 2 and man["profile"]["starting_balance"] == 20.0
        assert set(man["rules"]) == set(PREFERENCE) and "BTCUSDT" not in TRADE_COINS

    def test_the_live_runner_decides_on_bizzys_minute(self):
        # V6Bot runs the engine with signal_tf = spec.timeframe: anything but 1m and Bizzy never sees a decision bar
        assert all(s.timeframe == BizzyV12.signal_tf == "1m" for s in v12.field_plan())

    def test_identity_and_never_eliminated(self):
        eid, ident = v12.experiment_identity(v12.load_freeze(), "m")
        assert eid.startswith("v12x-") and ident["protocol"] == "V12_BIZZY_PAPER_V1"
        assert v12.should_eliminate({"equity": 1.0, "start_equity": 20.0}, 30 * DAY) is None
        assert v12.bot_status({"trades": 3}, DAY)[0] == "ACTIVE"


class TestBreakout:
    def test_one_trade_a_day_full_size_stop_at_the_open_out_at_the_close(self):
        eng, res = _run()
        tr = sorted(res.trades, key=lambda t: t.entry_ts)
        assert len(tr) == 2 and {t.entry_ts // DAY for t in tr} == {D0 // DAY + 1, D0 // DAY + 2}
        b, c = tr
        # day B: ETH (preferred over SOL in the same minute), decided on minute 300's close, filled at the first open
        # 60 s after it (minute 302: the decision window), ridden to 23:59
        assert b.symbol == "ETHUSDT" and b.entry_ts == D0 + DAY + 302 * MINUTE
        assert b.exit_kind == "time" and D0 + 2 * DAY - 3 * MINUTE <= b.exit_ts < D0 + 2 * DAY and b.net > 0
        # The signal proposed 2x at 102.6. The delayed fill is near 103, farther
        # from the stop at 100: keep the approved dollar risk by shrinking size.
        signal_risk = (2 * 20.0 / 102.6) * (102.6 - 100.0)
        assert b.qty * (b.entry_price - 100.0) <= signal_risk + 1e-9
        assert 0 < b.qty * b.entry_price < 2 * 20.0
        # day C: the breakout at minute 100 fails back through the day's open (103) inside a bar -> stopped AT the open
        # (a gap through it would fill at the bar's open instead)
        assert c.symbol == "ETHUSDT" and c.exit_kind == "stop" and c.exit_price == pytest.approx(103.0, rel=1e-3)
        assert c.net < 0

    def test_no_levels_without_a_full_previous_day(self):
        _, res = _run(days=1, since=D0)
        assert res.trades == []

    def test_the_day_counts_only_once_a_position_is_open(self):
        s = BizzyV12.for_universe(SYMS)()
        s.opened(D0 + DAY + 5 * MINUTE)
        assert s.traded_day == (D0 + DAY) // DAY


class TestJevTwin:
    def _gate(self, decide, now):
        from app.ai.jev.v12 import JevStateBuilderV12
        from app.live.v12_worker import JevGateV12
        em = []
        g = JevGateV12(decide=decide, model="m", mid=lambda s: 100.0, spread=lambda s: (0.5, "observed"),
                       builder=JevStateBuilderV12(), window_ms=v12.DECISION_WINDOW_MS,
                       margin_ms=v12.JEV_DEADLINE_MARGIN_MS, eliminated_at={}, late_after_ms=v12.LATE_AFTER_MS,
                       experiment_id="v12x-j", session_id="s",
                       bot={"key": "V12.1-DAY+JEV", "symbol": "", "pair_id": "v12:V12.1-DAY", "horizon": "DAY",
                            "strategy_id": "V12.1", "timeframe": "1d"},
                       emit=lambda k, d: em.append((k, d)), board=gates.DecisionBoard(lambda s, t: (True, None)),
                       coverage=gates.Coverage([(T0, T0 + 3 * DAY)]), recorded=None, live_from_ms=T0,
                       wall=lambda: now / 1000)
        return g, em

    def _ctx(self, ts):
        from types import SimpleNamespace

        from app.backtest.replay import ReplayEngine
        rules = {s: MarketRules(s, 0.0001, 0.001, 0.001, 5.0) for s in ("ETHUSDT", ANCHOR)}
        eng = ReplayEngine(v12.settings_v12(), ["ETHUSDT", ANCHOR], rules=rules, seed=7)
        for i in range(80):
            t = ts + 1 - (80 - i) * 5 * MINUTE
            eng.ctx.push(Candle("ETHUSDT", "5m", t, 100.0, 100.4, 99.6, 100.1, 10.0, t + 5 * MINUTE - 1))
        return {"ts": ts, "sizing": {}, "health": {"peak": 20.0}, "equity": 20.0, "start_equity": 20.0,
                "taker_fee": 0.00055, "half_spread_bps": 0.5, "trades": [], "ctx": eng.ctx,
                "decision": SimpleNamespace(risk_usd=0.6, leverage=2.0, notional=40.0)}

    def _sig(self, ts):
        s = Signal("V12.1", "ETHUSDT", "entry", "long", ts, "1m", 102.6, stop=100.0, take_profits=[], reason="t")
        s.meta.update(stop_pct=0.025, signal_tf="1m", setup="DAY_BREAKOUT", day_open=100.0, trigger=102.0,
                      through_trigger_pct=0.58, day_move_pct=2.6, prev_range_pct=4.0, margin_pct=1.0)
        return s

    def _answer(self, choice, p_contradict):
        from app.ai.jev.models import JevDecision, JevOutcome
        rest = 1.0 - p_contradict
        probs = {"SKIP": p_contradict, "TAKE": rest * 0.7, "ATTACK": rest * 0.3}
        dec = JevDecision(take_probability=rest, risk_state=choice, risk_probabilities=probs, setup_quality=0.0,
                          quality_score=0.0, quality_probabilities={}, regime="", regime_probabilities={}, model_resolved="m")
        return lambda state: JevOutcome(True, decision=dec, latency_ms=300)

    def test_support_is_go_at_full_size(self):
        ts = T0 + 10 * HOUR - 1
        g, em = self._gate(self._answer("TAKE", 0.2), ts + 1 + 3000)
        s = self._sig(ts)
        v = g(s, self._ctx(ts))
        assert v.multiplier == 1.0 and s.take_profits == [] and s.meta["margin_pct"] == 1.0
        row = em[0][1]
        assert row["final_action"] == "TAKE" and "BREAKOUT" in row["reason"] and row["prompt_version"] == "JEV_PROMPT_V12_BIZZY"

    def test_contradict_or_no_answer_is_wait(self):
        from app.ai.jev.models import JevOutcome
        ts = T0 + 10 * HOUR - 1
        g, em = self._gate(self._answer("SKIP", 0.7), ts + 1 + 3000)
        assert g(self._sig(ts), self._ctx(ts)).multiplier == 0.0 and em[-1][1]["reason"].startswith("WAIT")
        g, em = self._gate(lambda state: JevOutcome(False, error_code="TIMEOUT"), ts + 1 + 3000)
        assert g(self._sig(ts), self._ctx(ts)).multiplier == 0.0 and "WAIT" in em[-1][1]["reason"]

    def test_the_state_holds_the_breakout_levels_and_never_the_future(self):
        from app.ai.jev.state import LookAheadError
        from app.ai.jev.v12 import JevStateBuilderV12
        ts = T0 + 10 * HOUR
        snap = JevStateBuilderV12().build(t=ts, bot={"strategy_id": "V12.1"}, sig=self._sig(ts - 1), series={"5m": []},
                                          execution={"taker_fee": 0.00055, "half_spread_bps": 0.5}, sizing={},
                                          health={}, position={})
        assert snap.state["setup"]["trigger"] == 102.0 and snap.state["setup"]["day_open"] == 100.0
        late = [Candle("ETHUSDT", "5m", ts, 1, 1, 1, 1, 1, ts + 5 * MINUTE - 1)]
        with pytest.raises(LookAheadError):
            JevStateBuilderV12().build(t=ts, bot={}, sig=self._sig(ts - 1), series={"5m": late}, execution={},
                                       sizing={}, health={}, position={})


class TestWiring:
    def test_service_names_and_roster(self, tmp_path):
        from app.core import bot_names, roster_view
        from app.live.v6_service import V6ForwardService
        svc = V6ForwardService(str(tmp_path / "v12.db"), env={"V12_FORWARD_ENABLED": "true"}, program="V12")
        assert svc.retired and not svc.enabled and svc.cfg["jev"] is True  # retired 2026-10-08 (app/core/programs.py): never starts
        names = bot_names.assign([("v12", "V12.1-DAY"), ("v11", "V11.1-SCAN")])
        bizzy = names[("v12", "V12.1-DAY")]                     # a champion, like every bot
        assert bizzy in bot_names.NAMES
        d = bot_names.describe("v12", {"key": "V12.1-DAY+JEV", "strategy_id": "V12.1", "coin": "DAY"}, names)
        assert d["name"] == bizzy + " AI" and d["where"] == "ETH · SOL · HYPE"
        assert "v12" in roster_view.PINNED and roster_view.PROGRAMS["v12"] == "V12 Bizzy"

    def test_the_v12_route_is_get_only(self):
        from app.core.api_public import router
        methods = {m for r in router.routes if getattr(r, "path", "").endswith("/v12") for m in r.methods}
        assert methods and methods <= {"GET", "HEAD"}
