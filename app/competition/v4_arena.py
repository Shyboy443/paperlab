"""V4 INTRADAY SPECIALIST arena runner (docs/V4_PROTOCOL.md).

    RAW       every family x coin x timeframe of the grid with an OBSERVER in the gate slot: each legal setup is
              simulated in the shadow book at TAKE size, nothing trades. Its sequenced outcomes are the
              EVIDENCE of the expected-edge model (never a competitor).
    CONTROL   legality + cost + EXPECTED-EDGE gate (causal, same window) + AGGRESSIVE_V4 sizing
    JEV       the same pipeline + Jev V4 (CONTRADICT / SUPPORT / STRONGLY SUPPORT -> SKIP / TAKE / ATTACK)
    TAKE      the same pipeline, every candidate TAKE (ALWAYS-TAKE)
    RANDOM    the same pipeline, an action drawn with the JEV bot's own SKIP / TAKE / ATTACK rates
    CAPACITY  CONTROL at 50 / 100 USDT (diagnostic only; never part of the 20 USDT competition)

The engine construction is V3.1's (V31Arena: Bybit fees and filters, the execution model, the frozen fee gate
and RiskManager, the 1m tape) with the V4 parts plugged in; nothing of V3.1 is modified.
"""
from __future__ import annotations

import dataclasses
import time
from collections import defaultdict, deque
from typing import Any, Mapping, Sequence

from app.backtest.brackets import BracketTable
from app.backtest.replay import ReplayEngine
from app.competition import metrics as mx
from app.competition import v31_arena as a31
from app.competition.arena import stream
from app.competition.v3_arena import day_ms
from app.competition.v4_config import SizingV4, V4Config, V4Identity, min_trades
from app.competition.v4_edge import EdgeModelV4
from app.competition.v31_edge import EdgeGate

# docs/V4_FREEZE.md, Amendment 1 (activity only, before any Jev decision existed): the edge gate refuses almost
# every setup of a family with no recent edge, so at 30 executed entries only 1 pair qualified for the Jev twins.
FIELD_MIN_ENTRIES = 10
SIZING_MODE = {"CONTROL": "CONTROL", "CAPACITY": "CONTROL", "JEV": "GATED", "TAKE": "GATED",
               "RANDOM": "GATED", "RAW": "RAW"}


def family_of(sid: str) -> str:
    from app.strategies.registry import load_v4
    return getattr(load_v4().get(sid), "family", "")


class V4Arena(a31.V31Arena):
    def __init__(self, settings: Any, cfg: V4Config, rules: dict[str, Any], evidence: Sequence[Mapping[str, Any]] = (),
                 brackets: BracketTable | None = None):
        from app.strategies.registry import load_v4
        self.settings = settings
        self.cfg = cfg  # type: ignore[assignment]
        self.rules = rules
        self.evidence = list(evidence)
        self.brackets = brackets or BracketTable.fallback()
        self.classes = load_v4()
        self._funding: dict[str, Any] = {}

    def engine(self, ident: V4Identity, gate: Any = None) -> tuple[ReplayEngine, EdgeGate | None, dict[str, list[str]]]:  # type: ignore[override]
        """THE construction of a V4 bot's engine (every role)."""
        cfg = self.cfg
        balance = float(ident.balance if ident.role == "CAPACITY" else cfg.starting_balance)
        raw = ident.role == "RAW"
        edge_gate = None if raw else EdgeGate(EdgeModelV4(self.evidence, cfg.edge))
        sizing = SizingV4(dataclasses.replace(cfg.risk, starting_balance=balance), mode=SIZING_MODE[ident.role])
        eng = ReplayEngine(a31.settings_v31(self.settings, cfg, balance), [ident.symbol], rules=self.rules, seed=cfg.seed,
                           funding=self.funding(ident.symbol), execution=cfg.execution, fees=cfg.fees,
                           brackets=self.brackets, fee_source="schedule", sizing=sizing,
                           leverage_policy=cfg.leverage_policy, max_risk_pct=cfg.risk.max_risk_pct, gate=gate,
                           cost_gate=edge_gate, shadow_cost_rejects=ident.role == "CONTROL")
        eng.portfolio.closed_trades = deque(eng.portfolio.closed_trades, maxlen=None)
        if eng.shadow is not None:
            eng.shadow.closed_trades = deque(eng.shadow.closed_trades, maxlen=None)
        probes = a31._Probes(eng)
        sizing.probes = probes
        if edge_gate is not None:
            edge_gate.probe = probes.legal
        if gate is not None and hasattr(gate, "probe"):
            gate.probe = probes.resize
        kinds: dict[str, list[str]] = defaultdict(list)
        if eng.shadow is not None:
            orig = eng.shadow.close_position

            def close_position(position_id: str, fraction: float, ref_price: float, kind: str, *a: Any, **k: Any):
                kinds[position_id].append(kind)
                return orig(position_id, fraction, ref_price, kind, *a, **k)
            eng.shadow.close_position = close_position  # type: ignore[method-assign]
        return eng, edge_gate, kinds

    def run(self, ident: V4Identity, gate: Any = None) -> dict[str, Any]:  # type: ignore[override]
        t0 = time.time()
        cls = self.bound(ident)
        eng, edge_gate, kinds = self.engine(ident, gate)
        since = self.cfg.observe_from if ident.role == "RAW" else self.cfg.trade_from
        res = eng.run(cls, stream(self.settings, ident.symbol, self.cfg.months), since_ms=day_ms(since),
                      leverage=self.cfg.leverage_ceiling, signal_tf=ident.timeframe, only_symbol=ident.symbol)
        m = mx.compute(ident.strategy_id, res.trades, res.fills, res.equity, res.starting_equity,
                       rejects=res.rejects, halted=res.halted, version=ident.key, leverage=res)
        m.configured_max_leverage = self.cfg.leverage_ceiling
        rec = a31.build_record(ident, cls, res, m, gate, edge_gate, kinds, self.cfg, since, time.time() - t0)  # type: ignore[arg-type]
        rec["family"] = getattr(cls, "family", "")
        if rec["role"] != "RAW":
            rec["activity"]["participation_min"] = min_trades(ident.timeframe, rec["window"]["days"])
        return rec


def select_field(controls: Sequence[dict[str, Any]], cfg: V4Config) -> dict[str, Any]:
    """Neutral and deterministic -- activity only, never PnL: for every family x timeframe slot, the
    `field_coins_per_slot` coins with the most EXECUTED entries among bots with at least FIELD_MIN_ENTRIES of
    them; ties alphabetical. These pairs get the Jev / always-take / random / capacity twins."""
    by: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for r in controls:
        idn = r["identity"]
        if idn["role"] == "CONTROL":
            by[(idn["strategy_id"], idn["timeframe"])].append((int(r["activity"]["entries"]), idn["coin"]))
    picked, thin = [], []
    for tf in cfg.timeframes:
        for sid in sorted({k[0] for k in by}):
            cands = sorted(by.get((sid, tf), []), key=lambda x: (-x[0], x[1]))
            ok = [(n, coin) for n, coin in cands if n >= FIELD_MIN_ENTRIES]
            for n, coin in ok[:cfg.field_coins_per_slot]:
                picked.append({"strategy_id": sid, "coin": coin, "timeframe": tf, "activity": n})
            if len(ok) < cfg.field_coins_per_slot:
                thin.append({"strategy_id": sid, "timeframe": tf, "eligible": len(ok),
                             "best": [{"coin": c, "entries": n} for n, c in cands[:3]]})
    return {"pairs": picked, "thin_slots": thin, "n": len(picked),
            "rule": (f"activity only: top {cfg.field_coins_per_slot} coins per family x timeframe by executed "
                     f"entries (>= {FIELD_MIN_ENTRIES}), ties alphabetical; never PnL")}
