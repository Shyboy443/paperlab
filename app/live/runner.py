"""One live shadow bot: the arena's engine, fed live bars in its own thread.

`ReplayEngine.run()` consumes an iterator of 1m bars. Here that iterator blocks on a queue the live
market fills, so the engine code is literally the replay's: same fills, fees, stops, cost gate,
ATTACK policy and RiskManager. Warmup bars (REST history) are replayed first with `since_ms` set to
the go-live instant, so indicators are primed and nothing trades on history.

After every bar the runner diffs the engine's own records (fills, closed trades, shadow trades,
cost-gate rejections, gate verdicts, reject counters) and emits what changed. It never reaches into
the engine's decisions; it only reads what the engine already wrote down.

Two instants (app/live/continuity.py): `since_ms` is the FORWARD EXPERIMENT's start T0, from which the
book trades; `live_from_ms` is this session's go-live. A resumed session re-derives the book over
[T0, go-live) from the closed 1m tape; what earlier sessions already recorded is checked, not
recorded twice. Stopping never invents an exit: an open position is carried to the next session.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Iterator

from app.competition.bots import AttackPolicy, attack_state
from app.live.continuity import Tracker, cand_ident, open_ident, trade_ident

log = logging.getLogger("paperlab.live.runner")

EQUITY_KEEP = 1440          # engine equity samples kept in memory (1 day of 1m bars)


def with_sink(cls: Any, sink: Callable[[list[Any], Any], None]) -> Any:
    """The strategy class, unchanged, except that every entry signal it emits is also reported."""

    class Live(cls):          # type: ignore[misc,valid-type]
        def on_candle(self, c, ctx):          # noqa: ANN001
            sigs = super().on_candle(c, ctx)
            entries = [s for s in (sigs or []) if getattr(s, "kind", "entry") != "exit"]
            if entries:
                sink(entries, c)
            return sigs

    Live.__name__ = cls.__name__
    Live.__qualname__ = cls.__qualname__
    return Live


class ShadowBot:
    def __init__(self, *, key: str, role: str, pair_id: str | None, spec: Any, cls: Any, engine: Any,
                 bars: queue.Queue, since_ms: int, emit: Callable[[str, dict[str, Any]], None],
                 profile: Any = None, clock: Callable[[], float] = time.time,
                 live_from_ms: int | None = None, recorded: Any = None):
        self.key = key
        self.role = role
        self.pair_id = pair_id
        self.spec = spec
        self.cls = cls
        self.eng = engine
        self.q = bars
        self.since_ms = int(since_ms)                  # the experiment's forward start: trading counts
        self.live_from_ms = int(since_ms if live_from_ms is None else live_from_ms)   # this session
        self.track = Tracker(recorded, self.live_from_ms)
        self.emit = emit
        self.profile = profile
        self.clock = clock
        self.thread: threading.Thread | None = None
        self.error: str | None = None
        self.evaluating = False
        self.stopping = False
        self.warm_bars = 0
        self.live_bars = 0
        self.last_bar_close = 0
        self.peak = 0.0
        self.max_dd = 0.0
        self._cands: list[tuple[Any, Any]] = []
        self._seen = {"fills": 0, "trades": 0, "shadow": 0, "cost": 0, "gate": 0}
        self._rejects: dict[str, int] = {}
        self._pending_ids: set[int] = set()
        self.status: dict[str, Any] = self._status()
        if engine.gate is not None:                  # mark "evaluating" while Jev is being asked
            inner = engine.gate

            def gate(sig: Any, ctx: Any) -> Any:
                if int(ctx.get("ts") or 0) >= self.live_from_ms:      # re-derivation asks no one
                    self.evaluating = True
                    self.emit("evaluating", {"bot_key": self.key, "pair_id": self.pair_id,
                                             "symbol": spec.symbol, "timeframe": spec.timeframe,
                                             "side": sig.side})
                try:
                    return inner(sig, ctx)
                finally:
                    self.evaluating = False
            engine.gate = gate

    # -- thread ---------------------------------------------------------------------------------
    def start(self) -> None:
        self.thread = threading.Thread(target=self._target, name=f"shadow-{self.key}", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping = True
        self.q.put(None)

    def _target(self) -> None:
        try:
            self.eng.run(with_sink(self.cls, self._on_signals), self._bars(), since_ms=self.since_ms,
                         leverage=self.spec.max_leverage, signal_tf=self.spec.timeframe,
                         only_symbol=self.spec.symbol)
        except Exception as exc:
            self.error = f"{exc.__class__.__name__}: {str(exc)[:200]}"
            log.exception("shadow bot %s died", self.key)
            self.emit("error", {"bot_key": self.key, "error": self.error})
        finally:
            try:
                self.after_bar(final=True)
            except Exception:
                log.exception("final accounting failed for %s", self.key)

    def _bars(self) -> Iterator[Any]:
        while True:
            self.after_bar()
            if self.stopping:                    # do not chew through a warmup backlog on the way out
                return
            bar = self.q.get()
            if bar is None:
                return
            self.last_bar_close = bar.close_time
            if bar.close_time >= self.live_from_ms:
                if self.live_bars == 0:
                    self.track.handoff()        # everything before go-live has been re-derived
                self.live_bars += 1
            else:
                self.warm_bars += 1
            yield bar

    def _out(self, kind: str, data: dict[str, Any], ident: tuple, ts: int | None,
             net: float | None = None) -> None:
        route = self.track.route(ident, ts, net)
        if route == "recorded":                  # an earlier session of this experiment has it
            return
        self.emit(kind, {**data, "rederived": True} if route == "rederived" else data)

    # -- what the strategy said -----------------------------------------------------------------
    def _on_signals(self, sigs: list[Any], candle: Any) -> None:
        for s in sigs:
            self._cands.append((s, candle))

    # -- after each bar: read what the engine recorded --------------------------------------------
    def _equity(self) -> float:
        return self.eng.portfolio.wallet_equity(self.spec.strategy_id, self.eng.ctx.prices)

    def after_bar(self, final: bool = False) -> None:
        res = getattr(self.eng, "_res", None)
        if res is None:
            return
        sid = self.spec.strategy_id
        eq = self._equity()
        if self.last_bar_close >= self.since_ms:
            self.peak = max(self.peak or eq, eq)
            if self.peak > 0:
                self.max_dd = max(self.max_dd, 1.0 - eq / self.peak)
        # candidates of this bar and what became of them
        rej = dict(res.rejects)
        new_rej = {k: v - self._rejects.get(k, 0) for k, v in rej.items() if v != self._rejects.get(k, 0)}
        self._rejects = rej
        cost_new = res.cost_gate_events[self._seen["cost"]:]
        self._seen["cost"] = len(res.cost_gate_events)
        gate_new = res.gate_events[self._seen["gate"]:]
        self._seen["gate"] = len(res.gate_events)
        pending = {id(p[1]) for p in getattr(self.eng, "_pending", [])}
        for sig, candle in self._cands:
            meta = getattr(sig, "meta", {}) or {}
            if id(sig) in pending:
                outcome = "ORDERED"
                self._pending_ids.add(id(sig))
            elif any(e.get("ts") == candle.close_time and e.get("side") == sig.side for e in cost_new):
                outcome = "COST_REJECTED"
            elif any(e.get("result") == "SKIPPED" for e in gate_new):
                outcome = "JEV_SKIP"
            elif any(str(e.get("result", "")).startswith("RISK_REJECTED") for e in gate_new):
                outcome = "JEV_RESIZE_REJECTED"
            elif new_rej:
                outcome = "REJECTED:" + ",".join(sorted(new_rej))
            else:
                outcome = "NOT_TRADED"
            self._out("candidate", {
                "bot_key": self.key, "role": self.role, "pair_id": self.pair_id, "symbol": sig.symbol,
                "timeframe": self.spec.timeframe, "strategy_id": sid, "side": sig.side,
                "signal_ts": candle.close_time, "price": sig.entry_price, "stop": sig.stop,
                "signal_quality": meta.get("signal_quality"), "edge_to_cost": meta.get("edge_to_cost"),
                "expected_move_pct": meta.get("expected_move_pct"), "outcome": outcome,
                "reason": (getattr(sig, "reason", "") or "")[:120]},
                cand_ident(self.key, sig.side, candle.close_time), candle.close_time)
        self._cands = []
        # fills: entries (OPEN) -- exits are reported as closed trades below
        for f in res.fills[self._seen["fills"]:]:
            if f.kind == "entry":
                m = f.meta or {}
                self._out("open", {"bot_key": self.key, "role": self.role, "pair_id": self.pair_id,
                                   "symbol": f.symbol, "timeframe": self.spec.timeframe,
                                   "side": f.position_side, "qty": f.qty, "price": f.price, "fee": f.fee,
                                   "ts": f.ts, "leverage": f.leverage, "attack_state": m.get("attack_state"),
                                   "risk_pct": m.get("risk_pct"), "decision_id": m.get("decision_id"),
                                   "position_id": f.position_id, "signal_quality": m.get("signal_quality"),
                                   "edge_to_cost": m.get("edge_to_cost"), "latency_ms": m.get("latency_ms")},
                          open_ident(self.key, f.position_side, f.ts), f.ts)
        self._seen["fills"] = len(res.fills)
        mine = [t for t in self.eng.portfolio.closed_trades if t.strategy_id == sid]
        for t in mine[self._seen["trades"]:]:
            if final and self.stopping and t.exit_kind == "time":
                continue                         # the stop's flatten is not an exit: carried over
            self._out("closed", self._trade(t, counterfactual=False, decision_id=res.entry_links.get(t.position_id)),
                      trade_ident(self.key, t.side, t.entry_ts, False), t.exit_ts, t.net)
        self._seen["trades"] = len(mine)
        shadow = list(self.eng.shadow.closed_trades) if self.eng.shadow is not None else []
        for t in shadow[self._seen["shadow"]:]:
            if final and self.stopping and t.exit_kind == "time":
                continue
            self._out("closed", self._trade(t, counterfactual=True, decision_id=res.shadow_links.get(t.position_id)),
                      trade_ident(self.key, t.side, t.entry_ts, True), t.exit_ts, t.net)
        self._seen["shadow"] = len(shadow)
        if len(res.equity) > 2 * EQUITY_KEEP:
            del res.equity[:-EQUITY_KEEP]
        self.status = self._status(eq, mine)

    def _trade(self, t: Any, counterfactual: bool, decision_id: str | None) -> dict[str, Any]:
        return {"bot_key": self.key, "role": self.role, "pair_id": self.pair_id, "symbol": t.symbol,
                "timeframe": self.spec.timeframe, "side": t.side, "position_id": t.position_id,
                "entry_ts": t.entry_ts, "exit_ts": t.exit_ts, "qty": t.qty, "entry_price": t.entry_price,
                "exit_price": t.exit_price, "gross": t.pnl, "fees": t.fees, "net": t.net,
                "r": t.r_multiple, "exit_kind": t.exit_kind, "counterfactual": counterfactual,
                "decision_id": decision_id}

    def _status(self, eq: float | None = None, trades: list[Any] | None = None) -> dict[str, Any]:
        spec = self.spec
        start = float(getattr(getattr(self.eng, "settings", None), "strategy_starting_balance", 20.0) or 20.0)
        eq = start if eq is None else eq
        trades = trades or []
        positions = []
        try:
            for p in self.eng.portfolio.positions_of(spec.strategy_id):
                price = self.eng.ctx.prices.get(p.symbol) or p.entry_price
                upnl = (price - p.entry_price) * p.qty * (1 if p.side == "long" else -1)
                positions.append({"side": p.side, "qty": p.qty, "entry": p.entry_price, "mark": price,
                                  "upnl": upnl, "stop": getattr(p, "stop", None), "entry_ts": p.entry_ts,
                                  "leverage": getattr(p, "leverage", None)})
        except Exception:
            pass
        rs = [t.r_multiple for t in trades]
        live = self.last_bar_close >= self.live_from_ms
        mode = "WARMING_UP" if not live else "NORMAL"
        if live and self.profile is not None:
            recent = rs[-self.profile.health_window:]
            exp = sum(recent) / len(recent) if recent else 0.0
            dd = (1.0 - eq / self.peak) if self.peak > 0 else 0.0
            mode = attack_state(exp, dd, self.profile, trades=len(recent), quality="ORDINARY")
            last_entry = next((f for f in reversed(getattr(self.eng, "_fills", []) or []) if f.kind == "entry"), None)
            if positions and last_entry is not None and (last_entry.meta or {}).get("attack_state") == "ATTACK":
                mode = "ATTACK"
        if self.error:
            mode = "ERROR"
        return {"key": self.key, "role": self.role, "pair_id": self.pair_id, "strategy_id": spec.strategy_id,
                "symbol": spec.symbol, "coin": spec.coin, "timeframe": spec.timeframe,
                "params_version": spec.params_version, "live": live,
                "warm_bars": self.warm_bars, "live_bars": self.live_bars, "last_bar_ts": self.last_bar_close,
                "equity": eq, "start_equity": start, "net": eq - start, "max_dd": self.max_dd,
                "trades": len(trades), "wins": sum(1 for r in rs if r > 0),
                "net_closed": sum(t.net for t in trades), "sum_r": sum(rs),
                "open_positions": positions, "mode": mode, "evaluating": self.evaluating,
                "rejects": dict(self._rejects), "error": self.error,
                "continuity": {"forward_from_ms": self.since_ms, "live_from_ms": self.live_from_ms,
                               **self.track.stats}}


def attack_profile(arena_cfg: Any) -> Any:
    """The risk profile an AttackPolicy of this arena config uses (for the displayed mode)."""
    return AttackPolicy(arena_cfg.risk).profile
