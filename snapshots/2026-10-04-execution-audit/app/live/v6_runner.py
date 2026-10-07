"""One live V6 forward bot: the replay engine fed live Bybit bars in its own thread (docs/V6_PROTOCOL.md §7).

`ReplayEngine.run()` consumes an iterator of 1m bars; here that iterator blocks on the queue the live market fills,
so the engine code -- fills, fees, observed spread, funding, stops, targets, time stops, the RiskManager, the halts --
is the replay's own. Bars before the experiment's forward start T0 only warm the indicators (`since_ms` = T0: nothing
trades on history). A resumed session RE-DERIVES the book over [T0, this session's go-live) from the stored inputs,
with every gate replaying its recorded decision, then continues live: equity, open positions, stops, targets, peak,
drawdown, halts and funding carry over; a restart never closes a position.

After every bar the runner reads what the engine recorded (signals, fills, funding, closed trades, the shadow book of
skipped candidates, reject counters) and emits what changed. It never reaches into a decision.

`multi=True` (the V11 scanners) runs one book over many coins: the engine is not confined to one symbol, every event
names the coin it is about, and the continuity identities carry it ("<bot>@<symbol>") so two coins traded at the same
instant never collide. Single-coin bots (V6-V9) are unchanged: their identities stay "<bot>".
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from typing import Any, Callable, Iterator

from app.competition import metrics as mx
from app.competition.v6_config import AGGRESSIVE_V6, BotSpecV6, health_state
from app.live.continuity import Tracker, cand_ident, open_ident, trade_ident
from app.live.runner import with_sink

log = logging.getLogger("paperlab.live.v6")

EQUITY_KEEP = 1440
DAY = 86_400_000


class V6Bot:
    def __init__(self, *, spec: BotSpecV6, cls: Any, engine: Any, bars: queue.Queue, since_ms: int,
                 live_from_ms: int, emit: Callable[[str, dict[str, Any]], None], recorded: Any = None,
                 prev_snapshot: dict[str, Any] | None = None, clock: Callable[[], float] = time.time,
                 family: str = "", leverage: int = 20, multi: bool = False):
        self.spec = spec
        self.key = spec.key
        self.role = spec.role
        self.cls = cls
        self.eng = engine
        self.q = bars
        self.since_ms = int(since_ms)
        self.live_from_ms = int(live_from_ms)
        self.emit = emit
        self.clock = clock
        self.family = family
        self.leverage = int(leverage)                  # the engine's ceiling: 20x crypto perps, 4x US stocks (V9)
        self.multi = bool(multi)                       # one book over many coins (V11 scanners)
        self.track = Tracker(recorded, self.live_from_ms)
        self.prev = prev_snapshot or None
        self.continuity_check: dict[str, Any] | None = None
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
        # what each signal asked for (its stop and every take-profit), by signal id and by (symbol, side): a position that
        # opens AND closes inside one bar is gone before after_bar() runs, so its levels must come from the signal
        self._sig_levels: dict[Any, Any] = {}           # the Signal objects themselves: a gate may still change their TPs
        self._seen = {"fills": 0, "trades": 0, "shadow": 0, "gate": 0, "rows": 0}
        self._rejects: dict[str, int] = {}
        self._pos: dict[str, dict[str, Any]] = {}          # per open position: fees, slippage, funding, entry meta
        self._acc = {"gross": 0.0, "fees": 0.0, "slippage": 0.0, "funding_paid": 0.0, "funding_received": 0.0,
                     "net_closed": 0.0, "trades": 0, "wins": 0, "sum_r": 0.0, "win_net": 0.0, "loss_net": 0.0,
                     "candidates": 0, "min_notional_skips": 0}
        self._closed_ts: deque = deque(maxlen=2000)
        self._cand_ts: deque = deque(maxlen=5000)
        self.status: dict[str, Any] = self._status()
        engine.portfolio.closed_trades = deque(engine.portfolio.closed_trades, maxlen=None)
        if engine.gate is not None:
            inner = engine.gate

            def gate(sig: Any, ctx: Any) -> Any:
                live = int(ctx.get("ts") or 0) >= self.live_from_ms
                if live:
                    self.evaluating = True
                try:
                    return inner(sig, ctx)
                finally:
                    self.evaluating = False
            engine.gate = gate
            self._gate_obj = inner
        else:
            self._gate_obj = None

    # -- thread ---------------------------------------------------------------------------------------------
    def start(self) -> None:
        self.thread = threading.Thread(target=self._target, name=f"v6-{self.key}", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping = True
        self.q.put(None)

    def _target(self) -> None:
        try:
            self.eng.run(with_sink(self.cls, self._on_signals), self._bars(), since_ms=self.since_ms, leverage=self.leverage,
                         signal_tf=self.spec.timeframe, only_symbol=None if self.multi else self.spec.symbol)
        except Exception as exc:
            self.error = f"{exc.__class__.__name__}: {str(exc)[:200]}"
            log.exception("V6 bot %s died", self.key)
            self.emit("error", {"bot_key": self.key, "error": self.error})

    def _bars(self) -> Iterator[Any]:
        while True:
            self.after_bar()
            if self.stopping:
                return                       # never flatten on the way out: open positions carry to the next session
            bar = self.q.get()
            if bar is None:
                return
            self.last_bar_close = bar.close_time
            if bar.close_time >= self.live_from_ms:
                if self.live_bars == 0:
                    self.track.handoff()
                self.live_bars += 1
            else:
                self.warm_bars += 1
            yield bar

    def _out(self, kind: str, data: dict[str, Any], ident: tuple, ts: int | None, net: float | None = None) -> None:
        route = self.track.route(ident, ts, net)
        if route == "recorded":
            return
        self.emit(kind, {**data, "rederived": True} if route == "rederived" else data)

    def _on_signals(self, sigs: list[Any], candle: Any) -> None:
        for s in sigs:
            self._cands.append((s, candle))
            if getattr(s, "kind", "entry") == "entry" and getattr(s, "stop", None):
                if getattr(s, "id", None):
                    self._sig_levels[s.id] = s
                self._sig_levels[(s.symbol, s.side)] = s
        if len(self._sig_levels) > 4000:
            for k in list(self._sig_levels)[:2000]:
                self._sig_levels.pop(k, None)

    # -- after each bar ---------------------------------------------------------------------------------------------
    def _equity(self) -> float:
        return self.eng.portfolio.wallet_equity(self.spec.strategy_id, self.eng.ctx.prices)

    def _base(self, symbol: str | None = None) -> dict[str, Any]:
        sym = symbol if (self.multi and symbol) else self.spec.symbol
        coin = (sym[:-4] if sym.endswith("USDT") else sym) if (self.multi and symbol) else self.spec.coin
        return {"bot_key": self.key, "role": self.role, "pair_id": self.spec.pair_id, "symbol": sym,
                "coin": coin, "horizon": self.spec.horizon, "strategy_id": self.spec.strategy_id}

    def _ik(self, symbol: str) -> str:
        """The key continuity identities are filed under: the bot, plus the coin for a multi-coin book."""
        return f"{self.key}@{symbol}" if self.multi else self.key

    def after_bar(self) -> None:
        res = getattr(self.eng, "_res", None)
        if res is None:
            return
        sid = self.spec.strategy_id
        eq = self._equity()
        if self.last_bar_close >= self.since_ms:
            self.peak = max(self.peak or eq, eq)
            if self.peak > 0:
                self.max_dd = max(self.max_dd, 1.0 - eq / self.peak)
        rej = dict(res.rejects)
        new_rej = {k: v - self._rejects.get(k, 0) for k, v in rej.items() if v != self._rejects.get(k, 0)}
        self._rejects = rej
        gate_rows = list(getattr(self._gate_obj, "rows", []) or [])
        new_rows = gate_rows[self._seen["rows"]:]
        self._seen["rows"] = len(gate_rows)
        pending = {id(p[1]) for p in getattr(self.eng, "_pending", [])}
        for sig, candle in self._cands:
            if candle.close_time < self.since_ms:
                continue
            meta = getattr(sig, "meta", {}) or {}
            row = next((r for r in new_rows if r.get("signal_ts") == candle.close_time and r.get("side") == sig.side
                        and (not self.multi or r.get("symbol") == sig.symbol)), None)
            if id(sig) in pending:
                outcome = "ORDERED"
            elif row is not None and float(row.get("risk_multiplier") or 0.0) <= 0:
                outcome = "SKIPPED:" + str(row.get("reason") or row.get("final_level") or "gate")
            elif new_rej:
                outcome = "REJECTED:" + ",".join(sorted(new_rej))
            else:
                outcome = "NOT_TRADED"
            if any(k.startswith("min_notional") or k.startswith("below_min") for k in new_rej) and outcome.startswith("REJECTED"):
                self._acc["min_notional_skips"] += 1
            self._acc["candidates"] += 1
            self._cand_ts.append(candle.close_time + 1)
            tp = sig.take_profits[0].price if getattr(sig, "take_profits", None) else None
            self._out("candidate", {**self._base(sig.symbol), "side": sig.side, "signal_ts": candle.close_time,
                                    "decision_ms": candle.close_time + 1, "price": sig.entry_price, "stop": sig.stop,
                                    "target": tp, "stop_pct": meta.get("stop_pct"), "quality": meta.get("signal_quality"),
                                    "setup": meta.get("setup"), "regime": meta.get("regime"), "outcome": outcome,
                                    "tier": (row or {}).get("tier"), "gate": (row or {}).get("final_level"),
                                    "reason": (row or {}).get("reason")},
                      cand_ident(self._ik(sig.symbol), sig.side, candle.close_time), candle.close_time)
        self._cands = []
        for f in res.fills[self._seen["fills"]:]:
            p = self._pos.setdefault(f.position_id, {"fees": 0.0, "slip": 0.0, "paid": 0.0, "recv": 0.0, "meta": {}})
            if f.kind == "funding":
                if f.realized_pnl < 0:
                    p["paid"] -= f.realized_pnl
                    self._acc["funding_paid"] -= f.realized_pnl
                else:
                    p["recv"] += f.realized_pnl
                    self._acc["funding_received"] += f.realized_pnl
                continue
            p["fees"] += f.fee
            p["slip"] += mx.slippage_usdt(f)
            self._acc["fees"] += f.fee
            self._acc["slippage"] += mx.slippage_usdt(f)
            if f.kind == "entry" or f.is_open:
                m = dict(f.meta or {})
                p["meta"] = m
                pos = self.eng.portfolio.positions.get(f.position_id)
                # the stop and whole ladder the position OPENED with: from its signal (a TP hit, a stop move or even the
                # full exit inside the fill's own bar has already changed or removed the position)
                sig0 = self._sig_levels.get(getattr(pos, "signal_id", "") or "") or \
                    self._sig_levels.get((f.symbol, f.position_side))
                # read NOW, not when the signal was emitted: a gate (Jev) may have rewritten its take-profits since
                p["tps"] = ([tp.price for tp in (sig0.take_profits or [])] if sig0 is not None else []) or \
                    ([tp.price for tp in pos.take_profits] if pos is not None else [])
                p["stop0"] = sig0.stop if sig0 is not None and sig0.stop else getattr(pos, "stop", None)
                self._out("open", {**self._base(f.symbol), "side": f.position_side, "qty": f.qty, "price": f.price,
                                   "ref_price": f.ref_price, "fee": f.fee, "ts": f.ts, "leverage": f.leverage,
                                   "risk_pct": m.get("risk_pct"), "risk_usd": m.get("risk_usd"), "tier": m.get("tier"),
                                   "jev_level": m.get("jev_level"), "jev_multiplier": m.get("jev_multiplier"),
                                   "decision_id": m.get("decision_id"), "position_id": f.position_id,
                                   "latency_ms": m.get("latency_ms"), "spread_cost": m.get("spread_cost"),
                                   "execution_level": m.get("execution_level"),
                                   "stop": p["stop0"], "target": p["tps"][0] if p["tps"] else None, "tps": p["tps"]},
                          open_ident(self._ik(f.symbol), f.position_side, f.ts), f.ts)
            elif f.kind == "tp":
                # a PARTIAL take-profit (the last one closes the trade: "closed" reports it). Display / notification
                # only: TP1 / TP2 hit, what it booked, what is left and where the stop now sits.
                pos = self.eng.portfolio.positions.get(f.position_id)
                if pos is not None and pos.qty > 0:
                    tps = p.get("tps") or []
                    self._out("tp", {**self._base(f.symbol), "side": f.position_side, "position_id": f.position_id,
                                     "entry_ts": pos.entry_ts, "entry_price": pos.entry_price, "price": f.price,
                                     "qty": f.qty, "fee": f.fee, "pnl": f.realized_pnl, "ts": f.ts,
                                     "tp_index": (len(tps) - len(pos.take_profits)) if tps else None, "tps": tps,
                                     "remaining_qty": pos.qty, "stop": pos.stop},
                              ("tp", self._ik(f.symbol), f.position_side, pos.entry_ts, f.ts), f.ts)
        self._seen["fills"] = len(res.fills)
        mine = [t for t in self.eng.portfolio.closed_trades if t.strategy_id == sid]
        for t in mine[self._seen["trades"]:]:
            row = self._trade(t, counterfactual=False, decision_id=res.entry_links.get(t.position_id))
            if row["exit_ts"] >= self.since_ms:
                self._acc["gross"] += row["gross"]
                self._acc["net_closed"] += row["net"]
                self._acc["trades"] += 1
                self._acc["wins"] += int(row["net"] > 0)
                self._acc["sum_r"] += row["r_net"] or 0.0
                self._acc["win_net" if row["net"] > 0 else "loss_net"] += row["net"]
                self._closed_ts.append(row["exit_ts"])
            self._out("closed", row, trade_ident(self._ik(t.symbol), t.side, t.entry_ts, False), t.exit_ts, row["net"])
        self._seen["trades"] = len(mine)
        shadow = list(self.eng.shadow.closed_trades) if self.eng.shadow is not None else []
        for t in shadow[self._seen["shadow"]:]:
            self._out("closed", self._trade(t, counterfactual=True, decision_id=res.shadow_links.get(t.position_id)),
                      trade_ident(self._ik(t.symbol), t.side, t.entry_ts, True), t.exit_ts, t.net)
        self._seen["shadow"] = len(shadow)
        if len(res.equity) > 2 * EQUITY_KEEP:
            del res.equity[:-EQUITY_KEEP]
        self.status = self._status(eq)
        if (self.prev and self.continuity_check is None and self.last_bar_close
                and self.last_bar_close >= int(self.prev.get("last_bar_ts") or 0) > 0):
            self.continuity_check = self._check_continuity()

    def _trade(self, t: Any, counterfactual: bool, decision_id: str | None) -> dict[str, Any]:
        p = self._pos.get(t.position_id, {}) if not counterfactual else {}
        m = p.get("meta") or {}
        slip = p.get("slip", 0.0)
        paid, recv = p.get("paid", 0.0), p.get("recv", 0.0)
        funding = recv - paid
        net = t.net + funding
        risk = abs(t.net / t.r_multiple) if abs(t.r_multiple) > 1e-9 and abs(t.net) > 1e-12 else float(m.get("risk_usd") or 0.0)
        extra = {"multi_symbol": True} if self.multi else {}
        return {**self._base(t.symbol), **extra, "side": t.side, "position_id": t.position_id, "entry_ts": t.entry_ts,
                "exit_ts": t.exit_ts, "hold_s": t.hold_s, "qty": t.qty, "entry_price": t.entry_price,
                "exit_price": t.exit_price, "gross": t.pnl + slip, "fees": t.fees, "slippage": slip,
                "funding": funding, "funding_paid": paid, "funding_received": recv, "net": net,
                "r": t.r_multiple, "r_net": (net / risk) if risk else None, "exit_kind": t.exit_kind,
                "risk_pct": m.get("risk_pct"), "risk_usd": m.get("risk_usd") or (risk or None), "tier": m.get("tier"),
                "jev_level": m.get("jev_level"), "jev_multiplier": m.get("jev_multiplier"),
                "counterfactual": counterfactual, "decision_id": decision_id}

    # -- status -------------------------------------------------------------------------------------------------
    def risk_state(self) -> str:
        sid = self.spec.strategy_id
        try:
            if sid in self.eng.risk.state.halted_strategies or getattr(getattr(self.eng, "_meta", None), "halted", False):
                return "FLOOR_HALT"
            state, _ = health_state(self.eng._health(sid), AGGRESSIVE_V6)
            return state
        except Exception:
            return "UNKNOWN"

    def _status(self, eq: float | None = None) -> dict[str, Any]:
        spec = self.spec
        start = float(getattr(getattr(self.eng, "settings", None), "strategy_starting_balance", 20.0) or 20.0)
        eq = start if eq is None else eq
        positions = []
        try:
            for p in self.eng.portfolio.positions_of(spec.strategy_id):
                price = self.eng.ctx.prices.get(p.symbol) or p.entry_price
                acc = self._pos.get(p.id, {})
                m = acc.get("meta") or {}
                positions.append({"symbol": p.symbol, "side": p.side, "qty": p.qty, "entry": p.entry_price, "mark": price,
                                  "upnl": p.upnl(price), "stop": p.stop,
                                  "target": p.take_profits[0].price if p.take_profits else None,
                                  "entry_ts": p.entry_ts, "time_stop_ts": p.max_hold_deadline,
                                  "leverage": p.leverage, "risk_usd": p.initial_risk_usd, "risk_pct": m.get("risk_pct"),
                                  "tier": m.get("tier"), "jev_level": m.get("jev_level"),
                                  "funding_paid": acc.get("paid", 0.0), "funding_received": acc.get("recv", 0.0),
                                  "trail": bool(p.trail), "position_id": p.id,
                                  "tps": acc.get("tps") or [tp.price for tp in p.take_profits]})
        except Exception:
            pass
        a = self._acc
        now_ms = int(self.clock() * 1000)
        n = a["trades"]
        live = self.last_bar_close >= self.live_from_ms
        return {"key": self.key, "role": self.role, "pair_id": spec.pair_id, "control_key": spec.control_key,
                "strategy_id": spec.strategy_id, "family": self.family, "coin": spec.coin, "symbol": spec.symbol,
                "horizon": spec.horizon, "timeframe": spec.timeframe, "live": live, "warm_bars": self.warm_bars,
                "live_bars": self.live_bars, "last_bar_ts": self.last_bar_close, "equity": eq, "start_equity": start,
                "net": eq - start, "return_pct": (eq / start - 1.0) if start else None, "peak": self.peak or start,
                "max_dd": self.max_dd, "dd_now": (1.0 - eq / self.peak) if self.peak else 0.0,
                "trades": n, "wins": a["wins"], "win_rate": (a["wins"] / n) if n else None,
                "expectancy_r": (a["sum_r"] / n) if n else None,
                "profit_factor": (a["win_net"] / -a["loss_net"]) if a["loss_net"] < 0 else (None if not a["win_net"] else 999.0),
                "gross": a["gross"], "fees": a["fees"], "slippage": a["slippage"], "funding_paid": a["funding_paid"],
                "funding_received": a["funding_received"], "funding_net": a["funding_received"] - a["funding_paid"],
                "net_closed": a["net_closed"], "trades_24h": sum(1 for x in self._closed_ts if x >= now_ms - DAY),
                "candidates": a["candidates"], "candidates_24h": sum(1 for x in self._cand_ts if x >= now_ms - DAY),
                "min_notional_skips": a["min_notional_skips"], "open_positions": positions,
                "risk_state": self.risk_state() if live else "WARMING_UP", "evaluating": self.evaluating,
                "rejects": dict(self._rejects), "error": self.error,
                "continuity": {"forward_from_ms": self.since_ms, "live_from_ms": self.live_from_ms, **self.track.stats,
                               "check": self.continuity_check}}

    def _check_continuity(self) -> dict[str, Any]:
        """The re-derived book at the previous session's last snapshot vs that snapshot."""
        prev, now = self.prev or {}, self.status
        pp = [(p.get("side"), round(float(p.get("entry") or 0), 8), round(float(p.get("stop") or 0), 8))
              for p in prev.get("open_positions") or []]
        np_ = [(p.get("side"), round(float(p.get("entry") or 0), 8), round(float(p.get("stop") or 0), 8))
               for p in now.get("open_positions") or []]
        eq_diff = abs(float(now.get("equity") or 0) - float(prev.get("equity") or 0))
        return {"at_ts": self.last_bar_close, "snapshot_ts": prev.get("last_bar_ts"),
                "equity_prev": prev.get("equity"), "equity_rederived": now.get("equity"), "equity_diff": eq_diff,
                "positions_prev": len(pp), "positions_rederived": len(np_), "positions_match": pp == np_,
                "trades_prev": prev.get("trades"), "trades_rederived": now.get("trades"),
                "ok": pp == np_ and eq_diff < 0.02 and prev.get("trades") == now.get("trades")}
