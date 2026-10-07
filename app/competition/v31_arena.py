"""V3.1 AGGRESSIVE EDGE arena runner (docs/V31_PROTOCOL.md).

    RAW       every family x coin x timeframe with an OBSERVER in the gate slot: each legal candidate is
              simulated in the shadow book at TAKE size, nothing trades. Its sequenced outcomes are the
              EVIDENCE of the expected-net-edge model (never a competitor).
    CONTROL   EDGE GATE + AGGRESSIVE_V31 deterministic sizing (TAKE 1.0%, ATTACK 1.5%, STRONG 2.0%)
    JEV       the same pipeline + Jev V3 (SKIP / TAKE / ATTACK) on every candidate that passed the edge gate
    TAKE      the same pipeline, every candidate TAKE (ALWAYS-TAKE)
    RANDOM    the same pipeline, an action drawn with the JEV bot's own SKIP / TAKE / ATTACK rates
    CAPACITY  CONTROL at 50 / 100 USDT (diagnostic only; never part of the 20 USDT competition)

Every bot is replayed by THE same engine construction (`V31Arena.engine`): Bybit fees and filters, the
execution model, the frozen fee gate and RiskManager, the 1m tape with entries from the first trading
day. The edge model is CAUSAL in DEVELOPMENT (a candidate at t only sees evidence that exited before t)
and FROZEN in TEST (the DEVELOPMENT ledger; no holdout outcome ever enters it).
"""
from __future__ import annotations

import dataclasses
import logging
import time
from collections import defaultdict
from typing import Any, Mapping, Sequence

from app.backtest.brackets import BracketTable
from app.backtest.replay import ReplayEngine
from app.competition import metrics as mx
from app.competition.arena import _downsample, funding_for, stream
from app.competition.v3_arena import day_ms, decision_rows, trade_rows
from app.competition.v31_config import SizingV31, V31Config, V31Identity, min_trades
from app.competition.v31_edge import EdgeGate, EdgeModel, sequence

log = logging.getLogger("paperlab.competition.v31")

FAMILY = {"S33.1": "PULLBACK_REGIME_CONTINUATION", "S35.1": "FLOW_PERSISTENCE_MOMENTUM",
          "S37": "IMPULSE_CONTINUATION"}
FAMILY_LABEL = {"S33.1": "pullback regime continuation", "S35.1": "flow persistence momentum",
                "S37": "impulse continuation"}
SIZING_MODE = {"CONTROL": "CONTROL", "CAPACITY": "CONTROL", "JEV": "GATED", "TAKE": "GATED",
               "RANDOM": "GATED", "RAW": "RAW"}


def settings_v31(base: Any, cfg: V31Config, balance: float) -> Any:
    risk = cfg.risk
    return dataclasses.replace(base, strategy_starting_balance=float(balance),
                               risk_per_trade_pct=float(risk.ordinary_risk_pct),
                               max_fee_share_of_r=float(cfg.max_fee_share_of_r),
                               min_notional_safety_multiplier=float(cfg.min_notional_safety_multiplier))


class _Probes:
    """Dry-run questions to the RiskManager, answered in the engine's CURRENT state (no side effects)."""

    def __init__(self, eng: ReplayEngine):
        self.eng = eng

    def legal(self, sig: Any) -> Any:
        e = self.eng
        return e.risk.approve(sig, e._meta, e.ctx.prices, venue_kind=e.venue, dry_run=True)

    def resize(self, sig: Any, mult: float) -> tuple[bool, str]:
        e = self.eng
        meta = e._meta
        saved = meta.size_mult
        meta.size_mult = saved * float(mult)
        try:
            d = e.risk.approve(sig, meta, e.ctx.prices, venue_kind=e.venue, dry_run=True)
        finally:
            meta.size_mult = saved
        if not d.approved:
            return False, str(d.reason).split(":")[0]
        if e.max_risk_pct is not None and not e._cap_risk(d, sig, meta):
            return False, "risk_above_hard_max"
        return True, ""


class _SizingWithProbe(SizingV31):
    """CONTROL's deterministic ATTACK falls back to TAKE when the larger order would not be legal."""

    def __init__(self, *a: Any, probes: _Probes | None = None, **k: Any):
        super().__init__(*a, **k)
        self.probes = probes

    def __call__(self, sig: Any, health: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        mult, info = super().__call__(sig, health)
        meta = getattr(sig, "meta", None) or {}
        edge = meta.get("edge") or {}
        info.update({"edge_candidate_id": meta.get("edge_candidate_id"), "qband": edge.get("qband"),
                     "regime": meta.get("regime"), "vol_band": meta.get("vol_band")})
        if mult > 1.0 and self.probes is not None:
            ok, why = self.probes.resize(sig, mult)
            if not ok:
                info.update({"tier": "TAKE", "target_risk_pct": self.profile.ordinary_risk_pct,
                             "attack_downgrade": f"ATTACK_NOT_LEGAL:{why}"})
                return 1.0, info
        return mult, info


class V31Arena:
    def __init__(self, settings: Any, cfg: V31Config, rules: dict[str, Any], evidence: Sequence[Mapping[str, Any]] = (),
                 brackets: BracketTable | None = None):
        from app.strategies.registry import load_v31
        self.settings = settings
        self.cfg = cfg
        self.rules = rules
        self.evidence = list(evidence)
        self.brackets = brackets or BracketTable.fallback()
        self.classes = load_v31()
        self._funding: dict[str, Any] = {}

    def funding(self, symbol: str) -> Any:
        if symbol not in self._funding:
            self._funding[symbol] = funding_for(self.settings, [symbol], self.cfg.months)
        return self._funding[symbol]

    def bound(self, ident: V31Identity) -> Any:
        return self.classes[ident.strategy_id].for_timeframe(ident.timeframe)

    def engine(self, ident: V31Identity, gate: Any = None) -> tuple[ReplayEngine, EdgeGate | None, dict[str, list[str]]]:
        """THE construction of a V3.1 bot's engine (every role)."""
        from collections import deque
        cfg = self.cfg
        balance = float(ident.balance if ident.role == "CAPACITY" else cfg.starting_balance)
        raw = ident.role == "RAW"
        edge_gate = None if raw else EdgeGate(EdgeModel(self.evidence, cfg.edge))
        sizing = _SizingWithProbe(dataclasses.replace(cfg.risk, starting_balance=balance), mode=SIZING_MODE[ident.role])
        eng = ReplayEngine(settings_v31(self.settings, cfg, balance), [ident.symbol], rules=self.rules, seed=cfg.seed,
                           funding=self.funding(ident.symbol), execution=cfg.execution, fees=cfg.fees,
                           brackets=self.brackets, fee_source="schedule", sizing=sizing,
                           leverage_policy=cfg.leverage_policy, max_risk_pct=cfg.risk.max_risk_pct, gate=gate,
                           cost_gate=edge_gate, shadow_cost_rejects=ident.role == "CONTROL")
        # Keep every closed trade (the Portfolio's 5,000-trade memory bound is for the live engine).
        eng.portfolio.closed_trades = deque(eng.portfolio.closed_trades, maxlen=None)
        if eng.shadow is not None:
            eng.shadow.closed_trades = deque(eng.shadow.closed_trades, maxlen=None)
        probes = _Probes(eng)
        sizing.probes = probes
        if edge_gate is not None:
            edge_gate.probe = probes.legal
        if gate is not None and hasattr(gate, "probe"):
            gate.probe = probes.resize
        # Which exits each SHADOW position took (did it reach its first target?). Real trades have fills.
        kinds: dict[str, list[str]] = defaultdict(list)
        if eng.shadow is not None:
            orig = eng.shadow.close_position

            def close_position(position_id: str, fraction: float, ref_price: float, kind: str, *a: Any, **k: Any):
                kinds[position_id].append(kind)
                return orig(position_id, fraction, ref_price, kind, *a, **k)
            eng.shadow.close_position = close_position  # type: ignore[method-assign]
        return eng, edge_gate, kinds

    def run(self, ident: V31Identity, gate: Any = None) -> dict[str, Any]:
        t0 = time.time()
        cls = self.bound(ident)
        eng, edge_gate, kinds = self.engine(ident, gate)
        since = self.cfg.observe_from if ident.role == "RAW" else self.cfg.trade_from
        res = eng.run(cls, stream(self.settings, ident.symbol, self.cfg.months), since_ms=day_ms(since),
                      leverage=self.cfg.leverage_ceiling, signal_tf=ident.timeframe, only_symbol=ident.symbol)
        m = mx.compute(ident.strategy_id, res.trades, res.fills, res.equity, res.starting_equity,
                       rejects=res.rejects, halted=res.halted, version=ident.key, leverage=res)
        m.configured_max_leverage = self.cfg.leverage_ceiling
        return build_record(ident, cls, res, m, gate, edge_gate, kinds, self.cfg, since, time.time() - t0)


# ---- the per-bot record -----------------------------------------------------------------------------------

def trade_rows_v31(res: Any, symbol: str) -> list[dict[str, Any]]:
    rows = trade_rows(res.trades, res.fills)
    entry = {f.position_id: f for f in res.fills if f.kind == "entry" or f.is_open}
    tp = {f.position_id for f in res.fills if f.kind == "tp"}
    for r in rows:
        f = entry.get(r["pid"])
        em = dict(getattr(f, "meta", None) or {}) if f is not None else {}
        ref = getattr(f, "ref_price", None) if f is not None else None
        r.update({"symbol": symbol, "decision_price": ref, "tp_hit": r["pid"] in tp,
                  "half_spread_bps": round(abs(r["entry"] / ref - 1.0) * 1e4, 4) if ref else None,
                  "tier": em.get("tier"), "health": em.get("health"), "edge_net_r": em.get("edge_net_r"),
                  "edge_lower_r": em.get("edge_lower_r"), "edge_candidate_id": em.get("edge_candidate_id"),
                  "qband": em.get("qband"), "regime": em.get("regime"), "vol_band": em.get("vol_band"),
                  "attack_downgrade": em.get("attack_downgrade")})
    return rows


def _shadow_outcomes(res: Any, kinds: Mapping[str, list[str]]) -> dict[str, dict[str, Any]]:
    """decision / candidate id -> the shadow trade it produced."""
    shadow = {t.position_id: t for t in res.shadow_trades}
    out = {}
    for pid, did in res.shadow_links.items():
        t = shadow.get(pid)
        if t is None:
            continue
        out[did] = {"entry_ts": t.entry_ts, "exit_ts": t.exit_ts, "net": round(t.net, 6), "r": round(t.r_multiple, 4),
                    "exit_kind": t.exit_kind, "tp_hit": "tp" in kinds.get(pid, []),
                    "stopped": t.exit_kind == "stop", "hold_s": max(0, (t.exit_ts - t.entry_ts) // 1000)}
    return out


def observation_rows(gate: Any, res: Any, kinds: Mapping[str, list[str]], cooldown_ms: int) -> list[dict[str, Any]]:
    """RAW: every observed candidate with its simulated outcome, then SEQUENCED like an ungated bot."""
    outcomes = _shadow_outcomes(res, kinds)
    rows = []
    for c in getattr(gate, "rows", []):
        o = outcomes.get(c["id"])
        rows.append({**c, "entry_ts": o["entry_ts"] if o else None, "exit_ts": o["exit_ts"] if o else None,
                     "net_r": o["r"] if o else None, "net": o["net"] if o else None,
                     "tp_hit": o["tp_hit"] if o else None, "stopped": o["stopped"] if o else None,
                     "exit_kind": o["exit_kind"] if o else None, "hold_s": o["hold_s"] if o else None})
    sequence(rows, cooldown_ms)
    return rows


def edge_rows(edge_gate: EdgeGate | None, res: Any, rows_trades: Sequence[dict[str, Any]],
              kinds: Mapping[str, list[str]]) -> list[dict[str, Any]]:
    """Every edge-gate verdict with what became of the candidate: TRADED (the real trade's R) or, for a
    refused one, SHADOW (what it would have done at TAKE size) -- the edge-calibration evidence."""
    if edge_gate is None:
        return []
    traded = {t["edge_candidate_id"]: t for t in rows_trades if t.get("edge_candidate_id")}
    shadow = _shadow_outcomes(res, kinds)
    by_cg = {e.get("candidate_id"): e["id"] for e in res.cost_gate_events if e.get("candidate_id")}
    out = []
    for r in edge_gate.rows:
        t = traded.get(r["id"])
        s = shadow.get(by_cg.get(r["id"], ""))
        if t is not None:
            realized, outcome = t["r"], "TRADED"
        elif s is not None:
            realized, outcome = s["r"], "SHADOW"
        else:
            realized, outcome = None, None
        out.append({**r, "outcome": outcome, "realized_r": realized})
    return out


def funnel(res: Any, rows_edge: Sequence[dict[str, Any]], decisions: Sequence[dict[str, Any]], role: str,
           entries: int) -> dict[str, Any]:
    """raw setups -> legal -> positive expected edge -> Jev TAKE/ATTACK -> executed."""
    raw = int(res.signals)
    legal = sum(1 for r in rows_edge if r.get("legal"))
    positive = sum(1 for r in rows_edge if r.get("legal") and r.get("status") == "PASS")
    out = {"raw_setups": raw, "legal": legal if rows_edge else None, "positive_edge": positive if rows_edge else None,
           "jev_accepted": None, "executed": entries,
           "edge_rejected": sum(1 for r in rows_edge if r.get("status") != "PASS"),
           "edge_reasons": _count(r.get("reason") for r in rows_edge if r.get("status") != "PASS"),
           "illegal_reasons": _count(r.get("legal_reason") for r in rows_edge if r.get("legal") is False)}
    if role in ("JEV", "RANDOM", "TAKE"):
        out["jev_accepted"] = sum(1 for d in decisions if d.get("level") and d["level"] != "SKIP")
        out["gate_decisions"] = len(decisions)
        out["min_notional_after_jev"] = sum(1 for d in decisions
                                            if str(d.get("result") or "").startswith("RISK_REJECTED:below_min"))
        out["attack_not_legal"] = sum(1 for d in decisions if str(d.get("downgrade") or "").startswith("ATTACK_NOT_LEGAL"))
    return out


def _count(items: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        if x is None:
            continue
        out[str(x)] = out.get(str(x), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def build_record(ident: V31Identity, cls: Any, res: Any, m: Any, gate: Any, edge_gate: EdgeGate | None,
                 kinds: Mapping[str, list[str]], cfg: V31Config, since: str, elapsed: float) -> dict[str, Any]:
    strat = cls()
    rej = dict(res.rejects)
    below_min = sum(v for k, v in rej.items() if k.startswith("below_min"))
    entries = sum(1 for f in res.fills if f.kind == "entry")
    days = float((day_ms(cfg.trade_to) + 86_400_000 - day_ms(since)) / 86_400_000)
    rec: dict[str, Any] = {
        "identity": ident.to_dict(), "key": ident.key, "role": ident.role, "pair_id": ident.pair_id,
        "family": FAMILY.get(ident.strategy_id, ""), "fingerprint": ident.fingerprint(strat.params),
        "window": {"from": since, "to": cfg.trade_to, "days": days},
        "balance": float(ident.balance if ident.role == "CAPACITY" else cfg.starting_balance),
        "elapsed_s": round(elapsed, 2),
    }
    if ident.role == "RAW":
        obs = observation_rows(gate, res, kinds, int(strat.cooldown_ms() or 0))
        rec.update({"metrics": {}, "trades": [], "decisions": [], "equity": [], "observations": obs,
                    "activity": {"signals": res.signals, "legal_candidates": len(obs),
                                 "with_outcome": sum(1 for o in obs if o.get("net_r") is not None),
                                 "sequenced": sum(1 for o in obs if o.get("sequenced")),
                                 "risk_rejected": rej}})
        return rec
    trades = trade_rows_v31(res, ident.symbol)
    decisions = decision_rows(gate, res) if gate is not None else []
    rows_edge = edge_rows(edge_gate, res, trades, kinds)
    rec.update({
        "metrics": m.to_dict(),
        "activity": {"signals": res.signals, "edge_rejected": rej.get("cost_gate", 0),
                     "below_exchange_minimum": below_min, "entries": entries,
                     "risk_rejected": {k: v for k, v in rej.items() if k != "cost_gate"},
                     "gate_decisions": getattr(gate, "decisions", 0) if gate is not None else 0,
                     "halted": bool(res.halted), "halt_ts": res.halt_ts,
                     "participation_min": min_trades(ident.timeframe, days)},
        "funnel": funnel(res, rows_edge, decisions, ident.role, entries),
        "trades": trades,
        "decisions": decisions,
        "edge_rows": rows_edge if ident.role == "CONTROL" else [],
        "equity": _downsample(res.equity, 400),
    })
    return rec


# ---- field selection (activity only, never PnL) ---------------------------------------------------------------

def select_field(controls: Sequence[dict[str, Any]], cfg: V31Config) -> dict[str, Any]:
    """Neutral, deterministic: for every family x timeframe slot, the `field_coins_per_slot` coins with
    the most EXECUTED entries (legal, positive expected edge, RiskManager-approved) -- never PnL -- among
    bots with valid data and at least the adequate sample (30 closed trades). Ties alphabetical. If the
    field has fewer than `field_min_pairs` pairs the protocol's shortfall is reported, never filled by
    looking at results."""
    by: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for r in controls:
        idn = r["identity"]
        if idn["role"] != "CONTROL" or idn["timeframe"] not in cfg.timeframes:
            continue
        by[(idn["strategy_id"], idn["timeframe"])].append((int(r["activity"]["entries"]), idn["coin"]))
    picked, thin = [], []
    for tf in cfg.timeframes:
        for sid in sorted({k[0] for k in by}):
            cands = sorted(by.get((sid, tf), []), key=lambda x: (-x[0], x[1]))
            ok = [(n, coin) for n, coin in cands if n >= 30]
            for n, coin in ok[:cfg.field_coins_per_slot]:
                picked.append({"strategy_id": sid, "coin": coin, "timeframe": tf, "activity": n})
            if len(ok) < cfg.field_coins_per_slot:
                thin.append({"strategy_id": sid, "timeframe": tf, "eligible": len(ok),
                             "best": [{"coin": c, "entries": n} for n, c in cands[:3]]})
    return {"pairs": picked, "thin_slots": thin, "n": len(picked),
            "meets_minimum": len(picked) >= cfg.field_min_pairs,
            "rule": (f"activity only: top {cfg.field_coins_per_slot} coins per family x timeframe by executed "
                     f"entries (>= 30), ties alphabetical; never PnL")}
