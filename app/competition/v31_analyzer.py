"""BOT ANALYZER V3.1: why a bot makes or loses money, where its edge is lost, and what Jev adds.

Per bot:  ACTIVITY and the PARTICIPATION FUNNEL (raw setups -> legal -> positive edge -> Jev -> executed),
          EDGE and COST STRUCTURE (gross vs net, USDT / R / bps), PROFIT CONCENTRATION (without the best
          trade / best 3, top-3 share), ENTRY QUALITY (signed drift after entry at 15m..12h, MFE/MAE),
          EXIT QUALITY (exit mix, give-back after +1R, post-exit continuation), HOLDING PERIOD (median,
          gross by bucket), EDGE CALIBRATION (predicted vs realized net R, CONTROL), JEV (P(support) and
          action-confidence vs realized expectancy, SELECTION ALPHA vs the matched random action),
          ATTACK EFFECTIVENESS (ATTACK trades vs their counterfactual TAKE size), the QUALIFICATION V3.1
          gates, the DIAGNOSES and one primary failure mode.

The analyzer only reads finished bots. It never edits a strategy, a threshold or the edge model.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Mapping, Sequence

from app.competition import v3_analyzer as a3
from app.competition.jev_experiment import auc_with_ci
from app.competition.v31_config import PARTICIPATION_PER_DAY, MIN_SAMPLE, TIER_RISK, V31Config, min_trades
from app.competition.v31_diagnostics import HOLD_BUCKETS, Tape, summarize, trade_path
from app.competition.v31_diagnostics import verdict as path_verdict
from app.competition.v31_edge import calibration

_r, _mean, _pct, _pf = a3._r, a3._mean, a3._pct, a3._pf

LABEL = {"LIQUIDATION": "LIQUIDATION", "MIN_NOTIONAL_LIMITED": "MIN NOTIONAL LIMITED",
         "JEV_OVER_FILTERING": "JEV OVER FILTERED", "ENTRY_HAS_NO_EDGE": "ENTRY HAS NO EDGE",
         "EXIT_DESTROYS_EDGE": "EXIT DESTROYS EDGE", "HOLD_TOO_SHORT": "HOLD TOO SHORT",
         "COST_DESTROYED": "COST DESTROYED", "DRAWDOWN_FAILURE": "DRAWDOWN", "TOO_LITTLE_ACTIVITY": "TOO LITTLE ACTIVITY",
         "PROFIT_CONCENTRATED": "PROFIT CONCENTRATED", "MARGINAL_EDGE": "MARGINAL EDGE",
         "JEV_NO_SELECTION_ALPHA": "JEV NO SELECTION ALPHA", "INSUFFICIENT_SAMPLE": "INSUFFICIENT SAMPLE",
         "NO_TRADES": "NO TRADES", "ROBUST": "HEALTHY"}
SUPPORT_BUCKETS = ((0.0, 0.4), (0.4, 0.55), (0.55, 0.7), (0.7, 0.85), (0.85, 1.0001))
ATTACK_LEVELS = ("ATTACK", "STRONG_ATTACK")


# ---- per-bot blocks -----------------------------------------------------------------------------------------

def activity(rec: Mapping[str, Any]) -> dict[str, Any]:
    days = float(rec["window"]["days"]) or 1.0
    tf = rec["identity"]["timeframe"]
    trades = rec.get("trades") or []
    a = rec["activity"]
    entries = sorted(t["entry_ts"] for t in trades)
    gaps = [(b - x) / 60000.0 for x, b in zip(entries, entries[1:])]
    tpd = len(trades) / days
    need = PARTICIPATION_PER_DAY[tf]
    return {"days": days, "raw_setups": a["signals"], "raw_setups_per_day": _r(a["signals"] / days, 3),
            "trades": len(trades), "trades_per_day": _r(tpd, 3), "required_per_day": need,
            "participation_ok": tpd >= need, "adequate_sample": len(trades) >= MIN_SAMPLE,
            "min_trades": min_trades(tf, days),
            "avg_hold_min": _r(_mean([t["hold_s"] / 60.0 for t in trades]), 1),
            "median_gap_min": _r(statistics.median(gaps), 1) if gaps else None,
            "below_exchange_minimum": a.get("below_exchange_minimum", 0), "edge_rejected": a.get("edge_rejected", 0)}


def concentration(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    nets = sorted((float(t["net"]) for t in trades), reverse=True)
    if not nets:
        return {"n": 0}
    pos = sum(x for x in nets if x > 0)
    top3 = sum(x for x in nets[:3] if x > 0)
    return {"n": len(nets), "net": _r(sum(nets)), "best_trade": _r(nets[0]),
            "net_without_best": _r(sum(nets) - nets[0]), "net_without_top3": _r(sum(nets) - sum(nets[:3])),
            "top3_share_of_profit": _r(top3 / pos, 3) if pos > 0 else None,
            "best_share_of_profit": _r(max(0.0, nets[0]) / pos, 3) if pos > 0 else None}


def cost_structure(trades: Sequence[Mapping[str, Any]], cost: Mapping[str, Any]) -> dict[str, Any]:
    risk = [t.get("risk_usd") or 0.0 for t in trades]
    cost_r = [(t["fees"] + t["slippage"]) / r for t, r in zip(trades, risk) if r]
    gross_r = [t["gross"] / r for t, r in zip(trades, risk) if r]
    return {**dict(cost), "cost_r_per_trade": _r(_mean(cost_r)), "gross_r_per_trade": _r(_mean(gross_r)),
            "fee_share_of_cost": _r(sum(t["fees"] for t in trades) / max(1e-12, sum(t["fees"] + t["slippage"] for t in trades)), 3)
            if trades else None}


def entry_exit(trades: Sequence[Mapping[str, Any]], tape: Tape | None) -> dict[str, Any]:
    if not trades:
        return {"n": 0}
    paths = [trade_path(dict(t), tape) for t in trades]
    s = summarize([dict(t) for t in trades], paths)
    s["verdict"] = path_verdict(s) if tape is not None else None
    s["tp_hit_share"] = _r(sum(1 for t in trades if t.get("tp_hit")) / len(trades), 3)
    return s


def attack_effectiveness(trades: Sequence[Mapping[str, Any]], role: str) -> dict[str, Any]:
    """ATTACK / STRONG_ATTACK trades vs their counterfactual TAKE size (PnL scales with size)."""
    def level(t: Mapping[str, Any]) -> str:
        return str((t.get("jev_level") if role in ("JEV", "RANDOM") else t.get("tier")) or "TAKE")

    def mult(t: Mapping[str, Any]) -> float:
        m = t.get("jev_mult") if role in ("JEV", "RANDOM") else None
        if isinstance(m, (int, float)) and m > 0:
            return float(m)
        rp = t.get("risk_pct")
        return max(1.0, float(rp) / TIER_RISK["TAKE"]) if isinstance(rp, (int, float)) and rp > 0 else 1.0
    att = [t for t in trades if level(t) in ATTACK_LEVELS]
    take = [t for t in trades if level(t) not in ATTACK_LEVELS]
    net = sum(float(t["net"]) for t in att)
    normal = sum(float(t["net"]) / mult(t) for t in att)
    return {"attack_trades": len(att), "strong_attack_trades": sum(1 for t in att if level(t) == "STRONG_ATTACK"),
            "attack_share": _r(len(att) / len(trades), 3) if trades else None,
            "attack_expectancy_r": _r(_mean([t["r"] for t in att])), "take_expectancy_r": _r(_mean([t["r"] for t in take])),
            "attack_win_rate": _r(sum(1 for t in att if t["net"] > 0) / len(att), 3) if att else None,
            "attack_net": _r(net), "counterfactual_normal_net": _r(normal), "attack_added_net": _r(net - normal),
            "downgrades": a3_count(t.get("attack_downgrade") for t in trades)}


def a3_count(items: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        if not x:
            continue
        k = str(x).split(":")[0] if str(x).startswith("ATTACK_NOT_LEGAL") else str(x)[:60]
        out[k] = out.get(k, 0) + 1
    return out


def jev_block(rec: Mapping[str, Any]) -> dict[str, Any] | None:
    if rec["role"] != "JEV":
        return None
    ds = rec.get("decisions") or []
    n = len(ds)
    chosen: dict[str, int] = defaultdict(int)
    final: dict[str, int] = defaultdict(int)
    for d in ds:
        chosen[d.get("chosen") or "ERROR"] += 1
        final[d.get("level") or "SKIP"] += 1
    resolved = [d for d in ds if d.get("r") is not None]
    win = lambda d: 1 if (d.get("net") or 0) > 0 else 0  # noqa: E731
    lab = [(float(d["p_support"]), win(d)) for d in resolved if d.get("p_support") is not None]
    conf = [(1.0 - float(d["p_skip"]), win(d)) for d in resolved if d.get("p_skip") is not None]
    by_action = {}
    for lvl in ("SKIP", "TAKE", "ATTACK", "STRONG_ATTACK"):
        part = [d for d in resolved if d.get("level") == lvl]
        by_action[lvl] = {"n": len(part), "mean_r": _r(_mean([d["r"] for d in part]), 3),
                          "win_rate": _r(sum(win(d) for d in part) / len(part), 3) if part else None,
                          "net": _r(sum(float(d.get("net") or 0.0) for d in part), 4),
                          "outcome": "shadow (not traded)" if lvl == "SKIP" else "traded"}
    by_choice = {}
    for lvl in ("SKIP", "TAKE", "ATTACK"):
        part = [d for d in resolved if d.get("chosen") == lvl]
        by_choice[lvl] = {"n": len(part), "mean_r": _r(_mean([d["r"] for d in part]), 3)}
    support = []
    for lo, hi in SUPPORT_BUCKETS:
        part = [d for d in resolved if d.get("p_support") is not None and lo <= d["p_support"] < hi]
        support.append({"bucket": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": len(part),
                        "mean_r": _r(_mean([d["r"] for d in part]), 3),
                        "win_rate": _r(sum(win(d) for d in part) / len(part), 3) if part else None})
    skipped = [d for d in resolved if d.get("level") == "SKIP"]
    api = [d for d in ds if d.get("source") == "api"]
    lat = [d["latency_ms"] for d in api if d.get("latency_ms") and not d.get("error")]
    errors = [d for d in ds if d.get("error")]
    ps = [d["p_support"] for d in ds if d.get("p_support") is not None]
    return {"decisions": n, "chosen": dict(chosen), "final": dict(final),
            "skip_rate": _r(final.get("SKIP", 0) / n, 4) if n else None,
            "attack_rate": _r((final.get("ATTACK", 0) + final.get("STRONG_ATTACK", 0)) / n, 4) if n else None,
            "acceptance_rate": _r(1 - final.get("SKIP", 0) / n, 4) if n else None,
            "p_support_quartiles": [_r(_pct(ps, q), 3) for q in (0.0, 0.25, 0.5, 0.75, 1.0)] if ps else None,
            "auc_support": {k: _r(v, 4) if isinstance(v, float) else v for k, v in auc_with_ci(lab).items()},
            "auc_not_skip": {k: _r(v, 4) if isinstance(v, float) else v for k, v in auc_with_ci(conf).items()},
            "by_action": by_action, "by_choice": by_choice, "support_buckets": support,
            "skipped_counterfactual_net": _r(sum(float(d.get("net") or 0.0) for d in skipped), 4),
            "downgrades": a3_count(d.get("downgrade") for d in ds),
            "min_notional_after_jev": sum(1 for d in ds if str(d.get("result") or "").startswith("RISK_REJECTED:below_min")),
            "latency_ms": {"p50": _r(_pct(lat, 0.5), 0), "p95": _r(_pct(lat, 0.95), 0), "p99": _r(_pct(lat, 0.99), 0),
                           "n": len(lat)},
            "api_calls": len(api), "errors": len(errors), "error_rate": _r(len(errors) / n, 4) if n else None,
            "cost_usd": _r(sum(float(d.get("cost_usd") or 0.0) for d in ds), 5)}


def selection_alpha(jev: Mapping[str, Any] | None, randoms: Sequence[Mapping[str, Any]], pct: float) -> dict[str, Any]:
    """JEV SELECTION ALPHA = Jev realized - the matched random-action twins (same action rates, same rules)."""
    if jev is None or not randoms:
        return {"n": 0}
    jm = jev.get("metrics") or {}
    net = float(jm.get("net_profit") or 0.0)
    nets = [float((r.get("metrics") or {}).get("net_profit") or 0.0) for r in randoms]
    exps = [(r.get("metrics") or {}).get("expectancy_r") for r in randoms]
    med = _pct(nets, 0.5)
    return {"n": len(nets), "jev_net": _r(net), "random_p10": _r(_pct(nets, 0.10)), "random_median": _r(med),
            "random_p90": _r(_pct(nets, pct)), "alpha_usdt": _r(net - med) if med is not None else None,
            "jev_expectancy_r": _r(jm.get("expectancy_r")), "random_expectancy_r": _r(_mean([x for x in exps if x is not None])),
            "alpha_r": _r((jm.get("expectancy_r") or 0.0) - (_mean([x for x in exps if x is not None]) or 0.0)),
            "p_value": _r((sum(1 for x in nets if x >= net) + 1) / (len(nets) + 1)),
            "jev_percentile": _r(sum(1 for x in nets if x < net) / len(nets), 3)}


# ---- gates, diagnoses, state --------------------------------------------------------------------------------

def gates(rec: Mapping[str, Any], act: Mapping[str, Any], edge: Mapping[str, Any], conc: Mapping[str, Any],
          jev: Mapping[str, Any] | None, base: Mapping[str, Any] | None, cfg: V31Config) -> list[dict[str, Any]]:
    g, m = cfg.gates, rec.get("metrics") or {}
    halted = bool(rec["activity"].get("halted"))
    top3 = conc.get("top3_share_of_profit")
    out = [
        {"name": "adequate sample", "ok": act["trades"] >= MIN_SAMPLE, "actual": act["trades"], "threshold": f">= {MIN_SAMPLE} trades"},
        {"name": "participation", "ok": bool(act["participation_ok"]), "actual": act["trades_per_day"],
         "threshold": f">= {act['required_per_day']} trades/day"},
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
            {"name": "latency", "ok": (jev["latency_ms"].get("p95") or 0) <= g.max_latency_p95_ms,
             "actual": jev["latency_ms"].get("p95"), "threshold": f"p95 <= {g.max_latency_p95_ms:.0f} ms"},
        ]
    return out


def diagnoses(rec: Mapping[str, Any], act: Mapping[str, Any], edge: Mapping[str, Any], cost: Mapping[str, Any],
              conc: Mapping[str, Any], path: Mapping[str, Any], jev: Mapping[str, Any] | None,
              gs: Sequence[Mapping[str, Any]], cfg: V31Config) -> tuple[str, list[str]]:
    """Every diagnosis that applies, and the ROOT cause first (a bot that loses before costs and is
    then halted failed for lack of edge; its drawdown and short trade list are consequences)."""
    m = rec.get("metrics") or {}
    failed = {x["name"] for x in gs if not x["ok"]}
    found: list[str] = []
    gross, net = m.get("gross_pnl") or 0.0, m.get("net_profit") or 0.0
    trades = act["trades"]
    raw = max(1, act["raw_setups"])
    if (m.get("liquidation_count") or 0) > 0:
        found.append("LIQUIDATION")
    if act["below_exchange_minimum"] >= 0.25 * raw or (rec.get("funnel") or {}).get("min_notional_after_jev"):
        found.append("MIN_NOTIONAL_LIMITED")
    if jev is not None and (jev.get("skip_rate") or 0) > cfg.gates.max_skip_rate:
        found.append("JEV_OVER_FILTERING")
    if trades == 0:
        found.append("NO_TRADES")
    elif gross <= 0:
        v = path.get("verdict")
        if v == "EXIT DESTROYS EDGE":
            found.append("EXIT_DESTROYS_EDGE")
        elif v == "HOLD TOO SHORT":
            found.append("HOLD_TOO_SHORT")
        else:
            found.append("ENTRY_HAS_NO_EDGE")
    elif net <= 0:
        found.append("COST_DESTROYED")
    if bool(rec["activity"].get("halted")) or (m.get("max_drawdown_pct") or 0) >= cfg.gates.max_drawdown_pct:
        found.append("DRAWDOWN_FAILURE")
    if not act["participation_ok"]:
        found.append("TOO_LITTLE_ACTIVITY")
    if trades and trades < MIN_SAMPLE:
        found.append("INSUFFICIENT_SAMPLE")
    if "not concentrated" in failed and net > 0:
        found.append("PROFIT_CONCENTRATED")
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
    if not act["participation_ok"] and (m.get("expectancy_r") or 0) > 0 and (m.get("net_profit") or 0) > 0 and not others:
        return "LOW_ACTIVITY_EDGE"
    return "FAIL"


def analyze_bot(rec: Mapping[str, Any], cfg: V31Config, tape: Tape | None = None,
                base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    act = activity(rec)
    edge, cost = a3.edge_and_cost(dict(rec))
    trades = rec.get("trades") or []
    conc = concentration(trades)
    costs = cost_structure(trades, cost)
    path = entry_exit(trades, tape)
    jev = jev_block(rec)
    att = attack_effectiveness(trades, rec["role"])
    calib = calibration(rec.get("edge_rows") or []) if rec.get("edge_rows") else None
    gs = gates(rec, act, edge, conc, jev, base, cfg)
    primary, found = diagnoses(rec, act, edge, cost, conc, path, jev, gs, cfg)
    state = state_of(rec, act, gs)
    hold = {"median_hold_min": path.get("median_hold_min"), "buckets": path.get("hold_buckets")}
    exits = {"mix": path.get("exits"), "tp_hit_share": path.get("tp_hit_share"),
             "gave_back_share": path.get("gave_back_share"), "post_exit_drift_r_winners": path.get("post_exit_drift_r_winners"),
             "post_exit_drift_r_losers": path.get("post_exit_drift_r_losers"), "avg_win_r": path.get("avg_win_r"),
             "avg_loss_r": path.get("avg_loss_r"), "verdict": path.get("verdict")}
    entry = {"drift_bps": path.get("drift_bps"), "best_drift_bps": path.get("best_drift_bps"),
             "mfe_r_mean": path.get("mfe_r_mean"), "mae_r_mean": path.get("mae_r_mean"),
             "reached_1r_share": path.get("reached_1r_share"), "win_rate": path.get("win_rate")}
    summary = {"result": "PASS" if state == "ADVANCE" else state, "primary_issue": LABEL.get(primary, primary),
               "diagnoses": [LABEL.get(x, x) for x in found], "gross_expectancy_r": edge["gross_expectancy_r"],
               "net_expectancy_r": edge["net_expectancy_r"], "trades_per_day": act["trades_per_day"],
               "max_drawdown_pct": edge["max_drawdown_pct"], "net_without_top3": conc.get("net_without_top3"),
               "jev_skip": jev["skip_rate"] if jev else None,
               "jev_auc": (jev.get("auc_support") or {}).get("auc") if jev else None,
               "selection_alpha": ((base or {}).get("selection") or {}).get("alpha_usdt")}
    return {"activity": act, "funnel": rec.get("funnel"), "edge": edge, "cost": costs, "concentration": conc,
            "entry": entry, "exit": exits, "holding": hold, "calibration": calib, "jev": jev, "attack": att,
            "gates": gs, "state": state, "failure_mode": primary, "diagnoses": found, "summary": summary,
            "baselines": dict(base) if base else None}


# ---- arena level ---------------------------------------------------------------------------------------------

def viability(controls: Sequence[Mapping[str, Any]], raw: Sequence[Mapping[str, Any]], tfs: Sequence[str]) -> dict[str, Any]:
    """SMALL-TIMEFRAME VIABILITY: per timeframe, the RAW evidence (every sequenced candidate, ungated) and
    the edge-gated CONTROL bots: signals, trades, gross/cost/net expectancy, PF, drawdown, profitable %."""
    out: dict[str, Any] = {}
    for tf in tfs:
        cs = [r for r in controls if r["identity"]["timeframe"] == tf]
        obs = [o for r in raw if r["identity"]["timeframe"] == tf for o in (r.get("observations") or []) if o.get("sequenced")]
        trades = [t for r in cs for t in (r.get("trades") or [])]
        risk = [t.get("risk_usd") or 0.0 for t in trades]
        g_r = [t["gross"] / x for t, x in zip(trades, risk) if x]
        c_r = [(t["fees"] + t["slippage"]) / x for t, x in zip(trades, risk) if x]
        n_r = [t["r"] for t in trades]
        notional = [t.get("notional") or 0.0 for t in trades]
        out[tf] = {
            "raw": {"candidates": sum(len(r.get("observations") or []) for r in raw if r["identity"]["timeframe"] == tf),
                    "sequenced": len(obs), "gross_r": _r(_mean([o["net_r"] + o["cost_r"] for o in obs if o.get("net_r") is not None])),
                    "cost_r": _r(_mean([o["cost_r"] for o in obs if o.get("cost_r") is not None])),
                    "net_r": _r(_mean([o["net_r"] for o in obs if o.get("net_r") is not None])),
                    "win_rate": _r(sum(1 for o in obs if (o.get("net_r") or 0) > 0) / len(obs), 3) if obs else None},
            "bots": len(cs), "signals": sum(r["activity"]["signals"] for r in cs),
            "edge_passed": sum((r.get("funnel") or {}).get("positive_edge") or 0 for r in cs),
            "trades": len(trades),
            "trades_per_bot_day": _r(len(trades) / max(1e-9, sum(r["window"]["days"] for r in cs)), 3) if cs else None,
            "gross_expectancy_r": _r(_mean(g_r)), "cost_r": _r(_mean(c_r)), "net_expectancy_r": _r(_mean(n_r)),
            "gross_bps": _r(_mean([t["gross"] / n * 1e4 for t, n in zip(trades, notional) if n]), 2),
            "cost_bps": _r(_mean([(t["fees"] + t["slippage"]) / n * 1e4 for t, n in zip(trades, notional) if n]), 2),
            "net_pnl": _r(sum(t["net"] for t in trades)), "pf": _r(_pf([t["net"] for t in trades]), 3),
            "max_dd_mean": _r(_mean([(r.get("metrics") or {}).get("max_drawdown_pct") for r in cs])),
            "profitable_bots": sum(1 for r in cs if ((r.get("metrics") or {}).get("net_profit") or 0) > 0),
            "profitable_pct": _r(sum(1 for r in cs if ((r.get("metrics") or {}).get("net_profit") or 0) > 0) / len(cs), 3) if cs else None,
            "profitable_active": sum(1 for r in cs if ((r.get("metrics") or {}).get("net_profit") or 0) > 0
                                     and len(r.get("trades") or []) >= MIN_SAMPLE)}
    return out


def funnel_totals(recs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    keys = ("raw_setups", "legal", "positive_edge", "jev_accepted", "executed")
    tot: dict[str, Any] = {k: 0 for k in keys}
    for r in recs:
        f = r.get("funnel") or {}
        for k in keys:
            if isinstance(f.get(k), (int, float)):
                tot[k] += f[k]
    return tot


def holding_table(recs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Gross / net and mean R per holding bucket, pooled per timeframe."""
    out: dict[str, Any] = {}
    for tf in ("3m", "5m", "15m", "30m"):
        trades = [t for r in recs if r["identity"]["timeframe"] == tf for t in (r.get("trades") or [])]
        if not trades:
            continue
        rows = {}
        for name, lo, hi in HOLD_BUCKETS:
            part = [t for t in trades if lo <= t["hold_s"] < hi]
            rows[name] = {"n": len(part), "gross": _r(sum(t["gross"] for t in part), 4),
                          "net": _r(sum(t["net"] for t in part), 4), "mean_r": _r(_mean([t["r"] for t in part]), 3)}
        out[tf] = {"trades": len(trades), "median_hold_min": _r(statistics.median([t["hold_s"] for t in trades]) / 60.0, 1),
                   "buckets": rows}
    return out


def scores(rows: Sequence[Mapping[str, Any]], cfg: V31Config) -> list[float]:
    """Ranking only (never a gate): mean percentile of return/DD, expectancy, PF (capped), trades/day
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


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)
