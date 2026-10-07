"""THE ROSTER: stable unique bot names, twins sharing their control's name, and the stage rule (in profit after costs,
enough trades, not eliminated, twins shown once, closest bots fill a thin stage). Display only; no network."""
from __future__ import annotations

from app.core import roster_view as rv
from app.core.bot_names import NAMES, assign, control_of, describe


class NoStore:
    class conn:
        @staticmethod
        def execute(*a):
            class R:
                def fetchone(self):
                    return None

                def fetchall(self):
                    return []
            return R()


def _row(key, net, trades, status="ACTIVE", start=20.0, coin="ENA", sid=None):
    return {"key": key, "net_now": net, "trades": trades, "program_status": status, "start_equity": start,
            "equity_now": start + net, "coin": coin, "strategy_id": sid or key.split("-")[0], "timeframe": "5m",
            "role": control_of(key)[1], "curve": [], "open_positions": []}


def _build(payloads):
    saved = rv.program_payloads                       # restored: later test files build the real roster
    rv.program_payloads = lambda state: {p: ({"experiment": {"experiment_id": p + "x"}, "leaderboard": rows}, NoStore())
                                         for p, rows in payloads.items()}
    try:
        return rv.build(None, now_ms=1)
    finally:
        rv.program_payloads = saved


def test_names_are_unique_stable_and_shared_by_twins():
    controls = [("v8", f"V8.{f}-{c}-5M") for f in (1, 2, 3) for c in ("ETH", "SOL", "XRP", "DOGE", "ARB", "ENA")]
    a, b = assign(controls), assign(list(reversed(controls)))
    assert a == b and len(set(a.values())) == len(controls) and set(a.values()) <= set(NAMES)
    names = assign([("v8", "V8.3-ENA-5M")])
    base = names[("v8", "V8.3-ENA-5M")]
    assert describe("v8", {"key": "V8.3-ENA-5M+JEV", "strategy_id": "V8.3", "coin": "ENA"}, names)["name"] == base + " AI"
    d = describe("v8", {"key": "V8.3-ENA-5M+LADDER", "strategy_id": "V8.3", "coin": "ENA"}, names)
    assert d["name"] == base + " Ladder" and d["persona"] == "the snapper" and "three steps" in d["job"]
    assert describe("v11", {"key": "V11.4-SCAN", "strategy_id": "V11.4", "coin": "ALL"},
                    assign([("v11", "V11.4-SCAN")]))["where"] == "30 coins"


def test_champion_names_match_the_lovable_dashboard():
    # the same FNV-1a slot the Lovable app's JavaScript picked, so a bot keeps the champion the user saw there
    from app.core.bot_names import _fnv1a
    assert _fnv1a("") == 2166136261 and _fnv1a("a") == 0xE40C292C          # FNV-1a 32-bit reference values
    names = assign([("v6", "V6.6-XRP-1H"), ("v8", "V8.3-ETH-5M"), ("v14", "V14.1-ETH-5M")])
    assert names[("v6", "V6.6-XRP-1H")] == "Lux" and names[("v8", "V8.3-ETH-5M")] == "Lulu"
    assert names[("v14", "V14.1-ETH-5M")] == "Lulu HTF"                    # a V14 copy carries its original's name


def test_the_stage_takes_winners_with_enough_trades_best_first():
    out = _build({
        "v8": [_row("V8.3-ENA-5M", 1.0, 35), _row("V8.3-ENA-5M+JEV", 1.0, 35),          # identical twin: shown once
               _row("V8.1-ETH-5M", 2.0, 2),                                               # too few: "new" slot
               _row("V8.2-SOL-5M", 3.0, 10, status="ELIMINATED"),                         # eliminated
               _row("V8.2-ARB-5M", -0.5, 20)],                                            # losing
        "v9": [_row("V9.3-NVDA-5M", 19.5, 9, start=1000.0, coin="NVDA")],
        "v7": [_row("V7.1-ENA-15M", 0.9, 8), _row("V7.2-XRP-15M", 0.1, 3)]})
    stage = [(x["program"], x["key"]) for x in out["roster"]]
    assert stage == [("v8", "V8.3-ENA-5M"), ("v7", "V7.1-ENA-15M"), ("v9", "V9.3-NVDA-5M"), ("v7", "V7.2-XRP-15M"),
                     ("v8", "V8.1-ETH-5M")]
    assert [x["rising"] for x in out["roster"]] == [False, False, False, False, True]
    assert out["roster"][0]["rank"] == 1 and out["roster"][0]["behind"] == 0 and out["roster"][2]["currency"] == "USD"
    assert out["kpis"]["on_stage"] == 5 and out["kpis"]["retired"] == 3 and out["kpis"]["bots_total"] == 8
    assert {r["key"] for r in out["retired"]} == {"V8.3-ENA-5M+JEV", "V8.2-SOL-5M", "V8.2-ARB-5M"}
    assert len(out["names"]) == 8 and all("_st" not in x for x in out["roster"])


def test_a_thin_stage_is_filled_by_the_closest_bots_and_scanners_are_always_shown():
    out = _build({"v8": [_row("V8.3-SOL-5M", -0.05, 41), _row("V8.2-XRP-5M", -0.86, 12), _row("V8.1-ETH-5M", -0.29, 1)],
                  "v7": [_row("V7.1-ENA-15M", 0.9, 8)],
                  "v11": [_row("V11.3-SCAN", -0.37, 45, coin="ALL", sid="V11.3"),
                          _row("V11.1-SCAN", -0.29, 1, coin="ALL", sid="V11.1")]})
    assert [(x["key"], x["chasing"]) for x in out["roster"]] == [("V7.1-ENA-15M", False), ("V8.3-SOL-5M", True),
                                                                  ("V8.2-XRP-5M", True)]
    assert [(x["key"], x["rank"], x["pinned"]) for x in out["scanners"]] == [("V11.1-SCAN", 1, True), ("V11.3-SCAN", 2, True)]
    assert not {r["key"] for r in out["retired"]} & {"V11.1-SCAN", "V11.3-SCAN"} and out["kpis"]["scanners"] == 2
    assert "25% / 50% / 25%" in out["scanners"][0]["job"]


def test_after_a_restart_newer_bots_in_profit_fill_the_free_places():
    out = _build({"v8": [_row("V8.3-ARB-5M", 0.21, 3), _row("V8.3-SOL-5M", 0.30, 1), _row("V8.3-SOL-5M+LADDER", 0.30, 1),
                         _row("V8.3-ETH-5M", 0.25, 2), _row("V8.3-XRP-5M", -0.10, 1), _row("V8.3-ENA-5M", 0.0, 0)],
                  "v7": [_row("V7.1-ENA-15M", -0.17, 1)]})
    assert [(x["key"], x["chasing"], x["rising"]) for x in out["roster"]] == [
        ("V8.3-ARB-5M", False, False), ("V8.3-SOL-5M", True, True), ("V8.3-ETH-5M", True, True)]
    assert out["kpis"]["in_profit"] == 3 and out["roster"][0]["rank"] == 1
    assert {"V8.3-SOL-5M+LADDER", "V8.3-XRP-5M", "V8.3-ENA-5M", "V7.1-ENA-15M"} == {r["key"] for r in out["retired"]}


def test_the_v6_backtest_winners_are_always_on_screen():
    out = _build({"v6": [_row("V6.6-ARB-1H", 0.0, 0, coin="ARB", sid="V6.6"), _row("V6.6-XRP-1H", 0.0, 0, coin="XRP", sid="V6.6"),
                         _row("V6.2-XRP-1H", 0.0, 0, coin="XRP", sid="V6.2"), _row("V6.1-XRP-4H", 0.0, 0, coin="XRP", sid="V6.1"),
                         _row("V6.6-XRP-1H+JEV", 0.0, 0, coin="XRP", sid="V6.6")]})
    assert [x["key"] for x in out["ready"]] == ["V6.6-XRP-1H", "V6.6-ARB-1H"]        # best two-year backtest first
    assert out["ready"][0]["ready"] and out["ready"][0]["backtest"]["return_pct"] > out["ready"][1]["backtest"]["return_pct"]
    assert {r["key"] for r in out["retired"]} == {"V6.2-XRP-1H", "V6.1-XRP-4H", "V6.6-XRP-1H+JEV"}


def test_the_roster_route_is_get_only():
    from app.core import api_public
    routes = [r for r in api_public.router.routes if r.path.endswith("/roster")]
    assert len(routes) == 1 and set(routes[0].methods) <= {"GET", "HEAD"}


def test_one_trade_idea_is_one_row_with_every_bot_that_saw_it():
    """Zap TAKE + Zap AI SKIP at 18:10, Zap's fill at 18:12, its close at 18:40: ONE idea, two bots, not three trades."""
    from app.core.roster_view import trade_ideas
    t = 1_790_791_799_999
    pair = "v11:V11.3-SCAN"
    ev = [
        {"program": "v11", "kind": "decision", "ts": t + 4000, "bot_key": "V11.3-SCAN", "name": "Zap", "pair_id": pair,
         "symbol": "XPLUSDT", "side": "long", "signal_ts": t, "final_level": "TAKE", "reason": "CONTROL"},
        {"program": "v11", "kind": "decision", "ts": t + 4000, "bot_key": "V11.3-SCAN+JEV", "name": "Zap AI", "pair_id": pair,
         "symbol": "XPLUSDT", "side": "long", "signal_ts": t, "final_level": "SKIP", "reason": "Jev: CONTRADICT", "p_support": 0.03},
        {"program": "v11", "kind": "open", "ts": t + 120_000, "bot_key": "V11.3-SCAN", "name": "Zap", "pair_id": pair,
         "symbol": "XPLUSDT", "side": "long", "open_ts": t + 60_001, "price": 0.58},
        {"program": "v11", "kind": "closed", "ts": t + 1_800_000, "bot_key": "V11.3-SCAN", "name": "Zap", "pair_id": pair,
         "symbol": "XPLUSDT", "side": "long", "entry_ts": t + 60_001, "exit_ts": t + 1_790_000, "net": 0.12, "r_net": 0.5,
         "exit_kind": "tp"},
        {"program": "v8", "kind": "decision", "ts": t, "bot_key": "V8.3-ENA-5M", "name": "Gamma", "pair_id": "v8pair:V8.3-ENA-5M",
         "symbol": "ENAUSDT", "side": "short", "signal_ts": t - 600_000, "final_level": "TAKE", "reason": "CONTROL"},
    ]
    ideas = trade_ideas(ev)
    assert len(ideas) == 2
    xpl = ideas[0]
    assert (xpl["symbol"], xpl["side"], xpl["ts"]) == ("XPLUSDT", "long", t + 1)
    assert [b["name"] for b in xpl["bots"]] == ["Zap", "Zap AI"]
    zap, twin = xpl["bots"]
    assert zap["verdict"] == "TAKE" and zap["open_price"] == 0.58 and zap["net"] == 0.12 and zap["exit_kind"] == "tp"
    assert twin["verdict"] == "SKIP" and twin["p_support"] == 0.03 and "net" not in twin
    assert ideas[1]["symbol"] == "ENAUSDT" and len(ideas[1]["bots"]) == 1
