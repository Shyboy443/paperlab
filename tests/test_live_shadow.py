"""Live shadow: live bars in order, the live Jev gate's clock and failure handling, the runner's
event trail, forward analytics and the verdict rules, and the public payload's allow-list.

No network: the market is fed by hand and Jev is a stub."""
from __future__ import annotations

import queue
import time

import pytest

from app.ai.jev.models import JevDecision, JevOutcome
from app.core.storage import Storage
from app.core.types import Candle
from app.live import analytics as an
from app.live.jev_live import LiveJevGate, latency_slippage_bps
from app.live.market import LiveFunding, LiveMarket, fetch_klines
from app.live.runner import ShadowBot, with_sink
from tests.conftest import T0, settings_factory
from tests.test_competition import SYMBOL, OneShot, big_rules, rising


def bar(i: int, px: float = 100.0, symbol: str = "SOLUSDT") -> Candle:
    t = T0 + i * 60_000
    return Candle(symbol, "1m", t, px, px * 1.001, px * 0.999, px, 10.0, t + 59_999, True, 1000.0, 5, "live")


# ---- market -------------------------------------------------------------------------------------

class TestMarket:
    def test_bars_are_delivered_once_and_in_order(self):
        m = LiveMarket(["SOLUSDT"], 0)
        q = m.subscribe("SOLUSDT")
        for i in (1, 2, 2, 1, 3):
            m._deliver(bar(i))
        got = [q.get_nowait().open_time for _ in range(q.qsize())]
        assert got == [bar(1).open_time, bar(2).open_time, bar(3).open_time]

    async def test_a_hole_is_repaired_from_rest_before_the_next_bar(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return [[bar(i).open_time, "100", "100.1", "99.9", "100", "10", bar(i).close_time, "1000", 5]
                    for i in (2, 3)]
        m = LiveMarket(["SOLUSDT"], 0, fetch=fetch, clock=lambda: (T0 + 10 * 60_000) / 1000)
        q = m.subscribe("SOLUSDT")
        m._deliver(bar(1))
        await m._deliver_live(bar(4))
        got = [q.get_nowait().open_time for _ in range(q.qsize())]
        assert got == [bar(i).open_time for i in (1, 2, 3, 4)]
        assert m.stats["gaps_repaired"] == 1 and m.stats["gap_bars"] == 2 and "klines" in calls[0]

    def test_rest_backfill_never_hands_on_the_forming_bar(self):
        rows = [[bar(i).open_time, "1", "1", "1", "1", "1", bar(i).close_time, "1", 1] for i in (1, 2, 3)]
        out = fetch_klines("SOLUSDT", bar(1).open_time, bar(3).open_time, now_ms=bar(3).close_time - 5,
                           fetch=lambda url: rows)
        assert [c.open_time for c in out] == [bar(1).open_time, bar(2).open_time]
        assert all(c.source == "backfill" for c in out)

    def test_closed_klines_only(self):
        m = LiveMarket(["SOLUSDT"], 0)

        class Q:
            items = []

            def put_nowait(self, x):
                self.items.append(x)
        inbox = Q()
        k = {"t": T0, "T": T0 + 59_999, "o": "1", "h": "1", "l": "1", "c": "1", "v": "1", "x": False}
        m._on_kline({"e": "kline", "s": "SOLUSDT", "k": k}, inbox)
        m._on_kline({"e": "kline", "s": "SOLUSDT", "k": {**k, "x": True}}, inbox)
        assert len(inbox.items) == 1 and inbox.items[0].source == "live"

    def test_book_gives_mid_and_half_spread(self):
        m = LiveMarket(["SOLUSDT"], 0)
        m._on_book({"e": "bookTicker", "s": "SOLUSDT", "b": "99.99", "a": "100.01", "T": 1})
        assert m.mid("SOLUSDT") == pytest.approx(100.0)
        assert m.half_spread_bps("SOLUSDT") == pytest.approx(1.0, rel=1e-3)

    def test_funding_is_recorded_against_its_settlement_instant(self):
        f = LiveFunding()
        f.upsert("SOLUSDT", 1000, 0.0001)
        f.upsert("SOLUSDT", 1000, 0.0002)          # the estimate moves until settlement
        f.upsert("SOLUSDT", 2000, 0.0003)
        f.upsert("SOLUSDT", 1500, 0.9)             # never out of order
        assert f.by_symbol["SOLUSDT"] == [(1000, 0.0002), (2000, 0.0003)]
        assert f.events("SOLUSDT", 999, 1000) == [(1000, 0.0002)]


# ---- the live Jev gate ------------------------------------------------------------------------------

class _D:
    risk_usd, notional, leverage = 0.2, 10.0, 2


def _ctx(ts: int, candles) -> dict:
    return {"ts": ts, "candles": candles, "funding": [], "health": {"r": [], "peak": 20.0},
            "equity": 20.0, "start_equity": 20.0, "available": 20.0, "open_positions": 0,
            "leverage_ceiling": 20, "decision": _D(), "taker_fee": 0.0005, "half_spread_bps": 0.5,
            "expected_slippage_bps": 0.5}


def _gate(decide, mids, emitted):
    it = iter(mids)
    clock = iter([1000.0, 1000.05, 1000.40, 1000.41])
    bot = {"key": "S26-SOLUSDT-30m@20x-v2+JEV", "strategy_id": "S26", "strategy_name": "Trend Rider v2",
           "params_version": "v2", "symbol": "SOLUSDT", "timeframe": "30m", "control_version": "v"}
    return LiveJevGate(decide, bot, "pair:S26-SOLUSDT-30m@20x-v2", "sess", "typesafe/jev-1.13",
                       lambda k, d: emitted.append((k, d)), lambda s: next(it), 400,
                       wall=lambda: next(clock))


class TestLiveJevGate:
    def candles(self):
        return [Candle("SOLUSDT", "30m", T0 + i * 1_800_000, 100, 101, 99, 100 + i * 0.1, 5,
                       T0 + (i + 1) * 1_800_000 - 1) for i in range(60)]

    def sig(self, side="long"):
        from app.core.types import Signal, TakeProfit
        return Signal("S26", "SOLUSDT", "entry", side, T0, "30m", 100.0, stop=99.0,
                      take_profits=[TakeProfit(103.0, 1.0)], reason="t")

    def test_a_decision_carries_its_whole_clock_and_the_latency_slippage(self):
        emitted = []
        dec = JevDecision(0.30, "NORMAL", {}, 0.4, 1.6, {}, "TREND", {}, model_resolved="typesafe/jev-1.13-x")
        g = _gate(lambda s: JevOutcome(True, dec, latency_ms=350), [100.0, 100.05], emitted)
        cs = self.candles()
        v = g(self.sig(), _ctx(cs[-1].close_time, cs))
        assert v.multiplier == 0.0 and not v.error                  # 0.30 < 0.55 -> SKIP
        kind, row = emitted[0]
        assert kind == "decision" and row["final_action"] == "SKIP"
        assert row["candidate_wall_ms"] == 1_000_000 and row["request_start_ms"] == 1_000_050
        assert row["response_ms"] == 1_000_400 and row["latency_ms"] == 350
        assert row["submit_ms"] == row["decision_ms"] + 400
        assert row["latency_slippage_bps"] == pytest.approx(5.0)    # long, price ran up 5 bps
        assert row["prompt_version"] == "JEV_PROMPT_V1" and row["policy_version"] == "JEV_POLICY_V1"

    def test_a_timeout_is_skip_never_control_behaviour(self):
        emitted = []
        g = _gate(lambda s: JevOutcome(False, error_code="TIMEOUT"), [100.0, 100.0], emitted)
        cs = self.candles()
        v = g(self.sig(), _ctx(cs[-1].close_time, cs))
        assert v.multiplier == 0.0 and v.error
        assert emitted[0][1]["timed_out"] == 1 and emitted[0][1]["final_action"] == "SKIP"

    def test_a_crashing_client_is_skip(self):
        emitted = []

        def boom(state):
            raise RuntimeError("socket exploded")
        g = _gate(boom, [100.0, 100.0], emitted)
        cs = self.candles()
        assert g(self.sig(), _ctx(cs[-1].close_time, cs)).multiplier == 0.0
        assert emitted[0][1]["error_code"] == "CLIENT_ERROR"

    def test_latency_slippage_is_signed_against_the_side(self):
        assert latency_slippage_bps("long", 100.0, 100.1) == pytest.approx(10.0)
        assert latency_slippage_bps("short", 100.0, 100.1) == pytest.approx(-10.0)
        assert latency_slippage_bps("long", None, 100.0) is None


# ---- the runner -------------------------------------------------------------------------------------

class _Spec:
    strategy_id = "T01"
    symbol = SYMBOL
    coin = "BTC"
    timeframe = "1m"
    max_leverage = 10
    params_version = "v1"
    key = "T01-BTCUSDT-1m@10x-v1"


def _run_bot(gate=None, role="CONTROL"):
    from app.backtest.replay import ReplayEngine
    eng = ReplayEngine(settings_factory(balance=20), [SYMBOL], rules=big_rules(), seed=1, gate=gate)
    q: queue.Queue = queue.Queue()
    events: list = []
    bars = rising()
    bot = ShadowBot(key=_Spec.key + ("+JEV" if role == "JEV" else ""), role=role, pair_id="pair:x",
                    spec=_Spec, cls=OneShot, engine=eng, bars=q, since_ms=bars[3].close_time,
                    emit=lambda k, d: events.append((k, d)))
    for b in bars:
        q.put(b)
    q.put(None)
    bot.start()
    bot.thread.join(10)
    return bot, events


class TestRunner:
    def test_warmup_bars_never_trade_and_the_trail_is_complete(self):
        bot, events = _run_bot()
        kinds = [k for k, _ in events]
        assert "candidate" in kinds and "open" in kinds and "closed" in kinds
        cand = next(d for k, d in events if k == "candidate")
        assert cand["outcome"] == "ORDERED" and cand["signal_ts"] >= bot.since_ms
        assert bot.warm_bars == 3 and bot.live_bars > 0 and bot.status["live"] is True
        assert bot.error is None

    def test_a_skipping_twin_records_the_counterfactual(self):
        from app.ai.jev.gate import GateVerdict
        bot, events = _run_bot(gate=lambda s, g: GateVerdict(0.0, {"decision_id": "d1"}), role="JEV")
        closed = [d for k, d in events if k == "closed"]
        assert closed and all(d["counterfactual"] for d in closed)
        assert closed[0]["decision_id"] == "d1"
        cand = next(d for k, d in events if k == "candidate")
        assert cand["outcome"] == "JEV_SKIP"
        assert any(k == "evaluating" for k, _ in events)

    def test_the_sink_does_not_change_what_the_strategy_emits(self):
        seen = []
        Live = with_sink(OneShot, lambda sigs, c: seen.extend(sigs))
        assert Live.__name__ == "OneShot" and Live.id == OneShot.id


# ---- analytics and the verdict ------------------------------------------------------------------

def _ds(n, auc_good=True):
    out = []
    for i in range(n):
        win = (i % 2 == 0)
        p = (0.8 if win else 0.2) if auc_good else 0.5
        out.append({"pair_id": f"pair:{i % 12}", "take_probability": p, "outcome_r": 1.0 if win else -1.0,
                    "final_action": "TAKE" if p >= 0.55 else "SKIP", "latency_ms": 300 + i,
                    "latency_slippage_bps": 1.0, "notional": 10.0})
    return out


def _pairs(n, ctl, jev, taken=30):
    return [{"control": {"net": ctl, "expectancy_r": 0.1, "trades": 40},
             "jev": {"net": jev, "trades": taken // n}} for _ in range(n)]


class TestVerdict:
    def test_too_few_controls_is_no_verdict(self):
        v = an.verdict(5, _pairs(5, 1, 2), _ds(200))
        assert v["status"] == "INSUFFICIENT JEV-ELIGIBLE CONTROLS" and v["verdict"] == "NO VERDICT"

    def test_too_few_resolved_decisions_is_collecting(self):
        assert an.verdict(12, _pairs(12, 1, 2), _ds(40))["status"] == "COLLECTING"

    def test_coin_flip_discrimination_retires_v1(self):
        v = an.verdict(12, _pairs(12, 1, 2), _ds(200, auc_good=False))
        assert v["verdict"] == "NO EDGE" and v["status"] == "RETIRE JEV V1"

    def test_edge_needs_auc_controls_and_the_always_skip_baseline(self):
        good = an.verdict(12, _pairs(12, 0.1, 0.5, taken=240), _ds(200))
        assert good["verdict"] == "EDGE (PROVISIONAL)"
        losing = an.verdict(12, _pairs(12, -1.0, -0.5, taken=240), _ds(200))
        assert losing["verdict"] == "NO EDGE" and "always-skip" in losing["why"]

    def test_decision_stats_percentiles_and_rates(self):
        ds = _ds(100) + [{"error_code": "TIMEOUT", "timed_out": 1, "final_action": "SKIP"}]
        s = an.decision_stats(ds)
        assert s["decisions"] == 101 and s["timeouts"] == 1 and s["failure_rate"] == pytest.approx(1 / 101)
        assert s["latency_p50_ms"] == pytest.approx(349.5) and s["latency_p99_ms"] >= s["latency_p95_ms"]

    def test_calibration_uses_quintile_buckets(self):
        rows = an.calibration(_ds(100))
        assert [r["bucket"] for r in rows] == ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]
        assert rows[4]["win_rate"] == 1.0 and rows[1]["win_rate"] == 0.0

    def test_historical_v1_is_kept(self):
        assert an.HISTORICAL_V1["verdict"] == "NO EDGE" and an.HISTORICAL_V1["auc"] == 0.512
        assert an.HISTORICAL_V1["skip_rate"] == 0.997


# ---- forward experiment continuity -------------------------------------------------------------------

def _session(tape, since_ms, live_from_ms=None, recorded=None, stop_after=False):
    """One session of one bot over `tape`. `stop_after` stops it like a deploy does once the tape
    is consumed (instead of the tape simply ending)."""
    from app.backtest.replay import ReplayEngine
    eng = ReplayEngine(settings_factory(balance=20), [SYMBOL], rules=big_rules(), seed=1)
    q: queue.Queue = queue.Queue()
    events: list = []
    bot = ShadowBot(key=_Spec.key, role="CONTROL", pair_id=None, spec=_Spec, cls=OneShot, engine=eng, bars=q,
                    since_ms=since_ms, emit=lambda k, d: events.append((k, d)), live_from_ms=live_from_ms,
                    recorded=recorded)
    for b in tape:
        q.put(b)
    if not stop_after:
        q.put(None)
    bot.start()
    if stop_after:
        deadline = time.time() + 10
        while (bot.last_bar_close < tape[-1].close_time or not q.empty()) and time.time() < deadline:
            time.sleep(0.005)
        bot.stop()
    bot.thread.join(10)
    return bot, [(k, d) for k, d in events if k in ("candidate", "open", "closed")]


def _recorded(events):
    from app.live import continuity as cont
    rec = cont.Recorded()
    for k, d in events:
        if k == "candidate":
            rec.seen.add(cont.cand_ident(d["bot_key"], d["side"], d["signal_ts"]))
        elif k == "open":
            rec.seen.add(cont.open_ident(d["bot_key"], d["side"], d["ts"]))
        elif k == "closed":
            i = cont.trade_ident(d["bot_key"], d["side"], d["entry_ts"], d["counterfactual"])
            rec.seen.add(i)
            rec.nets[i] = d["net"]
    return rec


class TestContinuity:
    """A deploy must neither drop an open position nor reset a book: a resumed session re-derives
    the book from the experiment's forward start and records only what no session recorded."""

    def _full(self):
        tape = rising()
        t0 = tape[3].close_time
        _, ev = _session(tape, t0)
        closed = [d for k, d in ev if k == "closed"]
        assert len(closed) == 1
        want = closed[0]
        entry_i = next(i for i, c in enumerate(tape) if c.close_time >= want["entry_ts"])
        exit_i = next(i for i, c in enumerate(tape) if c.close_time >= want["exit_ts"])
        assert exit_i - entry_i >= 5
        return tape, t0, want, entry_i, exit_i

    def test_a_stop_never_invents_an_exit(self):
        tape, t0, want, entry_i, exit_i = self._full()
        bot, ev = _session(tape[:entry_i + 2], t0, stop_after=True)
        assert [k for k, _ in ev] == ["candidate", "open"]          # open when the deploy hit: carried
        assert bot.error is None

    def test_a_restart_carries_the_open_position_and_records_each_event_once(self):
        tape, t0, want, entry_i, exit_i = self._full()
        _, ev_a = _session(tape[:entry_i + 2], t0, stop_after=True)
        live_i = entry_i + 4                                          # the server was down in between
        b, ev_b = _session(tape, t0, live_from_ms=tape[live_i].close_time, recorded=_recorded(ev_a))
        assert [k for k, _ in ev_b] == ["closed"]                     # candidate and open were recorded
        got = ev_b[0][1]
        assert (got["entry_ts"], got["exit_ts"], got["exit_kind"]) == (want["entry_ts"], want["exit_ts"], want["exit_kind"])
        assert got["net"] == pytest.approx(want["net"]) and "rederived" not in got
        assert b.track.stats == {"rederived": 0, "matched": 2, "diverged": 0, "unreproduced": 0}
        assert b.warm_bars == live_i and b.live_bars == len(tape) - live_i and b.status["live"] is True
        assert b.status["continuity"]["forward_from_ms"] == t0

    def test_an_exit_no_session_saw_is_rederived_once_and_flagged(self):
        tape, t0, want, entry_i, exit_i = self._full()
        _, ev_a = _session(tape[:entry_i + 2], t0, stop_after=True)
        b, ev_b = _session(tape, t0, live_from_ms=tape[exit_i + 2].close_time, recorded=_recorded(ev_a))
        assert [k for k, _ in ev_b] == ["closed"] and ev_b[0][1]["rederived"] is True
        assert ev_b[0][1]["net"] == pytest.approx(want["net"])
        assert b.track.stats["rederived"] == 1 and b.track.stats["matched"] == 2

    def test_what_the_replay_cannot_reproduce_is_counted_never_hidden(self):
        from app.live import continuity as cont
        tape, t0, want, entry_i, exit_i = self._full()
        _, ev_a = _session(tape, t0)
        rec = _recorded(ev_a)
        rec.seen.add(cont.cand_ident(_Spec.key, "short", tape[2].close_time))      # never happened
        ti = cont.trade_ident(_Spec.key, "long", want["entry_ts"], False)
        rec.nets[ti] = want["net"] + 1.0                                            # recorded differently
        b, ev_b = _session(tape, t0, live_from_ms=tape[-1].close_time, recorded=rec)
        assert ev_b == []                                  # everything was recorded: nothing twice
        assert b.track.stats == {"rederived": 0, "matched": 3, "diverged": 1, "unreproduced": 1}

    def test_the_past_is_never_asked_again(self):
        calls, emitted = [], []
        g = _gate(lambda s: calls.append(s) or JevOutcome(False, error_code="TIMEOUT"), [100.0] * 4, emitted)
        t = TestLiveJevGate()
        cs = t.candles()
        ts = cs[-1].close_time
        g.live_from_ms = ts + 1
        g.recorded = {(g.bot["key"], ts, "long"): {"id": "dec1", "final_action": "REDUCE", "final_level": "L1",
                                                  "risk_multiplier": 0.5, "error_code": None}}
        v = g(t.sig("long"), _ctx(ts, cs))
        assert v.multiplier == 0.5 and v.info["decision_id"] == "dec1" and v.info["jev_source"] == "recorded"
        v2 = g(t.sig("short"), _ctx(ts, cs))                   # a candidate no session observed
        assert v2.multiplier == 1.0 and v2.info["jev_source"] == "not_observed" and not v2.error
        assert calls == [] and emitted == [] and g.decisions == 0

    def test_settled_funding_history_merges_in_order(self):
        f = LiveFunding()
        f.upsert("SOLUSDT", 3000, 0.0003)                  # the next settlement, from premiumIndex
        f.merge_settled("SOLUSDT", [(2000, 0.0002), (1000, 0.0001)])
        assert f.by_symbol["SOLUSDT"] == [(1000, 0.0001), (2000, 0.0002), (3000, 0.0003)]
        f.upsert("SOLUSDT", 3000, 0.0004)
        assert f.by_symbol["SOLUSDT"][-1] == (3000, 0.0004)

    async def test_a_resumed_market_warms_up_from_the_experiments_own_start(self):
        urls = []

        def fetch(url):
            urls.append(url)
            if "fundingRate" in url:
                return [{"symbol": "SOLUSDT", "fundingTime": T0 + 8 * 3_600_000, "fundingRate": "0.0001"}]
            return []
        m = LiveMarket(["SOLUSDT"], 10 * 60_000, fetch=fetch, clock=lambda: (T0 + 86_400_000) / 1000,
                       start_ms=T0 - 60_000, funding_from_ms=T0)
        await m._funding_history()
        await m._backfill_all()
        assert f"startTime={T0 - 60_000}" in next(u for u in urls if "klines" in u)
        assert m.funding.events("SOLUSDT", T0, T0 + 86_400_000) == [(T0 + 8 * 3_600_000, 0.0001)]
        assert m.stats["funding_history"] == 1

    def test_the_plan_resumes_only_the_same_experiment(self, tmp_path):
        from app.core.shadow_view import experiment_summary, experiments
        from app.live import continuity as cont
        st = Storage(str(tmp_path / "s.db"))
        st.shadow_session_start("a1", T0, "run", {"go_live_ms": T0, "warmup_hours": 1.0},
                                experiment_id="fx-a", experiment={})
        st.shadow_session_start("b1", T0 + 5, "run", {"go_live_ms": T0 + 5, "warmup_hours": 1.0},
                                experiment_id="fx-b", experiment={})
        st.shadow_event_add("a1", T0 + 60_000, "candidate", {"bot_key": "K", "side": "long", "signal_ts": T0 + 59_999})
        st.shadow_event_add("a1", T0 + 120_000, "open", {"bot_key": "K", "side": "long", "ts": T0 + 60_399})
        st.shadow_trade_save("a1", {"bot_key": "K", "position_id": "p1", "side": "long", "entry_ts": T0 + 60_399,
                                    "exit_ts": T0 + 600_000, "net": 0.1, "exit_kind": "tp"})
        st.shadow_trade_save("a1", {"bot_key": "K", "position_id": "p2", "side": "long", "entry_ts": T0 + 700_000,
                                    "exit_ts": T0 + 800_000, "net": -0.1, "exit_kind": "session_end"})
        p = cont.plan(st, "fx-a", T0 + 3_600_000, 2 * 3_600_000)
        assert p["forward_from_ms"] == T0 and p["backfill_from_ms"] == T0 - 3_600_000
        assert p["resumed_from_sessions"] == ["a1"]
        assert p["recorded"].counts() == {"candidates": 1, "opens": 1, "trades": 1, "decisions": 0}
        assert cont.public(p)["recorded"]["trades"] == 1
        fresh = cont.plan(st, "fx-new", T0 + 3_600_000, 2 * 3_600_000)
        assert fresh["forward_from_ms"] == T0 + 3_600_000 and fresh["resumed_from_sessions"] == []
        assert fresh["backfill_from_ms"] == T0 - 3_600_000
        fx = experiment_summary(st, "fx-a", experiments(st), None, now_ms=T0 + 3_600_000)
        assert fx["started_ts"] == T0 and fx["closed_trades"] == 1      # the forced session_end exit is no trade
        st.close()


# ---- storage and the public payload -----------------------------------------------------------------

class TestPublicPayload:
    def test_outcomes_resolve_and_nothing_private_leaks(self, tmp_path):
        from app.core.shadow_view import activity_payload, shadow_payload
        st = Storage(str(tmp_path / "s.db"))
        st.shadow_session_start("s1", 1, "run", {"eligible_controls": ["A"], "min_pairs": 10})
        st.shadow_decision_save({"id": "d1", "session_id": "s1", "bot_key": "A+JEV", "pair_id": "pair:A",
                                 "take_probability": 0.3, "final_action": "SKIP", "latency_ms": 320,
                                 "state_json": '{"secret_ish": true}', "candidate_wall_ms": 5})
        st.shadow_trade_save("s1", {"bot_key": "A+JEV", "role": "JEV", "pair_id": "pair:A", "position_id": "p1",
                                    "net": -0.2, "r": -1.0, "exit_kind": "stop", "exit_ts": 9,
                                    "counterfactual": True, "decision_id": "d1"})
        st.shadow_event_add("s1", 10, "decision", {"type": "decision", "bot_key": "A+JEV", "state_json": "x"})
        d = st.shadow_decisions()[0]
        assert d["outcome_kind"] == "SHADOW" and d["outcome_r"] == -1.0
        p = shadow_payload(st)
        blob = repr(p) + repr(activity_payload(st))
        assert "state_json" not in blob and "secret_ish" not in blob
        assert p["jev_live"]["decisions"] == 1 and p["jev_historical"]["run_id"] == "527331f5d0c7"
        assert p["status"]["status"] == "DISABLED"
        st.close()

    def test_the_service_is_off_by_default(self, tmp_path):
        from app.live.service import LiveShadowService
        svc = LiveShadowService(str(tmp_path / "x.db"), env={})
        svc.start()
        assert svc.health()["status"] == "DISABLED" and svc._proc is None

    def test_public_shadow_routes_are_get_only(self):
        from app.main import create_app
        app = create_app(settings_factory(data_dir="/tmp/shadowroutes"))
        paths = {path: {method.upper() for method in methods} for path, methods in app.openapi()["paths"].items()}
        for p in ("/api/public/competition/shadow", "/api/public/competition/shadow/activity",
                  "/api/public/stream"):
            assert p in paths and set(paths[p]) <= {"GET", "HEAD"}, p
