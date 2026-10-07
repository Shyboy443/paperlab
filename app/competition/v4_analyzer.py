"""BOT ANALYZER V4: is there a raw edge, what do the costs take, what do the exits do, and does Jev select?

Per bot:  ACTIVITY and the FUNNEL (raw setups -> legal -> positive expected edge -> Jev -> executed),
          EDGE (gross vs net expectancy, PF, drawdown, bootstrap P(mean <= 0)), EDGE QUALITY (edge per trade,
          per unit of turnover, per fee dollar, per day), CONCENTRATION (without the best 3, top-3 share),
          EXITS (the exit analyzer: MFE / MAE, profit left, stopped-then-favourable vs random entries,
          post-exit drift, hold buckets), JEV (actions, AUC, SELECTION ALPHA vs the matched random action),
          ATTACK (ATTACK trades vs the same trades at normal size), the V4 GATES, the diagnoses and ONE
          primary failure in root-cause order (no raw edge -> costs -> drawdown -> activity -> concentration).

It only reads finished bots; it never edits a strategy, a threshold or the edge model.
"""
from __future__ import annotations

import random
import statistics
from collections import defaultdict
from typing import Any, Mapping, Sequence

from app.competition import exit_analyzer as ex
from app.competition import v3_analyzer as a3
from app.competition import v31_analyzer as a31
from app.competition.v4_config import MIN_SAMPLE, PARTICIPATION_PER_DAY, TIER_RISK, V4Config, min_trades
from app.competition.v31_edge import calibration

_r, _mean, _pct, _pf = a3._r, a3._mean, a3._pct, a3._pf
concentration, cost_structure, jev_block, selection_alpha = (a31.concentration, a31.cost_structure, a31.jev_block,
                                                             a31.selection_alpha)
funnel_totals = a31.funnel_totals

LABEL = {"LIQUIDATION": "LIQUIDATION", "MIN_NOTIONAL_LIMITED": "MIN NOTIONAL LIMITED", "NO_TRADES": "NO TRADES",
         "NO_RAW_EDGE": "NO RAW EDGE (gross <= 0)", "COST_DESTROYED": "EDGE DESTROYED BY COSTS",
         "EXIT_DESTROYS_EDGE": "EXIT DESTROYS EDGE", "DRAWDOWN_FAILURE": "DRAWDOWN", "TOO_LITTLE_ACTIVITY": "TOO LITTLE ACTIVITY",
         "INSUFFICIENT_SAMPLE": "INSUFFICIENT SAMPLE", "PROFIT_CONCENTRATED": "PROFIT CONCENTRATED",
         "NOT_SIGNIFICANT": "NOT DISTINGUISHABLE FROM ZERO", "MARGINAL_EDGE": "MARGINAL EDGE",
         "JEV_OVER_FILTERING": "JEV OVER FILTERED", "JEV_NO_SELECTION_ALPHA": "JEV NO SELECTION ALPHA", "ROBUST": "HEALTHY"}


def activity(rec: Mapping[str, Any]) -> dict[str, Any]:
    days = float(rec["window"]["days"]) or 1.0
    tf = rec["identity"]["timeframe"]
    trades = rec.get("trades") or []
    a = rec["activity"]
    tpd = len(trades) / days
    need = PARTICIPATION_PER_DAY[tf]
    return {"days": days, "raw_setups": a["signals"], "raw_setups_per_day": _r(a["signals"] / days, 3),
            "trades": len(trades), "trades_per_day": _r(tpd, 3), "required_per_day": need,
            "participation_ok": tpd >= need, "adequate_sample": len(trades) >= MIN_SAMPLE,
            "min_trades": min_trades(tf, days), "avg_hold_min": _r(_mean([t["hold_s"] / 60.0 for t in trades]), 1),
            "below_exchange_minimum": a.get("below_exchange_minimum", 0), "edge_rejected": a.get("edge_rejected", 0),
            "halted": bool(a.get("halted"))}


def p_mean_le_zero(rs: Sequence[float], n_boot: int = 2000, seed: int = 7) -> float | None:
    """Bootstrap probability that the mean net R is <= 0 (one-sided)."""
    if len(rs) < 2:
        return None
    rng = random.Random(seed)
    n = len(rs)
    return sum(1 for _ in range(n_boot) if sum(rng.choices(rs, k=n)) <= 0) / n_boot


def exits(rec: Mapping[str, Any], tape: ex.Tape | None, seed: int = 7) -> dict[str, Any]:
    trades = [dict(t) for t in rec.get("trades") or []]
    if not trades:
        return {"n": 0}
    rng = random.Random(seed)
    exc, base = [], []
    for t in trades:
        e = ex.excursion(t, tape)
        exc.append(e)
        if e is not None and tape is not None and str(t.get("exit_kind")) == "stop":
            base.extend(ex.stopped_then_2r_baseline(tape, ex.stop_distance(t), int(t["entry_ts"]), rng))
    return ex.summarize(trades, exc, days=float(rec["window"]["days"]), baseline=base)


def attack_test(trades: Sequence[Mapping[str, Any]], role: str) -> dict[str, Any]:
    """ATTACK trades vs the SAME trades at normal (TAKE) size: PnL scales with size, so the normal-size
    counterfactual is net / multiplier. `attack_added_net` is what sizing up actually added."""
    def level(t: Mapping[str, Any]) -> str:
        return str((t.get("jev_level") if role in ("JEV", "RANDOM") else t.get("tier")) or "TAKE")

    def mult(t: Mapping[str, Any]) -> float:
        m = t.get("jev_mult") if role in ("JEV", "RANDOM") else None
        if isinstance(m, (int, float)) and m > 0:
            return float(m)
        rp = t.get("risk_pct")
        return max(1.0, float(rp) / TIER_RISK["TAKE"]) if isinstance(rp, (int, float)) and rp > 0 else 1.0
    att = [t for t in trades if level(t) == "ATTACK"]
    take = [t for t in trades if level(t) != "ATTACK"]
    net = sum(float(t["net"]) for t in att)
    normal = sum(float(t["net"]) / mult(t) for t in att)
    return {"attack_trades": len(att), "attack_share": _r(len(att) / len(trades), 3) if trades else None,
            "attack_win_rate": _r(sum(1 for t in att if t["net"] > 0) / len(att), 3) if att else None,
            "attack_expectancy_r": _r(_mean([t["r"] for t in att])), "take_expectancy_r": _r(_mean([t["r"] for t in take])),
            "attack_net": _r(net), "same_trades_at_normal_size": _r(normal), "attack_added_net": _r(net - normal),
            "downgrades": a31.a3_count(t.get("attack_downgrade") for t in trades)}


def gates(rec: Mapping[str, Any], act: Mapping[str, Any], edge: Mapping[str, Any], conc: Mapping[str, Any],
          p0: float | None, jev: Mapping[str, Any] | None, base: Mapping[str, Any] | None, cfg: V4Config) -> list[dict[str, Any]]:
    g, m = cfg.gates, rec.get("metrics") or {}
    top3 = conc.get("top3_share_of_profit")
    out = [
        {"name": "adequate sample", "ok": act["trades"] >= MIN_SAMPLE, "actual": act["trades"], "threshold": f">= {MIN_SAMPLE} trades"},
        {"name": "participation", "ok": bool(act["participation_ok"]), "actual": act["trades_per_day"],
         "threshold": f">= {act['required_per_day']} trades/day"},
        {"name": "raw edge (gross expectancy)", "ok": (edge.get("gross_expectancy_r") or 0) > g.min_gross_expectancy_r,
         "actual": edge.get("gross_expectancy_r"), "threshold": "> 0 R before any cost"},
        {"name": "net PnL after all costs", "ok": (m.get("net_profit") or 0) > g.min_net_profit,
         "actual": _r(m.get("net_profit"), 4), "threshold": "> 0"},
        {"name": "net expectancy", "ok": (m.get("expectancy_r") or 0) > g.min_expectancy_r,
         "actual": _r(m.get("expectancy_r"), 4), "threshold": "> 0 R"},
        {"name": "profit factor", "ok": (m.get("profit_factor") or 0) >= g.min_profit_factor,
         "actual": _r(m.get("profit_factor"), 3), "threshold": f">= {g.min_profit_factor}"},
        {"name": "distinguishable from zero", "ok": p0 is not None and p0 <= g.max_p_mean_le_0,
         "actual": p0, "threshold": f"bootstrap P(mean net R <= 0) <= {g.max_p_mean_le_0:.0%}"},
        {"name": "drawdown / never halted", "ok": (m.get("max_drawdown_pct") or 0) <= g.max_drawdown_pct and not act["halted"],
         "actual": _r(m.get("max_drawdown_pct"), 4), "threshold": f"<= {g.max_drawdown_pct:.0%}, no halt"},
        {"name": "no liquidation", "ok": (m.get("liquidation_count") or 0) <= g.max_liquidations,
         "actual": m.get("liquidation_count") or 0, "threshold": "0"},
        {"name": "not concentrated", "ok": (conc.get("net_without_top3") or -1) > 0 and (top3 is None or top3 <= g.max_top3_share),
         "actual": f"ex-top3 {conc.get('net_without_top3')}, top3 share {top3}",
         "threshold": f"> 0 without the best 3; top 3 <= {g.max_top3_share:.0%} of profit"},
    ]
    if rec["role"] == "JEV" and jev is not None:
        b = base or {}
        net = m.get("net_profit") or 0.0
        sa = b.get("selection") or {}
        auc = jev.get("auc_support") or {}
        out += [
            {"name": "Jev participation", "ok": (jev.get("skip_rate") or 1.0) <= g.max_skip_rate,
             "actual": jev.get("skip_rate"), "threshold": f"skip <= {g.max_skip_rate:.0%}"},
            {"name": "beats matched RANDOM action", "ok": sa.get("random_p90") is not None and net > sa["random_p90"]
             and (sa.get("alpha_usdt") or 0) > 0, "actual": f"alpha {sa.get('alpha_usdt')} USDT, p={sa.get('p_value')}",
             "threshold": f"> random {int(g.random_percentile * 100)}th pct"},
            {"name": "meaningfully beats CONTROL", "ok": b.get("control_net") is not None
             and net - b["control_net"] >= g.min_delta_vs_control, "actual": _r(net - (b.get("control_net") or 0.0), 4),
             "threshold": f">= +{g.min_delta_vs_control} USDT"},
            {"name": "confidence discrimination", "ok": auc.get("low") is not None and auc["low"] > g.min_auc_low,
             "actual": f"AUC {auc.get('auc')} [{auc.get('low')}, {auc.get('high')}]", "threshold": "95% CI above 0.50"},
            {"name": "API reliability", "ok": (jev.get("error_rate") or 0.0) <= g.max_error_rate,
             "actual": jev.get("error_rate"), "threshold": f"<= {g.max_error_rate:.0%} errors"},
        ]
    return out


def diagnoses(rec: Mapping[str, Any], act: Mapping[str, Any], edge: Mapping[str, Any], ex_s: Mapping[str, Any],
              jev: Mapping[str, Any] | None, gs: Sequence[Mapping[str, Any]], cfg: V4Config) -> tuple[str, list[str]]:
    """Every diagnosis that applies, ROOT cause first: a bot with no raw edge failed for that reason; its
    costs, drawdown and short trade list are consequences."""
    m = rec.get("metrics") or {}
    failed = {x["name"] for x in gs if not x["ok"]}
    found: list[str] = []
    trades = act["trades"]
    raw = max(1, act["raw_setups"])
    if (m.get("liquidation_count") or 0) > 0:
        found.append("LIQUIDATION")
    if act["below_exchange_minimum"] >= 0.25 * raw:
        found.append("MIN_NOTIONAL_LIMITED")
    if jev is not None and (jev.get("skip_rate") or 0) > cfg.gates.max_skip_rate:
        found.append("JEV_OVER_FILTERING")
    if trades == 0:
        found.append("NO_TRADES")
    elif (m.get("gross_pnl") or 0.0) <= 0:
        found.append("EXIT_DESTROYS_EDGE" if "WINNERS CUT EARLY" in " ".join(ex_s.get("flags") or []) else "NO_RAW_EDGE")
    elif (m.get("net_profit") or 0.0) <= 0:
        found.append("COST_DESTROYED")
    if act["halted"] or (m.get("max_drawdown_pct") or 0) >= cfg.gates.max_drawdown_pct:
        found.append("DRAWDOWN_FAILURE")
    if not act["participation_ok"]:
        found.append("TOO_LITTLE_ACTIVITY")
    if trades and trades < MIN_SAMPLE:
        found.append("INSUFFICIENT_SAMPLE")
    net = m.get("net_profit") or 0.0
    if "not concentrated" in failed and net > 0:
        found.append("PROFIT_CONCENTRATED")
    if net > 0 and "distinguishable from zero" in failed:
        found.append("NOT_SIGNIFICANT")
    if net > 0 and failed & {"profit factor", "net expectancy"}:
        found.append("MARGINAL_EDGE")
    if jev is not None and failed & {"beats matched RANDOM action", "meaningfully beats CONTROL", "confidence discrimination"}:
        found.append("JEV_NO_SELECTION_ALPHA")
    if not found:
        found.append("ROBUST")
    return found[0], found


def state_of(rec: Mapping[str, Any], act: Mapping[str, Any], gs: Sequence[Mapping[str, Any]]) -> str:
    m = rec.get("metrics") or {}
    if all(x["ok"] for x in gs):
        return "ADVANCE"
    if act["trades"] < MIN_SAMPLE:
        return "INSUFFICIENT_SAMPLE"
    others = [x for x in gs if not x["ok"] and x["name"] != "participation"]
    if not act["participation_ok"] and (m.get("net_profit") or 0) > 0 and not others:
        return "LOW_ACTIVITY_EDGE"
    return "FAIL"


def analyze_bot(rec: Mapping[str, Any], cfg: V4Config, tape: ex.Tape | None = None,
                base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    act = activity(rec)
    edge, cost = a3.edge_and_cost(dict(rec))
    trades = rec.get("trades") or []
    conc = concentration(trades)
    costs = cost_structure(trades, cost)
    ex_s = exits(rec, tape)
    jev = jev_block(rec)
    att = attack_test(trades, rec["role"])
    p0 = p_mean_le_zero([float(t["r"]) for t in trades])
    calib = calibration(rec.get("edge_rows") or []) if rec.get("edge_rows") else None
    gs = gates(rec, act, edge, conc, p0, jev, base, cfg)
    primary, found = diagnoses(rec, act, edge, ex_s, jev, gs, cfg)
    state = state_of(rec, act, gs)
    summary = {"result": "PASS" if state == "ADVANCE" else state, "primary_issue": LABEL.get(primary, primary),
               "diagnoses": [LABEL.get(x, x) for x in found], "gross_expectancy_r": edge["gross_expectancy_r"],
               "net_expectancy_r": edge["net_expectancy_r"], "p_mean_le_0": p0, "trades_per_day": act["trades_per_day"],
               "max_drawdown_pct": edge["max_drawdown_pct"], "net_without_top3": conc.get("net_without_top3"),
               "exit_flags": ex_s.get("flags"), "jev_skip": jev["skip_rate"] if jev else None,
               "jev_auc": (jev.get("auc_support") or {}).get("auc") if jev else None,
               "selection_alpha": ((base or {}).get("selection") or {}).get("alpha_usdt")}
    return {"activity": act, "funnel": rec.get("funnel"), "edge": edge, "cost": costs, "concentration": conc,
            "exits": ex_s, "edge_quality": ex_s.get("edge"), "calibration": calib, "jev": jev, "attack": att,
            "p_mean_le_0": p0, "gates": gs, "state": state, "failure_mode": primary, "diagnoses": found,
            "summary": summary, "baselines": dict(base) if base else None}


# ---- arena level ---------------------------------------------------------------------------------------------

def capacity_verdict(book20: Mapping[str, Any] | None, a20: Mapping[str, Any] | None,
                     bigger: Sequence[Mapping[str, Any]]) -> str:
    """BAD vs ACCOUNT SIZE CONSTRAINED -- a diagnostic only; a 50 / 100 USDT result never qualifies a 20 USDT bot.

    OK_AT_20                  the 20 USDT bot passed every gate
    ACCOUNT_SIZE_CONSTRAINED  the 20 USDT book was limited by exchange minimums (>= 25% of setups below the
                              minimum order) while a bigger book of the same strategy was net-positive with a
                              positive raw edge
    BAD                       the strategy loses (or has no raw edge) at every account size
    """
    if a20 is not None and a20.get("state") == "ADVANCE":
        return "OK_AT_20"
    def good(b: Mapping[str, Any]) -> bool:
        m = b.get("metrics") or {}
        return (m.get("net_profit") or 0) > 0 and (m.get("gross_pnl") or 0) > 0 and (m.get("trades") or 0) >= MIN_SAMPLE
    constrained = a20 is not None and "MIN_NOTIONAL_LIMITED" in (a20.get("diagnoses") or [])
    if any(good(b) for b in bigger) and (constrained or (book20 is not None and not good(book20))):
        return "ACCOUNT_SIZE_CONSTRAINED" if constrained else "BAD_AT_20_BETTER_WHEN_BIGGER"
    return "BAD"


def edge_quality_table(recs: Sequence[Mapping[str, Any]], key) -> list[dict[str, Any]]:
    """gross -> fees -> slippage -> net per group, with edge per trade / turnover / fee dollar / bot-day."""
    groups: dict[Any, list[Mapping[str, Any]]] = defaultdict(list)
    for r in recs:
        groups[key(r)].append(r)
    out = []
    for k, rs in groups.items():
        trades = [t for r in rs for t in (r.get("trades") or [])]
        n = len(trades)
        gross = sum(float(t["gross"]) for t in trades)
        fees = sum(float(t["fees"]) for t in trades)
        slip = sum(float(t["slippage"]) for t in trades)
        fund = sum(float(t.get("funding") or 0.0) for t in trades)
        net = sum(float(t["net"]) for t in trades)
        turn = sum(float(t.get("notional") or 0.0) for t in trades)
        bot_days = sum(float(r["window"]["days"]) for r in rs)
        risk = sum(float(t.get("risk_usd") or 0.0) for t in trades)
        out.append({"group": k, "bots": len(rs), "trades": n, "gross": _r(gross), "fees": _r(fees), "slippage": _r(slip),
                    "funding": _r(fund), "net": _r(net),
                    "gross_r_per_trade": _r(gross / risk) if risk else None,
                    "net_r_per_trade": _r(_mean([float(t["r"]) for t in trades])),
                    "gross_bps_of_turnover": _r(gross / turn * 1e4, 2) if turn else None,
                    "net_bps_of_turnover": _r(net / turn * 1e4, 2) if turn else None,
                    "gross_per_fee_dollar": _r(gross / fees, 3) if fees else None,
                    "net_per_bot_day": _r(net / bot_days, 5) if bot_days else None,
                    "diagnosis": ("NO TRADES" if not n else "NO RAW EDGE" if gross <= 0 else
                                  "EDGE DESTROYED BY COSTS" if net <= 0 else "EDGE SURVIVES COSTS")})
    out.sort(key=lambda x: (not x["trades"], -(x["net"] or 0)))      # groups that traded first
    return out


def raw_edge_table(raw: Sequence[Mapping[str, Any]], key) -> list[dict[str, Any]]:
    """The RAW evidence -- every legal setup simulated at TAKE size, sequenced like an ungated bot -- per group:
    the raw edge BEFORE any gate or cost (gross R), what the costs take (cost R) and what is left (net R)."""
    groups: dict[Any, list[tuple[float, float]]] = defaultdict(list)
    for r in raw:
        for o in r.get("observations") or []:
            if o.get("sequenced") and o.get("net_r") is not None and o.get("cost_r") is not None:
                groups[key(r)].append((float(o["net_r"]) + float(o["cost_r"]), float(o["cost_r"])))
    out = []
    for k, xs in groups.items():
        n = len(xs)
        g = [a for a, _ in xs]
        mean = sum(g) / n
        sd = statistics.pstdev(g) if n > 1 else 0.0
        cost = sum(c for _, c in xs) / n
        out.append({"group": k, "setups": n, "gross_r": _r(mean), "t": _r(mean / (sd / n ** 0.5), 2) if sd else None,
                    "cost_r": _r(cost), "net_r": _r(mean - cost), "win_rate": _r(sum(1 for a, c in xs if a - c > 0) / n, 3),
                    "diagnosis": "NO RAW EDGE" if mean <= 0 else "EDGE DESTROYED BY COSTS" if mean - cost <= 0 else "EDGE SURVIVES COSTS"})
    out.sort(key=lambda x: -(x["gross_r"] or -9))
    return out


def exit_table(analyses: Mapping[str, Mapping[str, Any]], recs: Sequence[Mapping[str, Any]], key) -> dict[str, Any]:
    """Pooled exit-analyzer answers per group (trade-weighted means of the per-bot summaries)."""
    groups: dict[Any, list[Mapping[str, Any]]] = defaultdict(list)
    for r in recs:
        s = (analyses.get(r["key"]) or {}).get("exits") or {}
        if s.get("n"):
            groups[key(r)].append(s)
    fields = ("mfe_r_mean", "mae_r_mean", "profit_left_r_mean", "stopped_then_2r_share", "random_stopped_then_2r_share",
              "winners_post_exit_4h_r_mean", "winner_hold_min_p50", "loser_hold_min_p50", "reached_1r_share")
    out = {}
    for k, ss in groups.items():
        tot = sum(s["n"] for s in ss)
        row = {"trades": tot}
        for f in fields:
            pairs = [(s.get(f), s["n"]) for s in ss if isinstance(s.get(f), (int, float))]
            w = sum(n for _, n in pairs)
            row[f] = _r(sum(v * n for v, n in pairs) / w, 3) if w else None
        buckets: dict[str, dict[str, float]] = {}
        for s in ss:
            for b, v in (s.get("hold_buckets") or {}).items():
                acc = buckets.setdefault(b, {"n": 0, "net": 0.0})
                acc["n"] += v.get("n") or 0
                acc["net"] += v.get("net") or 0.0
        row["hold_buckets"] = {b: {"n": v["n"], "net": _r(v["net"])} for b, v in buckets.items()}
        row["flags"] = pooled_flags(row)
        out[str(k)] = row
    return out


def pooled_flags(row: Mapping[str, Any]) -> list[str]:
    """The exit questions on pooled numbers, each against its baseline (see exit_analyzer.flags)."""
    out = []
    st, base = row.get("stopped_then_2r_share"), row.get("random_stopped_then_2r_share")
    if st is not None and base is not None and st >= base + 0.10:
        out.append("STOPS TOO TIGHT")
    if (row.get("winners_post_exit_4h_r_mean") or 0) >= 0.3:
        out.append("WINNERS CUT EARLY")
    wh, lh = row.get("winner_hold_min_p50"), row.get("loser_hold_min_p50")
    if wh and lh and lh > 1.5 * wh:
        out.append("LOSERS HELD TOO LONG")
    return out or ["NO EXIT FLAG"]


def scores(rows: Sequence[Mapping[str, Any]], cfg: V4Config) -> list[float]:
    """Ranking only (never a gate): mean percentile of return/DD, net expectancy, PF (capped), trades/day
    (capped at 2x the participation gate), gross-to-cost, selection alpha."""
    def dim(f):
        return [f(r) for r in rows]
    ranks = [a3._rank(dim(lambda r: (r["edge"]["net_return_pct"] or 0.0) / max(r["edge"]["max_drawdown_pct"] or 0.0, 0.05))),
             a3._rank(dim(lambda r: r["edge"]["net_expectancy_r"])),
             a3._rank(dim(lambda r: min(r["edge"]["net_pf"], 3.0) if r["edge"]["net_pf"] is not None else None)),
             a3._rank(dim(lambda r: min(r["activity"]["trades_per_day"] or 0.0, 2 * PARTICIPATION_PER_DAY[r["tf"]]))),
             a3._rank(dim(lambda r: r["cost"].get("realized_edge_to_cost"))),
             a3._rank(dim(lambda r: r.get("selection_alpha") or 0.0))]
    out = []
    for i, r in enumerate(rows):
        s = sum(rk[i] for rk in ranks) / len(ranks)
        if (r["edge"]["max_drawdown_pct"] or 0) >= cfg.gates.catastrophic_drawdown or r.get("liquidations"):
            s = min(s, 0.25)
        out.append(round(s, 4))
    return out


def median_or_none(xs: Sequence[float]) -> float | None:
    return statistics.median(xs) if xs else None
