"""BOT ANALYZER for the V3 aggressive arena: WHY a bot succeeds or fails, not just where it ranks.

Per bot:   activity, edge (gross vs net, USDT / R / bps of notional), cost (fees, slippage, funding,
           cost-to-edge), signal-quality buckets, Jev (P(win) AUC with interval, calibration, action
           mix, accepted vs skipped outcomes, latency, reliability), regime, UTC session, the
           discovery gates, ONE primary failure mode and a NEXT VERSION HYPOTHESIS.
Per arena: leaderboards, failure-mode counts, strategy x timeframe and coin x timeframe matrices,
           CONTROL vs +JEV2 against ALWAYS-TAKE / ALWAYS-SKIP / RANDOM-FILTER, the ranking score
           (separate from the gates) and the ADVANCED SET.

The analyzer only READS finished bots. It proposes hypotheses in words; it never edits a strategy,
never re-runs one on the same data with a change, and never touches a threshold.
"""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from typing import Any, Sequence

from app.ai.jev.v2 import session_of
from app.competition.jev_experiment import auc_with_ci
from app.competition.v3_config import PARTICIPATION_PER_30D, V3Config, min_trades

LEVELS = ("SKIP", "DEFENSIVE", "NORMAL", "ATTACK")
QUALITY_BUCKETS = ((0.0, 0.30), (0.30, 0.45), (0.45, 0.60), (0.60, 0.75), (0.75, 1.0001))
PROB_BUCKETS = ((0.0, 0.2), (0.2, 0.35), (0.35, 0.5), (0.5, 0.65), (0.65, 1.0001))
SLOWER = {"1m": "3m", "3m": "5m", "5m": "15m", "15m": "30m", "30m": "1h"}
LABEL = {"NO_GROSS_EDGE": "GROSS NEGATIVE", "FEE_DESTROYED": "FEE DESTROYED",
         "SLIPPAGE_DESTROYED": "SLIPPAGE DESTROYED", "TOO_LOW_ACTIVITY": "TOO LITTLE ACTIVITY",
         "OVERTRADING": "OVERTRADING", "JEV_OVER_FILTERING": "JEV OVER FILTERED",
         "JEV_BAD_DISCRIMINATION": "JEV NO DISCRIMINATION", "DRAWDOWN_FAILURE": "DRAWDOWN",
         "LIQUIDATION": "LIQUIDATION", "MIN_NOTIONAL_CONSTRAINED": "MIN NOTIONAL",
         "REGIME_DEPENDENT": "REGIME DEPENDENT", "PROFIT_CONCENTRATION": "PROFIT CONCENTRATION",
         "LATENCY_SENSITIVE": "LATENCY SENSITIVE", "MARGINAL_EDGE": "MARGINAL EDGE",
         "JEV_NO_VALUE": "JEV ADDS NO VALUE", "ROBUST": "HEALTHY"}


def _mean(xs: Sequence[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def _pct(xs: Sequence[float], q: float) -> float | None:
    v = sorted(x for x in xs if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _pf(xs: Sequence[float]) -> float | None:
    win = sum(x for x in xs if x > 0)
    loss = -sum(x for x in xs if x < 0)
    if loss <= 0:
        return None if win <= 0 else 999.0
    return win / loss


def _r(x: Any, nd: int = 4) -> Any:
    return round(x, nd) if isinstance(x, (int, float)) and math.isfinite(x) else None


# ---- per-bot blocks -----------------------------------------------------------------------------------------

def activity(rec: dict[str, Any]) -> dict[str, Any]:
    days = float(rec["window"]["days"]) or 1.0
    a, trades, ds = rec["activity"], rec.get("trades") or [], rec.get("decisions") or []
    entries = sorted(t["entry_ts"] for t in trades)
    gaps = [(b - a_) / 60000.0 for a_, b in zip(entries, entries[1:])]
    accepted = [d for d in ds if d.get("level") and d["level"] != "SKIP"]
    return {"days": days, "candidates": a["signals"], "candidates_per_day": _r(a["signals"] / days, 3),
            "jev_calls": len(ds) if rec["role"] == "JEV" else 0,
            "jev_calls_per_day": _r(len(ds) / days, 3) if rec["role"] == "JEV" else None,
            "accepted_per_day": _r(len(accepted) / days, 3) if ds else None,
            "trades": len(trades), "trades_per_day": _r(len(trades) / days, 3),
            "trades_per_30d": _r(len(trades) * 30.0 / days, 2),
            "avg_hold_min": _r(_mean([t["hold_s"] / 60.0 for t in trades]), 1),
            "median_gap_min": _r(statistics.median(gaps), 1) if gaps else None,
            "below_exchange_minimum": a.get("below_exchange_minimum", 0),
            "cost_gate_rejected": a.get("cost_gate_rejected", 0)}


def edge_and_cost(rec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    trades = rec.get("trades") or []
    m = rec["metrics"]
    gross = [t["gross"] for t in trades]
    net = [t["net"] for t in trades]
    risk = [t.get("risk_usd") for t in trades]
    gross_r = [g / r for g, r in zip(gross, risk) if r]
    notional = [t.get("notional") or 0.0 for t in trades]
    costs = [t["fees"] + t["slippage"] for t in trades]
    gross_bps = [g / n * 1e4 for g, n in zip(gross, notional) if n]
    cost_bps = [c / n * 1e4 for c, n in zip(costs, notional) if n]
    winners = sum(g for g in gross if g > 0)
    tot_cost = sum(costs)
    mean_gross_bps, mean_cost_bps = _mean(gross_bps), _mean(cost_bps)
    edge = {"gross_pnl": _r(m.get("gross_pnl"), 4), "net_pnl": _r(m.get("net_profit"), 4),
            "net_return_pct": _r(m.get("net_return_pct")),
            "gross_expectancy_usdt": _r(_mean(gross), 5), "net_expectancy_usdt": _r(_mean(net), 5),
            "gross_expectancy_r": _r(_mean(gross_r), 4), "net_expectancy_r": _r(m.get("expectancy_r"), 4),
            "gross_pf": _r(_pf(gross), 3), "net_pf": _r(m.get("profit_factor"), 3),
            "win_rate": _r(m.get("win_rate"), 4), "max_drawdown_pct": _r(m.get("max_drawdown_pct"), 4),
            "gross_bps_per_trade": _r(mean_gross_bps, 2),
            "net_without_top3": _r(sum(net) - sum(sorted(net, reverse=True)[:3]), 4) if net else None,
            "largest_winner_share": _r(max(net) / sum(x for x in net if x > 0), 3)
            if net and sum(x for x in net if x > 0) > 0 else None}
    cost = {"fees": _r(m.get("fees_paid"), 4), "slippage": _r(m.get("slippage_cost"), 4),
            "funding": _r(m.get("funding_paid"), 4), "total_cost": _r(tot_cost, 4),
            "avg_cost_per_trade": _r(tot_cost / len(trades), 5) if trades else None,
            "round_trip_cost_bps": _r(mean_cost_bps, 2),
            "cost_share_of_gross_winners": _r(tot_cost / winners, 3) if winners > 0 else None,
            "cost_to_edge": _r(tot_cost / m["gross_pnl"], 3) if (m.get("gross_pnl") or 0) > 0 else None,
            "realized_edge_to_cost": _r(mean_gross_bps / mean_cost_bps, 3)
            if (mean_gross_bps is not None and mean_cost_bps) else None,
            "planned_edge_to_cost_median": _r(_pct([t.get("e2c") for t in trades], 0.5), 2)}
    return edge, cost


def buckets(rows: Sequence[dict[str, Any]], key: str, edges: Sequence[tuple[float, float]]) -> list[dict[str, Any]]:
    out = []
    for lo, hi in edges:
        part = [t for t in rows if isinstance(t.get(key), (int, float)) and lo <= t[key] < hi]
        rs = [t["r"] for t in part if t.get("r") is not None]
        out.append({"bucket": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": len(part),
                    "win_rate": _r(sum(1 for r in rs if r > 0) / len(rs), 3) if rs else None,
                    "mean_r": _r(_mean(rs), 3), "net": _r(sum(t.get("net") or 0.0 for t in part), 4)})
    return out


def by_label(trades: Sequence[dict[str, Any]], label_of) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        groups[label_of(t)].append(t)
    return {k: {"n": len(v), "net": _r(sum(t["net"] for t in v), 4),
                "mean_r": _r(_mean([t["r"] for t in v]), 3),
                "win_rate": _r(sum(1 for t in v if t["net"] > 0) / len(v), 3)} for k, v in sorted(groups.items())}


def regime_breakdown(trades: Sequence[dict[str, Any]], table: dict[int, dict[str, str]] | None) -> dict[str, Any]:
    if not table:
        return {"trend": {}, "vol": {}, "note": "no daily regime table"}
    day = lambda t: t["entry_ts"] - t["entry_ts"] % 86_400_000  # noqa: E731
    return {"trend": by_label(trades, lambda t: (table.get(day(t)) or {}).get("trend") or "UNKNOWN"),
            "vol": by_label(trades, lambda t: (table.get(day(t)) or {}).get("vol") or "UNKNOWN")}


def jev_block(rec: dict[str, Any]) -> dict[str, Any] | None:
    ds = rec.get("decisions") or []
    if rec["role"] != "JEV":
        return None
    n = len(ds)
    chosen = defaultdict(int)
    final = defaultdict(int)
    for d in ds:
        chosen[d.get("chosen") or "ERROR"] += 1
        final[d.get("level") or "SKIP"] += 1
    resolved = [d for d in ds if d.get("r") is not None]
    lab = [(float(d["p_win"]), 1 if (d.get("net") or 0) > 0 else 0) for d in resolved if d.get("p_win") is not None]
    lvl = [(LEVELS.index(d["level"]) + 0.0, 1 if (d.get("net") or 0) > 0 else 0) for d in resolved if d.get("level") in LEVELS]
    taken = [d for d in resolved if d.get("outcome") == "TAKEN"]
    # Two different reasons a candidate is not traded: Jev said SKIP, or Jev shrank it (DEFENSIVE 0.5x)
    # and the RiskManager refused the smaller order -- at 20 USDT a half-size order is often below the
    # exchange minimum. Both are simulated in the shadow book; they are reported separately.
    skipped = [d for d in resolved if d.get("outcome") == "SHADOW" and not str(d.get("result") or "").startswith("RISK_REJECTED")]
    refused = [d for d in resolved if d.get("outcome") == "SHADOW" and str(d.get("result") or "").startswith("RISK_REJECTED")]
    api = [d for d in ds if d.get("source") == "api"]
    lat = [d["latency_ms"] for d in api if d.get("latency_ms") and not d.get("error")]
    errors = [d for d in ds if d.get("error")]
    calib = []
    for lo, hi in PROB_BUCKETS:
        part = [d for d in resolved if d.get("p_win") is not None and lo <= d["p_win"] < hi]
        calib.append({"bucket": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": len(part),
                      "predicted": _r(_mean([d["p_win"] for d in part]), 3),
                      "realized_win_rate": _r(sum(1 for d in part if (d.get("net") or 0) > 0) / len(part), 3) if part else None,
                      "mean_r": _r(_mean([d["r"] for d in part]), 3)})
    ps = [d["p_win"] for d in ds if d.get("p_win") is not None]
    return {"decisions": n, "chosen": dict(chosen), "final": dict(final),
            "skip_rate": _r(final.get("SKIP", 0) / n, 4) if n else None,
            "attack_rate": _r(final.get("ATTACK", 0) / n, 4) if n else None,
            "acceptance_rate": _r(1 - final.get("SKIP", 0) / n, 4) if n else None,
            "p_win_quartiles": [_r(_pct(ps, q), 3) for q in (0.0, 0.25, 0.5, 0.75, 1.0)] if ps else None,
            "auc": {k: _r(v, 4) if isinstance(v, float) else v for k, v in auc_with_ci(lab).items()},
            "auc_level": {k: _r(v, 4) if isinstance(v, float) else v for k, v in auc_with_ci(lvl).items()},
            "calibration": calib,
            "accepted": {"n": len(taken), "winners": sum(1 for d in taken if d["net"] > 0),
                         "losers": sum(1 for d in taken if d["net"] <= 0), "mean_r": _r(_mean([d["r"] for d in taken]), 3)},
            "skipped": {"n": len(skipped), "winners": sum(1 for d in skipped if d["net"] > 0),
                        "losers": sum(1 for d in skipped if d["net"] <= 0), "mean_r": _r(_mean([d["r"] for d in skipped]), 3),
                        "counterfactual_net": _r(sum(d["net"] for d in skipped), 4)},
            "refused_after_resize": {"n": len(refused), "winners": sum(1 for d in refused if d["net"] > 0),
                                     "losers": sum(1 for d in refused if d["net"] <= 0),
                                     "mean_r": _r(_mean([d["r"] for d in refused]), 3),
                                     "counterfactual_net": _r(sum(d["net"] for d in refused), 4)},
            "not_traded_rate": _r((len(skipped) + len(refused)) / len(resolved), 4) if resolved else None,
            "latency_ms": {"p50": _r(_pct(lat, 0.5), 0), "p95": _r(_pct(lat, 0.95), 0), "p99": _r(_pct(lat, 0.99), 0),
                           "n": len(lat)},
            "api_calls": len(api), "errors": len(errors), "error_rate": _r(len(errors) / n, 4) if n else None,
            "cost_usd": _r(sum(float(d.get("cost_usd") or 0.0) for d in ds), 5)}


def permutation_p(ds: Sequence[dict[str, Any]], perms: int, seed: int) -> dict[str, Any]:
    """Selection skill at trade level: is sum(multiplier x outcome R) higher than when the SAME
    multipliers are shuffled across the SAME candidates? (path effects ignored; the RANDOM twins
    replay those.)"""
    rows = [(float(d.get("mult") or 0.0), float(d["r"])) for d in ds if d.get("r") is not None]
    if len(rows) < 10:
        return {"n": len(rows), "p": None, "statistic": None}
    mults = [m for m, _ in rows]
    rs = [r for _, r in rows]
    stat = sum(m * r for m, r in rows)
    rng = random.Random(seed)
    ge = 0
    for _ in range(perms):
        rng.shuffle(mults)
        if sum(m * r for m, r in zip(mults, rs)) >= stat - 1e-12:
            ge += 1
    return {"n": len(rows), "statistic": _r(stat, 4), "p": _r((ge + 1) / (perms + 1), 4)}


# ---- gates, failure mode, hypothesis -------------------------------------------------------------------------

def gates(rec: dict[str, Any], a: dict[str, Any], edge: dict[str, Any], jev: dict[str, Any] | None,
          base: dict[str, Any] | None, cfg: V3Config) -> list[dict[str, Any]]:
    g, m, tf = cfg.gates, rec["metrics"], rec["identity"]["timeframe"]
    need = min_trades(tf, cfg.days)
    halted = bool(rec["activity"].get("halted"))
    out = [
        {"name": "participation", "ok": a["trades"] >= need, "actual": a["trades"], "threshold": f">= {need} trades"},
        {"name": "net PnL after all costs", "ok": (m.get("net_profit") or 0) > g.min_net_profit,
         "actual": _r(m.get("net_profit"), 4), "threshold": "> 0"},
        {"name": "net expectancy", "ok": (m.get("expectancy_r") or 0) > g.min_expectancy_r,
         "actual": _r(m.get("expectancy_r"), 4), "threshold": "> 0 R"},
        {"name": "profit factor", "ok": (m.get("profit_factor") or 0) >= g.min_profit_factor,
         "actual": _r(m.get("profit_factor"), 3), "threshold": f">= {g.min_profit_factor}"},
        {"name": "drawdown / never halted", "ok": (m.get("max_drawdown_pct") or 0) <= g.max_drawdown_pct and not halted,
         "actual": _r(m.get("max_drawdown_pct"), 4), "threshold": f"<= {g.max_drawdown_pct:.0%}, no halt"},
        {"name": "no liquidation", "ok": (m.get("liquidation_count") or 0) <= g.max_liquidations,
         "actual": m.get("liquidation_count") or 0, "threshold": "0"},
        {"name": "not 3 lucky trades", "ok": (edge.get("net_without_top3") or -1) > 0,
         "actual": edge.get("net_without_top3"), "threshold": "> 0 without the best 3"},
    ]
    if rec["role"] == "JEV" and jev is not None:
        b = base or {}
        net = m.get("net_profit") or 0.0
        rnd = b.get("random") or {}
        auc = jev.get("auc") or {}
        out += [
            {"name": "Jev participation", "ok": (jev.get("skip_rate") or 1.0) <= g.max_skip_rate
             and (jev["accepted"]["n"] >= need), "actual": f"skip {jev.get('skip_rate')}; accepted trades {jev['accepted']['n']}",
             "threshold": f"skip <= {g.max_skip_rate:.0%}, accepted >= {need}"},
            {"name": "beats matched CONTROL", "ok": b.get("control_net") is not None and net > b["control_net"],
             "actual": _r(net - (b.get("control_net") or 0.0), 4), "threshold": "> control net"},
            {"name": "beats ALWAYS-SKIP", "ok": net > 0, "actual": _r(net, 4), "threshold": "> 0"},
            {"name": "beats RANDOM-FILTER", "ok": rnd.get("p90") is not None and net > rnd["p90"],
             "actual": f"p={rnd.get('p_value')}", "threshold": f"> random {int(g.random_percentile * 100)}th pct"},
            {"name": "Jev discrimination", "ok": auc.get("low") is not None and auc["low"] > g.min_auc_low,
             "actual": f"AUC {auc.get('auc')} [{auc.get('low')}, {auc.get('high')}] n={(auc.get('wins') or 0) + (auc.get('losses') or 0)}",
             "threshold": "95% CI above 0.50"},
            {"name": "API reliability", "ok": (jev.get("error_rate") or 0.0) <= g.max_error_rate,
             "actual": jev.get("error_rate"), "threshold": f"<= {g.max_error_rate:.0%} errors"},
            {"name": "latency", "ok": (jev["latency_ms"].get("p95") or 0) <= g.max_latency_p95_ms,
             "actual": jev["latency_ms"].get("p95"), "threshold": f"p95 <= {g.max_latency_p95_ms:.0f} ms"},
        ]
    return out


def failure_mode(rec: dict[str, Any], a: dict[str, Any], edge: dict[str, Any], cost: dict[str, Any],
                 jev: dict[str, Any] | None, gs: list[dict[str, Any]], reg: dict[str, Any], cfg: V3Config) -> str:
    """The ROOT cause, not the last symptom: a bot that loses before costs and is then halted by the
    frozen floor failed for lack of edge -- its drawdown and its short trade list are consequences."""
    m, tf = rec["metrics"], rec["identity"]["timeframe"]
    need = min_trades(tf, cfg.days)
    halted = bool(rec["activity"].get("halted"))
    if (m.get("liquidation_count") or 0) > 0:
        return "LIQUIDATION"
    sig = max(1, rec["activity"]["signals"])
    if a["trades"] < need and a["below_exchange_minimum"] >= 0.5 * sig:
        return "MIN_NOTIONAL_CONSTRAINED"
    if jev is not None and (jev.get("skip_rate") or 0) > cfg.gates.max_skip_rate:
        return "JEV_OVER_FILTERING"
    gross, net = m.get("gross_pnl") or 0.0, m.get("net_profit") or 0.0
    if a["trades"] and gross <= 0:
        return "NO_GROSS_EDGE"
    if a["trades"] and net <= 0:
        if (a["trades_per_30d"] or 0) >= 2.5 * PARTICIPATION_PER_30D[tf]:
            return "OVERTRADING"
        return "SLIPPAGE_DESTROYED" if (cost.get("slippage") or 0) > (cost.get("fees") or 0) else "FEE_DESTROYED"
    if halted or (m.get("max_drawdown_pct") or 0) >= cfg.gates.max_drawdown_pct:
        return "DRAWDOWN_FAILURE"
    if a["trades"] < need:
        return "TOO_LOW_ACTIVITY"
    failed = {x["name"] for x in gs if not x["ok"]}
    if "not 3 lucky trades" in failed:
        return "PROFIT_CONCENTRATION"
    trend = reg.get("trend") or {}
    pos = [k for k, v in trend.items() if (v.get("net") or 0) > 0 and k != "UNKNOWN"]
    if len(pos) == 1 and (trend[pos[0]]["net"] or 0) > net:
        return "REGIME_DEPENDENT"
    if {"profit factor", "net expectancy"} & failed:
        return "MARGINAL_EDGE"
    if jev is not None and ("Jev discrimination" in failed):
        return "JEV_BAD_DISCRIMINATION"
    if jev is not None and failed & {"beats matched CONTROL", "beats RANDOM-FILTER", "Jev participation"}:
        return "JEV_NO_VALUE"
    if failed:
        return "MARGINAL_EDGE"
    return "ROBUST"


def hypothesis(mode: str, rec: dict[str, Any], a: dict[str, Any], edge: dict[str, Any], cost: dict[str, Any],
               quality: list[dict[str, Any]], jev: dict[str, Any] | None, reg: dict[str, Any],
               sessions: dict[str, Any], cfg: V3Config) -> list[str]:
    """NEXT VERSION HYPOTHESIS, in words. Proposed -- never applied: a change is V3.1, developed on
    DEVELOPMENT data and judged only on data it has never seen."""
    idn = rec["identity"]
    tf = idn["timeframe"]
    rt = cost.get("round_trip_cost_bps")
    gb = edge.get("gross_bps_per_trade")
    best_q = max((b for b in quality if b["n"] >= 10 and b["mean_r"] is not None), key=lambda b: b["mean_r"], default=None)
    worst_session = min(sessions.items(), key=lambda kv: kv[1]["net"] or 0.0)[0] if sessions else None
    trend = reg.get("trend") or {}
    best_reg = max(trend.items(), key=lambda kv: kv[1]["net"] or 0.0)[0] if trend else None
    h: list[str] = []
    if mode == "NO_GROSS_EDGE":
        if best_q and best_q["mean_r"] > 0:
            h.append(f"entries lose before costs except signal_quality {best_q['bucket']} "
                     f"({best_q['mean_r']:+.2f}R on {best_q['n']}): V3.1 could trade only that band")
        else:
            h.append(f"the {idn['strategy_id']} trigger does not predict direction on {idn['coin']} {tf} in any "
                     f"quality band: retire this family x timeframe or change the entry logic itself")
    elif mode in ("FEE_DESTROYED", "OVERTRADING"):
        h.append(f"gross {gb} bps/trade vs round trip {rt} bps: require an expected move >= 3x cost "
                 f"(cost gate 3.0) or test {SLOWER.get(tf, tf)}, where moves are larger relative to cost")
        if mode == "OVERTRADING":
            h.append(f"{a['trades_per_30d']} trades per 30 days: add a confirmation or a cooldown to cut frequency")
    elif mode == "SLIPPAGE_DESTROYED":
        h.append(f"slippage {cost.get('slippage')} exceeds fees {cost.get('fees')}: maker entries or avoid "
                 f"{worst_session} hours")
    elif mode == "TOO_LOW_ACTIVITY":
        h.append(f"{a['trades']} trades vs {min_trades(tf, cfg.days)} required: loosen the filter that rejects "
                 f"most signals, or run the family on a faster timeframe")
    elif mode == "JEV_OVER_FILTERING" and jev:
        h.append(f"Jev V2 skipped {jev['skip_rate']:.0%}; skipped candidates averaged {jev['skipped']['mean_r']}R: "
                 + ("Jev is refusing positive trades -- a V3 policy must not veto this setup"
                    if (jev['skipped']['mean_r'] or 0) > 0 else "the skips avoided losers, but the bot no longer trades aggressively"))
    if jev and (jev.get("refused_after_resize") or {}).get("n", 0) > max(10, 0.2 * (jev.get("decisions") or 1)):
        rr = jev["refused_after_resize"]
        h.append(f"{rr['n']} of {jev['decisions']} candidates were refused after Jev's DEFENSIVE half size (below the exchange "
                 f"minimum at 20 USDT): a smaller size is not a legal order here, so DEFENSIVE acts as a veto")
    if mode in ("JEV_BAD_DISCRIMINATION", "JEV_NO_VALUE") and jev:
        auc = jev.get("auc") or {}
        h.append(f"P(win) AUC {auc.get('auc')} [{auc.get('low')}, {auc.get('high')}]: Jev does not separate "
                 f"winners from losers here; run the bot without Jev, or give JEV_POLICY_V3 features that do")
    elif mode == "DRAWDOWN_FAILURE":
        h.append(f"max drawdown {edge.get('max_drawdown_pct')}: cap ATTACK sizing and add a regime filter "
                 f"(worst trend regime by net: {min(trend.items(), key=lambda kv: kv[1]['net'] or 0.0)[0] if trend else '?'})")
    elif mode == "MIN_NOTIONAL_CONSTRAINED":
        h.append(f"{a['below_exchange_minimum']} of {a['candidates']} signals below Bybit's minimum order: "
                 f"use a coin with a smaller minimum or a tighter structural stop")
    elif mode == "REGIME_DEPENDENT":
        h.append(f"all profit comes from {best_reg}: a V3.1 {best_reg} filter, then prove it on TEST")
    elif mode == "PROFIT_CONCENTRATION":
        h.append(f"net without the best 3 trades {edge.get('net_without_top3')}: the edge is a few outliers -- "
                 f"needs a larger sample before any claim")
    elif mode == "MARGINAL_EDGE":
        h.append(f"PF {edge.get('net_pf')} / expectancy {edge.get('net_expectancy_r')}R: too thin to survive "
                 f"cost uncertainty; look for a stronger filter in the best quality band")
    elif mode == "ROBUST":
        h.append(f"passes every discovery gate: freeze and run the HOLDOUT TEST ({cfg_test_window()})")
    return h


def cfg_test_window() -> str:
    from app.competition.v3_config import TEST_FROM, TEST_TO
    return f"{TEST_FROM} -> {TEST_TO}"


def analyze_bot(rec: dict[str, Any], cfg: V3Config, regime_table: dict[int, dict[str, str]] | None = None,
                base: dict[str, Any] | None = None) -> dict[str, Any]:
    a = activity(rec)
    edge, cost = edge_and_cost(rec)
    trades = rec.get("trades") or []
    quality = buckets(trades, "quality", QUALITY_BUCKETS)
    jev = jev_block(rec)
    if jev is not None:
        jev["permutation"] = permutation_p(rec.get("decisions") or [], cfg.permutations, cfg.seed)
    reg = regime_breakdown(trades, regime_table)
    sess = by_label(trades, lambda t: session_of((t["entry_ts"] // 3_600_000) % 24))
    gs = gates(rec, a, edge, jev, base, cfg)
    mode = failure_mode(rec, a, edge, cost, jev, gs, reg, cfg)
    need = min_trades(rec["identity"]["timeframe"], cfg.days)
    if rec.get("experimental"):
        state = "EXPERIMENTAL"
    elif a["trades"] < need or (jev is not None and (jev.get("skip_rate") or 0) > cfg.gates.max_skip_rate):
        state = "INSUFFICIENT_AGGRESSIVE_PARTICIPATION"
    elif all(x["ok"] for x in gs):
        state = "ADVANCE"
    else:
        state = "FAIL"
    hyp = hypothesis(mode, rec, a, edge, cost, quality, jev, reg, sess, cfg)
    summary = {"result": "PASS" if state == "ADVANCE" else state if state != "FAIL" else "FAIL",
               "primary_issue": LABEL.get(mode, mode), "gross_expectancy_r": edge["gross_expectancy_r"],
               "net_expectancy_r": edge["net_expectancy_r"], "round_trip_cost_bps": cost["round_trip_cost_bps"],
               "edge_to_cost": cost["realized_edge_to_cost"], "trades_per_day": a["trades_per_day"],
               "max_drawdown_pct": edge["max_drawdown_pct"],
               "jev_take": jev["acceptance_rate"] if jev else None,
               "jev_auc": (jev.get("auc") or {}).get("auc") if jev else None, "hypothesis": hyp}
    return {"activity": a, "edge": edge, "cost": cost, "quality": quality, "jev": jev, "regime": reg,
            "sessions": sess, "gates": gs, "state": state, "failure_mode": mode, "summary": summary,
            "baselines": base}


# ---- arena level ---------------------------------------------------------------------------------------------

def _rank(values: Sequence[float | None], higher_better: bool = True) -> list[float]:
    idx = [i for i, v in enumerate(values) if v is not None]
    out = [0.0] * len(values)
    if not idx:
        return out
    order = sorted(idx, key=lambda i: values[i], reverse=not higher_better)
    for pos, i in enumerate(order):
        out[i] = (pos + 1) / len(order) if len(order) > 1 else 1.0
    return out


def scores(rows: Sequence[dict[str, Any]], cfg: V3Config) -> list[float]:
    """Ranking only (docs/V3_PROTOCOL.md): mean percentile rank of risk-adjusted return, expectancy,
    PF (capped), activity (capped at 2x the timeframe minimum), realized cost efficiency, Jev
    contribution and regime consistency. Catastrophic bots are capped at 0.25."""
    def dim(f):
        return [f(r) for r in rows]
    ret_dd = dim(lambda r: (r["edge"]["net_return_pct"] or 0.0) / max(r["edge"]["max_drawdown_pct"] or 0.0, 0.05))
    exp_r = dim(lambda r: r["edge"]["net_expectancy_r"])
    pf = dim(lambda r: min(r["edge"]["net_pf"], 3.0) if r["edge"]["net_pf"] is not None else None)
    act = dim(lambda r: min(r["activity"]["trades_per_30d"] or 0.0, 2 * PARTICIPATION_PER_30D[r["tf"]]))
    eff = dim(lambda r: r["cost"]["realized_edge_to_cost"])
    jev = dim(lambda r: r.get("jev_contribution") or 0.0)
    reg = dim(lambda r: r.get("regime_consistency"))
    ranks = [_rank(ret_dd), _rank(exp_r), _rank(pf), _rank(act), _rank(eff), _rank(jev), _rank(reg)]
    out = []
    for i, r in enumerate(rows):
        s = sum(rk[i] for rk in ranks) / len(ranks)
        if (r["edge"]["max_drawdown_pct"] or 0) >= cfg.gates.catastrophic_drawdown or r.get("liquidations"):
            s = min(s, 0.25)
        out.append(round(s, 4))
    return out


def regime_consistency(reg: dict[str, Any]) -> float | None:
    trend = {k: v for k, v in (reg.get("trend") or {}).items() if k != "UNKNOWN" and v["n"] >= 5}
    if not trend:
        return None
    return sum(1 for v in trend.values() if (v["mean_r"] or 0) > 0) / len(trend)


def matrix(rows: Sequence[dict[str, Any]], row_key: str, tfs: Sequence[str]) -> dict[str, Any]:
    """rows x timeframe: mean net return, pooled expectancy / PF / trades, mean drawdown, cost ratio."""
    cells: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        cells[r[row_key]][r["tf"]].append(r)
    out = {}
    for rk in sorted(cells):
        out[rk] = {}
        for tf in tfs:
            part = cells[rk].get(tf) or []
            if not part:
                out[rk][tf] = None
                continue
            nets = [p["edge"]["net_return_pct"] or 0.0 for p in part]
            out[rk][tf] = {
                "bots": len(part), "net_return_mean": _r(_mean(nets), 4),
                "profitable": sum(1 for x in nets if x > 0),
                "expectancy_r": _r(_mean([p["edge"]["net_expectancy_r"] for p in part]), 4),
                "pf": _r(_mean([min(p["edge"]["net_pf"], 5.0) for p in part if p["edge"]["net_pf"] is not None]), 3),
                "trades": sum(p["activity"]["trades"] for p in part),
                "max_dd_mean": _r(_mean([p["edge"]["max_drawdown_pct"] for p in part]), 4),
                "cost_ratio": _r(_mean([p["cost"]["cost_to_edge"] for p in part if p["cost"]["cost_to_edge"] is not None]), 3),
                "gross_positive": sum(1 for p in part if (p["edge"]["gross_pnl"] or 0) > 0)}
    return out


def random_baseline(jev_net: float, randoms: Sequence[float], pct: float) -> dict[str, Any]:
    if not randoms:
        return {"n": 0}
    return {"n": len(randoms), "p10": _r(_pct(randoms, 0.10), 4), "p50": _r(_pct(randoms, 0.50), 4),
            "p90": _r(_pct(randoms, pct), 4), "mean": _r(_mean(randoms), 4),
            "p_value": _r((sum(1 for x in randoms if x >= jev_net) + 1) / (len(randoms) + 1), 4),
            "jev_percentile": _r(sum(1 for x in randoms if x < jev_net) / len(randoms), 3)}
