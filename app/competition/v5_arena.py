"""V5 HOURLY / DAILY arena runner (docs/V5_PROTOCOL.md).

    CONTROL   ungated: every legal setup the family finds, one position at a time, TAKE size (1%), baseline exit
              (stage 1) or the DEVELOPMENT-selected exit (stage 2). The CONTROLs ARE the raw-edge evidence -- there is
              no gate between the setup and the RiskManager.
    JEV       the same bot + Jev V5 (CONTRADICT / SUPPORT / STRONGLY SUPPORT -> SKIP / TAKE / ATTACK)
    TAKE      the same bot through the Jev slot with a constant TAKE (ALWAYS-TAKE)
    RANDOM    the same bot, an action drawn with the Jev bot's own SKIP / TAKE / ATTACK rates
    CAPACITY  CONTROL at 50 / 100 USDT (diagnostic only; never part of the 20 USDT competition)

Every bot runs on the Bybit-native 1m tape with the Bybit fee schedule, the execution model, Bybit instrument filters,
paper liquidation and the actual Bybit funding settlements (paid and received are recorded separately).
"""
from __future__ import annotations

import dataclasses
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.backtest import archive
from app.backtest.brackets import BracketTable
from app.backtest.bybit_archive import read_series
from app.backtest.funding import FundingSchedule
from app.backtest.replay import ReplayEngine
from app.competition import metrics as mx
from app.competition import v31_arena as a31
from app.competition.arena import _downsample, stream
from app.competition.v3_arena import day_ms, decision_rows, trade_rows
from app.competition.v5_config import SizingV5, V5Config, V5Identity
from app.competition.v5_features import PositioningFeed


class V5Arena:
    def __init__(self, settings: Any, cfg: V5Config, rules: dict[str, Any], data_dir: str | Path,
                 brackets: BracketTable | None = None):
        from app.strategies.registry import load_v5
        self.settings = settings
        self.cfg = cfg
        self.rules = rules
        self.data_dir = Path(data_dir)
        self.brackets = brackets or BracketTable.fallback()
        self.classes = load_v5()
        self._funding: dict[str, list[tuple[int, float]]] = {}
        self._feeds: dict[str, PositioningFeed] = {}

    def funding_rows(self, symbol: str) -> list[tuple[int, float]]:
        if symbol not in self._funding:
            self._funding[symbol] = archive.load_funding(self.settings, symbol, self.cfg.months)
        return self._funding[symbol]

    def feed(self, symbol: str) -> PositioningFeed:
        if symbol not in self._feeds:
            root = self.data_dir / "positioning"
            self._feeds[symbol] = PositioningFeed(funding=self.funding_rows(symbol), oi=read_series(root, symbol, "oi"),
                                                  premium=read_series(root, symbol, "premium"),
                                                  ratio=read_series(root, symbol, "ratio"))
        return self._feeds[symbol]

    def bound(self, ident: V5Identity) -> Any:
        return self.classes[ident.strategy_id].for_class(ident.horizon, self.feed(ident.symbol),
                                                         ident.time_stop_h or None, ident.target_r or None)

    def engine(self, ident: V5Identity, gate: Any = None) -> ReplayEngine:
        cfg = self.cfg
        balance = float(ident.balance if ident.role == "CAPACITY" else cfg.starting_balance)
        sizing = SizingV5(dataclasses.replace(cfg.risk, starting_balance=balance))
        eng = ReplayEngine(a31.settings_v31(self.settings, cfg, balance), [ident.symbol], rules=self.rules, seed=cfg.seed,  # type: ignore[arg-type]
                           funding=FundingSchedule({ident.symbol: self.funding_rows(ident.symbol)}),
                           execution=cfg.execution, fees=cfg.fees, brackets=self.brackets, fee_source="schedule",
                           sizing=sizing, leverage_policy=cfg.leverage_policy, max_risk_pct=cfg.risk.max_risk_pct,
                           gate=gate, cost_gate=None)
        eng.portfolio.closed_trades = deque(eng.portfolio.closed_trades, maxlen=None)
        probes = a31._Probes(eng)
        sizing.probes = probes
        if gate is not None and hasattr(gate, "probe"):
            gate.probe = probes.resize
        return eng

    def run(self, ident: V5Identity, gate: Any = None) -> dict[str, Any]:
        t0 = time.time()
        cls = self.bound(ident)
        eng = self.engine(ident, gate)
        res = eng.run(cls, stream(self.settings, ident.symbol, self.cfg.months), since_ms=day_ms(self.cfg.trade_from),
                      leverage=self.cfg.leverage_ceiling, signal_tf=cls.signal_tf, only_symbol=ident.symbol)
        m = mx.compute(ident.strategy_id, res.trades, res.fills, res.equity, res.starting_equity,
                       rejects=res.rejects, halted=res.halted, version=ident.key, leverage=res)
        m.configured_max_leverage = self.cfg.leverage_ceiling
        return build_record(ident, cls, res, m, gate, self.cfg, time.time() - t0)


def build_record(ident: V5Identity, cls: Any, res: Any, m: Any, gate: Any, cfg: V5Config, elapsed: float) -> dict[str, Any]:
    by_pos: dict[str, list[Any]] = defaultdict(list)
    for f in res.fills:
        if f.position_id:
            by_pos[f.position_id].append(f)
    journal = getattr(cls, "journal", {}) or {}
    rows = trade_rows(res.trades, res.fills)
    for r in rows:
        fs = by_pos.get(r["pid"], [])
        paid = -sum(f.realized_pnl for f in fs if f.kind == "funding" and f.realized_pnl < 0)
        recv = sum(f.realized_pnl for f in fs if f.kind == "funding" and f.realized_pnl > 0)
        entry = next((f for f in fs if f.kind == "entry" or f.is_open), None)
        sid = (getattr(entry, "signal_id", None) or "") if entry is not None else ""
        ctx = journal.get(sid, {})
        parts = sid.rsplit(":", 2)
        r.update({"symbol": ident.symbol, "signal_ts": int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else None,
                  "funding_paid": round(paid, 6), "funding_received": round(recv, 6),
                  "maker_fees": round(sum(f.fee for f in fs if (f.meta or {}).get("liquidity_role") == "MAKER"), 6),
                  "taker_fees": round(sum(f.fee for f in fs if f.kind != "funding" and (f.meta or {}).get("liquidity_role") != "MAKER"), 6),
                  "setup": ctx.get("setup"), "stop_pct": ctx.get("stop_pct"), "expected_move_pct": ctx.get("expected_move_pct"),
                  "expected_funding_pct": ctx.get("expected_funding_pct"), "positioning": ctx.get("positioning"),
                  "trend_c1": ctx.get("trend_c1"), "trend_c2": ctx.get("trend_c2"), "regime": ctx.get("regime"),
                  "vol_band": ctx.get("vol_band")})
    rej = dict(res.rejects)
    below_min = sum(v for k, v in rej.items() if k.startswith("below_min"))
    entries = sum(1 for f in res.fills if f.kind == "entry")
    days = float((day_ms(cfg.trade_to) + 86_400_000 - day_ms(cfg.trade_from)) / 86_400_000)
    fees_by = {"maker": 0.0, "taker": 0.0}
    for f in res.fills:
        if f.kind == "funding":
            continue
        fees_by["maker" if (f.meta or {}).get("liquidity_role") == "MAKER" else "taker"] += f.fee
    strat = cls()
    return {
        "identity": ident.to_dict(), "key": ident.key, "role": ident.role, "pair_id": ident.pair_id,
        "family": getattr(cls, "family", ""), "horizon": ident.horizon,
        "exit_plan": {"time_stop_h": cls.time_stop_h, "target_r": cls.target_r},
        "window": {"from": cfg.trade_from, "to": cfg.trade_to, "days": days},
        "balance": float(ident.balance if ident.role == "CAPACITY" else cfg.starting_balance),
        "params": {f.name: getattr(strat.params, f.name) for f in dataclasses.fields(strat.params)},
        "metrics": m.to_dict(),
        "activity": {"signals": res.signals, "entries": entries, "below_exchange_minimum": below_min,
                     "risk_rejected": rej, "halted": bool(res.halted), "halt_ts": res.halt_ts,
                     "gate_decisions": getattr(gate, "decisions", 0) if gate is not None else 0},
        "costs": {"maker_fees": round(fees_by["maker"], 6), "taker_fees": round(fees_by["taker"], 6),
                  "funding_paid": round(sum(r["funding_paid"] for r in rows), 6),
                  "funding_received": round(sum(r["funding_received"] for r in rows), 6)},
        "trades": rows,
        "decisions": decision_rows(gate, res) if gate is not None else [],
        "equity": _downsample(res.equity, 400),
        "elapsed_s": round(elapsed, 2),
    }
