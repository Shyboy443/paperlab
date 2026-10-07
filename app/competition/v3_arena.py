"""V3 AGGRESSIVE DISCOVERY: the arena runner (docs/V3_PROTOCOL.md).

    SCAN     every family x coin x timeframe as a CONTROL bot (AGGRESSIVE_V3 tiers, no Jev)
    FIELD    one coin per family x timeframe, chosen by ACTIVITY (legal, cost-gate-passing entries)
             -- never by PnL -- each family on a different coin per timeframe
    JEV      the +JEV2 twin of every field bot (Jev V2 answers every candidate that reached it)
    TAKE     the same pipeline with a constant NORMAL verdict (ALWAYS-TAKE)
    RANDOM   20 seeds of a random verdict drawn from the +JEV2 bot's own action distribution

Every bot is replayed by THE same engine construction (`V3Arena.engine`): 20 USDT isolated book,
Bybit fees and filters, the execution model, the frozen fee gate and RiskManager, the cost gate at
EDGE_COST_RATIO 2.0, the 1m tape with entries from the first DEVELOPMENT day. Only the gate differs
between a CONTROL and its twins -- plus the sizing mode: a twin sizes at normal risk x the verdict.

The record kept per bot is everything the Bot Analyzer needs and nothing it has to recompute from the
tape: every closed trade with its costs and entry context, every gate decision with the outcome of
the candidate (the real trade, or the shadow trade the engine simulated for a skip).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import time
from collections import defaultdict
from typing import Any, Sequence

from app.backtest.brackets import BracketTable
from app.backtest.replay import ReplayEngine
from app.competition import metrics as mx
from app.competition.arena import _downsample, funding_for, stream
from app.competition.v3_config import AttackPolicyV3, V3Config, V3Identity, min_trades

log = logging.getLogger("paperlab.competition.v3")

FAMILY = {"S31": "MOMENTUM_BREAKOUT", "S32": "VOLATILITY_EXPANSION", "S33": "PULLBACK_CONTINUATION",
          "S34": "RANGE_REJECTION", "S35": "FLOW_MOMENTUM", "S36": "FAST_MEAN_REVERSION"}
FAMILY_LABEL = {"S31": "momentum breakout", "S32": "volatility expansion", "S33": "pullback continuation",
                "S34": "range rejection", "S35": "flow momentum", "S36": "fast mean reversion"}


def day_ms(date: str) -> int:
    return int(dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def settings_v3(base: Any, cfg: V3Config) -> Any:
    risk = cfg.risk
    return dataclasses.replace(base, strategy_starting_balance=float(cfg.starting_balance),
                               risk_per_trade_pct=float(risk.ordinary_risk_pct),
                               max_fee_share_of_r=float(cfg.max_fee_share_of_r),
                               min_notional_safety_multiplier=float(cfg.min_notional_safety_multiplier))


class V3Arena:
    def __init__(self, settings: Any, cfg: V3Config, rules: dict[str, Any], brackets: BracketTable | None = None):
        from app.strategies.registry import load_v3
        self.settings = settings
        self.cfg = cfg
        self.rules = rules
        self.brackets = brackets or BracketTable.fallback()
        self.classes = load_v3()
        self._funding: dict[str, Any] = {}

    def funding(self, symbol: str) -> Any:
        if symbol not in self._funding:
            self._funding[symbol] = funding_for(self.settings, [symbol], self.cfg.months)
        return self._funding[symbol]

    def bound(self, ident: V3Identity) -> Any:
        return self.classes[ident.strategy_id].for_timeframe(ident.timeframe)

    def engine(self, ident: V3Identity, gate: Any = None) -> ReplayEngine:
        """THE construction of a V3 bot's engine (CONTROL and every twin)."""
        from collections import deque

        from app.competition.cost_gate import CostGate, CostGateConfig
        cfg = self.cfg
        eng = ReplayEngine(settings_v3(self.settings, cfg), [ident.symbol], rules=self.rules, seed=cfg.seed,
                           funding=self.funding(ident.symbol), execution=cfg.execution, fees=cfg.fees,
                           brackets=self.brackets, fee_source="schedule",
                           sizing=AttackPolicyV3(cfg.risk, jev_mode=ident.role != "CONTROL"),
                           leverage_policy=cfg.leverage_policy, max_risk_pct=cfg.risk.max_risk_pct, gate=gate,
                           cost_gate=CostGate(CostGateConfig(min_edge_to_cost=float(cfg.cost_gate_min_ratio))),
                           shadow_cost_rejects=False)
        # The Portfolio keeps the last 5,000 closed trades (a live-engine memory bound). A fast intraday
        # bot can close more than that in six months, and its earliest trades would silently vanish
        # from its metrics -- a V3 replay keeps every one.
        eng.portfolio.closed_trades = deque(eng.portfolio.closed_trades, maxlen=None)
        if eng.shadow is not None:
            eng.shadow.closed_trades = deque(eng.shadow.closed_trades, maxlen=None)
        return eng

    def run(self, ident: V3Identity, gate: Any = None) -> dict[str, Any]:
        t0 = time.time()
        cls = self.bound(ident)
        eng = self.engine(ident, gate)
        res = eng.run(cls, stream(self.settings, ident.symbol, self.cfg.months), since_ms=day_ms(self.cfg.trade_from),
                      leverage=self.cfg.leverage_ceiling, signal_tf=ident.timeframe, only_symbol=ident.symbol)
        m = mx.compute(ident.strategy_id, res.trades, res.fills, res.equity, res.starting_equity,
                       rejects=res.rejects, halted=res.halted, version=ident.key, leverage=res)
        m.configured_max_leverage = self.cfg.leverage_ceiling
        return build_record(ident, cls, res, m, gate, self.cfg, time.time() - t0)


# ---- the per-bot record -----------------------------------------------------------------------------------

def _fills_by_position(fills: Sequence[Any]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = defaultdict(list)
    for f in fills:
        if f.position_id:
            out[f.position_id].append(f)
    return out


def trade_rows(trades: Sequence[Any], fills: Sequence[Any]) -> list[dict[str, Any]]:
    """One row per closed trade: gross at decision prices, fees, slippage, funding, net, R and the
    context it was entered with (quality, edge-to-cost, expected move, stop, sizing state, Jev)."""
    by_pos = _fills_by_position(fills)
    rows = []
    for t in trades:
        fs = by_pos.get(t.position_id, [])
        slip = sum(mx.slippage_usdt(f) for f in fs if f.kind != "funding")
        fund = sum(f.realized_pnl for f in fs if f.kind == "funding")
        entry = next((f for f in fs if f.is_open or f.kind == "entry"), None)
        em = dict(entry.meta or {}) if entry is not None else {}
        rows.append({
            "pid": t.position_id, "side": t.side, "entry_ts": t.entry_ts, "exit_ts": t.exit_ts,
            "hold_s": max(0, (t.exit_ts - t.entry_ts) // 1000), "qty": t.qty,
            "entry": t.entry_price, "exit": t.exit_price,
            "gross": round(t.pnl + slip, 6), "fees": round(t.fees, 6), "slippage": round(slip, 6),
            "funding": round(fund, 6), "net": round(t.net + fund, 6), "r": round(t.r_multiple, 4),
            "exit_kind": t.exit_kind, "leverage": int(entry.leverage) if entry is not None else None,
            "notional": round(t.qty * t.entry_price, 4),
            "quality": em.get("signal_quality"), "e2c": em.get("edge_to_cost"),
            "state": em.get("attack_state"), "tier": em.get("quality"), "risk_pct": em.get("risk_pct"),
            "risk_usd": em.get("risk_usd"),
            "jev_level": em.get("jev_level"), "jev_mult": em.get("jev_multiplier"),
            "decision_id": em.get("decision_id"), "spread_cost": em.get("spread_cost"),
            "latency_ms": em.get("latency_ms")})
    return rows


def decision_rows(gate: Any, res: Any) -> list[dict[str, Any]]:
    """Every gate verdict with what became of its candidate: TRADED (the real trade), SHADOW (the
    counterfactual trade the engine simulated for a skip), or no outcome (never filled)."""
    if gate is None:
        return []
    results = {e.get("decision_id"): e.get("result") for e in res.gate_events if e.get("decision_id")}
    real = {t.position_id: t for t in res.trades}
    shadow = {t.position_id: t for t in res.shadow_trades}
    outcome: dict[str, tuple[str, Any]] = {}
    for pid, did in res.entry_links.items():
        if pid in real:
            outcome[did] = ("TAKEN", real[pid])
    for pid, did in res.shadow_links.items():
        if pid in shadow:
            outcome[did] = ("SHADOW", shadow[pid])
    out = []
    for row in getattr(gate, "rows", []):
        kind, t = outcome.get(row["id"], (None, None))
        out.append({**row, "result": results.get(row["id"]), "outcome": kind,
                    "net": round(t.net, 6) if t is not None else None,
                    "r": round(t.r_multiple, 4) if t is not None else None})
    return out


def build_record(ident: V3Identity, cls: Any, res: Any, m: Any, gate: Any, cfg: V3Config,
                 elapsed: float) -> dict[str, Any]:
    trades = trade_rows(res.trades, res.fills)
    rej = dict(res.rejects)
    below_min = sum(v for k, v in rej.items() if k.startswith("below_min"))
    entries = sum(1 for f in res.fills if f.kind == "entry")
    need = min_trades(ident.timeframe, cfg.days)
    return {
        "identity": ident.to_dict(), "key": ident.key, "role": ident.role, "pair_id": ident.pair_id,
        "family": FAMILY.get(ident.strategy_id, ""), "fingerprint": ident.fingerprint(getattr(cls, "Params", None) and cls().params),
        "experimental": ident.timeframe in ("1m",), "window": {"from": cfg.trade_from, "to": cfg.trade_to, "days": cfg.days},
        "metrics": m.to_dict(),
        "activity": {"signals": res.signals, "cost_gate_rejected": rej.get("cost_gate", 0),
                     "below_exchange_minimum": below_min, "entries": entries,
                     "risk_rejected": {k: v for k, v in rej.items() if k not in ("cost_gate",)},
                     "gate_decisions": getattr(gate, "decisions", 0) if gate is not None else 0,
                     "halted": bool(res.halted), "halt_ts": res.halt_ts,
                     "participation_min": need},
        "trades": trades,
        "decisions": decision_rows(gate, res),
        "equity": _downsample(res.equity, 400),
        "elapsed_s": round(elapsed, 2),
    }


# ---- field selection (activity only) ------------------------------------------------------------------------

def select_field(scan: Sequence[dict[str, Any]], cfg: V3Config) -> dict[str, Any]:
    """One coin per family x field timeframe, by ACTIVITY (entries that passed legality, the cost gate
    and the RiskManager) -- never PnL. Greedy over timeframes in field order, families sorted; each
    family takes a coin it has not used on another timeframe; ties alphabetical. A slot whose most
    active eligible coin cannot reach the participation minimum is left empty and reported."""
    by: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for r in scan:
        idn = r["identity"]
        if idn["role"] != "CONTROL" or idn["timeframe"] not in cfg.field_timeframes:
            continue
        by[(idn["strategy_id"], idn["timeframe"])].append((int(r["activity"]["entries"]), idn["coin"]))
    used: dict[str, set[str]] = defaultdict(set)
    picked, empty = [], []
    families = sorted({k[0] for k in by})
    for tf in cfg.field_timeframes:
        need = min_trades(tf, cfg.days)
        for sid in families:
            cands = sorted(by.get((sid, tf), []), key=lambda x: (-x[0], x[1]))
            cands = [(n, coin) for n, coin in cands if coin not in used[sid]]
            if not cands or cands[0][0] < need:
                empty.append({"strategy_id": sid, "timeframe": tf, "need": need,
                              "best": {"coin": cands[0][1], "entries": cands[0][0]} if cands else None})
                continue
            n, coin = cands[0]
            used[sid].add(coin)
            picked.append({"strategy_id": sid, "coin": coin, "timeframe": tf, "activity": n, "need": need})
    return {"pairs": picked, "empty_slots": empty,
            "rule": "activity only: most legal cost-gate-passing entries; distinct coin per family per timeframe"}


def action_distribution(decisions: Sequence[dict[str, Any]]) -> tuple[dict[str, float], float]:
    """The +JEV2 bot's own CHOSEN actions (an error counts as the SKIP it became), and the share of its
    ATTACKs that sized 2x -- the RANDOM-FILTER baseline draws from exactly this."""
    n = len(decisions)
    if not n:
        return {"NORMAL": 1.0}, 0.0
    counts: dict[str, int] = defaultdict(int)
    for d in decisions:
        counts[d.get("chosen") or "SKIP"] += 1
    attacks = [d for d in decisions if d.get("level") == "ATTACK"]
    strong = sum(1 for d in attacks if (d.get("mult") or 0) >= 2.0)
    return {k: v / n for k, v in counts.items()}, (strong / len(attacks)) if attacks else 0.0
