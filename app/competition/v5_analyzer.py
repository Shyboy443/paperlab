"""BOT ANALYZER V5 (docs/V5_PROTOCOL.md): raw edge first, then costs, then activity, then Jev.

Per bot:    activity (opportunities checked, signals / day, trades / day and / week, MIN_NOTIONAL refusals, hold time),
            the GROSS -> maker fees -> taker fees -> slippage -> funding paid / received -> NET waterfall, net edge per
            trade and per day, gross and net expectancy in R (net INCLUDES funding), PF, drawdown, bootstrap
            P(mean <= 0) and a 90% interval, concentration, exit mix, MFE / MAE / drift at 1h..72h after the entry, an
            activity label, the V5 gates and ONE primary failure in root-cause order.
Per family x horizon (pooled over its coins): the RAW-EDGE gate (gross R of the 100 USDT CAPACITY twins -- Amendment
            1), the ECONOMIC gate (net R of the 20 USDT books), the regime and volatility split, and on DEVELOPMENT
            the exit grid (time stop x target, the same entries, costs and actual funding settlements).
Arena:      HOURLY / DAILY VIABILITY, the cost totals and the leaderboard rows.

It only reads finished bots; it never edits a strategy, a threshold or an exit.
"""
from __future__ import annotations

import datetime as dt
import math
import random
import statistics
from bisect import bisect_left
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from app.competition import v31_analyzer as a31
from app.competition import v4_analyzer as a4
from app.competition.v31_diagnostics import Tape
from app.competition.v5_config import V5Config

concentration, jev_block, selection_alpha = a31.concentration, a31.jev_block, a31.selection_alpha
HOUR = 3_600_000
DAY = 86_400_000

FAMILY_VERDICT = {"PASSED": "raw edge and after-cost economics both passed",
                  "NO_RAW_EDGE": "no raw (gross) edge on the 100 USDT twins",
                  "COST_DESTROYED": "a raw edge that fees, slippage and funding remove at 20 USDT",
                  "SMALL_ACCOUNT_CONSTRAINED": "a raw edge, but the 20 USDT books cannot legally take enough of its setups",
                  "NOT_EVALUATED": "not evaluated"}
PRIORITY = ("NO_TRADES", "LIQUIDATION", "NO_RAW_EDGE", "COST_DESTROYED", "MIN_NOTIONAL_LIMITED", "INSUFFICIENT_SAMPLE",
            "TOO_LITTLE_ACTIVITY", "NOT_SIGNIFICANT", "MARGINAL_EDGE", "DRAWDOWN_FAILURE", "PROFIT_CONCENTRATED",
            "FAMILY_NOT_QUALIFIED", "JEV_OVER_FILTERING", "JEV_NO_SELECTION_ALPHA", "JEV_UNRELIABLE")


def _r(x: Any, nd: int = 4) -> Any:
    return round(float(x), nd) if isinstance(x, (int, float)) and math.isfinite(float(x)) else None


def _mean(xs: Iterable[Any]) -> float | None:
    v = [float(x) for x in xs if isinstance(x, (int, float)) and math.isfinite(float(x))]
    return sum(v) / len(v) if v else None


def _day_ms(d: str) -> int:
    return int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def bootstrap(xs: Sequence[float], n_boot: int = 2000, seed: int = 7) -> dict[str, Any]:
    """One-sided P(mean <= 0) and the 90% percentile interval of the mean (resampling trades)."""
    if len(xs) < 2:
        return {"p_mean_le_0": None, "ci90": None}
    rng = random.Random(seed)
    n = len(xs)
    means = sorted(sum(rng.choices(xs, k=n)) / n for _ in range(n_boot))
    return {"p_mean_le_0": sum(1 for m in means if m <= 0) / n_boot,
            "ci90": [_r(means[int(0.05 * n_boot)]), _r(means[int(0.95 * n_boot) - 1])]}


# ---- per trade ---------------------------------------------------------------------------------------------------

def stop_dist(t: Mapping[str, Any]) -> float | None:
    """Per-unit stop distance. The engine's R is (PnL - fees) / (qty x |fill - stop|) and EXCLUDES funding, while a
    trade row's net includes it, so the funding is taken out before recovering the distance."""
    try:
        qty, r = float(t["qty"]), float(t.get("r") or 0.0)
        base = float(t.get("net") or 0.0) - float(t.get("funding") or 0.0)
        if qty > 0 and abs(r) > 1e-9 and abs(base) > 1e-12:
            return abs(base / r) / qty
        return float(t["risk_usd"]) / qty if t.get("risk_usd") and qty > 0 else None
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def risk_of(t: Mapping[str, Any]) -> float | None:
    sd = stop_dist(t)
    return sd * float(t["qty"]) if sd else None


def gross_r(t: Mapping[str, Any]) -> float | None:
    """Price movement only (decision prices), before any fee, slippage or funding, in R."""
    risk = risk_of(t)
    return float(t["gross"]) / risk if risk else None


def net_r(t: Mapping[str, Any]) -> float | None:
    """After fees, slippage AND funding, in R."""
    risk = risk_of(t)
    return float(t["net"]) / risk if risk else None


def cost_r(t: Mapping[str, Any]) -> float | None:
    risk = risk_of(t)
    return (float(t.get("fees") or 0.0) + float(t.get("slippage") or 0.0)) / risk if risk else None


def funding_r(t: Mapping[str, Any]) -> float | None:
    risk = risk_of(t)
    return float(t.get("funding") or 0.0) / risk if risk else None


# ---- per bot -------------------------------------------------------------------------------------------------

def horizon_excursions(trades: Sequence[Mapping[str, Any]], tape: Tape | None,
                       horizons: Sequence[int]) -> dict[str, Any]:
    """For every trade, the favourable / adverse excursion and the drift (close vs entry) within h hours of the
    entry, WHATEVER the actual exit was, in R of the trade's own stop distance. DEVELOPMENT evidence for the exit."""
    return excursion_summary(excursion_acc(trades, tape, horizons))


def excursion_summary(acc: Mapping[int, Mapping[str, list[float]]]) -> dict[str, Any]:
    return {f"{h}h": {"n": len(v["mfe"]), "mfe_r": _r(_mean(v["mfe"])), "mae_r": _r(_mean(v["mae"])),
                      "drift_r": _r(_mean(v["drift"])),
                      "reached_1r": _r(sum(1 for x in v["mfe"] if x >= 1.0) / len(v["mfe"]), 3)}
            for h, v in sorted(acc.items()) if v["mfe"]}


def excursion_acc(trades: Sequence[Mapping[str, Any]], tape: Tape | None, horizons: Sequence[int],
                  acc: dict[int, dict[str, list[float]]] | None = None) -> dict[int, dict[str, list[float]]]:
    """The per-trade lists behind horizon_excursions, so books on different coins (different tapes) can be pooled."""
    hs = sorted(horizons)
    acc = acc if acc is not None else {h: {"mfe": [], "mae": [], "drift": []} for h in hs}
    if tape is None or not trades:
        return acc
    n = len(tape.t)
    for t in trades:
        sd = stop_dist(t)
        if not sd:
            continue
        e, a = float(t["entry"]), int(t["entry_ts"])
        sgn = 1.0 if t.get("side") == "long" else -1.0
        i = tape.index(a)
        hi, lo = -math.inf, math.inf
        for h in hs:
            j = min(n, tape.index(a + h * HOUR))
            if j > i:
                hi, lo = max(hi, max(tape.h[i:j])), min(lo, min(tape.lo[i:j]))
                i = j
            if hi == -math.inf or j >= n:
                break
            fav, adv = (hi - e, e - lo) if sgn > 0 else (e - lo, hi - e)
            acc[h]["mfe"].append(max(0.0, fav) / sd)
            acc[h]["mae"].append(max(0.0, adv) / sd)
            acc[h]["drift"].append(sgn * (tape.c[j - 1] - e) / sd)
    return acc


def maker_bound_r(t: Mapping[str, Any], maker_rate: float, taker_rate: float) -> float | None:
    """BEST-CASE maker entry (Amendment 2b): the entry filled as maker at the signal price with no spread saves the
    taker-maker fee difference on the entry notional plus the entry half of the measured slippage. An upper bound:
    a real resting order also misses fills, mostly on the trades that run away (the winners)."""
    risk = risk_of(t)
    if not risk:
        return None
    notional = float(t.get("notional") or 0.0)
    return ((taker_rate - maker_rate) * notional + 0.5 * float(t.get("slippage") or 0.0)) / risk


def money(rec: Mapping[str, Any]) -> dict[str, Any]:
    """The GROSS -> NET waterfall. Every figure in USDT; the R figures use each trade's actual initial risk."""
    m, c = rec.get("metrics") or {}, rec.get("costs") or {}
    trades = rec.get("trades") or []
    days = float(rec["window"]["days"]) or 1.0
    n = len(trades)
    net = float(m.get("net_profit") or 0.0)
    nets = [float(t["net"]) for t in trades]
    win, loss = sum(x for x in nets if x > 0), -sum(x for x in nets if x < 0)
    paid, recv = float(c.get("funding_paid") or 0.0), float(c.get("funding_received") or 0.0)
    return {"gross": _r(m.get("gross_pnl")), "maker_fees": _r(c.get("maker_fees")), "taker_fees": _r(c.get("taker_fees")),
            "fees": _r(float(c.get("maker_fees") or 0.0) + float(c.get("taker_fees") or 0.0)),
            "slippage": _r(m.get("slippage_cost")), "funding_paid": _r(paid), "funding_received": _r(recv),
            "funding_net": _r(recv - paid), "net": _r(net), "return_pct": _r(m.get("net_return_pct")),
            "net_per_trade": _r(net / n) if n else None, "net_per_day": _r(net / days, 5),
            "gross_expectancy_r": _r(_mean(gross_r(t) for t in trades)),
            "cost_r": _r(_mean(cost_r(t) for t in trades)), "funding_r": _r(_mean(funding_r(t) for t in trades)),
            "net_expectancy_r": _r(_mean(net_r(t) for t in trades)),
            "profit_factor": _r(win / loss, 3) if loss else None,
            "win_rate": _r(sum(1 for x in nets if x > 0) / n, 3) if n else None,
            "max_drawdown_pct": _r(m.get("max_drawdown_pct")), "liquidations": int(m.get("liquidation_count") or 0)}


def activity(rec: Mapping[str, Any], cfg: V5Config) -> dict[str, Any]:
    a, trades = rec["activity"], rec.get("trades") or []
    days = float(rec["window"]["days"]) or 1.0
    hourly = rec.get("horizon") == "HOURLY"
    holds = [float(t.get("hold_s") or 0) / 3600 for t in trades]
    sig = int(a.get("signals") or 0)
    refused = int(a.get("below_exchange_minimum") or 0)
    need = dict(cfg.gates.min_trades_per_day)[rec["horizon"]]
    return {"days": days, "opportunities_checked": int(days * (24 if hourly else 6)), "signals": sig,
            "signals_per_day": _r(sig / days, 3), "trades": len(trades), "trades_per_day": _r(len(trades) / days, 4),
            "trades_per_week": _r(7 * len(trades) / days, 2), "required_per_day": _r(need, 4),
            "min_notional_refused": refused, "min_notional_share": _r(refused / sig, 3) if sig else None,
            "avg_hold_h": _r(_mean(holds), 2), "median_hold_h": _r(statistics.median(holds), 2) if holds else None,
            "halted": bool(a.get("halted"))}


def exit_mix(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for t in trades:
        by[str(t.get("exit_kind") or "?")].append(t)
    return {k: {"n": len(v), "gross_r": _r(_mean(gross_r(t) for t in v)), "net_r": _r(_mean(net_r(t) for t in v)),
                "avg_hold_h": _r(_mean(float(t.get("hold_s") or 0) / 3600 for t in v), 2)}
            for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))}


def label(act: Mapping[str, Any], mon: Mapping[str, Any], cfg: V5Config) -> tuple[str, list[str]]:
    """The activity x edge quadrant, plus MIN_NOTIONAL_LIMITED when a quarter or more of the setups were refused."""
    flags = ["MIN_NOTIONAL_LIMITED"] if (act.get("min_notional_share") or 0) >= 0.25 else []
    if act["trades"] == 0:
        return ("MIN_NOTIONAL_LIMITED" if act["min_notional_refused"] else "NO_TRADES"), flags
    active = (act["trades_per_day"] or 0) >= act["required_per_day"] and act["trades"] >= cfg.gates.min_trades
    positive = (mon["net"] or 0) > 0 and (mon["net_expectancy_r"] or 0) > 0
    if positive:
        return ("ACTIVE_POSITIVE_EDGE" if active else "POSITIVE_EDGE_LOW_ACTIVITY"), flags
    return ("ACTIVE_NO_EDGE" if active else "INACTIVE_NO_EDGE"), flags


def gates(rec: Mapping[str, Any], act: Mapping[str, Any], mon: Mapping[str, Any], conc: Mapping[str, Any],
          boot: Mapping[str, Any], family: Mapping[str, Any] | None, jev: Mapping[str, Any] | None,
          base: Mapping[str, Any] | None, cfg: V5Config) -> list[dict[str, Any]]:
    g = cfg.gates
    top3 = conc.get("top3_share_of_profit")
    p0 = boot.get("p_mean_le_0")
    fam_ok = bool(family and family.get("verdict") == "PASSED")
    limited = (act.get("min_notional_share") or 0) >= 0.5
    out = [
        {"code": "NO_TRADES", "name": "trades at all", "ok": act["trades"] > 0, "actual": act["trades"], "threshold": "> 0"},
        {"code": "MIN_NOTIONAL_LIMITED" if limited else "INSUFFICIENT_SAMPLE", "name": "adequate sample",
         "ok": act["trades"] >= g.min_trades, "actual": act["trades"], "threshold": f">= {g.min_trades}"},
        {"code": "TOO_LITTLE_ACTIVITY", "name": "activity", "ok": (act["trades_per_day"] or 0) >= act["required_per_day"],
         "actual": act["trades_per_day"], "threshold": f">= {act['required_per_day']:.3g} trades/day"},
        {"code": "NO_RAW_EDGE", "name": "raw edge (gross expectancy)", "ok": (mon["gross_expectancy_r"] or 0) > 0,
         "actual": mon["gross_expectancy_r"], "threshold": "> 0 R"},
        {"code": "COST_DESTROYED", "name": "net PnL after fees, slippage and funding", "ok": (mon["net"] or 0) > 0,
         "actual": mon["net"], "threshold": "> 0 USDT"},
        {"code": "COST_DESTROYED", "name": "net expectancy (incl. funding)", "ok": (mon["net_expectancy_r"] or 0) > 0,
         "actual": mon["net_expectancy_r"], "threshold": "> 0 R"},
        {"code": "MARGINAL_EDGE", "name": "profit factor", "ok": (mon["profit_factor"] or 0) >= g.min_profit_factor,
         "actual": mon["profit_factor"], "threshold": f">= {g.min_profit_factor}"},
        {"code": "NOT_SIGNIFICANT", "name": "distinguishable from zero", "ok": p0 is not None and p0 <= g.max_p_mean_le_0,
         "actual": p0, "threshold": f"bootstrap P(mean net R <= 0) <= {g.max_p_mean_le_0:.0%}"},
        {"code": "DRAWDOWN_FAILURE", "name": "drawdown / never halted",
         "ok": (mon["max_drawdown_pct"] or 0) <= g.max_drawdown_pct and not act["halted"],
         "actual": mon["max_drawdown_pct"], "threshold": f"<= {g.max_drawdown_pct:.0%}, no halt"},
        {"code": "LIQUIDATION", "name": "no liquidation", "ok": mon["liquidations"] <= g.max_liquidations,
         "actual": mon["liquidations"], "threshold": "0"},
        {"code": "PROFIT_CONCENTRATED", "name": "not concentrated",
         "ok": (conc.get("net_without_top3") or -1) > 0 and (top3 is None or top3 <= g.max_top3_share),
         "actual": f"ex-top3 {conc.get('net_without_top3')}, top3 share {top3}", "threshold": "> 0 without the best 3; top 3 <= 60%"},
        {"code": "FAMILY_NOT_QUALIFIED", "name": "family passed the raw-edge and economic gates", "ok": fam_ok,
         "actual": (family or {}).get("verdict", "NOT_EVALUATED"), "threshold": "PASSED on DEVELOPMENT"},
    ]
    if rec["role"] == "JEV" and jev is not None:
        b, sa = base or {}, (base or {}).get("selection") or {}
        net = mon["net"] or 0.0
        auc = jev.get("auc_support") or {}
        out += [
            {"code": "JEV_OVER_FILTERING", "name": "Jev participation", "ok": (jev.get("skip_rate") or 1.0) <= g.max_skip_rate,
             "actual": jev.get("skip_rate"), "threshold": f"skip <= {g.max_skip_rate:.0%}"},
            {"code": "JEV_NO_SELECTION_ALPHA", "name": "beats matched RANDOM action",
             "ok": sa.get("random_p90") is not None and net > sa["random_p90"] and (sa.get("alpha_usdt") or 0) > 0,
             "actual": f"alpha {sa.get('alpha_usdt')}", "threshold": "> random 90th percentile"},
            {"code": "JEV_NO_SELECTION_ALPHA", "name": "meaningfully beats CONTROL",
             "ok": b.get("control_net") is not None and net - b["control_net"] >= g.min_delta_vs_control,
             "actual": _r(net - (b.get("control_net") or 0.0)), "threshold": f">= +{g.min_delta_vs_control} USDT"},
            {"code": "JEV_NO_SELECTION_ALPHA", "name": "confidence discrimination",
             "ok": auc.get("low") is not None and auc["low"] > g.min_auc_low,
             "actual": f"AUC {auc.get('auc')} [{auc.get('low')}, {auc.get('high')}]", "threshold": "95% CI above 0.50"},
            {"code": "JEV_UNRELIABLE", "name": "API reliability", "ok": (jev.get("error_rate") or 0.0) <= g.max_error_rate,
             "actual": jev.get("error_rate"), "threshold": f"<= {g.max_error_rate:.0%}"}]
    return out


def primary_failure(gs: Sequence[Mapping[str, Any]]) -> str | None:
    failed = {x["code"] for x in gs if not x["ok"]}
    return next((c for c in PRIORITY if c in failed), None)


def attack_test(trades: Sequence[Mapping[str, Any]], role: str) -> dict[str, Any]:
    """ATTACK trades vs the SAME trades at normal (TAKE) size (PnL scales with size)."""
    return a4.attack_test(trades, role)


def analyze_bot(rec: Mapping[str, Any], cfg: V5Config, tape: Tape | None = None,
                family: Mapping[str, Any] | None = None, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    trades = rec.get("trades") or []
    act, mon = activity(rec, cfg), money(rec)
    conc = concentration(trades)
    boot = bootstrap([x for x in (net_r(t) for t in trades) if x is not None])
    jev = jev_block(rec)
    gs = gates(rec, act, mon, conc, boot, family, jev, base, cfg)
    lab, flags = label(act, mon, cfg)
    fm = primary_failure(gs)
    return {"activity": act, "money": mon, "concentration": conc, "p_mean_le_0": boot["p_mean_le_0"],
            "ci90_net_r": boot["ci90"], "exit_mix": exit_mix(trades),
            "horizons": horizon_excursions(trades, tape, cfg.mfe_horizons_h), "jev": jev,
            "attack": attack_test(trades, rec["role"]) if rec["role"] in ("JEV", "RANDOM") else None,
            "gates": gs, "label": lab, "flags": flags, "state": "ADVANCE" if fm is None else "REJECTED",
            "failure_mode": fm, "failed": [x["name"] for x in gs if not x["ok"]],
            "family_verdict": (family or {}).get("verdict"), "baselines": dict(base) if base else None}


# ---- family x horizon ------------------------------------------------------------------------------------------

def _subperiod(ts: int, subs: Sequence[tuple[str, str]]) -> int | None:
    for i, (a, b) in enumerate(subs):
        if _day_ms(a) <= ts < _day_ms(b) + DAY:
            return i
    return None


def edge_test(recs: Sequence[Mapping[str, Any]], cfg: V5Config, measure: str = "gross") -> dict[str, Any]:
    """The pooled family x horizon test over its coins' books: `gross` (the RAW-EDGE gate) or `net` (the ECONOMIC
    gate, after fees, slippage and funding)."""
    use_net = measure == "net"
    horizon = recs[0]["horizon"] if recs else "HOURLY"
    fn = net_r if use_net else gross_r
    xs = [(r["identity"]["coin"], t, fn(t)) for r in recs for t in (r.get("trades") or [])]
    xs = [(c, t, v) for c, t, v in xs if v is not None]
    vals = [v for _, _, v in xs]
    n = len(vals)
    g, e = cfg.raw_edge, cfg.economic
    need_n = dict(g.min_trades)[horizon]
    mean = _mean(vals)
    boot = bootstrap(vals)
    subs: dict[int, list[float]] = defaultdict(list)
    for _, t, v in xs:
        k = _subperiod(int(t["entry_ts"]), cfg.subperiods)
        if k is not None:
            subs[k].append(v)
    sub_means = [_r(_mean(subs.get(i, []))) for i in range(len(cfg.subperiods))]
    sub_n = [len(subs.get(i, [])) for i in range(len(cfg.subperiods))]
    by_coin: dict[str, list[float]] = defaultdict(list)
    for c, _, v in xs:
        by_coin[c].append(v)
    coins = {c: {"n": len(v), "mean_r": _r(_mean(v))} for c, v in sorted(by_coin.items())}
    judged = {c: d for c, d in coins.items() if d["n"] >= g.coin_min_trades}
    k = max(1, int(round(g.drop_top_share * n))) if n else 0
    trimmed = sorted(vals)[:-k] if n > k else []
    p_max = e.max_p_mean_le_0 if use_net else g.max_p_mean_le_0
    need_sub = e.min_positive_subperiods if use_net else g.min_positive_subperiods
    checks = {"sample": n >= need_n, "positive_mean": (mean or 0) > 0,
              "significant": boot["p_mean_le_0"] is not None and boot["p_mean_le_0"] <= p_max,
              "subperiods": sum(1 for s in sub_means if (s or 0) > 0) >= need_sub}
    if not use_net:
        checks["coins"] = bool(judged) and sum(1 for d in judged.values() if (d["mean_r"] or 0) > 0) / len(judged) >= g.min_coin_share
        checks["without_top_5pct"] = bool(trimmed) and (_mean(trimmed) or 0) >= 0
    return {"measure": measure, "books": len(recs), "trades": n, "needed": need_n, "mean_r": _r(mean),
            "p_mean_le_0": boot["p_mean_le_0"], "ci90": boot["ci90"], "subperiod_mean_r": sub_means,
            "subperiod_trades": sub_n, "coins": coins, "mean_r_without_top_5pct": _r(_mean(trimmed)),
            "checks": checks, "passed": all(checks.values())}


def split(recs: Sequence[Mapping[str, Any]], field: str) -> dict[str, Any]:
    """Gross and net R by a trade's entry context (regime WITH / AGAINST / FLAT, volatility band, side, setup)."""
    by: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in recs:
        for t in r.get("trades") or []:
            by[str(t.get(field) or "UNKNOWN")].append(t)
    return {k: {"n": len(v), "gross_r": _r(_mean(gross_r(t) for t in v)), "net_r": _r(_mean(net_r(t) for t in v))}
            for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))}


def family_verdict(raw: Mapping[str, Any], econ20: Mapping[str, Any] | None) -> str:
    if not raw["passed"]:
        return "NO_RAW_EDGE"
    if econ20 is None:
        return "NOT_EVALUATED"
    if econ20["passed"]:
        return "PASSED"
    return "SMALL_ACCOUNT_CONSTRAINED" if not econ20["checks"]["sample"] else "COST_DESTROYED"


def pool_costs(recs: Sequence[Mapping[str, Any]], cfg: V5Config) -> dict[str, Any]:
    trades = [t for r in recs for t in (r.get("trades") or [])]
    bot_days = sum(float(r["window"]["days"]) for r in recs) or 1.0
    stops = [float(t["stop_pct"]) for t in trades if t.get("stop_pct")]
    net = _mean(net_r(t) for t in trades)
    bound = _mean(maker_bound_r(t, cfg.fees.maker_rate, cfg.fees.taker_rate) for t in trades)
    return {"trades": len(trades), "trades_per_bot_day": _r(len(trades) / bot_days, 4),
            "avg_hold_h": _r(_mean(float(t.get("hold_s") or 0) / 3600 for t in trades), 2),
            "gross_r": _r(_mean(gross_r(t) for t in trades)), "cost_r": _r(_mean(cost_r(t) for t in trades)),
            "funding_r": _r(_mean(funding_r(t) for t in trades)), "net_r": _r(net),
            "maker_best_case_saving_r": _r(bound),
            "net_r_with_best_case_maker": _r(net + bound) if (net is not None and bound is not None) else None,
            "funding_paid": _r(sum(float(t.get("funding_paid") or 0.0) for t in trades)),
            "funding_received": _r(sum(float(t.get("funding_received") or 0.0) for t in trades)),
            "median_stop_pct": _r(statistics.median(stops), 5) if stops else None,
            "refused_share": _r(sum(int(r["activity"].get("below_exchange_minimum") or 0) for r in recs)
                                / max(1, sum(int(r["activity"].get("signals") or 0) for r in recs)), 3)}


# ---- the exit grid (DEVELOPMENT only; the same entries, different exits) ---------------------------------------------

def exit_grid(trades: Sequence[Mapping[str, Any]], tape: Tape | None, funding: Sequence[tuple[int, float]],
              time_stops_h: Sequence[int], targets_r: Sequence[float | None]) -> dict[str, list[float]]:
    """Net R per exit plan `X{h}` / `X{h}T{target}` for each trade: the trade's own stop (checked first inside a bar,
    so a bar touching both is a stop), a target at T x the stop distance, else the close at the time stop; minus the
    trade's own round-trip cost in R; plus funding at every actual settlement inside the counterfactual hold (longs
    pay a positive rate). A counterfactual on the SAME entries: a different hold would also change which later setups
    a one-position bot could take, which only the stage 2 replay answers."""
    out: dict[str, list[float]] = defaultdict(list)
    if tape is None:
        return out
    fts = [t for t, _ in funding]
    n_bars = len(tape.t)
    hmax = max(time_stops_h)
    for t in trades:
        sd = stop_dist(t)
        qty = float(t.get("qty") or 0.0)
        if not sd or qty <= 0:
            continue
        e, a = float(t["entry"]), int(t["entry_ts"])
        sgn = 1.0 if t.get("side") == "long" else -1.0
        c_r = (float(t.get("fees") or 0.0) + float(t.get("slippage") or 0.0)) / (qty * sd)
        i0 = tape.index(a)
        end_all = min(n_bars, i0 + hmax * 60)
        if end_all - i0 < hmax * 60:
            continue                                   # the tape ends inside the longest hold: not comparable
        stop_px = e - sgn * sd
        hit_stop = next((j for j in range(i0, end_all)
                         if (tape.lo[j] <= stop_px if sgn > 0 else tape.h[j] >= stop_px)), None)
        for T in targets_r:
            hit_tgt = None
            if T:
                tgt = e + sgn * T * sd
                hit_tgt = next((j for j in range(i0, end_all if hit_stop is None else hit_stop)
                                if (tape.h[j] >= tgt if sgn > 0 else tape.lo[j] <= tgt)), None)
            for H in time_stops_h:
                end = i0 + H * 60 - 1
                if hit_stop is not None and hit_stop <= end and (hit_tgt is None or hit_stop <= hit_tgt):
                    g, j_exit = -1.0, hit_stop
                elif hit_tgt is not None and hit_tgt <= end:
                    g, j_exit = float(T), hit_tgt
                else:
                    g, j_exit = sgn * (tape.c[end] - e) / sd, end
                t_exit = tape.t[j_exit] + 60_000
                f_r = -sum(sgn * funding[k][1] for k in range(bisect_left(fts, a + 1), bisect_left(fts, t_exit + 1))) * e / sd
                out[f"X{H}" + (f"T{T:g}" if T else "")].append(g - c_r + f_r)
    return out


def grid_table(pooled: Mapping[str, Sequence[float]]) -> dict[str, dict[str, Any]]:
    return {k: {"n": len(v), "net_r": _r(_mean(v)), "p_mean_le_0": bootstrap(list(v))["p_mean_le_0"]}
            for k, v in pooled.items()}


def plan_of(key: str) -> tuple[float, float | None]:
    h, _, tgt = key[1:].partition("T")
    return float(h), (float(tgt) if tgt else None)


def select_exit(table: Mapping[str, Mapping[str, Any]], baseline: str, min_improvement_r: float = 0.02) -> dict[str, Any]:
    """The DEVELOPMENT exit: the plan with the highest pooled net R. It must beat the pre-registered baseline by at
    least `min_improvement_r` to replace it (a tie keeps the baseline)."""
    base_r = (table.get(baseline) or {}).get("net_r")
    best = baseline
    if table:
        cand = max(table, key=lambda k: table[k]["net_r"] if table[k]["net_r"] is not None else -9e9)
        if base_r is None or (table[cand]["net_r"] or -9e9) >= base_r + min_improvement_r:
            best = cand
    h, tgt = plan_of(best)
    return {"plan": best, "time_stop_h": h, "target_r": tgt, "changed": best != baseline,
            "net_r": (table.get(best) or {}).get("net_r"), "baseline": baseline, "baseline_net_r": base_r}


# ---- arena level ----------------------------------------------------------------------------------------------------

def totals(recs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    trades = [t for r in recs for t in (r.get("trades") or [])]
    bot_days = sum(float(r["window"]["days"]) for r in recs) or 1.0

    def s(block: str, k: str) -> float:
        return sum(float((r.get(block) or {}).get(k) or 0.0) for r in recs)
    net = s("metrics", "net_profit")
    return {"bots": len(recs), "trades": len(trades), "gross": _r(s("metrics", "gross_pnl")),
            "maker_fees": _r(s("costs", "maker_fees")), "taker_fees": _r(s("costs", "taker_fees")),
            "slippage": _r(s("metrics", "slippage_cost")), "funding_paid": _r(s("costs", "funding_paid")),
            "funding_received": _r(s("costs", "funding_received")), "net": _r(net),
            "trades_per_bot_day": _r(len(trades) / bot_days, 4),
            "avg_hold_h": _r(_mean(float(t.get("hold_s") or 0) / 3600 for t in trades), 2),
            "profitable_bots": sum(1 for r in recs if float((r.get("metrics") or {}).get("net_profit") or 0.0) > 0),
            "net_per_trade": _r(sum(float(t["net"]) for t in trades) / len(trades)) if trades else None,
            "net_per_bot_day": _r(net / bot_days, 5),
            "refused_min_notional": sum(int(r["activity"].get("below_exchange_minimum") or 0) for r in recs),
            "signals": sum(int(r["activity"].get("signals") or 0) for r in recs)}


def viability(recs: Sequence[Mapping[str, Any]], analyses: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """HOURLY / DAILY VIABILITY: one row per horizon class."""
    out = []
    for horizon, tf in (("HOURLY", "1h"), ("SWING", "4h")):
        part = [r for r in recs if r["horizon"] == horizon]
        if not part:
            continue
        trades = [t for r in part for t in (r.get("trades") or [])]
        nets = [float(t["net"]) for t in trades]
        win, loss = sum(x for x in nets if x > 0), -sum(x for x in nets if x < 0)
        tot = totals(part)
        out.append({"horizon": horizon, "signal": tf, "bots": len(part), "trades": len(trades),
                    "trades_per_day_per_bot": tot["trades_per_bot_day"], "avg_hold_h": tot["avg_hold_h"],
                    "gross_exp_r": _r(_mean(gross_r(t) for t in trades)), "gross": tot["gross"],
                    "fees": _r((tot["maker_fees"] or 0) + (tot["taker_fees"] or 0)), "slippage": tot["slippage"],
                    "funding": _r((tot["funding_received"] or 0) - (tot["funding_paid"] or 0)),
                    "net_exp_r": _r(_mean(net_r(t) for t in trades)), "net": tot["net"],
                    "pf": _r(win / loss, 3) if loss else None,
                    "profitable_pct": _r(tot["profitable_bots"] / len(part), 3),
                    "qualified": sum(1 for r in part if (analyses.get(r["key"]) or {}).get("state") == "ADVANCE")})
    return out


def leaderboard_row(rec: Mapping[str, Any], a: Mapping[str, Any]) -> dict[str, Any]:
    idn, act, mon = rec["identity"], a["activity"], a["money"]
    j = a.get("jev") or {}
    fin = j.get("final") or {}
    n = sum(fin.values()) or 0
    bal = float(rec.get("balance") or 20.0)
    return {"key": rec["key"], "role": rec["role"], "strategy_id": idn["strategy_id"], "family": rec.get("family"),
            "coin": idn["coin"], "horizon": rec["horizon"], "exit": rec.get("exit_plan"), "balance": bal,
            "equity": _r(bal + (mon["net"] or 0.0)), "trades": act["trades"], "trades_per_day": act["trades_per_day"],
            "avg_hold_h": act["avg_hold_h"], "gross": mon["gross"], "fees": mon["fees"], "slippage": mon["slippage"],
            "funding": mon["funding_net"], "net": mon["net"], "net_per_trade": mon["net_per_trade"],
            "net_per_day": mon["net_per_day"], "exp_r": mon["net_expectancy_r"], "gross_exp_r": mon["gross_expectancy_r"],
            "pf": mon["profit_factor"], "max_dd": mon["max_drawdown_pct"], "p_mean_le_0": a.get("p_mean_le_0"),
            "jev": ({k: _r(v / n, 3) for k, v in fin.items()} if n else None),
            "selection_alpha": ((a.get("baselines") or {}).get("selection") or {}).get("alpha_usdt"),
            "label": a["label"], "flags": a.get("flags"), "state": a["state"], "failure_mode": a["failure_mode"],
            "pair_id": rec["pair_id"]}


def rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """After-cost ranking: net edge per day first (what the book actually earned), then net expectancy."""
    rows.sort(key=lambda r: (-(r["net_per_day"] if r["net_per_day"] is not None else -9e9),
                             -(r["exp_r"] if r["exp_r"] is not None else -9e9)))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def capacity_verdict(c20: Mapping[str, Any] | None, a20: Mapping[str, Any] | None,
                     bigger: Sequence[Mapping[str, Any]]) -> str:
    """OK_AT_20 / SMALL_ACCOUNT_CONSTRAINED / BAD_AT_20_BETTER_WHEN_BIGGER / BAD -- a diagnostic only; a 50 / 100 USDT
    result never qualifies the 20 USDT competition."""
    if a20 is not None and a20.get("state") == "ADVANCE":
        return "OK_AT_20"

    def good(b: Mapping[str, Any]) -> bool:
        mon = money(b)
        return (mon["net"] or 0) > 0 and (mon["gross_expectancy_r"] or 0) > 0 and len(b.get("trades") or []) >= 30
    constrained = a20 is not None and "MIN_NOTIONAL_LIMITED" in (a20.get("flags") or [])
    if any(good(b) for b in bigger) and (constrained or (c20 is not None and not good(c20))):
        return "SMALL_ACCOUNT_CONSTRAINED" if constrained else "BAD_AT_20_BETTER_WHEN_BIGGER"
    return "BAD"
