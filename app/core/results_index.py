"""THE RESULTS INDEX: every bot result PaperLab holds, in one schema, for one question -- what is the
real current state?

Each program stores its results its own way (V1 seasons and multi-year validation, V1/V2 arenas, the
V1 Jev experiment, V2 multi-year candidates, V3, V3.1, the forward shadow, the live paper bake-off).
This module reads them all -- READ ONLY, never re-running or rewriting anything -- and normalises every
bot into one flat row with the same money decomposition (gross -> fees -> slippage -> funding -> net),
the same quality numbers, its program status and ONE common root-cause label.

"Current" result of a bot = its most advanced COMPLETED research evaluation (multi-year > holdout >
development/discovery > season); the forward shadow is attached to it as live evidence, never summed
with it. Baselines that only exist to judge Jev (ALWAYS-TAKE, RANDOM) and diagnostics (RAW observers,
CAPACITY books) are never ranked as bots.

Every field is rebuilt from an allow-list of numbers and labels: no key, account, order id, header or
exception text can reach a row.
"""
from __future__ import annotations

import calendar
import datetime as dt
import math
import threading
import time
from typing import Any, Callable, Iterable, Mapping

from app.competition.cost_efficiency import cost_efficiency

MIN_RANK_TRADES = 30                    # a result with fewer closed trades is not ranked (listed, not ranked)
STAGE_ORDER = {"SEASON": 0, "DISCOVERY": 1, "DEVELOPMENT": 1, "JEV_EXPERIMENT": 1, "TEST": 2, "MULTI_YEAR": 3}
QUALIFIED_STATES = ("QUALIFIED", "SHADOW_LIVE", "LIVE_CANDIDATE")

# program-specific failure labels -> ONE common root-cause vocabulary
ROOT_CAUSE = {
    "GROSS_NEGATIVE": "NO_GROSS_EDGE", "NO_GROSS_EDGE": "NO_GROSS_EDGE", "ENTRY_HAS_NO_EDGE": "NO_GROSS_EDGE",
    "NO_RAW_EDGE": "NO_GROSS_EDGE", "NOT_SIGNIFICANT": "MARGINAL_EDGE",
    "FEE_DESTROYED": "FEE_DESTROYED", "COST_DESTROYED": "FEE_DESTROYED", "OVERTRADING": "FEE_DESTROYED",
    "SLIPPAGE_DESTROYED": "SLIPPAGE_DESTROYED",
    "EXIT_DESTROYS_EDGE": "EXIT_DESTROYS_EDGE", "HOLD_TOO_SHORT": "EXIT_DESTROYS_EDGE",
    "TOO_LOW_ACTIVITY": "LOW_ACTIVITY", "TOO_LITTLE_ACTIVITY": "LOW_ACTIVITY", "INSUFFICIENT_SAMPLE": "LOW_ACTIVITY",
    "NO_TRADES": "NO_TRADES",
    "PROFIT_CONCENTRATION": "PROFIT_CONCENTRATION", "PROFIT_CONCENTRATED": "PROFIT_CONCENTRATION",
    "MIN_NOTIONAL_CONSTRAINED": "MIN_NOTIONAL", "MIN_NOTIONAL_LIMITED": "MIN_NOTIONAL",
    "JEV_NO_VALUE": "JEV_NO_SELECTION_ALPHA", "JEV_BAD_DISCRIMINATION": "JEV_NO_SELECTION_ALPHA",
    "JEV_NO_SELECTION_ALPHA": "JEV_NO_SELECTION_ALPHA", "JEV_OVER_FILTERING": "JEV_OVER_FILTERING",
    "DRAWDOWN_FAILURE": "DRAWDOWN", "LIQUIDATION": "LIQUIDATION", "MARGINAL_EDGE": "MARGINAL_EDGE",
    "REGIME_DEPENDENT": "REGIME_DEPENDENT", "LATENCY_SENSITIVE": "MARGINAL_EDGE", "ROBUST": None,
}
ROOT_CAUSE_LABEL = {
    "NO_GROSS_EDGE": "NO GROSS EDGE", "FEE_DESTROYED": "FEE DESTROYED", "SLIPPAGE_DESTROYED": "SLIPPAGE DESTROYED",
    "EXIT_DESTROYS_EDGE": "EXIT DESTROYS EDGE", "LOW_ACTIVITY": "LOW ACTIVITY", "NO_TRADES": "NO TRADES",
    "PROFIT_CONCENTRATION": "PROFIT CONCENTRATION", "MIN_NOTIONAL": "MIN NOTIONAL",
    "JEV_NO_SELECTION_ALPHA": "JEV NO SELECTION ALPHA", "JEV_OVER_FILTERING": "JEV OVER FILTERING",
    "DRAWDOWN": "DRAWDOWN", "LIQUIDATION": "LIQUIDATION", "MARGINAL_EDGE": "MARGINAL EDGE",
    "REGIME_DEPENDENT": "REGIME DEPENDENT", "NOT_EVALUATED": "NOT EVALUATED",
}

ROW_FIELDS = (
    "bot_id", "program", "stage", "run_id", "window_from", "window_to", "days", "venue", "strategy", "family",
    "coin", "timeframe", "mode", "leverage", "starting_equity", "equity", "return_pct", "trades",
    "trades_per_day", "gross_pnl", "fees", "slippage", "funding", "net_pnl", "expectancy_r", "profit_factor",
    "max_drawdown", "win_rate", "cost_to_edge", "cost_class", "net_without_top3", "status", "failure_reason",
    "qualified", "passed_discovery", "passed_holdout", "passed_multi_year", "in_forward", "forward", "jev",
    "experimental",
)


# ---- small helpers ---------------------------------------------------------------------------------------

def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _r(x: Any, nd: int = 4) -> float | None:
    v = _f(x)
    return round(v, nd) if v is not None else None


def _month_days(first: str | None, last: str | None) -> float | None:
    try:
        y0, m0 = (int(x) for x in str(first).split("-")[:2])
        y1, m1 = (int(x) for x in str(last).split("-")[:2])
    except (TypeError, ValueError):
        return None
    days, y, m = 0, y0, m0
    while (y, m) <= (y1, m1):
        days += calendar.monthrange(y, m)[1]
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return float(days)


def _date_days(a: str | None, b: str | None) -> float | None:
    try:
        return float((dt.date.fromisoformat(str(b)[:10]) - dt.date.fromisoformat(str(a)[:10])).days + 1)
    except ValueError:
        return None


def _ms_date(ms: Any) -> str | None:
    v = _f(ms)
    if not v:
        return None
    return dt.datetime.fromtimestamp(v / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def _coin(symbol: Any) -> str:
    return str(symbol or "").replace("USDT", "")


def _top3_ex(nets: Iterable[float]) -> float | None:
    v = sorted((float(x) for x in nets), reverse=True)
    return round(sum(v) - sum(v[:3]), 4) if v else None


def money(m: Mapping[str, Any] | None, days: float | None, start: float | None = None) -> dict[str, Any]:
    """The common money + quality block of a result, from a PaperLab metrics dict."""
    m = m or {}
    ce = cost_efficiency(m, None, days)
    trades = int(m.get("trades") or 0)
    start_eq = _f(m.get("starting_equity")) or start
    net = _f(m.get("net_profit"))
    eq = _f(m.get("ending_equity"))
    if eq is None and start_eq is not None and net is not None:
        eq = start_eq + net
    ret = _f(m.get("net_return_pct"))
    if ret is None and start_eq and net is not None:
        ret = net / start_eq
    return {"starting_equity": _r(start_eq, 2), "equity": _r(eq, 4), "return_pct": _r(ret, 4), "trades": trades,
            "trades_per_day": _r(trades / days, 3) if days else None,
            "gross_pnl": _r(m.get("gross_pnl")), "fees": _r(m.get("fees_paid")), "slippage": _r(m.get("slippage_cost")),
            "funding": _r(m.get("funding_paid")), "net_pnl": _r(net),
            "expectancy_r": _r(m.get("expectancy_r")), "profit_factor": _r(min(_f(m.get("profit_factor")) or 0.0, 999.0), 3)
            if m.get("profit_factor") is not None else None,
            "max_drawdown": _r(m.get("max_drawdown_pct")), "win_rate": _r(m.get("win_rate"), 3),
            "cost_to_edge": _r(ce.get("cost_to_edge"), 3), "cost_class": ce.get("class"),
            "liquidations": int(m.get("liquidation_count") or 0)}


def derive_failure(mm: Mapping[str, Any], passed: bool, min_trades: int = MIN_RANK_TRADES,
                   halted: bool = False, below_min_share: float = 0.0, reasons: Iterable[str] = ()) -> str | None:
    """ONE root cause for a result that carries no analyzer label (V1 / V2), same order as the V3
    analyzers: what broke first, not the last symptom."""
    if passed:
        return None
    n = mm.get("trades") or 0
    gross, net = mm.get("gross_pnl") or 0.0, mm.get("net_pnl") or 0.0
    text = " ".join(str(x) for x in reasons).lower()
    if (mm.get("liquidations") or 0) > 0:
        return "LIQUIDATION"
    if n < min_trades and below_min_share >= 0.5:
        return "MIN_NOTIONAL"
    if n == 0:
        return "NO_TRADES"
    if gross <= 0:
        return "NO_GROSS_EDGE"
    if net <= 0:
        return "SLIPPAGE_DESTROYED" if (mm.get("slippage") or 0) > (mm.get("fees") or 0) else "FEE_DESTROYED"
    if halted or (mm.get("max_drawdown") or 0) >= 0.30:
        return "DRAWDOWN"
    if n < min_trades:
        return "LOW_ACTIVITY"
    if "concentr" in text or "top3" in text or "best 3" in text or "largest" in text:
        return "PROFIT_CONCENTRATION"
    return "MARGINAL_EDGE"


def _row(**k: Any) -> dict[str, Any]:
    out = {f: None for f in ROW_FIELDS}
    out.update({x: v for x, v in k.items() if x in out})
    for extra in ("liquidations",):
        if extra in k:
            out[extra] = k[extra]
    return out


# ---- program adapters (read only) --------------------------------------------------------------------------

def _v1_seasons(storage: Any) -> list[dict[str, Any]]:
    runs = [r for r in storage.competition_runs(limit=20) if r.get("status") in ("done", "complete")
            and "legacy" not in str(r.get("label") or "").lower()]
    if not runs:
        return []
    run = runs[0]
    start, end = _ms_date(run.get("start_ms")), _ms_date(run.get("end_ms"))
    days = _date_days(start, end)
    out = []
    for c in storage.competition_competitors(run["run_id"]):
        mm = money(c.get("metrics"), days, run.get("starting_balance"))
        q = c.get("qualification") or {}
        state = c.get("state") or q.get("state")
        passed = state in QUALIFIED_STATES
        out.append(_row(bot_id=f"v1s:{c['strategy_id']}", program="V1", stage="SEASON", run_id=run["run_id"],
                        window_from=start, window_to=end, days=days, venue="BINANCE_USDM", strategy=c["strategy_id"],
                        family=c.get("name"), coin="+".join(_coin(s) for s in run.get("symbols") or []),
                        timeframe=None, mode="CONTROL", **mm, status=state,
                        failure_reason=derive_failure(mm, passed, reasons=q.get("reasons") or ()),
                        qualified=passed))
    return out


def _v1_validation(storage: Any) -> list[dict[str, Any]]:
    runs = [r for r in storage.validation_runs(limit=20) if r.get("status") in ("done", "complete")]
    if not runs:
        return []
    run = max(runs, key=lambda r: (r.get("total_competitors") or 0, r.get("created_ts") or 0))
    days = _month_days(run.get("first_month"), run.get("last_month"))
    out = []
    for c in storage.validation_competitors(run["run_id"], heavy=True):
        res = c.get("result") or {}
        m = res.get("oos_metrics") or (res.get("walk_forward") or {}).get("metrics") or {}
        q = res.get("qualification") or {}
        state = c.get("state") or q.get("state")
        mm = money(m, days, run.get("starting_balance"))
        passed = state in QUALIFIED_STATES
        failure = derive_failure(mm, passed, reasons=q.get("reasons") or ())
        if state == "COMPETING":
            failure = "NOT_EVALUATED"
        out.append(_row(bot_id=f"v1v:{c['key']}", program="V1", stage="MULTI_YEAR", run_id=run["run_id"],
                        window_from=run.get("first_month"), window_to=run.get("last_month"), days=days,
                        venue="BINANCE_USDM", strategy=c["strategy_id"], family=c.get("name"),
                        coin="+".join(_coin(s) for s in run.get("symbols") or []), timeframe=None, mode="CONTROL",
                        leverage=c.get("leverage"), **mm, status=state, failure_reason=failure, qualified=passed,
                        passed_multi_year=passed))
    return out


def _arena_program(label: str, cfg: Mapping[str, Any]) -> tuple[str, str]:
    role = str(cfg.get("dataset_role") or "").upper()
    pv = str(cfg.get("params_version") or "")
    lab = label.lower()
    program = "V2" if (pv == "v2" or lab.startswith("v2")) else "V1"
    if role == "TEST" or " test " in f" {lab} ":
        return program, "TEST"
    if role == "DEVELOPMENT" or "development" in lab:
        return program, "DEVELOPMENT"
    return program, "DISCOVERY"


def _arenas(storage: Any) -> list[dict[str, Any]]:
    out = []
    for run in storage.arena_runs(limit=25):
        if run.get("status") not in ("complete", "done"):
            continue
        full = storage.arena_run(run["run_id"], heavy=True) or {}
        program, stage = _arena_program(str(run.get("label") or ""), full.get("config") or {})
        days = _month_days(run.get("first_month"), run.get("last_month"))
        for b in storage.arena_bots(run["run_id"], heavy=True):
            if b.get("entered") is False:
                continue
            m = b.get("metrics") or {}
            mm = money(m, days)
            state = b.get("state")
            advanced = state == "ADVANCE"
            ledger = b.get("trades_ledger") or []
            sig = int(b.get("signals") or 0)
            below = sum(v for k, v in (b.get("rejects") or {}).items() if str(k).startswith("below_min"))
            failure = derive_failure(mm, advanced, halted=bool(b.get("halted")),
                                     below_min_share=(below / sig) if sig else 0.0, reasons=b.get("reasons") or ())
            out.append(_row(bot_id=f"{program.lower()}{stage[0].lower()}:{b['key']}", program=program, stage=stage,
                            run_id=run["run_id"], window_from=run.get("first_month"), window_to=run.get("last_month"),
                            days=days, venue="BINANCE_USDM", strategy=b.get("strategy_id"), family=b.get("name"),
                            coin=b.get("coin") or _coin(b.get("symbol")), timeframe=b.get("timeframe"), mode="CONTROL",
                            leverage=b.get("max_leverage"), **mm,
                            net_without_top3=_top3_ex(t.get("net") or 0.0 for t in ledger),
                            status=state, failure_reason=failure, qualified=False,
                            passed_discovery=advanced if stage in ("DISCOVERY", "DEVELOPMENT") else None,
                            passed_holdout=advanced if stage == "TEST" else None, identity=b["key"]))
            out[-1]["_identity"] = ("ARENA", b["key"])
    return out


def _v1_jev(storage: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    runs = [r for r in storage.jev_runs(limit=10) if r.get("status") == "complete"]
    rows, exps = [], []
    for run in runs:
        days = _month_days(run.get("first_month"), run.get("last_month"))
        summary = (storage.jev_run(run["run_id"]) or {}).get("summary") or {}
        pairs = {p.get("jev_key"): p for p in summary.get("pairs_detail") or []}
        for b in storage.jev_bots(run["run_id"], heavy=True):
            mm = money(b.get("metrics") or {}, days)
            role = b.get("role") or "CONTROL"
            pair = pairs.get(b.get("key")) or {}
            stats = pair.get("jev_stats") or {}
            jev = None
            if role == "JEV":
                reviewed = stats.get("reviewed") or 0
                jev = {"jev_policy": run.get("policy_version"), "decisions": reviewed,
                       "skip_rate": _r((stats.get("skipped") or 0) / reviewed, 4) if reviewed else None,
                       "take_rate": _r(stats.get("acceptance_rate"), 4),
                       "attack_rate": _r((stats.get("attack") or 0) / reviewed, 4) if reviewed else None,
                       "auc": None, "selection_alpha": None, "avg_latency_ms": _r(stats.get("latency_avg_ms"), 0),
                       "control_net": _r((pair.get("control") or {}).get("net_profit")),
                       "delta_vs_control": _r(pair.get("edge_delta_usdt"))}
            if role != "JEV":
                continue           # the CONTROL side is the discovery-arena bot itself (already indexed)
            state = b.get("state")
            ledger = b.get("trades_ledger") or []
            rows.append(_row(bot_id=f"v1j:{b['key']}", program="V1", stage="JEV_EXPERIMENT", run_id=run["run_id"],
                             window_from=run.get("first_month"), window_to=run.get("last_month"), days=days,
                             venue="BINANCE_USDM", strategy=b.get("strategy_id"), family=b.get("name"),
                             coin=b.get("coin") or _coin(b.get("symbol")), timeframe=b.get("timeframe"), mode=role,
                             leverage=b.get("max_leverage"), **mm, net_without_top3=_top3_ex(t.get("net") or 0.0 for t in ledger),
                             status=state, failure_reason=derive_failure(mm, False, reasons=b.get("reasons") or ()),
                             qualified=False, jev=jev))
        auc = summary.get("auc")
        exps.append({"experiment": f"V1 Jev ({run.get('policy_version')})", "run_id": run["run_id"],
                     "window": f"{run.get('first_month')}..{run.get('last_month')}", "policy": run.get("policy_version"),
                     "prompt": run.get("prompt_version"), "pairs": summary.get("pairs") or run.get("pairs"),
                     "decisions": summary.get("jev_calls"), "take_rate": _r(summary.get("acceptance_rate"), 4),
                     "auc": _r(auc.get("auc") if isinstance(auc, dict) else auc, 4),
                     "beats_control": summary.get("improved"), "worse_than_control": summary.get("worsened"),
                     "jev_total": _r(summary.get("total_jev_net")), "control_total": _r(summary.get("total_control_net")),
                     "random_median_total": None, "selection_alpha": None, "verdict": summary.get("verdict")})
    return rows, exps


def _v2_candidates(storage: Any) -> list[dict[str, Any]]:
    runs = [r for r in storage.candidate_runs(limit=10) if r.get("status") == "complete"]
    if not runs:
        return []
    run = runs[0]
    days = _month_days(run.get("first_month"), run.get("last_month"))
    out = []
    for res in storage.candidate_results(run["run_id"]):
        man = res.get("manifest") or {}
        mm = money(res.get("metrics") or {}, days, man.get("starting_balance"))
        verdict = res.get("verdict")
        passed = verdict == "PASS"
        rob = res.get("robustness") or {}
        failed = [g.get("name") for g in res.get("gates") or [] if isinstance(g, dict) and not g.get("ok")]
        out.append(_row(bot_id=f"v2m:{res['key']}", program="V2", stage="MULTI_YEAR", run_id=run["run_id"],
                        window_from=run.get("first_month"), window_to=run.get("last_month"), days=days,
                        venue=res.get("venue") or run.get("venue"), strategy=man.get("strategy_id"),
                        family=man.get("strategy_name"), coin=man.get("coin") or _coin(man.get("symbol")),
                        timeframe=man.get("timeframe"), mode="CONTROL", leverage=man.get("leverage_ceiling"), **mm,
                        net_without_top3=_r(rob.get("net_without_best3")), status=f"MULTI-YEAR {verdict}",
                        failure_reason=derive_failure(mm, passed, halted=bool(res.get("halted")), reasons=failed),
                        qualified=False, passed_multi_year=passed))
        out[-1]["_identity"] = ("ARENA", res["key"])
    return out


def _v3_rows(storage: Any, program: str, table: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """V3, V3.1, V4 and V5 share the record shape (metrics, analysis with a primary failure mode). A V5 run keeps
    several books per slot (stage 1 baseline, stage 2 selected exit, capacity twins): only its OFFICIAL 20 USDT
    controls (the summary's leaderboard) and its Jev bots are results."""
    rows, exps = [], []
    runs = {"v3": lambda: storage.v3_runs(10), "v31": lambda: storage.v31_runs(20), "v4": lambda: storage.v4_runs(20),
            "v5": lambda: storage.v5_runs(20)}[table]()
    for run in runs:
        if run.get("status") != "complete":
            continue
        rid = run["run_id"]
        cfg = run.get("config") or {}
        summary = run.get("summary") or {}
        role_ds = str(run.get("dataset_role") or cfg.get("dataset_role") or "DEVELOPMENT").upper()
        stage = "TEST" if role_ds == "TEST" else "DEVELOPMENT"
        frm, to = cfg.get("trade_from"), cfg.get("trade_to")
        days = _date_days(frm, to)
        bots = {"v3": storage.v3_bots, "v31": storage.v31_bots, "v4": storage.v4_bots, "v5": storage.v5_bots}[table](rid)
        official = {r.get("key") for r in summary.get("leaderboard_controls") or []} if table == "v5" else None
        dev_passed = set(summary.get("dev_passed") or []) if stage == "TEST" else set()
        passed_all = set(summary.get("passed_all") or []) if table in ("v31", "v4", "v5") else set(
            (summary.get("advanced") or {}).get("strategies") or []) | set((summary.get("advanced") or {}).get("jev_bots") or [])
        for b in bots:
            role = b.get("role")
            if role not in ("CONTROL", "JEV"):
                continue
            if official is not None and role == "CONTROL" and b["key"] not in official:
                continue
            idn = b.get("identity") or {}
            a = b.get("analysis") or {}
            mm = money(b.get("metrics") or {}, days, b.get("balance") or 20.0)
            conc = a.get("concentration") or {"net_without_top3": (a.get("edge") or {}).get("net_without_top3")}
            jb = a.get("jev") or {}
            base = a.get("baselines") or {}
            jev = None
            if role == "JEV":
                n = jb.get("decisions") or 0
                fin = jb.get("final") or {}
                auc = jb.get("auc_support") or jb.get("auc") or {}
                sel = base.get("selection") or {}
                rnd = base.get("random") or {}
                alpha = sel.get("alpha_usdt")
                if alpha is None and rnd.get("p50") is not None:
                    alpha = (_f((b.get("metrics") or {}).get("net_profit")) or 0.0) - float(rnd["p50"])
                lat = jb.get("latency_ms") or {}
                jev = {"jev_policy": b.get("identity", {}).get("jev_policy") or POLICY_OF[table],
                       "decisions": n, "skip_rate": _r(jb.get("skip_rate"), 4),
                       "take_rate": _r((fin.get("TAKE", 0) + fin.get("NORMAL", 0) + fin.get("DEFENSIVE", 0)) / n, 4) if n else None,
                       "attack_rate": _r(jb.get("attack_rate"), 4), "auc": _r(auc.get("auc"), 4),
                       "auc_low": _r(auc.get("low"), 4), "auc_high": _r(auc.get("high"), 4),
                       "selection_alpha": _r(alpha), "avg_latency_ms": _r(lat.get("p50"), 0),
                       "control_net": _r(base.get("control_net")),
                       "delta_vs_control": _r((_f((b.get("metrics") or {}).get("net_profit")) or 0.0) - (_f(base.get("control_net")) or 0.0))
                       if base.get("control_net") is not None else None}
            state = b.get("state") or a.get("state")
            fm = b.get("failure_mode") or a.get("failure_mode")
            passed_here = b["key"] in passed_all
            prog = program
            rows.append(_row(bot_id=f"{'v3' if table == 'v3' else (table + ('t' if stage == 'TEST' else 'd'))}:{b['key']}",
                             program=prog, stage=stage, run_id=rid, window_from=frm, window_to=to, days=days,
                             venue=cfg.get("venue") or "BYBIT_LINEAR", strategy=idn.get("strategy_id"),
                             family=b.get("family"), coin=idn.get("coin"),
                             timeframe=b.get("horizon") if table == "v5" else idn.get("timeframe"), mode=role,
                             leverage=idn.get("max_leverage"), **mm, net_without_top3=_r(conc.get("net_without_top3")),
                             status=state, failure_reason=ROOT_CAUSE.get(fm, fm) if fm else None,
                             qualified=False,
                             passed_discovery=passed_here if stage == "DEVELOPMENT" else None,
                             passed_holdout=(passed_here and b["key"] in dev_passed) if stage == "TEST" else None,
                             jev=jev, experimental=bool(b.get("experimental")) or idn.get("timeframe") == "1m"))
            rows[-1]["_identity"] = (program, b["key"])
        pairs = summary.get("pairs") or []
        pooled = summary.get("jev_pooled") or {}
        if pairs:
            auc = pooled.get("auc_support") or pooled.get("auc") or {}
            bl = summary.get("baselines") or {}
            sa = summary.get("selection_alpha") or {}
            fin = pooled.get("final") or {}
            n = pooled.get("decisions") or 0
            exps.append({"experiment": f"{program} Jev ({POLICY_OF[table]}) {stage}",
                         "run_id": rid, "window": f"{frm}..{to}",
                         "policy": POLICY_OF[table],
                         "pairs": len(pairs), "decisions": n,
                         "skip_rate": _r(pooled.get("skip_rate"), 4), "attack_rate": _r(pooled.get("attack_rate"), 4),
                         "take_rate": _r(1 - (pooled.get("skip_rate") or 0) - (pooled.get("attack_rate") or 0), 4) if n else None,
                         "auc": _r(auc.get("auc"), 4), "auc_low": _r(auc.get("low"), 4), "auc_high": _r(auc.get("high"), 4),
                         "beats_control": bl.get("jev_beats_control"), "beats_random_p90": bl.get("jev_beats_random_p90"),
                         "jev_total": _r(bl.get("jev_total")), "control_total": _r(bl.get("control_total")),
                         "random_median_total": _r(bl.get("random_median_total")),
                         "selection_alpha": _r(sa.get("total_usdt") if sa else ((bl.get("jev_total") or 0) - (bl.get("random_median_total") or 0))),
                         "attack": (summary.get("attack") or {}).get("jev"),
                         "latency_p50_ms": _r((pooled.get("latency_ms") or {}).get("p50"), 0),
                         "verdict": (summary.get("answers") or {}).get("jev_selection") or (summary.get("answers") or {}).get("jev")
                         or (summary.get("answers") or {}).get("jev_makes_it_better")})
    return rows, exps


POLICY_OF = {"v3": "JEV_POLICY_V2", "v31": "JEV_POLICY_V3", "v4": "JEV_POLICY_V4", "v5": "JEV_POLICY_V5"}


def _forward(shadow_payload: Mapping[str, Any] | None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Forward shadow evidence per bot key (attached to the bot's research row) + the experiment block."""
    if not shadow_payload:
        return {}, {"status": "NOT_RUNNING"}
    st = shadow_payload.get("status") or {}
    fe = shadow_payload.get("forward_experiment") or {}
    per: dict[str, dict[str, Any]] = {}
    for b in shadow_payload.get("bots") or []:
        per[str(b.get("key"))] = {"role": b.get("role"), "trades": b.get("trades"), "net": _r(b.get("net")),
                                  "equity": _r(b.get("equity")), "max_dd": _r(b.get("max_dd")),
                                  "open_positions": len(b.get("open_positions") or []), "live": b.get("live")}
    jl = shadow_payload.get("jev_live") or {}
    cont = fe.get("continuity") or {}
    block = {"status": st.get("status"), "experiment_id": fe.get("experiment_id"),
             "program": "V2 (frozen v2 bots, live market data, simulated fills)",
             "started": _ms_date(fe.get("started_ts")) if fe.get("started_ts") else None,
             "hours": _r((fe.get("elapsed_ms") or 0) / 3_600_000, 1), "coverage": _r(fe.get("coverage"), 3),
             "bots": st.get("bots"), "controls": st.get("controls"), "jev_pairs": st.get("pairs"),
             "signals": fe.get("signals"), "closed_trades": fe.get("closed_trades"),
             "net": _r((shadow_payload.get("forward_total") or {}).get("net")),
             "positions_open": st.get("positions_open"), "sessions": fe.get("sessions"),
             "continuity": {"matched": cont.get("matched"), "diverged": cont.get("diverged"),
                            "unreproduced": cont.get("unreproduced")},
             "jev": {"policy": ((fe.get("identity") or {}).get("jev") or {}).get("policy_version"),
                     "decisions": jl.get("decisions"), "errors": jl.get("errors"), "skip_rate": _r(jl.get("skip_rate"), 4),
                     "latency_p50_ms": _r(jl.get("latency_p50_ms"), 0), "verdict": (jl.get("verdict") or {}).get("status")},
             "feed": {"klines": (st.get("feed") or {}).get("klines"), "book": (st.get("feed") or {}).get("book")}}
    return per, block


def live_paper_rows(engine: Any) -> list[dict[str, Any]]:
    """The V1 paper bake-off books running in the engine right now (DRY RUN: simulated fills)."""
    if engine is None or not getattr(engine, "strategies", None):
        return []
    out = []
    try:
        prices = engine.prices()
        for sid in sorted(engine.strategies):
            sb = engine.strategy_row(sid, {}, prices)
            trades = int(sb.get("trades_total") or 0)
            realized = _f(sb.get("realized")) or 0.0
            fees = _f(sb.get("fees")) or 0.0
            funding = _f(sb.get("funding")) or 0.0
            out.append(_row(bot_id=f"live:{sid}", program="V1", stage="LIVE_PAPER", run_id=f"epoch {engine.epoch}",
                            venue=str(getattr(engine.settings.venue, "label", "")), strategy=sid, family=sb.get("name"),
                            coin="+".join(_coin(s) for s in engine.symbols), mode="CONTROL",
                            starting_equity=_r(sb.get("allocation"), 2), equity=_r(sb.get("equity")),
                            trades=trades, gross_pnl=_r(realized), fees=_r(fees), funding=_r(funding),
                            net_pnl=_r(realized - fees + funding), profit_factor=_r(sb.get("profit_factor"), 3),
                            max_drawdown=_r(abs(_f(sb.get("max_dd_pct")) or 0.0) / 100.0), win_rate=_r(sb.get("win_rate"), 3),
                            status=("STOPPED (bake-off disabled)" if sb.get("enabled") is False else
                                    "HALTED" if sb.get("halted") else "RUNNING") + f" · life {sb.get('life')}",
                            failure_reason=None, qualified=False))
            out[-1]["running"] = sb.get("enabled") is not False and not sb.get("halted")
            out[-1]["lives"] = sb.get("life")
            out[-1]["realized_all_lives"] = _r(sb.get("realized_all_lives"))
    except Exception:            # the index never fails because the live engine is busy or booting
        return []
    return out


# ---- the index -------------------------------------------------------------------------------------------

_CACHE: dict[str, Any] = {"key": None, "ts": 0.0, "value": None}
_LOCK = threading.Lock()


def _research(storage: Any) -> dict[str, Any]:
    """Every completed research result (static between runs -> cached by the set of run ids)."""
    try:
        key = (tuple(r["run_id"] for r in storage.v5_runs(20) if r.get("status") == "complete"),
               tuple(r["run_id"] for r in storage.v4_runs(20) if r.get("status") == "complete"),
               tuple(r["run_id"] for r in storage.v31_runs(20) if r.get("status") == "complete"),
               tuple(r["run_id"] for r in storage.v3_runs(10) if r.get("status") == "complete"),
               tuple(r["run_id"] for r in storage.arena_runs(25)), tuple(r["run_id"] for r in storage.jev_runs(10)),
               tuple(r["run_id"] for r in storage.candidate_runs(10)), tuple(r["run_id"] for r in storage.validation_runs(20)),
               tuple(r["run_id"] for r in storage.competition_runs(20)))
    except Exception:
        key = None
    with _LOCK:
        if key is not None and _CACHE["key"] == key and _CACHE["value"] is not None:
            return _CACHE["value"]
    rows: list[dict[str, Any]] = []
    exps: list[dict[str, Any]] = []
    rows += _v1_seasons(storage)
    rows += _v1_validation(storage)
    rows += _arenas(storage)
    jr, je = _v1_jev(storage)
    rows += jr
    exps += je
    rows += _v2_candidates(storage)
    for program, table in (("V3", "v3"), ("V3.1", "v31"), ("V4", "v4"), ("V5", "v5")):
        r, e = _v3_rows(storage, program, table)
        rows += r
        exps += e
    value = {"rows": rows, "jev_experiments": exps, "built_ts": int(time.time() * 1000)}
    with _LOCK:
        _CACHE.update({"key": key, "ts": time.time(), "value": value})
    return value


def current_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per bot identity: its most advanced completed evaluation (see the module docstring)."""
    best: dict[Any, dict[str, Any]] = {}
    flags: dict[Any, dict[str, bool]] = {}
    for r in rows:
        ident = r.get("_identity") or r["bot_id"]
        cur = best.get(ident)
        if cur is None or (STAGE_ORDER.get(r["stage"], 0), r.get("window_to") or "") > \
                (STAGE_ORDER.get(cur["stage"], 0), cur.get("window_to") or ""):
            best[ident] = r
        # pipeline flags travel with the identity: a bot that passed discovery keeps that fact
        f = flags.setdefault(ident, {})
        for flag in ("passed_discovery", "passed_holdout", "passed_multi_year"):
            if r.get(flag) is not None:
                f[flag] = f.get(flag, False) or bool(r[flag])
    out = []
    for ident, r in best.items():
        r = dict(r)
        r.update(flags.get(ident, {}))
        out.append(r)
    return out


def build(storage: Any, shadow_payload: Mapping[str, Any] | None = None, engine: Any = None,
          live_paper: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    research = _research(storage)
    rows = [dict(r) for r in research["rows"]]
    fwd, fwd_block = _forward(shadow_payload)
    for r in rows:
        key = str(r.get("_identity", (None, ""))[1] or "")
        if r["program"] == "V2" and key in fwd:
            r["in_forward"] = True
            r["forward"] = fwd[key]
    cur = current_rows(rows)
    # the 5 forward +JEV bots are identities of their own (no research row)
    for key, f in fwd.items():
        if f.get("role") == "JEV":
            cur.append(_row(bot_id=f"fwd:{key}", program="V2", stage="FORWARD", mode="JEV", status="FORWARD SHADOW",
                            strategy=key.split("-")[0], coin=_coin(key.split("-")[1]) if "-" in key else None,
                            timeframe=key.split("-")[2].split("@")[0] if key.count("-") >= 2 else None,
                            trades=f.get("trades"), net_pnl=f.get("net"), equity=f.get("equity"), in_forward=True,
                            forward=f, failure_reason="LOW_ACTIVITY" if (f.get("trades") or 0) < MIN_RANK_TRADES else None,
                            qualified=False))
    live = live_paper if live_paper is not None else live_paper_rows(engine)
    return {"rows": rows, "current": cur, "live_paper": live, "forward": fwd_block,
            "jev_experiments": research["jev_experiments"], "built_ts": research["built_ts"]}


# ---- views over the index ---------------------------------------------------------------------------------

def ranked(rows: Iterable[dict[str, Any]], jev: bool = False, n: int = 10) -> list[dict[str, Any]]:
    """Top bots: >= MIN_RANK_TRADES closed trades, not experimental, ranked by net expectancy per trade
    (comparable across window lengths and account sizes), then net return."""
    pool = [r for r in rows if (r.get("trades") or 0) >= MIN_RANK_TRADES and not r.get("experimental")
            and (r.get("mode") == "JEV") == jev and r.get("stage") not in ("LIVE_PAPER", "FORWARD")]
    pool.sort(key=lambda r: (-(r.get("expectancy_r") if r.get("expectancy_r") is not None else -9e9),
                             -(r.get("return_pct") or -9e9)))
    out = []
    for i, r in enumerate(pool[:n], 1):
        out.append({"rank": i, **{k: v for k, v in r.items() if not k.startswith("_")}})
    return out


def counts(cur: list[dict[str, Any]], live_paper: list[dict[str, Any]], fwd_block: Mapping[str, Any]) -> dict[str, Any]:
    ev = [r for r in cur if r.get("stage") != "FORWARD"]
    traded = [r for r in ev if (r.get("trades") or 0) > 0]
    # stopped workers keep their evidence but are not "running" (the V1 bake-off and the V2 forward shadow were
    # stopped for V5: docs/V5_RUNTIME_AUDIT.md)
    fwd_running = str(fwd_block.get("status") or "").upper() in ("LIVE", "RUNNING", "WARMING_UP", "DEGRADED")
    running_books = [r for r in live_paper if r.get("running", True)]
    return {
        "evaluated_bots": len(ev),
        "bots_with_trades": len(traded),
        "running_now": ((fwd_block.get("bots") or 0) if fwd_running else 0) + len(running_books),
        "forward_shadow_bots": fwd_block.get("bots") or 0,
        "forward_shadow_running": fwd_running,
        "live_paper_books": len(live_paper),
        "live_paper_running": len(running_books),
        "gross_profitable": sum(1 for r in traded if (r.get("gross_pnl") or 0) > 0),
        "net_profitable": sum(1 for r in traded if (r.get("net_pnl") or 0) > 0),
        "positive_expectancy": sum(1 for r in traded if (r.get("expectancy_r") or 0) > 0),
        "pf_above_1": sum(1 for r in traded if (r.get("profit_factor") or 0) > 1),
        "net_profitable_30_trades": sum(1 for r in traded if (r.get("net_pnl") or 0) > 0 and (r.get("trades") or 0) >= MIN_RANK_TRADES),
        "passed_discovery": sum(1 for r in ev if r.get("passed_discovery")),
        "passed_holdout": sum(1 for r in ev if r.get("passed_holdout")),
        "passed_multi_year": sum(1 for r in ev if r.get("passed_multi_year")),
        "in_forward_shadow": sum(1 for r in cur if r.get("in_forward")),
        "qualified": sum(1 for r in ev if r.get("qualified")),
    }


def failure_counts(cur: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in cur:
        if r.get("stage") in ("FORWARD", "LIVE_PAPER"):
            continue
        k = r.get("failure_reason") or ("PASSED" if (r.get("passed_discovery") or r.get("passed_holdout")) else "NONE")
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def decomposition(rows: Iterable[dict[str, Any]], key: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    """gross -> fees -> slippage -> funding -> net, summed per group, with the diagnosis the user asked
    for: no raw edge, or an edge the costs destroy."""
    groups: dict[Any, dict[str, Any]] = {}
    for r in rows:
        if (r.get("trades") or 0) <= 0:
            continue
        g = groups.setdefault(key(r), {"bots": 0, "trades": 0, "gross": 0.0, "fees": 0.0, "slippage": 0.0,
                                       "funding": 0.0, "net": 0.0})
        g["bots"] += 1
        g["trades"] += r.get("trades") or 0
        for f, src in (("gross", "gross_pnl"), ("fees", "fees"), ("slippage", "slippage"), ("funding", "funding"),
                       ("net", "net_pnl")):
            g[f] += r.get(src) or 0.0
    out = []
    for k, g in groups.items():
        costs = g["fees"] + g["slippage"] + max(0.0, -g["funding"])
        diag = ("NO RAW EDGE" if g["gross"] <= 0 else "EDGE DESTROYED BY COSTS" if g["net"] <= 0
                else "EDGE SURVIVES COSTS")
        out.append({"group": k, **{x: (round(v, 4) if isinstance(v, float) else v) for x, v in g.items()},
                    "costs": round(costs, 4), "cost_share_of_gross": round(costs / g["gross"], 3) if g["gross"] > 0 else None,
                    "gross_per_trade": round(g["gross"] / g["trades"], 5) if g["trades"] else None,
                    "diagnosis": diag})
    out.sort(key=lambda x: -(x["net"]))
    return out
