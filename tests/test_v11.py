"""V11 SCAN: the freeze, the four multi-coin scanner bots, anchor-only decisions over the whole universe, the ordered
minute tape of the feed, per-coin gates and continuity identities, and the multi-coin book through the engine. No
network."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.competition import v11_config as v11
from app.core.storage import Storage
from app.core.types import Candle, Signal, TakeProfit
from app.live import v6_gates as gates
from app.live.scan_market import RELEASE_AFTER_MS, ScanMarket
from app.live.v11_worker import ScanGateV11, load_eliminated, load_recorded_v11, recorded_for
from app.strategies.v11.scan import ANCHOR, load_v11_scanners, rank_pct
from tests.conftest import T0

HOUR = 3_600_000
DAY = 24 * HOUR
MINUTE = 60_000
SYMS = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT", "FFFUSDT", "GGGUSDT", "HHHUSDT", "IIIUSDT", "JJJUSDT",
        "KKKUSDT", ANCHOR]


class TestFreezeAndField:
    def test_the_running_v11_is_the_frozen_manifest(self):
        man = v11.load_freeze()
        assert man is not None and v11.verify_freeze(man) == []
        assert man["field"] == ["V11.1-SCAN", "V11.1-SCAN+JEV", "V11.2-SCAN", "V11.2-SCAN+JEV", "V11.3-SCAN", "V11.3-SCAN+JEV", "V11.4-SCAN", "V11.4-SCAN+JEV"]
        assert man["profile"]["jev"]["twins"] is True and man["profile"]["ladder"]["shares"]["V11.1"] == [0.25, 0.5, 0.25]
        assert man["profile"]["ladder"]["lock_after_tp1"]["V11.2"] == 0.5
        assert set(man["rules"]) == set(v11.UNIVERSE) and len(v11.UNIVERSE) == 30 and ANCHOR in v11.UNIVERSE

    def test_a_change_is_detected_and_identity_is_v11(self):
        man = json.loads(json.dumps(v11.load_freeze()))
        man["params"]["V11.4"]["per_side"] = 3
        assert any("params" in d for d in v11.verify_freeze(man))
        man = json.loads(json.dumps(v11.load_freeze()))
        man["code"]["app.live.scan_market"] = "0" * 12
        assert any("code" in d for d in v11.verify_freeze(man))
        assert v11.experiment_identity(v11.load_freeze())[0].startswith("v11x-")
        assert v11.verify_freeze(None) == ["docs/V11_FREEZE.json is missing: V11 is not frozen"]

    def test_one_book_per_scanner_over_every_coin(self):
        specs = v11.field_plan()
        assert [s.key for s in specs] == ["V11.1-SCAN", "V11.1-SCAN+JEV", "V11.2-SCAN", "V11.2-SCAN+JEV", "V11.3-SCAN", "V11.3-SCAN+JEV", "V11.4-SCAN", "V11.4-SCAN+JEV"]
        assert [s.timeframe for s in specs] == ["15m", "15m", "15m", "15m", "5m", "5m", "1h", "1h"]
        assert [s.key for s in v11.field_plan(jev=False)] == ["V11.1-SCAN", "V11.2-SCAN", "V11.3-SCAN", "V11.4-SCAN"]
        c, j = specs[0], specs[1]
        assert j.role == "JEV" and j.pair_id == c.pair_id and j.control_key == c.key
        assert all(s.symbol == "" and s.coin == "ALL" for s in specs)
        assert [s.role for s in specs] == ["CONTROL", "JEV"] * 4
        assert v11.settings_v11().strategy_starting_balance == 20.0
        e = v11.EXECUTION_V11
        assert e.signal_latency_ms + e.order_latency_ms == v11.DECISION_WINDOW_MS + 1

    def test_elimination_and_qualification(self):
        row = {"start_equity": 20.0, "equity": 19.8, "trades_24h": 2}
        assert v11.should_eliminate(row, 23 * HOUR) is None
        assert v11.should_eliminate(row, 25 * HOUR) == "INACTIVE_UNDER_3_TRADES_24H"
        assert v11.should_eliminate({**row, "equity": 14.9}, HOUR) == "DRAWDOWN_25PCT"
        ok = {"trades": 45, "net_now": 12.0, "profit_factor": 1.4, "max_dd": 0.05, "risk_state": "NORMAL"}
        assert v11.bot_status(ok, 3 * DAY, None)[0] == "QUALIFIED"
        assert v11.bot_status({**ok, "profit_factor": 1.1}, 3 * DAY, None)[0] == "ACTIVE"


def test_rank_pct_averages_ties_like_pandas():
    assert rank_pct({"a": 1.0, "b": 3.0, "c": 3.0, "d": 5.0}) == {"a": 0.25, "b": 0.625, "c": 0.625, "d": 1.0}


# -- the scanners ---------------------------------------------------------------------------------------------------------
def _engine(symbols=SYMS):
    from app.backtest.replay import ReplayEngine
    from app.core.types import MarketRules
    rules = {s: MarketRules(s, 0.0001, 0.001, 0.001, 5.0) for s in symbols}
    return ReplayEngine(v11.settings_v11(), symbols, rules=rules, seed=7)


def _hour_bars(sym, drift, n=30, missing_last=False, start=T0):
    out, p = [], 100.0
    for i in range(n - (1 if missing_last else 0)):
        o = p
        c = p * (1 + drift)
        out.append(Candle(sym, "1h", start + i * HOUR, o, max(o, c) * 1.006, min(o, c) * 0.994, c, 1000.0,
                          start + (i + 1) * HOUR - 1))
        p = c
    return out


class TestScanners:
    def test_the_reversal_ranker_fades_the_top_and_buys_the_bottom_on_the_anchor_only(self):
        eng = _engine()
        drifts = {s: (i - 5) * 0.001 for i, s in enumerate(SYMS)}           # AAA falls most ... KKK rises most
        for s in SYMS:
            for c in _hour_bars(s, drifts[s], missing_last=(s == "KKKUSDT")):
                eng.ctx.push(c)
        cls = load_v11_scanners()["V11.4"].for_universe(SYMS)
        strat = cls()
        anchor_bar = eng.ctx.candles(ANCHOR, "1h")[-1]
        assert strat.on_candle(eng.ctx.candles("AAAUSDT", "1h")[-1], eng.ctx) == []        # not the anchor: nothing
        sigs = strat.on_candle(anchor_bar, eng.ctx)
        got = {(s.symbol, s.side) for s in sigs}
        # KKK's last candle is missing at the decision instant: it is not scanned, so JJJ + the anchor are the top two
        assert got == {("AAAUSDT", "long"), ("BBBUSDT", "long"), ("JJJUSDT", "short"), (ANCHOR, "short")}
        for s in sigs:
            assert 0.015 <= s.meta["stop_pct"] <= 0.08 and s.max_hold_s == 58 * 60 and s.tf == "1h"
            assert s.id.startswith("V11.4:SCAN:" + s.symbol)
        assert [s.meta["score"] for s in sigs] == sorted((s.meta["score"] for s in sigs), reverse=True)

    def test_top_cuts_come_from_the_study_discovery_half(self):
        study = json.loads(open("docs/V11_SCAN_STUDY.json", encoding="utf-8").read())
        fams = load_v11_scanners()
        assert fams["V11.1"].Params().min_score == round(study["families"]["RSB"]["top_cut"], 4)
        assert fams["V11.2"].Params().min_score == round(study["families"]["RSP"]["top_cut"], 4)
        assert fams["V11.3"].Params().min_score == 0.0 and fams["V11.4"].Params().min_score == 0.0

    def test_a_multi_coin_book_trades_many_coins_within_its_slots(self):
        """The whole engine on a synthetic 12-coin tape: one book, several coins, never more than 4 positions."""
        eng = _engine()
        cls = load_v11_scanners()["V11.4"].for_universe(SYMS)
        order = sorted(s for s in SYMS if s != ANCHOR) + [ANCHOR]

        def tape():
            import math
            for m in range(30 * 60):
                t = T0 + m * MINUTE
                for k, s in enumerate(order):
                    base = 100.0 * (1 + 0.02 * math.sin(m / 97.0 + k))
                    o, c = base, base * (1 + 0.0004 * math.sin(m / 7.0 + k))
                    yield Candle(s, "1m", t, o, max(o, c) * 1.001, min(o, c) * 0.999, c, 50.0, t + MINUTE - 1)
        res = eng.run(cls, tape(), since_ms=T0 + 24 * HOUR, leverage=20, signal_tf="1h", only_symbol=None)
        trades = [t for t in res.trades if t.exit_kind != "time" or t.exit_ts < T0 + 30 * HOUR - MINUTE]
        assert len({t.symbol for t in trades}) >= 4
        assert all(t.exit_ts - t.entry_ts <= 59 * MINUTE for t in trades)
        opens = sorted([(t.entry_ts, 1) for t in res.trades] + [(t.exit_ts, -1) for t in res.trades])
        live = peak = 0
        for _, d in opens:
            live += d
            peak = max(peak, live)
        assert peak <= 4


# -- the feed -----------------------------------------------------------------------------------------------------------
class FakeBybit:
    def __init__(self, bars: dict[str, list[int]]):
        self.bars = bars
        self.calls: list[str] = []

    def __call__(self, url: str) -> dict[str, Any]:
        self.calls.append(url)
        q = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
        if "/tickers" in url:
            return {"list": [{"symbol": s, "bid1Price": "99.99", "ask1Price": "100.01", "fundingRate": "0.0001",
                              "nextFundingTime": str(T0 + 8 * HOUR)} for s in self.bars]}
        sym, a, b = q["symbol"], int(q["start"]), int(q["end"])
        rows = [[str(o), "100", "101", "99", "100.5", "7", "700"] for o in sorted(self.bars.get(sym, []), reverse=True)
                if a <= o <= b]
        return {"list": rows[:1000]}


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _feed(bars, clock, history=None, until=None, symbols=("AAAUSDT", "BBBUSDT", ANCHOR)):
    from app.live.bybit_market import LiveFundingV6
    stored = []
    m = ScanMarket(list(symbols), ANCHOR, lambda k, p: stored.append((k, p)), LiveFundingV6(), T0,
                   {s: 480 for s in symbols}, history=history, history_until_ms=until, fetch=FakeBybit(bars),
                   clock=clock, sleep=lambda s: setattr(clock, "t", clock.t + s))
    return m, m.subscribe(), stored


def _drain(q):
    out = []
    while not q.empty():
        b = q.get_nowait()
        out.append((b.symbol, b.open_time))
    return out


class TestFeed:
    def test_history_then_catch_up_arrive_minute_by_minute_with_the_anchor_last(self):
        rows = {s: [{"symbol": s, "open_time": T0 + i * MINUTE, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
                    for i in range(3)] for s in ("AAAUSDT", "BBBUSDT", ANCHOR)}
        hist = lambda a, b: {s: [r for r in rs if a <= r["open_time"] < b] for s, rs in rows.items()}  # noqa: E731
        bars = {s: [T0 + i * MINUTE for i in range(3, 6)] for s in ("AAAUSDT", "BBBUSDT", ANCHOR)}
        m, q, stored = _feed(bars, Clock((T0 + 6 * MINUTE + 5000) / 1000), hist, T0 + 2 * MINUTE)
        m._replay_history()
        m._catch_up()
        got = _drain(q)
        assert got == [(s, T0 + i * MINUTE) for i in range(6) for s in ("AAAUSDT", "BBBUSDT", ANCHOR)]
        assert {p["source"] for k, p in stored if k == "bar"} == {"backfill"} and len(stored) >= 9   # history not re-stored

    def test_a_minute_waits_for_every_coin_then_releases_without_a_late_one(self):
        clock = Clock((T0 + MINUTE + 2000) / 1000)
        bars = {"AAAUSDT": [T0], "BBBUSDT": [], ANCHOR: [T0]}
        m, q, _ = _feed(bars, clock)
        m.delivered = {s: T0 - MINUTE for s in m.symbols}
        m.phase = "LIVE"
        m.poll_once()                                        # BBB's minute never came within the window
        assert _drain(q) == [("AAAUSDT", T0), (ANCHOR, T0)] and m.stats["incomplete_minutes"] == 1
        m.fetch.bars["BBBUSDT"] = [T0, T0 + MINUTE]          # BBB's late bar, then everyone's next minute
        m.fetch.bars["AAAUSDT"].append(T0 + MINUTE)
        m.fetch.bars[ANCHOR].append(T0 + MINUTE)
        clock.t = (T0 + 2 * MINUTE + 2000) / 1000
        m.poll_once()
        assert _drain(q) == [("BBBUSDT", T0), ("AAAUSDT", T0 + MINUTE), ("BBBUSDT", T0 + MINUTE), (ANCHOR, T0 + MINUTE)]
        assert m.stats["late_bars"] == 1 and m.ready()

    def test_complete_minutes_release_at_once_and_quotes_are_recorded(self):
        clock = Clock((T0 + MINUTE + 2000) / 1000)
        m, q, stored = _feed({s: [T0] for s in ("AAAUSDT", "BBBUSDT", ANCHOR)}, clock)
        m.delivered = {s: T0 - MINUTE for s in m.symbols}
        m.phase = "LIVE"
        m.poll_once()
        assert [s for s, _ in _drain(q)] == ["AAAUSDT", "BBBUSDT", ANCHOR]
        assert m.quote_half_spread("AAAUSDT", T0 + MINUTE) == pytest.approx(1.0, abs=1e-6)
        assert m.stream_ok("AAAUSDT") == (True, None) and m.mid(ANCHOR) == pytest.approx(100.0)
        assert all(p["half_spread_bps"] is not None for k, p in stored if k == "bar")
        assert RELEASE_AFTER_MS <= 20_000


# -- gates, identities, storage ---------------------------------------------------------------------------------------------
def _sig(sym, ts, side="long"):
    return Signal("V11.4", sym, "entry", side, ts, "1h", 100.0, stop=97.0, take_profits=[TakeProfit(109.0, 1.0)], reason="t")


def _gate(now, recorded=None, live_from=T0, board=None, eliminated_at=None):
    em = []
    g = ScanGateV11(eliminated_at=eliminated_at or {}, late_after_ms=v11.LATE_AFTER_MS, experiment_id="v11x-t",
                    session_id="s", bot={"key": "V11.4-SCAN", "symbol": "", "pair_id": "v11:V11.4-SCAN", "horizon": "SCAN",
                                         "strategy_id": "V11.4", "timeframe": "1h"},
                    emit=lambda k, d: em.append((k, d)), board=board or gates.DecisionBoard(lambda s, t: (True, None)),
                    coverage=gates.Coverage([(T0, T0 + DAY)]), recorded=recorded, live_from_ms=live_from,
                    wall=lambda: now / 1000)
    return g, em


class TestGateAndContinuity:
    def test_decisions_are_per_coin(self):
        ts = T0 + 2 * HOUR - 1
        g, em = _gate(ts + 5000)
        g(_sig("AAAUSDT", ts), {"ts": ts, "sizing": {}})
        g(_sig("BBBUSDT", ts), {"ts": ts, "sizing": {}})
        a, b = em[0][1], em[1][1]
        assert a["symbol"] == "AAAUSDT" and b["symbol"] == "BBBUSDT" and a["id"] != b["id"]

    def test_stale_data_is_judged_on_the_candidate_coin(self):
        ts = T0 + 2 * HOUR - 1
        board = gates.DecisionBoard(lambda s, t: (s != "BBBUSDT", None if s != "BBBUSDT" else "no bar"))
        g, em = _gate(ts + 5000, board=board)
        assert g(_sig("AAAUSDT", ts), {"ts": ts, "sizing": {}}).multiplier == 1.0
        assert g(_sig("BBBUSDT", ts), {"ts": ts, "sizing": {}}).multiplier == 0.0 and em[1][1]["reason"].startswith("DATA_STALE")

    def test_a_restart_replays_each_coins_recorded_decision(self, tmp_path):
        st = Storage(str(tmp_path / "v11.db"))
        ts = T0 + 2 * HOUR - 1
        g, em = _gate(ts + 5000)
        g(_sig("AAAUSDT", ts), {"ts": ts, "sizing": {}})
        g(_sig("BBBUSDT", ts, "short"), {"ts": ts, "sizing": {}})
        for _, row in em:
            st.fwd6_decision_save(row)
        for sym, side in (("AAAUSDT", "long"), ("BBBUSDT", "short")):
            st.fwd6_event_add("v11x-t", "s", ts, "candidate", {"bot_key": "V11.4-SCAN", "symbol": sym, "side": side,
                                                               "signal_ts": ts})
            st.fwd6_trade_save("v11x-t", "s", {"bot_key": "V11.4-SCAN", "symbol": sym, "side": "long", "entry_ts": ts + MINUTE,
                                               "exit_ts": ts + HOUR, "net": 1.0, "multi_symbol": True})
        assert len(st.fwd6_trades("v11x-t", counterfactual=None)) == 2       # same bot, instant and side: two coins
        rec = recorded_for(load_recorded_v11(st, "v11x-t"), "V11.4-SCAN")
        assert ("candidate", "V11.4-SCAN@AAAUSDT", "long", ts) in rec.seen and len(rec.decisions) == 2
        g2, em2 = _gate(ts + 10 * HOUR, recorded=rec.decisions, live_from=T0 + 5 * HOUR)
        assert g2(_sig("BBBUSDT", ts, "short"), {"ts": ts, "sizing": {}}).multiplier == 1.0 and em2 == []
        st.close()

    def test_eliminations_reload_and_scanners_cannot_go_live(self, tmp_path):
        import asyncio
        from app.live.mirror import MirrorError, MirrorService
        from app.live.providers import Providers
        st = Storage(str(tmp_path / "m.db"))
        st.fwd6_event_add("v11x-e", "s", T0 + DAY, "eliminated", {"bot_key": "V11.3-SCAN", "at": T0 + DAY, "reason": "DRAWDOWN_25PCT"})
        assert load_eliminated(st, "v11x-e") == {"V11.3-SCAN": {"at": T0 + DAY, "reason": "DRAWDOWN_25PCT"}}
        svc = MirrorService(st, Providers({}), lambda prog, key: {"key": key, "program_status": "QUALIFIED"})
        with pytest.raises(MirrorError, match="scanner"):
            asyncio.run(svc.arm({"program": "v11", "bot_key": "V11.1-SCAN", "exchange": "bybit", "network": "testnet",
                                 "amount_usdt": 50, "risk_pct": 0.01, "max_daily_loss": 5, "max_total_loss": 10,
                                 "confirm": "GO LIVE V11.1-SCAN"}))
        st.close()


class TestRunnerAndView:
    def test_a_multi_coin_runner_names_the_coin_and_keeps_single_coin_bots_unchanged(self):
        import queue
        from app.live.v6_runner import V6Bot
        spec = next(x for x in v11.field_plan() if x.key == "V11.4-SCAN")
        eng = _engine()
        bot = V6Bot(spec=spec, cls=load_v11_scanners()["V11.4"].for_universe(SYMS), engine=eng, bars=queue.Queue(),
                    since_ms=T0, live_from_ms=T0, emit=lambda k, d: None, multi=True)
        assert bot._ik("AAAUSDT") == "V11.4-SCAN@AAAUSDT" and bot._base("AAAUSDT")["coin"] == "AAA"
        from app.competition import v8_config as v8
        one_spec = next(s for s in v8.field_plan(v8.COINS) if s.symbol == "ETHUSDT" and s.role == "CONTROL")
        one = V6Bot(spec=one_spec, cls=None, engine=_engine(["ETHUSDT"]), bars=queue.Queue(), since_ms=T0,
                    live_from_ms=T0, emit=lambda k, d: None)
        assert one._ik("ETHUSDT") == one_spec.key and one._base("SOLUSDT")["symbol"] == "ETHUSDT"

    def test_the_live_path_emits_every_coin_separately(self):
        """V6Bot (multi) + ScanGateV11 + the engine on a synthetic 12-coin tape, as the worker wires them: candidates,
        decisions, opens and closes all name their own coin, and none is lost to an identity collision."""
        import math
        import queue
        from app.live.v6_runner import V6Bot
        spec = next(x for x in v11.field_plan() if x.key == "V11.4-SCAN")
        events: list[tuple[str, dict]] = []
        emit = lambda k, d: events.append((k, d))  # noqa: E731
        live_from = T0 + 26 * HOUR
        now = {"ms": T0}
        gate = ScanGateV11(eliminated_at={}, late_after_ms=v11.LATE_AFTER_MS, experiment_id="v11x-l", session_id="s",
                           bot={**spec.to_dict()}, emit=emit, board=gates.DecisionBoard(lambda s, t: (True, None)),
                           coverage=gates.Coverage([(T0, T0 + 2 * DAY)]), recorded=None, live_from_ms=live_from,
                           wall=lambda: now["ms"] / 1000)
        from app.backtest.replay import ReplayEngine
        from app.core.types import MarketRules
        rules = {s: MarketRules(s, 0.0001, 0.001, 0.001, 5.0) for s in SYMS}
        eng = ReplayEngine(v11.settings_v11(), SYMS, rules=rules, seed=7, gate=gate, execution=v11.EXECUTION_V11)
        q: queue.Queue = queue.Queue()
        from app.strategies.v11.ladder import ladder_class
        bot = V6Bot(spec=spec, cls=ladder_class(load_v11_scanners()["V11.4"]).for_universe(SYMS), engine=eng, bars=q,
                    since_ms=T0 + 24 * HOUR,
                    live_from_ms=live_from, emit=emit, multi=True)
        order = sorted(s for s in SYMS if s != ANCHOR) + [ANCHOR]
        for m in range(30 * 60):
            t = T0 + m * MINUTE
            for k, s in enumerate(order):
                base = 100.0 * (1 + 0.02 * math.sin(m / 97.0 + k))
                o, c = base, base * (1 + 0.0004 * math.sin(m / 7.0 + k))
                q.put(Candle(s, "1m", t, o, max(o, c) * 1.001, min(o, c) * 0.999, c, 50.0, t + MINUTE - 1))
        q.put(None)

        class Clocked:                              # the gate's wall clock follows the tape: decisions are timely
            def __init__(self, it):
                self.it = it

            def __iter__(self):
                for bar in self.it:
                    now["ms"] = bar.close_time + 5_000
                    yield bar
        bot._bars_orig = bot._bars
        bot._bars = lambda: Clocked(bot._bars_orig())
        bot._target()
        kinds = {}
        for k, d in events:
            kinds.setdefault(k, []).append(d)
        opens, closes, decs = kinds.get("open", []), kinds.get("closed", []), kinds.get("decision", [])
        assert opens and closes and decs and not kinds.get("error")
        assert {d["symbol"] for d in opens} <= set(SYMS) and len({d["symbol"] for d in opens}) >= 4
        assert all(d["coin"] == d["symbol"][:-4] for d in opens + closes)
        assert len({d["id"] for d in decs}) == len(decs)                                  # one decision id per coin
        same_instant = {}
        for d in opens:
            same_instant.setdefault((d["ts"], d["side"]), set()).add(d["symbol"])
        assert any(len(v) > 1 for v in same_instant.values())                            # two coins, one instant: both kept
        assert all(d.get("multi_symbol") for d in closes)
        assert all(len(d["tps"]) == 3 and d["target"] == d["tps"][0] for d in opens)       # the whole ladder, as opened
        assert all(len(p["tps"]) == 3 for p in bot.status["open_positions"])
        # a PARTIAL take-profit is its own event (Telegram replies with it): it names the trade's opening fill
        tps = kinds.get("tp", [])
        if any(c["exit_kind"] == "be" for c in closes):                  # a protective-stop exit means TP1 had filled
            assert tps
        open_ts = {(o["symbol"], o["side"], o["ts"]) for o in opens}
        assert all((d["symbol"], d["side"], d["entry_ts"]) in open_ts and d["tp_index"] >= 1 and d["remaining_qty"] > 0
                   for d in tps)

    def test_old_boxes_get_their_ladder_rebuilt(self):
        from app.core.arena_live_view import fill_ladder, ladder_rungs
        box = {"stop": 98.0, "target": 101.0}                       # TP1 at +0.5R of a signal at 100
        fill_ladder(box, (0.5, 1.0, 2.0))                         # any rungs: rebuilt from the stop and TP1
        assert box["tps"] == pytest.approx([101.0, 102.0, 104.0])
        short = {"stop": 102.0, "target": 99.0}
        fill_ladder(short, (0.5, 1.0, 2.0))
        assert short["tps"] == pytest.approx([99.0, 98.0, 96.0])
        assert ladder_rungs("v11", "V11.2-SCAN") == (0.5, 1.0, 1.5) and ladder_rungs("v8", "V8.1-ETH-5M+LADDER") == (0.75, 1.5, 2.5)
        assert ladder_rungs("v8", "V8.1-ETH-5M") is None

    def test_service_switch_and_get_only_routes(self, tmp_path):
        from app.core import api_public
        from app.live.v6_service import V6ForwardService
        assert V6ForwardService(str(tmp_path / "x.db"), env={}, program="V11").enabled is False
        assert not V6ForwardService(str(tmp_path / "x.db"), env={"V11_FORWARD_ENABLED": "true"}, program="V11").enabled  # retired 2026-10-08 (app/core/programs.py): never starts
        routes = [r for r in api_public.router.routes if r.path.endswith("/v11")]
        assert len(routes) == 1 and set(routes[0].methods) <= {"GET", "HEAD"}

    def test_enrich_lists_the_coins_each_scanner_traded(self, tmp_path):
        from app.core.arena_live_view import v11_enrich
        st = Storage(str(tmp_path / "v.db"))
        for i, sym in enumerate(("ETHUSDT", "ETHUSDT", "SOLUSDT")):
            st.fwd6_trade_save("v11x-q", "s", {"bot_key": "V11.1-SCAN", "symbol": sym, "side": "long",
                                               "entry_ts": T0 + i * HOUR, "exit_ts": T0 + i * HOUR + MINUTE, "net": 1.0,
                                               "multi_symbol": True})
        out = v11_enrich({"experiment": {"experiment_id": "v11x-q", "forward_start_ms": T0},
                          "leaderboard": [{"key": "V11.1-SCAN", "trades": 3}], "hero": {}}, st, now_ms=T0 + DAY)
        assert out["leaderboard"][0]["coins_traded"] == {"ETH": 2, "SOL": 1} and len(out["universe"]) == 30
        st.close()


class TestLadder:
    """The operator's ladder: TP1 closes 25% and the stop goes past entry + fees, TP2 closes 50% and the stop goes to
    TP1, TP3 closes the last 25%."""

    def _pos(self, side):
        from app.core.types import TakeProfit, VirtualPosition
        from app.strategies.v11.ladder import SHARES
        d = 1 if side == "long" else -1
        entry, stop = 100.0, 100.0 - d * 2.0                                  # R = 2 price units
        rungs = (1.0, 2.0, 3.0)                                               # equally spaced
        return VirtualPosition(id="p", strategy_id="V11.3", symbol="AAAUSDT", side=side, qty=4.0, qty_initial=4.0,
                               entry_price=entry, entry_ts=T0, leverage=5, margin=80.0, stop=stop,
                               take_profits=[TakeProfit(entry + d * r * 2.0, f) for r, f in zip(rungs, SHARES["V11.3"])],
                               trail=None, be_at_r=rungs[0], max_hold_deadline=None, initial_risk_usd=8.0,
                               extreme_price=entry), rungs

    def test_every_scanner_signal_carries_its_ladder(self):
        from app.strategies.v11.ladder import RUNGS, ladder_class
        eng = _engine()
        drifts = {s: (i - 5) * 0.001 for i, s in enumerate(SYMS)}
        for s in SYMS:
            for c in _hour_bars(s, drifts[s]):
                eng.ctx.push(c)
        cls = ladder_class(load_v11_scanners()["V11.4"]).for_universe(SYMS)
        sigs = cls().on_candle(eng.ctx.candles(ANCHOR, "1h")[-1], eng.ctx)
        assert sigs and set(RUNGS) == {"V11.1", "V11.2", "V11.3", "V11.4"}
        for s in sigs:
            risk = abs(s.entry_price - s.stop)
            d = 1 if s.side == "long" else -1
            assert [tp.fraction for tp in s.take_profits] == [0.25, 0.5, 0.25] and s.meta["tp1_lock"] == 0.5
            assert [round((tp.price - s.entry_price) * d / risk, 6) for tp in s.take_profits] == list(RUNGS["V11.4"])
            assert s.be_at_r == RUNGS["V11.4"][0] and s.meta["exits"] == "LADDER" and s.max_hold_s == 58 * 60

    @pytest.mark.parametrize("side", ["long", "short"])
    def test_tp1_moves_the_stop_to_entry_and_tp2_to_tp1(self, side):
        from app.core.positions import ExitEngine
        from app.strategies.v11.ladder import ladder_class
        d = 1 if side == "long" else -1
        pos, rungs = self._pos(side)
        ex = ExitEngine({})
        strat = ladder_class(load_v11_scanners()["V11.3"], rungs)()
        assert strat.manage(pos, None, None) is None                          # nothing before TP1
        intents = ex.on_price(pos, 100.0 + d * 2.1, None, T0 + MINUTE)        # TP1 (+1R) prints
        assert intents[0].kind == "tp" and intents[0].fraction == pytest.approx(0.25)
        assert pos.stop == pytest.approx(100.0 * (1 + d * 0.0006), rel=1e-3)   # entry (+ the engine's 6 bp fee buffer)
        pos.qty = 3.0
        intents = ex.on_price(pos, 100.0 + d * 4.1, None, T0 + 2 * MINUTE)    # TP2 (+2R): closes 50% of the original
        assert intents[0].fraction == pytest.approx(2.0 / 3.0)
        pos.qty = 1.0
        ex.apply_update(pos, strat.manage(pos, None, None), 100.0 + d * 4.1)
        assert pos.stop == pytest.approx(100.0 + d * 2.0)                     # the stop now sits on TP1
        intents = ex.on_price(pos, 100.0 + d * 6.1, None, T0 + 3 * MINUTE)    # TP3 (+3R): the rest
        assert intents[0].fraction == pytest.approx(1.0)


def test_a_box_recorded_without_levels_takes_them_from_its_decision(tmp_path):
    """A position that opened and closed inside one bar was once recorded with no stop / target: its candidate has them."""
    from app.core.arena_live_view import candles
    st = Storage(str(tmp_path / "b.db"))
    entry = T0 + 36 * MINUTE
    st.fwd6_bars_save([{"symbol": "TAOUSDT", "open_time": T0 + i * MINUTE, "open": 308.0, "high": 309.0, "low": 306.0,
                        "close": 308.0, "volume": 1.0, "turnover": 1.0, "source": "live"} for i in range(60)])
    st.fwd6_event_add("v11x-b", "s", entry, "candidate", {"bot_key": "V11.3-SCAN", "symbol": "TAOUSDT", "side": "short",
                                                          "signal_ts": entry - MINUTE - 1, "stop": 309.85, "target": 307.07})
    st.fwd6_event_add("v11x-b", "s", entry, "open", {"bot_key": "V11.3-SCAN", "symbol": "TAOUSDT", "side": "short",
                                                     "ts": entry, "position_id": "p1", "stop": None, "target": None, "tps": []})
    st.fwd6_trade_save("v11x-b", "s", {"bot_key": "V11.3-SCAN", "symbol": "TAOUSDT", "side": "short", "entry_ts": entry,
                                       "exit_ts": entry + 40_000, "entry_price": 307.96, "exit_price": 307.65, "net": -0.002,
                                       "multi_symbol": True, "position_id": "p1"})
    out = candles(st, {"experiment_id": "v11x-b"}, "TAOUSDT", "1m", 60, now_ms=T0 + 60 * MINUTE, program="v11")
    box = out["positions"][0]
    assert box["stop"] == 309.85 and box["target"] == 307.07 and len(box["tps"]) == 3
    assert box["tps"][0] == pytest.approx(307.07) and box["tps"][2] < box["tps"][1] < box["tps"][0]
    st.close()


class TestGapGuard:
    """Just before a scanner's order fills: a price already past TP1, or through the stop, skips the trade."""

    def _sig(self, side):
        d = 1 if side == "long" else -1
        return Signal("V11.3", "AAAUSDT", "entry", side, T0, "5m", 100.0, stop=100.0 - d * 2.0,
                      take_profits=[TakeProfit(100.0 + d * 1.0, 0.25), TakeProfit(100.0 + d * 2.0, 0.5),
                                    TakeProfit(100.0 + d * 4.0, 0.25)], reason="t")

    @pytest.mark.parametrize("side", ["long", "short"])
    def test_verdicts(self, side):
        from app.live.scan_engine import gap_verdict
        d = 1 if side == "long" else -1
        s = self._sig(side)
        assert gap_verdict(side, 100.0 + d * 0.5, s) is None                  # between entry and TP1: fill
        assert gap_verdict(side, 100.0 + d * 1.0, s) == "gap_past_tp1"         # at / past TP1: skip
        assert gap_verdict(side, 100.0 - d * 1.9, s) is None                  # adverse but inside the stop: fill
        assert gap_verdict(side, 100.0 - d * 2.0, s) == "gap_past_stop"        # at / through the stop: skip

    def test_the_engine_skips_a_gapped_order_and_fills_a_normal_one(self):
        from app.core.types import MarketRules
        from app.live.scan_engine import ScanReplayEngine

        class TwoShots(load_v11_scanners()["V11.3"].for_universe(["AAAUSDT", ANCHOR])):
            def on_candle(self, c, ctx):
                if c.symbol != "AAAUSDT" or c.tf != "5m":
                    return []
                if c.close_time == T0 + 10 * MINUTE - 1 or c.close_time == T0 + 20 * MINUTE - 1:
                    return [self.make_entry(symbol="AAAUSDT", side="long", ts=c.close_time, tf="5m", price=c.close,
                                            stop=c.close * 0.98, tps_r=[(0.5, 0.25), (1.0, 0.5), (2.0, 0.25)])]
                return []
        rules = {s: MarketRules(s, 0.0001, 0.001, 0.001, 5.0) for s in ("AAAUSDT", ANCHOR)}
        eng = ScanReplayEngine(v11.settings_v11(), ["AAAUSDT", ANCHOR], rules=rules, seed=7, execution=v11.EXECUTION_V11)

        def tape():
            for m in range(40):
                t = T0 + m * MINUTE
                px = 100.0 if m < 10 else 102.0 if m < 20 else 102.1       # a 2% jump right after the first decision
                for s in ("AAAUSDT", ANCHOR):
                    yield Candle(s, "1m", t, px, px * 1.0005, px * 0.9995, px, 5.0, t + MINUTE - 1)
        res = eng.run(TwoShots, tape(), since_ms=T0, leverage=20, signal_tf="5m", only_symbol=None)
        assert res.rejects.get("gap_past_tp1") == 1                                   # the first order: skipped
        assert len([f for f in res.fills if f.kind == "entry"]) == 1                  # the second one filled



class TestJevTwin:
    """The +JEV twin: Jev may skip; otherwise its confidence stretches the equally spaced ladder."""

    def _gate(self, decide, now, recorded=None, live_from=T0):
        from app.ai.jev.v11 import JevStateBuilderV11
        from app.live.v11_worker import JevGateV11
        em = []
        g = JevGateV11(decide=decide, model="m", mid=lambda s: 100.0, spread=lambda s: (0.5, "observed"),
                       builder=JevStateBuilderV11(), window_ms=v11.DECISION_WINDOW_MS, margin_ms=v11.JEV_DEADLINE_MARGIN_MS,
                       eliminated_at={}, late_after_ms=v11.LATE_AFTER_MS, experiment_id="v11x-j", session_id="s",
                       bot={"key": "V11.3-SCAN+JEV", "symbol": "", "pair_id": "v11:V11.3-SCAN", "horizon": "SCAN",
                            "strategy_id": "V11.3", "timeframe": "5m"},
                       emit=lambda k, d: em.append((k, d)), board=gates.DecisionBoard(lambda s, t: (True, None)),
                       coverage=gates.Coverage([(T0, T0 + DAY)]), recorded=recorded, live_from_ms=live_from,
                       wall=lambda: now / 1000)
        return g, em

    def _sig(self, ts):
        from app.strategies.v11.ladder import RUNGS, apply_ladder
        s = Signal("V11.3", "AAAUSDT", "entry", "long", ts, "5m", 100.0, stop=98.0, take_profits=[], reason="t")
        s.meta.update(stop_pct=0.02, signal_tf="5m")
        return apply_ladder(s, RUNGS["V11.3"], "V11.3")

    def _ctx(self, ts):
        from types import SimpleNamespace
        eng = _engine()
        for i in range(80):
            t = ts + 1 - (80 - i) * 5 * MINUTE
            eng.ctx.push(Candle("AAAUSDT", "5m", t, 100.0, 100.4, 99.6, 100.1, 10.0, t + 5 * MINUTE - 1))
        return {"ts": ts, "sizing": {"tier": "TAKE"}, "health": {"peak": 20.0}, "equity": 20.0, "start_equity": 20.0,
                "taker_fee": 0.00055, "half_spread_bps": 0.5, "trades": [], "ctx": eng.ctx,
                "decision": SimpleNamespace(risk_usd=0.2, leverage=2.0, notional=10.0)}

    def _answer(self, choice, p_contradict):
        from app.ai.jev.models import JevDecision, JevOutcome
        rest = 1.0 - p_contradict
        probs = {"SKIP": p_contradict, "TAKE": rest * 0.7, "ATTACK": rest * 0.3}
        dec = JevDecision(take_probability=rest, risk_state=choice, risk_probabilities=probs, setup_quality=0.0,
                          quality_score=0.0, quality_probabilities={}, regime="", regime_probabilities={}, model_resolved="m")
        return lambda state: JevOutcome(True, decision=dec, latency_ms=300)

    def test_confidence_stretches_the_ladder(self):
        from app.ai.jev.v11 import tp_scale
        ts = T0 + 10 * HOUR - 1
        g, em = self._gate(self._answer("TAKE", 0.2), ts + 1 + 3000)                # 80% sure
        s = self._sig(ts)
        v = g(s, self._ctx(ts))
        assert v.multiplier == 1.0 and s.meta["tp_scale"] == tp_scale(0.8) == 1.3
        assert [round((tp.price - 100.0) / 2.0, 4) for tp in s.take_profits] == [0.65, 1.3, 1.95]  # 0.5 / 1 / 1.5 R x 1.3
        assert [tp.fraction for tp in s.take_profits] == [0.25, 0.5, 0.25] and s.be_at_r == pytest.approx(0.65)
        row = em[0][1]
        assert row["p_support"] == pytest.approx(0.8) and row["symbol"] == "AAAUSDT" and "x1.30" in row["reason"]
        assert tp_scale(0.1) == 0.75 and tp_scale(1.0) == 1.5 and tp_scale(0.5) == 1.0

    def test_contradict_or_no_answer_skips(self):
        from app.ai.jev.models import JevOutcome
        ts = T0 + 10 * HOUR - 1
        g, em = self._gate(self._answer("SKIP", 0.7), ts + 1 + 3000)
        assert g(self._sig(ts), self._ctx(ts)).multiplier == 0.0 and em[0][1]["reason"] == "Jev: CONTRADICT"
        g, em = self._gate(lambda state: JevOutcome(False, error_code="TIMEOUT"), ts + 1 + 3000)
        assert g(self._sig(ts), self._ctx(ts)).multiplier == 0.0 and "TIMEOUT" in em[0][1]["reason"]
        g, em = self._gate(self._answer("TAKE", 0.2), ts + 1 + v11.LATE_AFTER_MS + 1)  # too late to ask
        assert g(self._sig(ts), self._ctx(ts)).multiplier == 0.0 and em[0][1]["reason"] == "LATE_DECISION"

    def test_a_restart_re_applies_the_recorded_stretch_without_asking(self):
        ts = T0 + 2 * HOUR - 1
        rec = {("V11.3-SCAN+JEV@AAAUSDT", ts, "long"): {"final_action": "TAKE", "final_level": "TAKE",
                                                        "risk_multiplier": 1.0, "p_support": 0.9}}
        asked = []
        g, em = self._gate(lambda state: asked.append(1), ts + 10 * HOUR, recorded=rec, live_from=T0 + 5 * HOUR)
        s = self._sig(ts)
        assert g(s, self._ctx(ts)).multiplier == 1.0 and not asked and em == []
        assert s.meta["tp_scale"] == 1.4 and round((s.take_profits[2].price - 100.0) / 2.0, 4) == 2.1

    def test_the_state_never_sees_the_future(self):
        from app.ai.jev.state import LookAheadError
        from app.ai.jev.v11 import JevStateBuilderV11
        ts = T0 + 10 * HOUR - 1
        s = self._sig(ts)
        future = [Candle("AAAUSDT", "5m", ts + 1, 1, 1, 1, 1, 1, ts + 5 * MINUTE)]
        with pytest.raises(LookAheadError):
            JevStateBuilderV11().build(t=ts + 1, bot={}, sig=s, series={"5m": future}, execution={}, sizing={},
                                       health={}, position={})



class TestExitFixes:
    """2026-10-01, study only (not adopted: no better per trade over 90 days): TPs from the fill, a fee-paying break-even."""

    def test_take_profits_re_placed_from_the_fill(self):
        import sys
        sys.path.insert(0, "scripts")
        from v11_ladder_study import _exit_fix_engine
        anchored = _exit_fix_engine()[1]
        tps = [TakeProfit(0.0948147, 0.25), TakeProfit(0.0945294, 0.5), TakeProfit(0.0942441, 0.25)]
        new = anchored(tps, 0.0951, 0.0956706, 0.094965, "short")          # the DOGE short: planned 0.09510, filled 0.094965
        r1 = 0.0956706 - 0.094965
        assert [round(t.price, 7) for t in new] == [round(0.094965 - k * r1, 7) for k in (0.5, 1.0, 1.5)]
        assert [t.fraction for t in new] == [0.25, 0.5, 0.25]

    def test_the_live_engine_keeps_the_frozen_behaviour(self):
        from app.core.types import MarketRules
        from app.live.scan_engine import ScanReplayEngine
        rules = {s: MarketRules(s, 0.0001, 0.001, 0.001, 5.0) for s in ("AAAUSDT", ANCHOR)}
        eng = ScanReplayEngine(v11.settings_v11(), ["AAAUSDT", ANCHOR], rules=rules, seed=7)
        assert eng.exits.fee_buffer_bps == 6.0 and not hasattr(eng, "anchor_tps")


class TestBreakEvenPaysFees:
    """2026-10-01: a trade that has hit TP1 must not end below zero after fees."""

    def test_the_engine_moves_the_stop_past_the_fees_when_tp1_prints(self):
        from app.core.types import MarketRules
        from app.live.scan_engine import BE_COVER_BPS, ScanReplayEngine
        rules = {s: MarketRules(s, 0.0001, 0.001, 0.001, 5.0) for s in ("AAAUSDT", ANCHOR)}
        eng = ScanReplayEngine(v11.settings_v11(), ["AAAUSDT", ANCHOR], rules=rules, seed=7, be_cover_bps=BE_COVER_BPS)
        assert eng.exits.fee_buffer_bps == 15.0 and BE_COVER_BPS * 1e-4 > 2 * 0.00055

    @pytest.mark.parametrize("side", ["long", "short"])
    def test_the_ladder_locks_entry_plus_fees_after_tp1(self, side):
        from app.core.positions import ExitEngine
        from app.strategies.v11.ladder import BE_COVER, ladder_class
        d = 1 if side == "long" else -1
        pos, rungs = TestLadder()._pos(side)
        pos.take_profits.pop(0)                                       # TP1 has printed (25% closed)
        pos.qty = 3.0
        ExitEngine({}).apply_update(pos, ladder_class(load_v11_scanners()["V11.3"], rungs)().manage(pos, None, None), 100.0)
        assert pos.stop == pytest.approx(100.0 * (1 + d * BE_COVER))  # TP1 unknown to the position: the fee lock
        # the worst case after TP1: 25% at TP1 (+1R = 2%), 75% at the break-even stop; fees 0.055% in and out
        pnl = 0.25 * 0.02 + 0.75 * BE_COVER
        assert pnl - 2 * 0.00055 > 0

    @pytest.mark.parametrize("side", ["long", "short"])
    def test_the_lock_after_tp1_goes_part_of_the_way_to_tp1(self, side):
        from app.core.positions import ExitEngine
        from app.strategies.v11.ladder import LOCK_AFTER_TP1, ladder_class, tp1_lock
        d = 1 if side == "long" else -1
        pos, rungs = TestLadder()._pos(side)
        pos.meta.update(tp1=pos.take_profits[0].price, tp1_lock=LOCK_AFTER_TP1["V11.3"])   # TP1 is 2 price units away
        pos.take_profits.pop(0)
        pos.qty = 2.8
        ExitEngine({}).apply_update(pos, ladder_class(load_v11_scanners()["V11.3"], rungs)().manage(pos, None, None), 100.0)
        assert pos.stop == pytest.approx(100.0 + d * 2.0 / 3.0)        # a third of the way to TP1
        assert tp1_lock(100.0, side, 100.0 + d * 0.1, 0.5) == pytest.approx(100.0 + d * 0.15)   # never short of the fees

    def test_the_engine_records_tp1_and_locks_after_tp1(self):
        from app.core.types import MarketRules, TakeProfit, VirtualPosition
        from app.live.scan_engine import BE_COVER_BPS, ScanReplayEngine
        from app.strategies.v11.ladder import ladder_class
        rules = {s: MarketRules(s, 0.0001, 0.001, 0.001, 5.0) for s in ("AAAUSDT", ANCHOR)}
        eng = ScanReplayEngine(v11.settings_v11(), ["AAAUSDT", ANCHOR], rules=rules, seed=7, be_cover_bps=BE_COVER_BPS)
        pos = VirtualPosition(id="p", strategy_id="V11.2", symbol="AAAUSDT", side="long", qty=2.6, qty_initial=4.0,
                              entry_price=100.0, entry_ts=T0, leverage=5, margin=80.0, stop=100.15,
                              take_profits=[TakeProfit(104.0, 0.45), TakeProfit(106.0, 0.2)], trail=None, be_at_r=1.0,
                              max_hold_deadline=None, initial_risk_usd=8.0, extreme_price=102.5, tp1_done=True)
        pos.meta.update(tp1=102.0, tp1_lock=0.5, n_tps=3)
        eng.portfolio.restore_position(pos)
        t = T0 + 5 * MINUTE
        eng._walk(Candle("AAAUSDT", "1m", t, 101.5, 101.6, 101.4, 101.5, 5.0, t + MINUTE - 1))
        assert pos.stop == pytest.approx(101.0) and pos.qty == 2.6     # half the way to TP1, nothing closed

        class OneShot(ladder_class(load_v11_scanners()["V11.3"]).for_universe(["AAAUSDT", ANCHOR])):
            def on_candle(self, c, ctx):
                if c.symbol != "AAAUSDT" or c.tf != "5m" or c.close_time != T0 + 10 * MINUTE - 1:
                    return []
                s = self.make_entry(symbol="AAAUSDT", side="long", ts=c.close_time, tf="5m", price=c.close,
                                    stop=c.close * 0.98, tps_r=[(0.5, 0.35), (1.0, 0.45), (1.5, 0.2)])
                s.meta["tp1_lock"] = 0.5
                return [s]
        eng = ScanReplayEngine(v11.settings_v11(), ["AAAUSDT", ANCHOR], rules=rules, seed=7, execution=v11.EXECUTION_V11)

        def tape():
            for m in range(30):
                t = T0 + m * MINUTE
                for s in ("AAAUSDT", ANCHOR):
                    yield Candle(s, "1m", t, 100.0, 100.05, 99.95, 100.0, 5.0, t + MINUTE - 1)
        seen = []
        orig = eng._walk

        def spy(bar):
            orig(bar)
            seen.extend(dict(p.meta) for p in eng.portfolio.positions_on(bar.symbol))
        eng._walk = spy
        eng.run(OneShot, tape(), since_ms=T0, leverage=20, signal_tf="5m", only_symbol=None)
        assert seen and seen[0]["tp1"] == pytest.approx(101.0, rel=2e-3) and seen[0]["tp1_lock"] == 0.5
        assert seen[0]["n_tps"] == 3


class TestLevelFills:
    """2026-10-01: a stop or take-profit the price passes inside a 1m bar fills at its own level, not at the bar's extreme
    or close; a gap through a level still fills at the open; the ladder's stop moves the moment a TP fills."""

    def _run(self, bar, level=True):
        from app.core.types import MarketRules, VirtualPosition
        from app.live.scan_engine import BE_COVER_BPS, ScanReplayEngine
        rules = {s: MarketRules(s, 0.0001, 0.001, 0.001, 5.0) for s in ("AAAUSDT", ANCHOR)}
        eng = ScanReplayEngine(v11.settings_v11(), ["AAAUSDT", ANCHOR], rules=rules, seed=7,
                               execution=v11.EXECUTION_V11, be_cover_bps=BE_COVER_BPS)
        eng.level_fills = level
        eng._fills = []
        eng.portfolio.ensure_wallet("V11.2", 20.0)
        pos = VirtualPosition(id="p", strategy_id="V11.2", symbol="AAAUSDT", side="long", qty=1.0, qty_initial=1.0,
                              entry_price=100.0, entry_ts=T0, leverage=5, margin=20.0, stop=99.0,
                              take_profits=[TakeProfit(102.0, 0.35), TakeProfit(104.0, 0.45), TakeProfit(106.0, 0.2)],
                              trail=None, be_at_r=2.0, max_hold_deadline=None, initial_risk_usd=1.0, extreme_price=100.0)
        pos.meta.update(tp1=102.0, n_tps=3, tp1_lock=0.5)
        eng.portfolio.restore_position(pos)
        eng._walk(Candle("AAAUSDT", "1m", T0 + 5 * MINUTE, *bar, 5.0, T0 + 6 * MINUTE - 1))
        return [(f.kind, round(f.ref_price, 6), round(f.qty, 6)) for f in eng._fills], pos

    def test_a_stop_passed_inside_the_bar_fills_at_the_stop(self):
        fills, _ = self._run((99.8, 100.0, 98.5, 99.5))                       # open, high, low, close
        assert fills == [("stop", 99.0, 1.0)]
        assert self._run((99.8, 100.0, 98.5, 99.5), level=False)[0] == [("stop", 98.5, 1.0)]   # the old: at the low

    def test_a_gap_through_the_stop_fills_at_the_open(self):
        assert self._run((98.7, 99.0, 98.5, 98.8))[0] == [("stop", 98.7, 1.0)]

    def test_tp1_fills_at_tp1_and_the_lock_follows_at_once(self):
        fills, pos = self._run((100.5, 102.6, 100.4, 100.2))                 # TP1, then back below the lock
        assert fills == [("tp", 102.0, 0.35), ("be", 101.0, 0.65)]          # TP1 at 102 (not the 102.6 high); lock 101
        old, opos = self._run((100.5, 102.6, 100.4, 100.2), level=False)
        assert old == [("tp", 102.6, 0.35)] and opos.stop == pytest.approx(101.0)   # the old: at the high, lock at bar end

    def test_tp2_moves_the_stop_onto_tp1(self):
        fills, pos = self._run((100.5, 104.5, 100.4, 104.2))
        assert fills == [("tp", 102.0, 0.35), ("tp", 104.0, 0.45)] and pos.stop == pytest.approx(102.0)


class TestMakerTakeProfits:
    """2026-10-02 (docs/V11_ZAP_STUDY.json): V11's take-profits rest on the book as limit orders -- filled at the TP price
    at the maker fee, and only when price trades 0.5 bp THROUGH the TP (a touch is not a fill)."""

    def _run(self, tp1_high: float, maker: bool = True):
        from app.core.types import MarketRules
        from app.live.scan_engine import BE_COVER_BPS, ScanReplayEngine

        class OneShot(load_v11_scanners()["V11.3"].for_universe(["AAAUSDT", ANCHOR])):
            def on_candle(self, c, ctx):
                if c.symbol != "AAAUSDT" or c.tf != "5m" or c.close_time != T0 + 10 * MINUTE - 1:
                    return []
                return [self.make_entry(symbol="AAAUSDT", side="long", ts=c.close_time, tf="5m", price=c.close,
                                        stop=c.close * 0.98, tps_r=[(0.5, 0.25), (1.0, 0.5), (1.5, 0.25)])]
        rules = {s: MarketRules(s, 0.0001, 0.001, 0.001, 5.0) for s in ("AAAUSDT", ANCHOR)}
        eng = ScanReplayEngine(v11.settings_v11(), ["AAAUSDT", ANCHOR], rules=rules, seed=7, execution=v11.EXECUTION_V11,
                               be_cover_bps=BE_COVER_BPS, maker_tp=maker)

        def tape():
            for m in range(30):
                t = T0 + m * MINUTE
                hi = tp1_high if m == 15 else 100.05
                for s in ("AAAUSDT", ANCHOR):
                    yield Candle(s, "1m", t, 100.0, hi if s == "AAAUSDT" else 100.05, 99.95, 100.0, 5.0, t + MINUTE - 1)
        res = eng.run(OneShot, tape(), since_ms=T0, leverage=20, signal_tf="5m", only_symbol=None)
        return [f for f in res.fills if f.kind == "tp"]

    def test_a_trade_through_fills_at_the_tp_at_the_maker_fee(self):
        fills = self._run(101.5)                                  # TP1 = 101.0 (0.5R of a 2% stop on 100)
        assert len(fills) == 1 and fills[0].price == pytest.approx(101.0, abs=1e-9)
        assert fills[0].fee == pytest.approx(fills[0].qty * 101.0 * 0.0002, rel=1e-6)          # Bybit maker 0.020%
        taker = self._run(101.5, maker=False)[0]
        assert taker.fee > fills[0].fee and taker.price < 101.0 + 1e-9                         # market: taker fee, spread

    def test_a_touch_is_not_a_fill(self):
        assert self._run(101.0) == []                             # exactly at TP1: the resting order may not be reached
        assert self._run(101.0 * (1 + 0.00006)) != []             # 0.6 bp through it: filled
