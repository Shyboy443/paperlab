"""V5 HOURLY / DAILY FUTURES ARENA: positioning features are causal, the universe never sees PnL, the eight families are
documented hourly / swing specialists with no private fee constants, the arena records funding paid and received
separately and never enlarges an order to meet the exchange minimum, the analyzer reads raw edge first (gross R of the
100 USDT twins), then costs, activity and Jev, the exit grid is an honest counterfactual, Jev V5 maps CONTRADICT /
SUPPORT / STRONGLY SUPPORT to SKIP / TAKE / ATTACK and sees no outcome, the pipeline never selects on TEST.
No network: Jev is a stub, the tape and positioning are synthetic."""
from __future__ import annotations

import dataclasses
import random
import re
from pathlib import Path

import pytest

from app.ai.jev import v5 as jv5
from app.ai.jev.models import JevDecision, JevOutcome, SchemaError
from app.ai.jev.state import LookAheadError
from app.competition import v5_analyzer as an
from app.competition import v5_run as vr
from app.competition import v5_universe as uv
from app.competition.v31_diagnostics import Tape
from app.competition.v5_arena import V5Arena, build_record
from app.competition.v5_config import (DEV, TEST, EconomicGate, RawEdgeGate, SizingV5, V5Config, V5Identity,
                                       health_state)
from app.competition.v5_features import HOUR, PositioningFeed
from app.core.storage import Storage
from app.core.types import Candle
from tests.conftest import T0, settings_factory
from tests.test_v31 import SYM, rules, tape

DAY = 24 * HOUR


def feed(days: int = 90, seed: int = 3, start: int = T0) -> tuple[PositioningFeed, list[tuple[int, float]]]:
    rng = random.Random(seed)
    funding = [(start + i * 8 * HOUR, rng.uniform(-0.0004, 0.0008)) for i in range(days * 3)]
    oi, ratio, prem = [], [], []
    level = 1e6
    for h in range(days * 24):
        level *= 1.0 + rng.gauss(0.0, 0.01)
        oi.append((start + h * HOUR, level))
        ratio.append((start + h * HOUR, rng.uniform(0.4, 0.7)))
        prem.append((start + h * HOUR, rng.gauss(0.0, 0.0005)))
    return PositioningFeed(funding=funding, oi=oi, premium=prem, ratio=ratio), funding


def v5_arena(balance_cfg: float = 20.0) -> V5Arena:
    cfg = V5Config(months=(), trade_from="2025-11-01", trade_to="2026-04-30", coins=("SUI",))
    a = V5Arena(settings_factory(balance=20), cfg, rules(), "unused")
    f, funding = feed()
    a._feeds[SYM] = f
    a._funding[SYM] = funding
    return a


def replay(sid: str, horizon: str = "HOURLY", role: str = "CONTROL", balance: float = 20.0, gate=None, bars=None,
           market=None):
    from app.competition import metrics as mx
    from app.live.runner import with_sink
    a = v5_arena()
    if market is not None:
        a.rules = market
    ident = V5Identity(sid, "SUI", horizon, role, balance=balance,
                       jev_policy="JEV_POLICY_V5" if role in ("JEV", "TAKE", "RANDOM") else "")
    cls = a.bound(ident)
    seen: list = []
    live = with_sink(cls, lambda sigs, c: seen.extend((s, c) for s in sigs))
    eng = a.engine(ident, gate)
    bars = bars or BARS
    res = eng.run(live, iter(bars), since_ms=bars[0].close_time + 20 * DAY, leverage=20, signal_tf=cls.signal_tf,
                  only_symbol=SYM)
    m = mx.compute(ident.strategy_id, res.trades, res.fills, res.equity, res.starting_equity, rejects=res.rejects,
                   halted=res.halted, version=ident.key, leverage=res)
    return build_record(ident, cls, res, m, gate, a.cfg, 0.0), seen, cls


BARS = tape(n=75 * 1440, seed=21)


# ---- positioning features ------------------------------------------------------------------------------------

class TestFeatures:
    def test_snapshot_is_causal(self):
        f, _ = feed()
        t = T0 + 40 * DAY + 5 * HOUR
        before = dict(f.snapshot(t))
        g = PositioningFeed(funding=list(zip(f.funding.t, f.funding.v)) + [(t + 1, 0.05)],
                            oi=list(zip(f.oi.t, f.oi.v)) + [(t + HOUR, 9e9)],
                            premium=list(zip(f.premium.t, f.premium.v)) + [(t, 0.5)],
                            ratio=list(zip(f.ratio.t, f.ratio.v)) + [(t + 1, 0.99)])
        assert g.snapshot(t) == before                       # nothing stamped after t, and no premium bar still open

    def test_visibility_rules(self):
        f = PositioningFeed(funding=[(T0, 0.001)], premium=[(T0, 0.002)], oi=[(T0, 5.0)])
        assert "funding_rate" not in f.snapshot(T0 - 1) and f.snapshot(T0)["funding_rate"] == 0.001
        assert "basis" not in f.snapshot(T0 + HOUR - 1)       # the premium bar opened at T0 closes at T0 + 1h
        assert f.snapshot(T0 + HOUR)["basis"] == 0.002

    def test_expected_funding_sign_and_interval(self):
        f = PositioningFeed(funding=[(T0, 0.0001), (T0 + 8 * HOUR, 0.0002)])
        t = T0 + 8 * HOUR
        assert f.funding_interval_h(t) == 8.0
        assert f.expected_funding(t, "long", 24) == pytest.approx(0.0006)       # longs pay a positive rate
        assert f.expected_funding(t, "short", 24) == pytest.approx(-0.0006)     # shorts receive it

    def test_oi_changes(self):
        f = PositioningFeed(oi=[(T0 + h * HOUR, 100.0 + h) for h in range(30)])
        s = f.snapshot(T0 + 29 * HOUR)
        assert s["oi_chg_1h"] == pytest.approx(129 / 128 - 1, abs=1e-5)
        assert s["oi_chg_24h"] == pytest.approx(129 / 105 - 1, abs=1e-5)


# ---- the universe ---------------------------------------------------------------------------------------------------

class TestUniverse:
    def k(self, n, close=1.0, rng=0.03, turnover=5e7):
        return [[T0 + i * DAY, close, close * (1 + rng), close * (1 - rng / 2), close, 1e6, turnover] for i in range(n)]

    def test_market_structure_only(self):
        rule = uv.UniverseRuleV5()
        filt = {"tick": 0.0001, "step": 1.0, "min_qty": 1.0, "min_notional": 5.0, "max_leverage": 25}
        m = uv.measure(self.k(30), self.k(180, rng=0.02), filt, rule)
        m.update({"listed_long_enough": True, "data": {"tape": True, "funding": True, "open_interest": True}})
        assert uv.gates(m, rule) == []
        assert m["take_notional_usdt"] == pytest.approx(20.0)     # 1% risk at the tightest (1%) stop
        young = {**m, "listed_long_enough": False}
        assert uv.gates(young, rule) == ["LISTED_TOO_RECENTLY"]
        costly = uv.measure(self.k(30, close=50000.0), self.k(180, close=50000.0), {**filt, "min_qty": 0.001}, rule)
        costly.update({"listed_long_enough": True, "data": {"tape": True, "funding": True, "open_interest": True}})
        assert "BELOW_EXCHANGE_MINIMUM_AT_20_USDT" in uv.gates(costly, rule) or \
               "QUANTITY_STEP_TOO_COARSE" in uv.gates(costly, rule)

    def test_universe_windows_do_not_overlap(self):
        assert DEV.trade_from > TEST.trade_to and TEST.months[-1] < DEV.trade_from[:7]


# ---- the families ---------------------------------------------------------------------------------------------------

class TestFamilies:
    def test_eight_documented_families_bound_to_a_class(self):
        from app.strategies.registry import load_v5, v5_fingerprints
        fams = load_v5()
        assert sorted(fams) == [f"V5.{i}" for i in range(1, 9)]
        for sid, cls in fams.items():
            for f in ("hypothesis", "thesis", "fails_when", "why_new", "expected_hold", "expected_frequency", "family"):
                assert getattr(cls, f), (sid, f)
            h = cls.for_class("HOURLY")
            s = cls.for_class("SWING")
            assert (h.signal_tf, h.timeframes, h.time_stop_h) == ("1h", ("1h", "4h", "1d"), 24.0)
            assert (s.signal_tf, s.timeframes, s.time_stop_h) == ("4h", ("4h", "1d", "1w"), 72.0)
            assert cls.for_class("HOURLY", time_stop_h=12, target_r=2.0).target_r == 2.0
        assert v5_fingerprints() == v5_fingerprints() and len(v5_fingerprints()) >= 9

    def test_no_private_fee_constants(self):
        src = Path(__file__).resolve().parents[1] / "app" / "strategies" / "v5"
        for p in src.glob("*.py"):
            text = p.read_text(encoding="utf-8")
            assert not re.search(r"\bfees?\b|taker|maker|commission", text, re.I), p.name

    @pytest.mark.parametrize("sid,horizon", [("V5.2", "HOURLY"), ("V5.7", "HOURLY"), ("V5.8", "HOURLY"), ("V5.6", "HOURLY")])
    def test_signals_are_class_bound_with_honest_stops(self, sid, horizon):
        rec, seen, cls = replay(sid, horizon, "CAPACITY", balance=100.0)
        assert seen, "these families signal on the synthetic tape"
        tf = "1h" if horizon == "HOURLY" else "4h"
        for sig, bar in seen:
            m = sig.meta
            assert bar.tf == tf and sig.ts == bar.close_time
            assert 0.008 - 1e-12 <= m["stop_pct"] <= 0.08 + 1e-12
            assert sig.trail is None and not sig.be_at_r and not sig.take_profits
            assert sig.max_hold_s == int(cls.time_stop_h * 3600)
            assert m["horizon"] == horizon and "positioning" in m and m["regime"] in ("WITH", "AGAINST", "FLAT", "UNKNOWN")
            assert sig.id in cls.journal
        for t in rec["trades"]:
            assert t["hold_s"] <= cls.time_stop_h * 3600 + 600


# ---- the arena -------------------------------------------------------------------------------------------------------

class TestArena:
    def test_record_splits_funding_and_fees(self):
        rec, _, _ = replay("V5.2", "HOURLY", "CAPACITY", balance=100.0)
        c, trades = rec["costs"], rec["trades"]
        assert trades, "the synthetic tape should produce trades"
        assert c["maker_fees"] == 0.0 and c["taker_fees"] > 0                     # every order is a MARKET order
        assert c["funding_paid"] == pytest.approx(sum(t["funding_paid"] for t in trades), abs=1e-6)
        assert c["funding_received"] == pytest.approx(sum(t["funding_received"] for t in trades), abs=1e-6)
        for t in trades:
            assert t["funding"] == pytest.approx(t["funding_received"] - t["funding_paid"], abs=1e-6)
            assert t["signal_ts"] and t["signal_ts"] < t["entry_ts"] and t["setup"] and t["stop_pct"]
            assert t["taker_fees"] == pytest.approx(t["fees"], abs=1e-9)
        assert rec["balance"] == 100.0 and rec["exit_plan"] == {"time_stop_h": 24.0, "target_r": None}

    def test_min_notional_is_skipped_never_enlarged(self):
        from app.core.types import MarketRules
        big = {SYM: MarketRules(SYM, 0.0001, 1.0, 1.0, 25.0, 0.025, 6, 2)}     # 25 USDT minimum order value
        a = v5_arena()
        a.rules = big
        ident = V5Identity("V5.2", "SUI", "HOURLY", "CONTROL")
        cls = a.bound(ident)
        eng = a.engine(ident)
        res = eng.run(cls, iter(BARS), since_ms=BARS[0].close_time + 20 * DAY, leverage=20, signal_tf="1h", only_symbol=SYM)
        assert sum(v for k, v in res.rejects.items() if k.startswith("below_min")) > 0
        assert all(f.qty * f.price >= 25.0 - 1e-6 for f in res.fills if f.kind == "entry")
        for f in res.fills:
            if f.kind == "entry":
                assert (f.meta or {}).get("risk_pct", 0) <= 0.0101

    def test_sizing_is_take_and_a_halted_book_takes_nothing(self):
        s = SizingV5()
        assert s(None, {"r": [], "drawdown": 0.0})[0] == 1.0
        assert s(None, {"r": [], "drawdown": 0.31})[0] == 0.0
        assert health_state({"r": [], "drawdown": 0.19})[0] == "NO_ATTACK"


# ---- the analyzer ------------------------------------------------------------------------------------------------------

def trade(i: int, gross_r: float, cost_r: float = 0.1, funding_r: float = 0.0, side: str = "long",
          day: int = 0, risk: float = 0.2, qty: float = 10.0) -> dict:
    sd = risk / qty
    gross, cost, fund = gross_r * risk, cost_r * risk, funding_r * risk
    net_ex = gross - cost
    ts = T0 + day * DAY + i * HOUR
    return {"pid": f"p{i}-{day}", "side": side, "entry_ts": ts, "exit_ts": ts + 8 * HOUR, "hold_s": 8 * 3600, "qty": qty,
            "entry": 2.0, "exit": 2.0 + gross / qty, "gross": gross, "fees": cost * 0.6, "slippage": cost * 0.4,
            "funding": fund, "funding_paid": max(0.0, -fund), "funding_received": max(0.0, fund), "net": net_ex + fund,
            "r": net_ex / risk if abs(net_ex) > 1e-12 else 0.0, "exit_kind": "time", "risk_usd": risk,
            "notional": qty * 2.0, "stop_pct": sd / 2.0, "regime": "WITH", "vol_band": "MID", "setup": "x"}


def rec(trades: list, role: str = "CONTROL", coin: str = "SUI", sid: str = "V5.1", horizon: str = "HOURLY",
        balance: float = 20.0, days: float = 90.0, signals: int | None = None, refused: int = 0, **idk) -> dict:
    ident = V5Identity(sid, coin, horizon, role, balance=balance, **idk)
    nets = [t["net"] for t in trades]
    return {"identity": ident.to_dict(), "key": ident.key, "role": role, "pair_id": ident.pair_id, "family": "F",
            "horizon": horizon, "exit_plan": {"time_stop_h": 24.0, "target_r": None}, "balance": balance,
            "window": {"from": "2025-03-01", "to": "2025-05-29", "days": days}, "trades": trades, "decisions": [],
            "metrics": {"net_profit": sum(nets), "gross_pnl": sum(t["gross"] for t in trades),
                        "slippage_cost": sum(t["slippage"] for t in trades), "max_drawdown_pct": 0.05,
                        "liquidation_count": 0, "net_return_pct": sum(nets) / balance, "trades": len(trades)},
            "costs": {"maker_fees": 0.0, "taker_fees": sum(t["fees"] for t in trades),
                      "funding_paid": sum(t["funding_paid"] for t in trades),
                      "funding_received": sum(t["funding_received"] for t in trades)},
            "activity": {"signals": signals if signals is not None else len(trades) + refused, "entries": len(trades),
                         "below_exchange_minimum": refused, "halted": False}, "equity": []}


def pool(mean: float, n: int = 60, spread: float = 1.0, seed: int = 1, cost: float = 0.1, days: int = 90) -> list:
    rng = random.Random(seed)
    return [trade(i % 20, mean + rng.gauss(0, spread), cost_r=cost, day=int(i * days / n)) for i in range(n)]


class TestAnalyzer:
    def test_r_excludes_then_includes_funding(self):
        t = trade(0, 1.0, cost_r=0.1, funding_r=-0.3)
        assert an.stop_dist(t) == pytest.approx(0.02)
        assert an.gross_r(t) == pytest.approx(1.0) and an.net_r(t) == pytest.approx(0.6)
        assert an.funding_r(t) == pytest.approx(-0.3) and an.cost_r(t) == pytest.approx(0.1)

    def test_raw_edge_gate_reads_gross_and_checks_robustness(self):
        cfg = V5Config(trade_from="2025-03-01", trade_to="2025-05-29",
                       subperiods=(("2025-03-01", "2025-03-30"), ("2025-03-31", "2025-04-29"), ("2025-04-30", "2025-05-29")))
        good = [rec(pool(0.4, n=60, seed=s, days=89), coin=c, balance=100.0, role="CAPACITY") for s, c in ((1, "A"), (2, "B"))]
        good = [dict(r, trades=[dict(t, entry_ts=_ts(t, "2025-03-01")) for t in r["trades"]]) for r in good]
        g = an.edge_test(good, cfg, "gross")
        assert g["passed"], g["checks"]
        assert g["trades"] == 120 and all(x > 0 for x in g["subperiod_mean_r"])
        bad = [rec(pool(-0.1, n=150, spread=0.3, seed=3), coin="A", balance=100.0, role="CAPACITY")]
        b = an.edge_test(bad, cfg, "gross")
        assert not b["passed"] and not b["checks"]["positive_mean"]
        assert an.family_verdict(b, None) == "NO_RAW_EDGE"
        small = an.edge_test([rec(pool(0.4, n=10, seed=4))], cfg, "net")
        assert an.family_verdict(g, small) == "SMALL_ACCOUNT_CONSTRAINED"
        assert an.family_verdict(g, dict(small, passed=False, checks={"sample": True})) == "COST_DESTROYED"

    def test_labels(self):
        cfg = V5Config()
        low = an.analyze_bot(rec(pool(0.6, n=12, spread=0.2)), cfg)
        assert low["label"] == "POSITIVE_EDGE_LOW_ACTIVITY"
        busy = an.analyze_bot(rec(pool(-0.3, n=80, spread=0.2), days=90.0), cfg)
        assert busy["label"] == "ACTIVE_NO_EDGE"
        refused = an.analyze_bot(rec([], refused=9), cfg)
        assert refused["label"] == "MIN_NOTIONAL_LIMITED" and refused["failure_mode"] == "NO_TRADES"

    def test_root_cause_order_and_family_gate(self):
        cfg = V5Config()
        nogross = an.analyze_bot(rec(pool(-0.2, n=60, spread=0.3)), cfg, family={"verdict": "PASSED"})
        assert nogross["failure_mode"] == "NO_RAW_EDGE"
        costly = an.analyze_bot(rec(pool(0.05, n=60, spread=0.02, cost=0.2)), cfg, family={"verdict": "PASSED"})
        assert costly["failure_mode"] == "COST_DESTROYED"
        fine = pool(0.5, n=90, spread=0.3, seed=9)
        healthy = an.analyze_bot(rec(fine), cfg, family={"verdict": "PASSED"})
        assert healthy["state"] == "ADVANCE", [g for g in healthy["gates"] if not g["ok"]]
        orphan = an.analyze_bot(rec(fine), cfg, family={"verdict": "NO_RAW_EDGE"})
        assert orphan["failure_mode"] == "FAMILY_NOT_QUALIFIED" and orphan["state"] == "REJECTED"

    def test_money_waterfall(self):
        r = rec([trade(0, 1.0, funding_r=-0.2), trade(1, -1.0, funding_r=0.1)])
        m = an.money(r)
        assert m["funding_paid"] == pytest.approx(0.04) and m["funding_received"] == pytest.approx(0.02)
        assert m["funding_net"] == pytest.approx(-0.02) and m["maker_fees"] == 0.0
        assert m["net"] == pytest.approx(sum(t["net"] for t in r["trades"]))

    def test_exit_grid_counterfactual(self):
        bars, px = [], 100.0
        path = [100.0] * 60 + [101.0] * 60 + [102.5] * 60 + [101.0] * 300 + [98.5] * 600 + [99.0] * 3000
        for i, p in enumerate(path):
            t = T0 + i * 60_000
            bars.append(Candle(SYM, "1m", t, p, p, p, p, 1.0, t + 59_999))
        tp = Tape(bars)
        t = {"side": "long", "entry": 100.0, "entry_ts": T0, "qty": 1.0, "net": -1.0, "r": -1.0, "funding": 0.0,
             "fees": 0.0, "slippage": 0.0}                    # stop distance 1.0 -> stop 99.0
        funding = [(T0 + 4 * HOUR, 0.001)]                    # one settlement: a long pays 0.1% of 100 = 0.1 R
        g = an.exit_grid([t], tp, funding, [8, 12, 24], [None, 2.0])
        assert g["X8T2"] == [pytest.approx(2.0)]              # 102.5 reaches the 2R target at 2h, before the 4h settlement
        assert g["X8"] == [pytest.approx(1.0 - 0.1)]          # the stop breaks at exactly 8h: the 8h plan closes at 101
        assert g["X12"] == [pytest.approx(-1.0 - 0.1)]        # the 12h plan is stopped (and paid the 4h settlement)
        assert g["X24T2"] == [pytest.approx(2.0)]
        tbl = an.grid_table(g)
        sel = an.select_exit(tbl, "X24", 0.02)
        assert sel["plan"] in tbl and sel["changed"] == (sel["plan"] != "X24")

    def test_select_exit_keeps_baseline_on_a_tie(self):
        tbl = {"X24": {"net_r": 0.10}, "X12": {"net_r": 0.11}, "X8T2": {"net_r": 0.05}}
        assert an.select_exit(tbl, "X24", 0.02)["plan"] == "X24"
        tbl["X12"]["net_r"] = 0.2
        s = an.select_exit(tbl, "X24", 0.02)
        assert (s["plan"], s["time_stop_h"], s["target_r"], s["changed"]) == ("X12", 12.0, None, True)

    def test_viability_and_totals(self):
        recs = [rec(pool(0.2, n=40)), rec(pool(-0.2, n=40, seed=5), coin="B"),
                rec(pool(0.1, n=10, seed=6), horizon="SWING", coin="C")]
        v = an.viability(recs, {})
        assert [r["horizon"] for r in v] == ["HOURLY", "SWING"] and v[0]["bots"] == 2 and v[0]["trades"] == 80
        tot = an.totals(recs)
        assert tot["maker_fees"] == 0.0 and tot["taker_fees"] > 0 and tot["trades"] == 90

    def test_maker_bound_is_positive_and_small(self):
        t = trade(0, 0.5)
        b = an.maker_bound_r(t, 0.0002, 0.00055)
        assert 0 < b < 0.2


def _ts(t: dict, start: str) -> int:
    import datetime as dt
    s = int(dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    return s + (int(t["entry_ts"]) - T0)


# ---- Jev V5 ---------------------------------------------------------------------------------------------------------------

def decision(choice: str) -> JevDecision:
    probs = {"SKIP": 0.1, "TAKE": 0.1, "ATTACK": 0.1}
    probs[choice] = 0.8
    return JevDecision(take_probability=1 - probs["SKIP"], risk_state=choice, risk_probabilities=probs, setup_quality=0.0,
                       quality_score=0.0, quality_probabilities={}, regime="", regime_probabilities={},
                       model_resolved="m", provider="p", request_id="r", input_tokens=0, output_tokens=0, cost_usd=0.0)


class TestJevV5:
    def body(self, choice):
        p = {"CONTRADICT": 0.1, "SUPPORT": 0.1, "STRONGLY_SUPPORT": 0.1}
        p[choice] = 0.8
        return {"answers": {"conditions": {"type": "choice", "choice": choice, "probabilities": p}}}

    def test_parse_and_mapping(self):
        assert jv5.parse_decision_v5(self.body("CONTRADICT")).risk_state == "SKIP"
        assert jv5.parse_decision_v5(self.body("SUPPORT")).risk_state == "TAKE"
        assert jv5.parse_decision_v5(self.body("STRONGLY_SUPPORT")).risk_state == "ATTACK"
        with pytest.raises(SchemaError):
            jv5.parse_decision_v5({"answers": {"conditions": {"type": "choice", "choice": "DEFENSIVE"}}})

    def test_policy(self):
        assert jv5.decide_v5(decision("ATTACK"), "OK").level == "ATTACK"
        assert jv5.decide_v5(decision("ATTACK"), "NO_ATTACK").level == "TAKE"
        assert jv5.decide_v5(None, "OK", error_code="TIMEOUT").level == "SKIP"
        assert dict(jv5.POLICY_V5.multipliers) == {"SKIP": 0.0, "TAKE": 1.0, "ATTACK": 2.0}

    def test_attack_that_is_not_legal_becomes_take(self):
        lvl, mult, why = jv5._legal("ATTACK", 2.0, object(), lambda s, m: (False, "below_min"), jv5.POLICY_V5)
        assert (lvl, mult) == ("TAKE", 1.0) and why.startswith("ATTACK_NOT_LEGAL")

    def test_state_is_causal_and_outcome_free(self):
        rec_, seen, cls = replay("V5.2", "HOURLY", "CAPACITY", balance=100.0)
        assert seen
        sig, bar = seen[len(seen) // 2]
        series = {tf: [c for c in _agg(BARS, tf) if c.close_time <= bar.close_time] for tf in ("1h", "4h", "1d")}
        b = jv5.JevStateBuilderV5()
        bot = {"strategy_id": "V5.2", "horizon": "HOURLY", "signal_tf": "1h", "family_raw_edge_r": 0.12}
        snap = b.build(ts=bar.close_time, bot=bot, sig=sig, series=series,
                       execution={"taker_fee": 0.00055, "half_spread_bps": 1.0, "expected_slippage_bps": 1.0},
                       health={"drawdown": 0.02}, position={})
        s = snap.state
        assert set(s) == {"bot", "setup", "trend", "positioning", "volatility", "economics", "execution", "bot_health",
                          "state_version"}
        assert s["economics"]["family_raw_edge_r"] == 0.12 and s["economics"]["round_trip_cost_r"] > 0
        assert s["positioning"]["liquidations"].startswith("not available")
        def keys(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    yield str(k).lower()
                    yield from keys(v)
            elif isinstance(x, list):
                for v in x:
                    yield from keys(v)
        for k in keys(s):
            assert not any(b in k for b in ("outcome", "result", "pnl", "rank", "holdout", "future", "exit_price")), k
        late = dict(series)
        late["1h"] = series["1h"] + [Candle(SYM, "1h", bar.close_time + 1, 1, 1, 1, 1, 1.0, bar.close_time + HOUR)]
        with pytest.raises(LookAheadError):
            b.build(ts=bar.close_time, bot=bot, sig=sig, series=late, execution={}, health={}, position={})

    def test_divergence_labels(self):
        assert jv5.divergence(0.03, 0.05).startswith("PRICE_UP_OI_UP")
        assert jv5.divergence(0.03, -0.05).startswith("PRICE_UP_OI_DOWN")
        assert jv5.divergence(-0.03, 0.05).startswith("PRICE_DOWN_OI_UP")
        assert jv5.divergence(None, 0.1) is None

    def test_random_twin_matches_the_distribution_and_is_seeded(self):
        dist = {"SKIP": 0.3, "TAKE": 0.5, "ATTACK": 0.2}
        g1 = jv5.PolicyGateV5("RANDOM", "bot", seed=3, distribution=dist)
        g2 = jv5.PolicyGateV5("RANDOM", "bot", seed=3, distribution=dist)

        class S:
            side, meta = "long", {}
        ctx = lambda ts: {"ts": ts, "health": {"r": [], "drawdown": 0.0}}  # noqa: E731
        a = [g1(S(), ctx(T0 + i * HOUR)).info["jev_level"] for i in range(3000)]
        b = [g2(S(), ctx(T0 + i * HOUR)).info["jev_level"] for i in range(3000)]
        assert a == b
        assert abs(a.count("SKIP") / 3000 - 0.3) < 0.04 and abs(a.count("ATTACK") / 3000 - 0.2) < 0.04

    def test_jev_gate_end_to_end_with_a_stub(self, tmp_path):
        st = Storage(str(tmp_path / "j.db"))

        class Stub:
            def decide(self, key, snap):
                ch = "SKIP" if snap.state["setup"]["side"] == "short" else "ATTACK"
                return JevOutcome(True, decision=decision(ch), latency_ms=3), "api"
        ident = V5Identity("V5.2", "SUI", "HOURLY", "JEV", jev_policy="JEV_POLICY_V5")
        bot = {"key": ident.key, "strategy_id": "V5.2", "params_version": "v5", "symbol": SYM, "timeframe": "1h",
               "signal_tf": "1h", "horizon": "HOURLY", "control_version": "c"}
        gate = jv5.JevGateV5(Stub(), st, "v5-test000000", bot, ident.pair_id, "stub/model")
        from app.core.types import MarketRules
        small = {SYM: MarketRules(SYM, 0.0001, 0.1, 0.1, 1.0, 0.025, 6, 2)}      # a 1 USDT minimum: 20 USDT can trade
        r, _, _ = replay("V5.2", "HOURLY", "JEV", gate=gate, market=small)
        levels = {d["level"] for d in r["decisions"]}
        assert r["decisions"] and levels <= {"SKIP", "TAKE", "ATTACK"}
        assert all(d["level"] == "SKIP" for d in r["decisions"] if d["side"] == "short")
        assert all(t["side"] == "long" for t in r["trades"])
        n = st.conn.execute("SELECT COUNT(*) FROM jev_decisions WHERE run_id='v5-test000000'").fetchone()[0]
        assert n == len(r["decisions"])

    def test_fingerprints(self):
        fp = jv5.v5_fingerprints()
        assert set(fp) == {"prompt", "policy", "state_version", "prompt_version", "state_builder"}
        assert fp == jv5.v5_fingerprints()


def _agg(bars, tf):
    from app.core.types import tf_ms
    span = tf_ms(tf)
    out, cur = [], None
    for b in bars:
        start = b.open_time - b.open_time % span
        if cur is None or cur[0] != start:
            if cur is not None and cur[5] == span // 60_000:
                out.append(Candle(SYM, tf, cur[0], cur[1], cur[2], cur[3], cur[4], 1.0, cur[0] + span - 1))
            cur = [start, b.open, b.high, b.low, b.close, 0]
        cur[2], cur[3], cur[4] = max(cur[2], b.high), min(cur[3], b.low), b.close
        cur[5] += 1
    return out


# ---- the pipeline -----------------------------------------------------------------------------------------------------------

class TestRun:
    def cfg(self, **k):
        return V5Config(trade_from="2025-03-01", trade_to="2025-05-29", months=(), coins=("A", "B"),
                        subperiods=(("2025-03-01", "2025-03-30"), ("2025-03-31", "2025-04-29"), ("2025-04-30", "2025-05-29")),
                        **k)

    def test_jobs(self):
        cfg = self.cfg()
        jobs = vr.stage1_jobs(cfg, "v5-x")
        assert len(jobs) == 8 * 2 * 2 * 2
        assert {j["ident"]["role"] for j in jobs} == {"CONTROL", "CAPACITY"}
        assert {j["ident"]["balance"] for j in jobs if j["ident"]["role"] == "CAPACITY"} == {100.0}
        assert vr._exit_kw(None) == {"time_stop_h": 0.0, "target_r": 0.0}
        assert vr._exit_kw({"changed": False, "time_stop_h": 24}) == {"time_stop_h": 0.0, "target_r": 0.0}
        sel = {"changed": True, "time_stop_h": 12.0, "target_r": 2.0}
        s2 = vr.stage2_jobs(cfg, "v5-x", {"survivors": [{"strategy_id": "V5.2", "horizon": "SWING", "exit": sel}]})
        keys = {V5Identity(**j["ident"]).key for j in s2}
        assert keys == {f"V5.2-{c}-S-X12T2-{r}@20x" for c in ("A", "B") for r in ("CONTROL", "CAP50", "CAP100")}
        tw = vr.twin_jobs({"pairs": [{"strategy_id": "V5.2", "coin": "A", "horizon": "SWING", "exit": sel}]}, "v5-x", "jev")
        assert V5Identity(**tw[0]["ident"]).key == "V5.2-A-S-X12T2-JEV5@20x"

    def test_analysis_pipeline_on_stored_books(self, tmp_path):
        cfg = self.cfg(raw_edge=RawEdgeGate(min_trades=(("HOURLY", 50), ("SWING", 20))),
                       economic=EconomicGate(max_p_mean_le_0=0.10, min_positive_subperiods=2))
        st = Storage(str(tmp_path / "p.db"))
        run_id = vr.new_run(st, cfg, "t")

        def dated(trs):
            return [dict(t, entry_ts=_ts(t, "2025-03-01"), exit_ts=_ts(t, "2025-03-01") + 8 * HOUR) for t in trs]
        for i, coin in enumerate(("A", "B")):
            st.v5_bot_save(run_id, rec(dated(pool(0.5, n=60, seed=i, spread=0.5, days=88)), coin=coin, days=90.0))
            st.v5_bot_save(run_id, rec(dated(pool(0.5, n=60, seed=10 + i, spread=0.5, days=88)), coin=coin, role="CAPACITY",
                                       balance=100.0, days=90.0))
            st.v5_bot_save(run_id, rec(dated(pool(-0.3, n=60, seed=20 + i, days=88)), coin=coin, sid="V5.2", days=90.0))
            st.v5_bot_save(run_id, rec(dated(pool(-0.3, n=60, seed=30 + i, days=88)), coin=coin, sid="V5.2", role="CAPACITY",
                                       balance=100.0, days=90.0))
        settings = settings_factory(balance=20, data_dir=str(tmp_path / "none"))
        field = vr.analyze_stage1(st, run_id, cfg, settings)
        fams = {f"{f['strategy_id']} {f['horizon']}": f for f in field["families"]}
        assert fams["V5.1 HOURLY"]["raw_edge"]["passed"] and not fams["V5.2 HOURLY"]["raw_edge"]["passed"]
        assert [s["strategy_id"] for s in field["survivors"]] == ["V5.1"]
        assert field["survivors"][0]["exit"]["plan"] == "X24" and not field["survivors"][0]["exit"]["changed"]
        field = vr.analyze_stage2(st, run_id, cfg)
        assert field["economic_passed"] == ["V5.1|HOURLY"] and {p["coin"] for p in field["pairs"]} == {"A", "B"}
        s = vr.analyze_run(st, run_id, cfg, settings)
        assert s["counts"]["controls"] == 4 and s["advanced_set"] in ("NONE", "PENDING PSEUDO-HOLDOUT")
        verdicts = {f"{f['strategy_id']} {f['horizon']}": f["verdict"] for f in s["families"]}
        assert verdicts["V5.1 HOURLY"] == "PASSED" and verdicts["V5.2 HOURLY"] == "NO_RAW_EDGE"
        assert "raw edge" in s["answers"]["raw_edge"] and s["answers"]["jev"].startswith("Jev beat") is False
        v2 = [r for r in s["leaderboard_controls"] if r["strategy_id"] == "V5.2"]
        assert all(r["failure_mode"] == "NO_RAW_EDGE" for r in v2)

    def test_test_pairs_come_from_development_never_from_test_outcomes(self, tmp_path):
        cfg = self.cfg(raw_edge=RawEdgeGate(min_trades=(("HOURLY", 50), ("SWING", 20))))
        st = Storage(str(tmp_path / "q.db"))
        run_id = vr.new_run(st, dataclasses.replace(cfg, dataset_role="TEST"), "t")
        for coin in ("A", "B"):
            st.v5_bot_save(run_id, rec(pool(-0.5, n=40, seed=1), coin=coin))           # losing on TEST
            st.v5_bot_save(run_id, rec(pool(-0.5, n=40, seed=2), coin=coin, role="CAPACITY", balance=100.0))
        tcfg = dataclasses.replace(cfg, dataset_role="TEST")
        settings = settings_factory(balance=20, data_dir=str(tmp_path / "none"))
        frozen = {"survivors": [{"strategy_id": "V5.1", "horizon": "HOURLY", "exit": {"plan": "X24", "changed": False}}]}
        field = vr.analyze_stage1(st, run_id, tcfg, settings, frozen)
        assert field["survivors"] == frozen["survivors"] and "exit_grid" not in field["families"][0]
        field = vr.analyze_stage2(st, run_id, tcfg, {"V5.1|HOURLY": {"raw_edge_r": 0.3}})
        assert {p["coin"] for p in field["pairs"]} == {"A", "B"}                    # DEV decided, TEST lost: still paired
        assert field["pairs"][0]["family_edge"] == {"raw_edge_r": 0.3}              # DEVELOPMENT evidence only

    def test_frozen_guard(self, tmp_path, monkeypatch):
        import scripts.run_v5_arena as script
        (tmp_path / "V5_RESULTS_FREEZE.md").write_text("| `v5-0123456789` | frozen |\n", encoding="utf-8")
        monkeypatch.setattr(script, "DOCS", tmp_path)
        assert script.frozen_runs() == {"v5-0123456789"}

    def test_config_fingerprint_covers_the_exit_rule(self):
        a = V5Config()
        assert a.fingerprint() != dataclasses.replace(a, exit_min_improvement_r=0.05).fingerprint()
        assert a.fingerprint(ignore_dataset=True) == dataclasses.replace(a, dataset_fingerprint="x").fingerprint(ignore_dataset=True)
        assert dict(a.exit_time_stops_by_class) == {"HOURLY": (8, 12, 24), "SWING": (12, 24, 48, 72)}


class TestStorage:
    def test_v5_tables_are_separate(self, tmp_path):
        st = Storage(str(tmp_path / "s.db"))
        tables = {r[0] for r in st.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"v5_runs", "v5_bots"} <= tables and {"v4_runs", "v4_bots"} <= tables
        run_id = vr.new_run(st, V5Config(coins=("A",)), "t")
        r = rec(pool(0.1, n=5))
        st.v5_bot_save(run_id, r)
        back = st.v5_bots(run_id, heavy=True)[0]
        assert back["key"] == r["key"] and len(back["trades"]) == 5 and st.v5_bot_keys(run_id) == {r["key"]}
        assert not st.conn.execute("SELECT COUNT(*) FROM v4_bots").fetchone()[0]


class TestReport:
    def test_v5_report_section_renders_verdicts_and_waterfall(self, tmp_path):
        from app.core import report_v5 as rv
        from app.core.system_view import v5_payload
        st = Storage(str(tmp_path / "r.db"))
        assert "No V5 run yet" in rv.v5_html(v5_payload(st))
        cfg = V5Config(trade_from="2025-03-01", trade_to="2025-05-29", months=(), coins=("A",))
        run_id = vr.new_run(st, cfg, "t")
        fam = {"strategy_id": "V5.8", "family": "MOMENTUM", "horizon": "HOURLY", "verdict": "NO_RAW_EDGE",
               "raw_edge": {"trades": 207, "mean_r": 0.129, "p_mean_le_0": 0.13, "subperiod_mean_r": [0.05, 0.29, None],
                            "passed": False}, "economic_20_baseline": {"trades": 115, "mean_r": 0.07},
               "costs_100": {"cost_r": 0.057, "funding_r": -0.005}}
        summary = {"run_id": run_id, "dataset_role": "DEVELOPMENT", "advanced_set": "NONE", "families": [fam],
                   "answers": {"raw_edge": "NO family x class has a raw edge"}, "counts": {"controls": 1},
                   "window": {"from": "2025-03-01", "to": "2025-05-29"}, "coins": ["A"],
                   "viability": [{"horizon": "HOURLY", "bots": 1, "trades": 3, "pf": 0.9, "qualified": 0}],
                   "costs": {"controls_20": {"gross": 1.5, "maker_fees": 0.0, "taker_fees": 2.0, "slippage": 1.0,
                                             "funding_paid": 0.2, "funding_received": 0.3, "net": -1.4, "trades": 3}},
                   "leaderboard_controls": [], "pairs": []}
        st.v5_run_update(run_id, summary_json=summary, status="complete")
        d = v5_payload(st)
        html, text = rv.v5_html(d), "\n".join(rv.v5_text(d))
        assert "ADVANCED SET: NONE" in html and "V5.8 MOMENTUM" in html and "funding received" in html
        assert "NO_RAW_EDGE" in html and "+0.129R" in html
        assert "ADVANCED SET NONE" in text and "net -1.4 USDT" in text
