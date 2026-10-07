"""Multi-year validation of the FROZEN v2 TEST survivors (docs/V2_MULTIYEAR_PROTOCOL.md).

    manifest    record and verify everything that defines a candidate: source fingerprint,
                parameters, symbol, timeframe, risk profile, cost gate, fees, execution, rules.
                A mismatch with the v2 freeze aborts -- it would be a new version, not v2.
    master      ONE continuous ledger per bot from 20 USDT over the whole window, no resets.
    windows     calendar quarters on that ledger (normalised: return on the quarter's own
                starting equity), plus calendar years.
    regimes     each trade labelled with its coin's trend and volatility regime on entry day.
    robustness  profit concentration; the result without the best 1 / 3 trades / best year.
    headroom    break-even round-trip cost vs the simulated Binance cost and Bybit fees.
    monte carlo 10,000 block-bootstrap paths of per-trade returns relative to equity.
    stress      each scenario a full re-simulation of the whole window.
    gates       the pre-registered multi-year gates; all must pass.

Nothing here edits a strategy, a parameter or a threshold. Replays run through `Arena.build_engine`
with the TEST run's own configuration, so the only thing that differs from TEST is the data.
"""
from __future__ import annotations

import bisect
import calendar
import dataclasses
import datetime as dt
import hashlib
import json
import math
import random
import statistics as stt
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

VENUE = "BINANCE_USDM"
SOURCE_TEST_RUN = "8227dfb422cb"
CANDIDATES = ("S26-XRPUSDT-30m@20x-v2", "S26-BNBUSDT-30m@20x-v2", "S12-XRPUSDT-15m@20x-v2")
# docs/V2_FREEZE.md -- the only source fingerprints that are "v2"
FROZEN_V2 = {"base": "bef9ced73cec", "S03": "05d0a8ee4379", "S04": "aae1fb697d67",
             "S12": "b6c81f3ee712", "S22": "7df4a8eedb68", "S26": "7da4115014b8"}
DAY = 86_400_000
# Bybit linear instrument filters, read 2026-09-23 from /v5/market/instruments-info (docs/VENUE_AUDIT.md).
# Only the tick differs from Binance for these two: BNBUSDT quotes in 0.10 on Bybit vs 0.01 on Binance,
# so a Bybit round trip crosses a spread ten times wider in price terms.
BYBIT_TICK = {"XRPUSDT": 0.0001, "BNBUSDT": 0.10}
BINANCE_TICK = {"XRPUSDT": 0.0001, "BNBUSDT": 0.01}


def _fp(obj: Any, n: int = 16) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:n]


# ---- protocol -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class GateConfig:
    min_trades: int = 200
    min_net_profit: float = 0.0              # >
    min_expectancy_r: float = 0.0            # >
    min_profit_factor: float = 1.15          # >=
    max_drawdown_pct: float = 0.30           # <   (the frozen AGGRESSIVE halt level)
    max_liquidations: int = 0                # <=
    min_profitable_window_ratio: float = 0.50  # >  (a majority of ACTIVE quarters)
    max_ruin_probability: float = 0.05       # <=
    stress_required: tuple[str, ...] = ("FEES_125", "FEES_150", "SLIP_150", "SLIP_200",
                                        "LATENCY_2X", "ALL_TAKER", "ADVERSE_FUNDING")
    require_positive_without_top3: bool = True
    require_positive_without_best_year: bool = True


@dataclass(frozen=True)
class Protocol:
    version: str = "V2_MULTIYEAR_V1.1"          # amendment 1: complete frozen halt set
    first_month: str = "2021-01"
    last_month: str = "2025-10"
    warmup_months: tuple[str, ...] = ("2020-12",)
    window: str = "quarter"
    active_window_min_trades: int = 5
    mc_paths: int = 10_000
    mc_block: int = 5
    mc_seed: int = 20260923
    ruin_drawdown: float = 0.30                 # AGGRESSIVE halt_drawdown (from peak)
    ruin_floor_pct: float = 0.25                # RiskManager strategy_halt_pct (below the start)
    trend_ema: int = 50
    trend_slope_days: int = 10
    trend_slope_pct: float = 0.02
    vol_days: int = 20
    vol_lookback_days: int = 365
    vol_min_history: int = 120
    vol_high: float = 0.67
    vol_low: float = 0.33
    gates: GateConfig = GateConfig()

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["gates"]["stress_required"] = list(self.gates.stress_required)
        return d

    def fingerprint(self) -> str:
        return _fp(self.to_dict())


# name -> what changes. Every other setting is the frozen TEST configuration.
SCENARIOS: dict[str, dict[str, Any]] = {
    "NORMAL": {},
    "FEES_125": {"fee_mult": 1.25},
    "FEES_150": {"fee_mult": 1.50},
    "SLIP_150": {"slippage_mult": 1.5},
    "SLIP_200": {"slippage_mult": 2.0},
    "LATENCY_2X": {"latency_mult": 2.0},
    "ALL_TAKER": {"force_taker": True},
    "ADVERSE_FUNDING": {"funding": "adverse"},
    "FEES_125+SLIP_150": {"fee_mult": 1.25, "slippage_mult": 1.5},
    "FEES_150+SLIP_200+LATENCY_2X": {"fee_mult": 1.50, "slippage_mult": 2.0, "latency_mult": 2.0},
    "ALL_STRESS": {"fee_mult": 1.50, "slippage_mult": 2.0, "latency_mult": 2.0, "force_taker": True,
                   "funding": "adverse"},
    "VENUE_BYBIT_FEES": {"fees": "bybit_linear"},
    "LATENCY_1BAR": {"latency_add_ms": 60_000},
}
SCENARIO_LABELS = {"NORMAL": "NORMAL", "FEES_125": "FEES +25%", "FEES_150": "FEES +50%",
                   "SLIP_150": "SLIPPAGE 1.5x", "SLIP_200": "SLIPPAGE 2x", "LATENCY_2X": "LATENCY 2x",
                   "ALL_TAKER": "ALL TAKER", "ADVERSE_FUNDING": "ADVERSE FUNDING",
                   "FEES_125+SLIP_150": "FEES +25% & SLIPPAGE 1.5x",
                   "FEES_150+SLIP_200+LATENCY_2X": "FEES +50% & SLIPPAGE 2x & LATENCY 2x",
                   "ALL_STRESS": "ALL (fees +50%, slippage 2x, latency 2x, all taker, adverse funding)",
                   "VENUE_BYBIT_FEES": "BYBIT LINEAR FEES (venue check)",
                   "LATENCY_1BAR": "LATENCY +1 BAR (every fill one 1m bar later; informational)"}


def stressed(cfg: Any, scenario: dict[str, Any]) -> Any:
    """The frozen arena config with ONE scenario's execution/cost changes applied."""
    from app.execution.config import BYBIT_LINEAR, FeeSchedule
    fees, execution = cfg.fees, cfg.execution
    if scenario.get("fees") == "bybit_linear":
        fees = BYBIT_LINEAR
    if scenario.get("fee_mult"):
        k = float(scenario["fee_mult"])
        fees = FeeSchedule(fees.maker_rate * k, fees.taker_rate * k, f"{fees.source} x{k:g} (stress)",
                           fees.updated_at)
    ex: dict[str, Any] = {}
    if scenario.get("slippage_mult"):
        ex["slippage_mult"] = execution.slippage_mult * float(scenario["slippage_mult"])
    if scenario.get("latency_mult"):
        k = float(scenario["latency_mult"])
        ex["signal_latency_ms"] = int(execution.signal_latency_ms * k)
        ex["order_latency_ms"] = int(execution.order_latency_ms * k)
    if scenario.get("latency_add_ms"):
        ex["signal_latency_ms"] = int(ex.get("signal_latency_ms", execution.signal_latency_ms)
                                      + int(scenario["latency_add_ms"]))
    if scenario.get("force_taker"):
        ex["force_taker"] = True
    if ex:
        execution = dataclasses.replace(execution, **ex)
    return dataclasses.replace(cfg, fees=fees, execution=execution)


# ---- manifest -------------------------------------------------------------------------------------

def manifest(test_run: dict[str, Any], key: str) -> dict[str, Any]:
    """Everything that defines a candidate, from the TEST run's stored configuration and the code."""
    from app.competition.cost_gate import CostGateConfig
    from app.competition.jev_experiment import arena_config_from
    from app.strategies.base import params_to_dict
    from app.strategies.registry import load_v2, v2_fingerprints
    cfg_stored = test_run.get("config") or {}
    cfg = arena_config_from(cfg_stored)
    bot = _bot_row(test_run, key)
    sid, sym, tf = bot["strategy_id"], bot["symbol"], bot["timeframe"]
    cls = load_v2()[sid].for_timeframe(tf)
    fps = v2_fingerprints()
    rules = (cfg_stored.get("rules") or {}).get(sym)
    m = {
        "key": key, "strategy_id": sid, "strategy_name": getattr(cls, "name", sid), "symbol": sym,
        "coin": sym[:-4], "timeframe": tf, "context_timeframe": getattr(cls, "context_tf", None),
        "params_version": cfg.params_version, "venue": VENUE,
        "source_fingerprint": {sid: fps.get(sid), "base": fps.get("base")},
        "parameters": params_to_dict(cls().params),
        "risk_profile": cfg.risk.to_dict(), "leverage_ceiling": cfg.leverage_ceiling,
        "leverage_policy": cfg.leverage_policy, "starting_balance": cfg.starting_balance,
        "max_fee_share_of_r": cfg.max_fee_share_of_r,
        "min_notional_safety_multiplier": cfg.min_notional_safety_multiplier,
        "cost_gate": {"min_edge_to_cost": cfg.cost_gate_min_ratio,
                      **{k: v for k, v in dataclasses.asdict(CostGateConfig()).items() if k != "min_edge_to_cost"}},
        "fee_schedule": cfg.fees.to_dict(), "execution": dataclasses.asdict(cfg.execution),
        "risk_engine": _risk_engine(cfg),
        "rules": rules, "seed": cfg.seed, "source_test_run": test_run.get("run_id"),
        "test_state": bot.get("state"),
    }
    m["manifest_fingerprint"] = _fp({k: v for k, v in m.items() if k not in ("test_state",)})
    return m


def _risk_engine(cfg: Any) -> dict[str, Any]:
    """The RiskManager settings a replay ACTUALLY runs with: the arena's per-bot settings (ordinary
    risk from the profile, fee share, min-notional safety) on top of the lab defaults the TEST run
    used -- including the strategy halt floor."""
    from app.competition.arena import Arena
    from app.competition.jev_experiment import _settings
    st = Arena(_settings(cfg.starting_balance), cfg, [], {})._settings_for(None)
    return {k: getattr(st, k, None) for k in ("strategy_halt_pct", "risk_per_trade_pct", "max_fee_share_of_r",
                                              "min_notional_safety_multiplier", "strategy_starting_balance")}


def verify(m: dict[str, Any], test_run: dict[str, Any]) -> list[str]:
    """Why this is NOT the frozen v2 candidate any more (empty = it is)."""
    problems = []
    frozen_run = (test_run.get("config") or {}).get("strategy_fingerprints") or {}
    for name, fp in m["source_fingerprint"].items():
        if fp != FROZEN_V2.get(name):
            problems.append(f"{name} source {fp} != v2 freeze {FROZEN_V2.get(name)}")
        if frozen_run and fp != frozen_run.get(name):
            problems.append(f"{name} source {fp} != TEST run's recorded {frozen_run.get(name)}")
    if m.get("test_state") != "ADVANCE":
        problems.append(f"{m['key']} did not ADVANCE on TEST (state {m.get('test_state')})")
    if (test_run.get("config") or {}).get("dataset_role") != "TEST":
        problems.append("source run is not the v2 TEST run")
    return problems


def _bot_row(test_run: dict[str, Any], key: str) -> dict[str, Any]:
    for b in test_run.get("bots") or []:
        if b.get("key") == key:
            return b
    raise ValueError(f"{key} is not a bot of TEST run {test_run.get('run_id')}")


# ---- months, time -----------------------------------------------------------------------------------

def months_between(first: str, last: str) -> list[str]:
    y, m = (int(x) for x in first.split("-"))
    y1, m1 = (int(x) for x in last.split("-"))
    out = []
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_start_ms(month: str) -> int:
    y, m = (int(x) for x in month.split("-"))
    return int(dt.datetime(y, m, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)


def month_end_ms(month: str) -> int:
    y, m = (int(x) for x in month.split("-"))
    return month_start_ms(month) + calendar.monthrange(y, m)[1] * DAY - 1


def quarter_of(ms: int) -> str:
    d = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def year_of(ms: int) -> str:
    return str(dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).year)


def day_of(ms: int) -> int:
    return ms - ms % DAY


# ---- one replay (runs in a worker process) ---------------------------------------------------------

def run_path(job: dict[str, Any]) -> dict[str, Any]:
    """One continuous replay of one candidate under one scenario. Top-level so it pickles."""
    import time
    from app.backtest import archive
    from app.backtest import brackets as bracket_mod
    from app.competition import metrics as mx
    from app.competition.arena import Arena, funding_for
    from app.competition.bots import BotSpec
    from app.competition.jev_experiment import _settings, arena_config_from
    from app.core.types import MarketRules
    from app.strategies.registry import load_v2

    t0 = time.time()
    cfg = stressed(arena_config_from(job["arena_config"]), SCENARIOS[job["scenario"]])
    settings = _settings(cfg.starting_balance)
    spec = BotSpec(**job["spec"])
    rules = {s: MarketRules(**r) for s, r in job["rules"].items()}
    months = list(job["warmup_months"]) + list(job["months"])
    arena = Arena(settings, cfg, months, rules, brackets=bracket_mod.load([spec.symbol], settings.data_dir))
    cls = arena.bind(spec, load_v2()[spec.strategy_id])
    eng = arena.build_engine(spec, funding=funding_for(settings, [spec.symbol], months))
    if SCENARIOS[job["scenario"]].get("funding") == "adverse":
        eng.funding_mode = "adverse"
    counts = {"bars": 0}

    def bars() -> Iterator[Any]:
        for month in months:
            for c in archive.load_klines(settings, spec.symbol, [month]):
                counts["bars"] += 1
                yield c

    since = month_start_ms(job["months"][0])
    res = eng.run(cls, bars(), since_ms=since, leverage=spec.max_leverage, signal_tf=spec.timeframe,
                  only_symbol=spec.symbol)
    m = mx.compute(spec.strategy_id, res.trades, res.fills, res.equity, res.starting_equity,
                   rejects=res.rejects, halted=res.halted, version=spec.version(), leverage=res)
    m.configured_max_leverage = spec.max_leverage
    eq = [(ts, e) for ts, e in res.equity if ts >= since]
    ts_list = [ts for ts, _ in eq]

    def eq_before(ms: int) -> float:
        i = bisect.bisect_left(ts_list, ms) - 1
        return eq[i][1] if i >= 0 else res.starting_equity

    entry_meta = {f.position_id: (f.meta or {}) for f in res.fills if f.kind == "entry"}
    trades = []
    for t in res.trades:
        em = entry_meta.get(t.position_id, {})
        trades.append({"entry_ts": t.entry_ts, "exit_ts": t.exit_ts, "side": t.side, "qty": t.qty,
                       "entry_price": t.entry_price, "exit_price": t.exit_price, "pnl": t.pnl, "fees": t.fees,
                       "net": t.net, "r": t.r_multiple, "exit_kind": t.exit_kind,
                       "notional": t.qty * t.entry_price, "equity_at_entry": eq_before(t.entry_ts),
                       "attack_state": em.get("attack_state"), "risk_pct": em.get("risk_pct"),
                       "leverage": em.get("position_leverage")})
    fees_role = {"taker": 0.0, "maker": 0.0}
    for f in res.fills:
        if f.kind == "funding" or f.qty <= 0:
            continue
        fees_role["maker" if (f.meta or {}).get("maker") or (f.meta or {}).get("liquidity_role") == "MAKER" else "taker"] += f.fee
    funding_total = sum(f.realized_pnl for f in res.fills if f.kind == "funding")
    daily: dict[int, float] = {}
    for ts, e in eq:
        daily[day_of(ts)] = e
    return {"key": job["key"], "scenario": job["scenario"], "metrics": m.to_dict(), "trades": trades,
            "daily_equity": sorted(daily.items()),
            "periods": _period_stats(eq, res.starting_equity), "fees_by_role": fees_role,
            "funding_total": funding_total, "halted": res.halted, "halt_ts": getattr(res, "halt_ts", None),
            "bars": counts["bars"], "signals": res.signals, "rejects": dict(res.rejects),
            "config": {"fees": cfg.fees.to_dict(), "execution": dataclasses.asdict(cfg.execution),
                       "funding_mode": eng.funding_mode},
            "elapsed_s": round(time.time() - t0, 1)}


def _period_stats(eq: Sequence[tuple[int, float]], start_equity: float) -> dict[str, dict[str, Any]]:
    """Equity at the start/end of every quarter and year, and each period's max drawdown, from the
    full-resolution (1m) curve of the continuous ledger."""
    out: dict[str, dict[str, Any]] = {}
    prev = start_equity
    for ts, e in eq:
        for key in (quarter_of(ts), year_of(ts)):
            p = out.get(key)
            if p is None:
                p = out[key] = {"start_equity": prev, "end_equity": e, "peak": max(prev, e), "max_dd": 0.0,
                                "first_ts": ts, "last_ts": ts}
            p["end_equity"] = e
            p["last_ts"] = ts
            if e > p["peak"]:
                p["peak"] = e
            elif p["peak"] > 0:
                p["max_dd"] = max(p["max_dd"], 1.0 - e / p["peak"])
        prev = e
    return out


# ---- analysis of the NORMAL ledger -------------------------------------------------------------------

def trade_stats(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    nets = [float(t["net"]) for t in trades]
    rs = [float(t["r"]) for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    gp, gl = sum(wins), -sum(losses)
    return {"trades": n, "net": sum(nets), "wins": len(wins),
            "win_rate": len(wins) / n if n else None, "expectancy_r": sum(rs) / n if n else None,
            "profit_factor": (gp / gl) if gl > 0 else (999.0 if gp > 0 else None),
            "avg_win": (gp / len(wins)) if wins else None, "avg_loss": (-gl / len(losses)) if losses else None}


def halt_info(normal: dict[str, Any]) -> dict[str, Any]:
    """Which frozen halt stopped the book, and when. Two exist (docs/V2_MULTIYEAR_PROTOCOL.md,
    amendment 1): the RiskManager floor (equity <= 75% of the start) and the AGGRESSIVE drawdown halt
    (30% from peak), which refuses every later entry as `bot_halted`."""
    trades = normal.get("trades") or []
    last = max((t["exit_ts"] for t in trades), default=None)
    rej = normal.get("rejects") or {}
    if normal.get("halted"):
        return {"kind": "FLOOR", "ts": normal.get("halt_ts"), "refused_after": rej.get("strategy_halted", 0),
                "note": "equity fell to 75% of the 20 USDT start: the RiskManager halted the book for good"}
    if rej.get("bot_halted"):
        return {"kind": "DRAWDOWN", "ts": last, "refused_after": rej.get("bot_halted", 0),
                "note": "drawdown from peak reached 30% (AGGRESSIVE halt): every later entry refused; "
                        "the date is the last trade before it"}
    return {}


def capacity(normal: dict[str, Any]) -> dict[str, Any]:
    """How many of the strategy's signals a 20 USDT book could actually place."""
    rej = normal.get("rejects") or {}
    n = int(normal.get("signals") or 0)
    small = int(rej.get("below_min_notional", 0)) + int(rej.get("below_min_qty", 0))
    return {"signals": n, "trades": len(normal.get("trades") or []), "below_exchange_minimum": small,
            "below_exchange_minimum_share": (small / n) if n else None,
            "note": "signals refused because the position a 1%-risk 20 USDT book can size is below the "
                    "exchange minimum (5 USDT notional / min qty) -- the bot is validated as it can trade"}


def windows(trades: Sequence[dict[str, Any]], periods: dict[str, dict[str, Any]], months: Sequence[str],
            min_trades: int) -> dict[str, Any]:
    quarters = sorted({quarter_of(month_start_ms(mo)) for mo in months})
    rows = []
    for q in quarters:
        ts = [t for t in trades if quarter_of(t["exit_ts"]) == q]
        p = periods.get(q) or {}
        s0, s1 = p.get("start_equity"), p.get("end_equity")
        ret = (s1 / s0 - 1.0) if s0 and s1 is not None else None
        st = trade_stats(ts)
        rows.append({"window": q, "trades": st["trades"], "net": st["net"], "return": ret,
                     "expectancy_r": st["expectancy_r"], "profit_factor": st["profit_factor"],
                     "win_rate": st["win_rate"], "max_dd": p.get("max_dd"),
                     "active": st["trades"] >= min_trades,
                     "profitable": (ret is not None and ret > 0)})
    active = [r for r in rows if r["active"]]
    rets = [r["return"] for r in active if r["return"] is not None]
    return {"rows": rows, "total": len(rows), "active": len(active),
            "profitable": sum(1 for r in active if r["profitable"]),
            "losing": sum(1 for r in active if not r["profitable"]),
            "profitable_ratio": (sum(1 for r in active if r["profitable"]) / len(active)) if active else None,
            "trades_per_window": (sum(r["trades"] for r in rows) / len(rows)) if rows else None,
            "median_return": stt.median(rets) if rets else None,
            "worst": min(active, key=lambda r: r["return"] if r["return"] is not None else 9e9, default=None),
            "best": max(active, key=lambda r: r["return"] if r["return"] is not None else -9e9, default=None)}


def years(trades: Sequence[dict[str, Any]], periods: dict[str, dict[str, Any]], months: Sequence[str]) -> list[dict[str, Any]]:
    out = []
    for y in sorted({mo[:4] for mo in months}):
        ts = [t for t in trades if year_of(t["exit_ts"]) == y]
        p = periods.get(y) or {}
        s0, s1 = p.get("start_equity"), p.get("end_equity")
        st = trade_stats(ts)
        out.append({"year": y, "months": sum(1 for mo in months if mo.startswith(y)), "trades": st["trades"],
                    "net": st["net"], "return": (s1 / s0 - 1.0) if s0 and s1 is not None else None,
                    "start_equity": s0, "end_equity": s1, "expectancy_r": st["expectancy_r"],
                    "profit_factor": st["profit_factor"], "win_rate": st["win_rate"], "max_dd": p.get("max_dd")})
    return out


def robustness(trades: Sequence[dict[str, Any]], yrs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    nets = sorted((float(t["net"]) for t in trades), reverse=True)
    total = sum(nets)
    gross_profit = sum(x for x in nets if x > 0)
    winners = [x for x in nets if x > 0]
    top_decile = winners[:max(1, math.ceil(len(winners) * 0.10))] if winners else []
    best_year = max(yrs, key=lambda y: y["net"], default=None)
    return {"net": total,
            "largest_winner": nets[0] if nets else None,
            "largest_winner_share_of_net": (nets[0] / total) if nets and total > 0 else None,
            "top3_share_of_net": (sum(nets[:3]) / total) if len(nets) >= 3 and total > 0 else None,
            "top10pct_winners_share_of_gross_profit": (sum(top_decile) / gross_profit) if gross_profit > 0 else None,
            "net_without_best": total - (nets[0] if nets else 0.0),
            "net_without_best3": total - sum(nets[:3]),
            "best_year": best_year["year"] if best_year else None,
            "net_without_best_year": total - (best_year["net"] if best_year else 0.0),
            "concentration_risk": bool(nets) and (total - sum(nets[:3])) <= 0}


def headroom(normal: dict[str, Any], symbol: str | None = None) -> dict[str, Any]:
    """Break-even round-trip cost vs what the venue costs, in bps of entry notional."""
    m, trades = normal["metrics"], normal["trades"]
    notional = sum(float(t["notional"]) for t in trades)
    if notional <= 0:
        return {"notional": 0.0}
    pnl = sum(float(t["pnl"]) for t in trades)            # at fill prices, before fees
    slip = float(m.get("slippage_cost") or 0.0)
    funding = float(normal.get("funding_total") or 0.0)
    fees = float(m.get("fees_paid") or 0.0)
    roles = normal.get("fees_by_role") or {}
    from app.execution.config import BINANCE_USDM, BYBIT_LINEAR
    bybit_fees = (roles.get("taker", 0.0) * BYBIT_LINEAR.taker_rate / BINANCE_USDM.taker_rate
                  + roles.get("maker", 0.0) * BYBIT_LINEAR.maker_rate / BINANCE_USDM.maker_rate)
    bps = lambda usdt: usdt / notional * 1e4                                          # noqa: E731
    breakeven = bps(pnl + slip + funding)
    binance = bps(fees + slip)
    # A wider Bybit tick widens the quoted spread one-for-one: a round trip pays the extra full tick.
    extra_tick = 0.0
    if symbol in BYBIT_TICK and symbol in BINANCE_TICK:
        d_tick = BYBIT_TICK[symbol] - BINANCE_TICK[symbol]
        extra_tick = sum(float(t["qty"]) * d_tick for t in trades)
    bybit = bps(bybit_fees + slip + extra_tick)
    all_taker_binance = 2 * BINANCE_USDM.taker_rate * 1e4 + bps(slip)
    return {"notional": notional, "trades": len(trades),
            "gross_edge_bps": bps(pnl + slip), "funding_bps": bps(funding),
            "breakeven_cost_bps": breakeven,
            "binance_cost_bps": binance, "binance_headroom_bps": breakeven - binance,
            "bybit_cost_bps": bybit, "bybit_headroom_bps": breakeven - bybit,
            "bybit_extra_tick_bps": bps(extra_tick),
            "all_taker_binance_cost_bps": all_taker_binance,
            "all_taker_headroom_bps": breakeven - all_taker_binance,
            "slippage_bps": bps(slip), "fees_bps": bps(fees),
            "maker_fee_share": (roles.get("maker", 0.0) / fees) if fees > 0 else None,
            "assumption": "Bybit cost = the same fills at Bybit linear fee rates (taker 0.055%, maker "
                          "0.020%), Binance's modelled slippage, plus Bybit's wider tick where it differs "
                          "(BNBUSDT 0.10 vs 0.01); Bybit depth itself is not modelled historically."}


# ---- regimes -----------------------------------------------------------------------------------------

def regime_table(daily: Sequence[tuple[int, float]], proto: Protocol) -> dict[int, dict[str, str]]:
    """day_open_ms -> {"trend", "vol"} using ONLY daily closes before that day."""
    closes = [c for _, c in daily]
    out: dict[int, dict[str, str]] = {}
    ema = None
    emas: list[float | None] = []
    k = 2.0 / (proto.trend_ema + 1)
    for c in closes:
        ema = c if ema is None else ema + k * (c - ema)
        emas.append(ema)
    rets = [math.log(closes[i] / closes[i - 1]) if i and closes[i - 1] > 0 else 0.0 for i in range(len(closes))]
    vols: list[float | None] = [None] * len(closes)
    for i in range(proto.vol_days, len(closes)):
        w = rets[i - proto.vol_days + 1:i + 1]
        vols[i] = stt.pstdev(w) * math.sqrt(365)
    for j in range(1, len(daily)):
        i = j - 1                                   # the regime of day j is known at day j-1's close
        c, e = closes[i], emas[i]
        e_then = emas[i - proto.trend_slope_days] if i >= proto.trend_slope_days else None
        trend = "RANGE"
        if i >= proto.trend_ema and e_then:
            slope = (e - e_then) / e_then
            if c > e and slope > proto.trend_slope_pct:
                trend = "TREND_UP"
            elif c < e and slope < -proto.trend_slope_pct:
                trend = "TREND_DOWN"
        vol = "NORMAL_VOL"
        hist = [v for v in vols[max(0, i - proto.vol_lookback_days):i + 1] if v is not None]
        if vols[i] is not None and len(hist) >= proto.vol_min_history:
            rank = sum(1 for v in hist if v <= vols[i]) / len(hist)
            vol = "HIGH_VOL" if rank >= proto.vol_high else "LOW_VOL" if rank <= proto.vol_low else "NORMAL_VOL"
        out[daily[j][0]] = {"trend": trend, "vol": vol}
    return out


def regimes(trades: Sequence[dict[str, Any]], table: dict[int, dict[str, str]],
            days: Sequence[int]) -> dict[str, Any]:
    by: dict[str, dict[str, list[dict[str, Any]]]] = {"trend": {}, "vol": {}}
    for t in trades:
        r = table.get(day_of(t["entry_ts"])) or {"trend": "UNKNOWN", "vol": "UNKNOWN"}
        for dim in ("trend", "vol"):
            by[dim].setdefault(r[dim], []).append(t)
    time_share: dict[str, dict[str, float]] = {"trend": {}, "vol": {}}
    labelled = [table[d] for d in days if d in table]
    for dim in ("trend", "vol"):
        for r in labelled:
            time_share[dim][r[dim]] = time_share[dim].get(r[dim], 0) + 1
        n = sum(time_share[dim].values()) or 1
        time_share[dim] = {k: v / n for k, v in time_share[dim].items()}
    out: dict[str, Any] = {}
    total = sum(float(t["net"]) for t in trades)
    for dim in ("trend", "vol"):
        rows = []
        for name, ts in sorted(by[dim].items()):
            st = trade_stats(ts)
            rows.append({"regime": name, "trades": st["trades"], "net": st["net"], "expectancy_r": st["expectancy_r"],
                         "profit_factor": st["profit_factor"], "win_rate": st["win_rate"],
                         "time_share": time_share[dim].get(name)})
        positive = [r for r in rows if r["net"] > 0]
        narrow = total > 0 and len(positive) == 1 and sum(r["net"] for r in rows if r["net"] <= 0) < 0
        out[dim] = {"rows": rows, "narrow": narrow,
                    "narrow_regime": positive[0]["regime"] if narrow else None}
    out["flag"] = any(out[d]["narrow"] for d in ("trend", "vol"))
    return out


def fetch_daily(symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """Daily closes from Binance USD-M (public). Used for regime labels only."""
    import urllib.request
    from app.config import METADATA_KLINES
    out: list[tuple[int, float]] = []
    cur = start_ms
    while cur <= end_ms:
        url = f"{METADATA_KLINES}?symbol={symbol}&interval=1d&startTime={cur}&endTime={end_ms}&limit=1500"
        req = urllib.request.Request(url, headers={"User-Agent": "paperlab-validation"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.loads(r.read())
        if not rows:
            break
        out.extend((int(x[0]), float(x[4])) for x in rows)
        cur = int(rows[-1][0]) + DAY
        if len(rows) < 1500:
            break
    return out


# ---- Monte Carlo ----------------------------------------------------------------------------------------

def monte_carlo(trades: Sequence[dict[str, Any]], start: float, proto: Protocol) -> dict[str, Any]:
    rets = [float(t["net"]) / float(t["equity_at_entry"]) for t in trades if float(t["equity_at_entry"] or 0) > 0]
    n = len(rets)
    if n < 20:
        return {"ran": False, "reason": f"only {n} trades"}
    rng = random.Random(proto.mc_seed)
    floor = start * (1.0 - proto.ruin_floor_pct)
    ends, ends_halted, dds, streaks = [], [], [], []
    ruined = 0
    for _ in range(proto.mc_paths):
        path: list[float] = []
        while len(path) < n:
            s = rng.randrange(n)
            path.extend(rets[s:s + proto.mc_block])
        path = path[:n]
        eq = peak = start
        worst = 0.0
        halted_at = None
        streak = longest = 0
        for r in path:
            eq *= (1.0 + r)
            if eq > peak:
                peak = eq
            dd = 1.0 - eq / peak if peak > 0 else 1.0
            if dd > worst:
                worst = dd
            if halted_at is None and (dd >= proto.ruin_drawdown or eq <= floor):
                halted_at = eq
            if r <= 0:
                streak += 1
                longest = max(longest, streak)
            else:
                streak = 0
        ends.append(eq)
        ends_halted.append(halted_at if halted_at is not None else eq)
        dds.append(worst)
        streaks.append(longest)
        ruined += halted_at is not None
    ends.sort()
    ends_halted.sort()
    dds.sort()
    streaks.sort()
    q = lambda v, p: v[min(len(v) - 1, max(0, int(round(p * (len(v) - 1)))))]     # noqa: E731
    return {"ran": True, "paths": proto.mc_paths, "block": proto.mc_block, "seed": proto.mc_seed,
            "trades_per_path": n, "starting_equity": start,
            "ruin_definition": (f"the frozen engine's halt, whichever comes first: equity <= "
                                f"{1 - proto.ruin_floor_pct:.0%} of the start ({start * (1 - proto.ruin_floor_pct):.2f} USDT) "
                                f"or drawdown from peak >= {proto.ruin_drawdown:.0%}; a ruined path stops trading"),
            "median_ending_equity": q(ends_halted, 0.5), "p5_ending_equity": q(ends_halted, 0.05),
            "p95_ending_equity": q(ends_halted, 0.95),
            "median_ending_equity_no_halt": q(ends, 0.5), "p5_ending_equity_no_halt": q(ends, 0.05),
            "median_max_dd": q(dds, 0.5), "p95_max_dd": q(dds, 0.95), "p99_max_dd": q(dds, 0.99),
            "median_longest_losing_streak": q(streaks, 0.5), "p95_longest_losing_streak": q(streaks, 0.95),
            "p_dd_over_20": sum(1 for d in dds if d > 0.20) / len(dds),
            "p_dd_over_30": sum(1 for d in dds if d > 0.30) / len(dds),
            "p_dd_over_50": sum(1 for d in dds if d > 0.50) / len(dds),
            "ruin_probability": ruined / proto.mc_paths}


# ---- gates -------------------------------------------------------------------------------------------------

def stress_row(name: str, r: dict[str, Any]) -> dict[str, Any]:
    m = r["metrics"]
    pf = m.get("profit_factor")
    return {"scenario": name, "label": SCENARIO_LABELS.get(name, name), "trades": m.get("trades"),
            "net": m.get("net_profit"), "expectancy_r": m.get("expectancy_r"),
            "profit_factor": 999.0 if pf == float("inf") else pf, "max_dd": m.get("max_drawdown_pct"),
            "fees": m.get("fees_paid"), "slippage": m.get("slippage_cost"), "funding": m.get("funding_paid"),
            "ending_equity": m.get("ending_equity"), "halted": r.get("halted"),
            "survives": bool((m.get("net_profit") or 0) > 0 and (m.get("expectancy_r") or 0) > 0 and not r.get("halted")),
            "gated": name in GateConfig().stress_required}


def gates(result: dict[str, Any], g: GateConfig) -> list[dict[str, Any]]:
    m = result["metrics"]
    pf = m.get("profit_factor")
    pf = 999.0 if pf == float("inf") else (pf or 0.0)
    w = result["windows"]
    mc = result["monte_carlo"]
    rb = result["robustness"]
    stress = {s["scenario"]: s for s in result["stress"]}
    failed_stress = [SCENARIO_LABELS[s] for s in g.stress_required if not (stress.get(s) or {}).get("survives")]

    def gate(name, ok, actual, threshold, stage):
        return {"name": name, "ok": bool(ok), "actual": actual, "threshold": threshold, "stage": stage}
    return [
        gate("sufficient trades", m["trades"] >= g.min_trades, m["trades"], f">= {g.min_trades}", "MULTI_YEAR"),
        gate("net PnL after all costs", m["net_profit"] > g.min_net_profit, round(m["net_profit"], 4), "> 0", "MULTI_YEAR"),
        gate("expectancy", (m["expectancy_r"] or 0) > g.min_expectancy_r, round(m["expectancy_r"] or 0, 4), "> 0 R", "MULTI_YEAR"),
        gate("profit factor", pf >= g.min_profit_factor, round(pf, 3), f">= {g.min_profit_factor}", "MULTI_YEAR"),
        gate("max drawdown / never halted", m["max_drawdown_pct"] < g.max_drawdown_pct and not result["halted"],
             round(m["max_drawdown_pct"], 4), f"< {g.max_drawdown_pct:.0%}, no HALT", "MULTI_YEAR"),
        gate("liquidations", m["liquidation_count"] <= g.max_liquidations, m["liquidation_count"], "0", "MULTI_YEAR"),
        gate("profitable active quarters", (w["profitable_ratio"] or 0) > g.min_profitable_window_ratio,
             f"{w['profitable']}/{w['active']}", f"> {g.min_profitable_window_ratio:.0%}", "MULTI_YEAR"),
        gate("not concentrated in 3 trades", (not g.require_positive_without_top3) or rb["net_without_best3"] > 0,
             round(rb["net_without_best3"], 4), "> 0 without best 3", "MULTI_YEAR"),
        gate("not one lucky year", (not g.require_positive_without_best_year) or rb["net_without_best_year"] > 0,
             round(rb["net_without_best_year"], 4), "> 0 without best year", "MULTI_YEAR"),
        gate("Monte Carlo ruin", mc.get("ran") and mc["ruin_probability"] <= g.max_ruin_probability,
             mc.get("ruin_probability"), f"<= {g.max_ruin_probability:.0%}", "MONTE_CARLO"),
        gate("stress survives", not failed_stress, ", ".join(failed_stress) or "all survive",
             "net > 0 and expectancy > 0 in every single-factor stress", "STRESS"),
    ]


def stage_status(gs: Sequence[dict[str, Any]]) -> dict[str, str]:
    out = {}
    for stage in ("MULTI_YEAR", "MONTE_CARLO", "STRESS"):
        rows = [x for x in gs if x["stage"] == stage]
        out[stage] = "PASS" if rows and all(x["ok"] for x in rows) else "FAIL"
    out["VERDICT"] = "PASS" if all(v == "PASS" for v in out.values()) else "FAIL"
    return out


def assemble(key: str, man: dict[str, Any], runs: dict[str, dict[str, Any]], daily: Sequence[tuple[int, float]],
             proto: Protocol) -> dict[str, Any]:
    """Everything the validation of one candidate reports, from its scenario replays."""
    from app.competition.cost_efficiency import cost_efficiency
    normal = runs["NORMAL"]
    months = months_between(proto.first_month, proto.last_month)
    trades = normal["trades"]
    m = normal["metrics"]
    yrs = years(trades, normal["periods"], months)
    halt = halt_info(normal)
    halt_ts = halt.get("ts")
    for y in yrs:          # a year the book spent HALTED is not a "flat" year: say so
        y["halted"] = bool(halt_ts) and halt_ts < month_start_ms(f"{y['year']}-01")
        y["halted_during"] = bool(halt_ts) and year_of(halt_ts) == y["year"]
    win = windows(trades, normal["periods"], months, proto.active_window_min_trades)
    rb = robustness(trades, yrs)
    table = regime_table(daily, proto)
    first, last = month_start_ms(months[0]), month_end_ms(months[-1])
    reg = regimes(trades, table, [d for d, _ in daily if first <= d <= last])
    ce = cost_efficiency(m, None, (last - first + 1) / DAY)
    result = {
        "key": key, "manifest": man, "venue": VENUE, "window": {"first": months[0], "last": months[-1],
                                                                "first_ms": first, "last_ms": last},
        "metrics": m, "halted": bool(halt.get("kind")), "halt_ts": halt_ts, "halt": halt,
        "capacity": capacity(normal),
        "signals": normal["signals"], "rejects": normal["rejects"], "bars": normal["bars"],
        "trade_stats": trade_stats(trades), "cost_efficiency": ce,
        "trades_per_month": len(trades) / len(months), "windows": win, "years": yrs, "robustness": rb,
        "regimes": reg, "headroom": headroom(normal, man.get("symbol")),
        "stress": [stress_row(n, runs[n]) for n in SCENARIOS if n in runs],
        "monte_carlo": monte_carlo(trades, m["starting_equity"], proto),
        "ledger": [{k: t.get(k) for k in ("entry_ts", "exit_ts", "side", "qty", "entry_price", "exit_price", "pnl",
                                          "fees", "net", "r", "exit_kind", "attack_state", "equity_at_entry",
                                          "notional")} for t in trades],
        "daily_equity": normal.get("daily_equity") or [],
    }
    result["gates"] = gates(result, proto.gates)
    result["stages"] = stage_status(result["gates"])
    result["verdict"] = result["stages"]["VERDICT"]
    return result
