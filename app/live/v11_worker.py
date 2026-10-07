"""The V11 SCAN worker (docs/V11_PROTOCOL.md): a separate process started at boot by app/live/v6_service.py when
V11_FORWARD_ENABLED=true. Four scanner bots, each ONE book over the 30-coin universe, on live Bybit data with simulated
fills -- the V6/V8 forward machinery, adapted to multi-coin books:

    verify     docs/V11_FREEZE.json must match the running V11 code and the shared V6 dependencies -- or no bot trades
    identity   the freeze fingerprint = the experiment (v11x-...); a compatible experiment is RESUMED
    tape       app/live/scan_market.py: stored history, REST catch-up, then live minute batches -- every coin of a
               minute in one fixed order, the anchor (BTCUSDT) last, so a restart re-derives the same decisions
    exits      the operator's take-profit ladder (app/strategies/v11/ladder.py): TP1 closes 25% and moves the stop to
               entry, TP2 closes 50% and moves it to TP1, TP3 closes the last 25%
    pre-trade  app/live/scan_engine.py: an order whose fill price has already run past TP1, or through the stop, is
               skipped instead of filled (the trade's premise is gone)
    +JEV       every scanner has a twin on the same candidates: Jev V11 (app/ai/jev/v11.py) may skip a trade, and its
               confidence stretches the twin's take-profit ladder (TP spacing x (0.5 + confidence), 0.75-1.5)
    gates      the V8 rules per CANDIDATE COIN: a decision later than 55 s, or stale data for that coin, is SKIP;
               decisions are recorded per (bot, coin, instant, side) and replayed on restart
    eliminate  after 24 h live, a bot with < 3 closed trades in the last 24 h, or 25% below its start, stops opening
               trades (recorded, re-applied on every restart)

Paper only: no exchange client, key or order path.
"""
from __future__ import annotations

import logging
import queue
import time
import uuid
from typing import Any

from app.live.v6_gates import ForwardGateV6, decision_id
from app.live.v6_worker import _public

log = logging.getLogger("paperlab.live.v11worker")

STATUS_EVERY_S = 2.0
HEARTBEAT_EVERY_S = 10.0
PERSIST_BOTS_EVERY_S = 30.0
ELIMINATION_EVERY_S = 30.0
MINUTE = 60_000
DAY = 86_400_000


class ScanGateV11(ForwardGateV6):
    """The V11 gate: the V6 CONTROL rules (recorded replay, MISSED downtime, NOT_RECORDED) keyed per candidate COIN,
    a 55 s lateness limit, stale data judged on the candidate's coin, and ELIMINATED once the bot has left the field."""

    def __init__(self, *, eliminated_at: dict[str, int], late_after_ms: int, **k: Any):
        super().__init__(**k)
        self.eliminated_at = eliminated_at
        self.late_after_ms = int(late_after_ms)

    def _ikey(self, sig: Any) -> str:
        return f"{self.bot['key']}@{sig.symbol}"

    def _base(self, sig: Any, g: Any, t: int) -> dict[str, Any]:
        row = super()._base(sig, g, t)
        row["id"] = decision_id(self.experiment_id, self._ikey(sig), int(g["ts"]), sig.side)
        row["symbol"] = sig.symbol
        return row

    def _replay(self, sig: Any, g: Any, t: int) -> Any:
        if int(g["ts"]) >= self.live_from_ms:
            return None
        rec = self.recorded.get((self._ikey(sig), int(g["ts"]), sig.side))
        row = self._base(sig, g, t)
        if rec is not None:
            row.update({k: rec.get(k) for k in ("final_action", "final_level", "reason", "error_code")},
                       source="recorded", rederived=True)
            return self._verdict(row, float(rec.get("risk_multiplier") or 0.0), error=bool(rec.get("error_code")),
                                 emit=False)
        if not self.coverage.observed(t):
            row.update(final_action="SKIP", final_level="SKIP", reason="MISSED_DOWNTIME", source="rederived",
                       rederived=True)
            return self._verdict(row, 0.0)
        return None

    def __call__(self, sig: Any, g: Any) -> Any:
        t = int(g["ts"]) + 1
        past = self._replay(sig, g, t)
        if past is not None:
            return past
        at = self.eliminated_at.get(self.bot["key"])
        if at is not None and int(g["ts"]) >= int(at):
            row = self._base(sig, g, t)
            row.update(final_action="SKIP", final_level="SKIP", reason="ELIMINATED", source="rule",
                       decision_ms=self._now())
            return self._verdict(row, 0.0)
        return super().__call__(sig, g)

    def _live_block(self, sig: Any, g: Any, t: int) -> str | None:
        if self._now() - t > self.late_after_ms:
            return "LATE_DECISION"
        ok, why = self.board.verdict(sig.symbol, t)
        return None if ok else f"DATA_STALE:{why}"


class JevGateV11(ScanGateV11):
    """The +JEV twin of a scanner: the scanner's per-coin rules (recorded replay, MISSED, NOT_RECORDED, 55 s lateness,
    stale data, ELIMINATED), then Jev V11 inside the 60 s window. CONTRADICT, a failure or an answer after H + 55 s is
    SKIP. Otherwise the twin TAKEs at the scanner's size, with the take-profit ladder stretched by Jev's confidence
    (tp_scale): the signal's take-profits are rewritten before the order is queued, so the position opens with them. A
    replayed decision re-applies the stretch from its recorded confidence, so a restart re-derives the same book."""
    role = "JEV"

    def __init__(self, *, decide: Any, model: str, mid: Any, spread: Any, builder: Any, window_ms: int, margin_ms: int,
                 **k: Any):
        super().__init__(**k)
        self.decide_fn, self.model, self.mid, self.spread, self.builder = decide, model, mid, spread, builder
        self.window_ms, self.margin_ms = int(window_ms), int(margin_ms)

    def _stretch(self, sig: Any, confidence: float | None) -> float:
        from app.ai.jev.v11 import tp_scale
        from app.strategies.v11.ladder import apply_ladder, rungs_for
        scale = tp_scale(confidence)
        fam = self.bot.get("strategy_id") or ""
        apply_ladder(sig, rungs_for(fam, scale), fam)
        sig.meta["tp_scale"] = scale
        return scale

    def _replay(self, sig: Any, g: Any, t: int) -> Any:
        past = super()._replay(sig, g, t)
        if past is not None and float(past.multiplier) > 0:
            rec = self.recorded.get((self._ikey(sig), int(g["ts"]), sig.side)) or {}
            self._stretch(sig, rec.get("p_support") if rec.get("p_support") is not None else 0.5)
        return past

    def __call__(self, sig: Any, g: Any) -> Any:
        import json

        from app.ai.jev.models import JevOutcome
        from app.ai.jev.v11 import PROMPT_VERSION_V11
        from app.ai.jev.v2 import _series, health_features_v2
        from app.live.v6_gates import latency_move_bps
        t = int(g["ts"]) + 1
        past = self._replay(sig, g, t)
        if past is not None:
            return past
        row = self._base(sig, g, t)
        at = self.eliminated_at.get(self.bot["key"])
        if at is not None and int(g["ts"]) >= int(at):
            row.update(final_action="SKIP", final_level="SKIP", reason="ELIMINATED", source="rule", decision_ms=self._now())
            return self._verdict(row, 0.0)
        if int(g["ts"]) < self.live_from_ms:            # observed, never recorded: follow the scanner, its own ladder
            row.update(final_action="TAKE", final_level="TAKE", reason="NOT_RECORDED", source="rederived", rederived=True)
            return self._verdict(row, 1.0)
        block = self._live_block(sig, g, t)
        if block:
            row.update(final_action="SKIP", final_level="SKIP", reason=block, source="rule", decision_ms=self._now())
            return self._verdict(row, 0.0)
        deadline = t + self.window_ms - self.margin_ms
        eq = float(g["equity"])
        d = g["decision"]
        h = g["health"]
        health = health_features_v2(eq, float(h.get("peak") or eq), float(g["start_equity"]), list(g.get("trades") or []),
                                    int(g["ts"]))
        half, source = self.spread(sig.symbol)
        position = {"proposed_risk_pct": round(d.risk_usd / eq, 5) if eq > 0 else None, "proposed_leverage": d.leverage}
        execution = {"taker_fee": g["taker_fee"], "half_spread_bps": half if half is not None else g["half_spread_bps"],
                     "spread_source": source}
        snap = self.builder.build(t=t, bot=self.bot, sig=sig, series=_series(g, sig.symbol, ["5m", "15m", "1h"]),
                                  execution=execution, sizing=g.get("sizing") or {}, health=health, position=position)
        t_candidate = self._now()
        mid_then = self.mid(sig.symbol)
        if t_candidate > deadline - 1000:
            out = JevOutcome(False, error_code="LATE", error_message="no time left in the decision window")
            t_request = t_response = t_candidate
        else:
            t_request = self._now()
            try:
                out = self.decide_fn(dict(snap.state))
            except Exception as exc:                  # a failed call is SKIP, never scanner behaviour
                out = JevOutcome(False, error_code="CLIENT_ERROR", error_message=type(exc).__name__)
            t_response = self._now()
            if out.ok and t_response > deadline:
                out = JevOutcome(False, error_code="DEADLINE", error_message="answered after the decision window",
                                 latency_ms=out.latency_ms)
        dec = out.decision if out.ok else None
        probs = dict((dec.risk_probabilities or {}) if dec else {})
        conf = float(dec.take_probability) if dec else None
        if dec is None:
            level, reason = "SKIP", f"JEV_ERROR {out.error_code or 'unknown'}"
        elif dec.risk_state == "SKIP":
            level, reason = "SKIP", "Jev: CONTRADICT"
        else:
            level = "TAKE"
            scale = self._stretch(sig, conf)
            reason = f"Jev {conf:.0%} sure: TPs x{scale:.2f} ({', '.join(f'{r:g}R' for r in sig.meta['ladder_r'])})"
        row.update({"source": "api", "candidate_wall_ms": t_candidate, "request_start_ms": t_request,
                    "response_ms": t_response, "decision_ms": self._now(),
                    "latency_ms": (t_response - t_request) if out.ok else (out.latency_ms or (t_response - t_request)),
                    "mid_at_candidate": mid_then, "mid_at_response": self.mid(sig.symbol),
                    "move_bps": latency_move_bps(sig.side, mid_then, self.mid(sig.symbol)), "model_requested": self.model,
                    "model_resolved": dec.model_resolved if dec else "", "prompt_version": PROMPT_VERSION_V11,
                    "policy_version": "JEV_TP_LADDER_V11", "state_fingerprint": snap.fingerprint,
                    "state_json": json.dumps(dict(snap.state)), "p_support": conf,
                    "p_skip": probs.get("SKIP"), "p_take": probs.get("TAKE"), "p_attack": probs.get("ATTACK"),
                    "choice": dec.risk_state if dec else None, "final_action": level, "final_level": level,
                    "reason": reason, "error_code": out.error_code or None,
                    "timed_out": int(out.error_code in ("TIMEOUT", "DEADLINE", "LATE")),
                    "input_tokens": dec.input_tokens if dec else 0, "output_tokens": dec.output_tokens if dec else 0,
                    "cost_usd": dec.cost_usd if dec else 0.0, "notional": float(getattr(d, "notional", 0.0) or 0.0)})
        return self._verdict(row, 1.0 if level == "TAKE" else 0.0, error=not out.ok)


def load_recorded_v11(storage: Any, experiment_id: str) -> Any:
    """What earlier sessions recorded, filed under '<bot>@<coin>' (a multi-coin book's continuity identities)."""
    from app.live.continuity import Recorded, cand_ident, open_ident, trade_ident
    rec = Recorded()
    for e in storage.fwd6_events_all(experiment_id, ("candidate", "open")):
        d = e["data"]
        key = e.get("bot_key") or d.get("bot_key")
        sym = d.get("symbol") or e.get("symbol")
        if not key or not sym or not d.get("side"):
            continue
        k = f"{key}@{sym}"
        if e["kind"] == "candidate" and d.get("signal_ts") is not None:
            rec.seen.add(cand_ident(k, d["side"], d["signal_ts"]))
        elif e["kind"] == "open" and d.get("ts") is not None:
            rec.seen.add(open_ident(k, d["side"], d["ts"]))
    for t in storage.fwd6_trades(experiment_id, counterfactual=None):
        i = trade_ident(f"{t['bot_key']}@{t['symbol']}", t["side"], t["entry_ts"], bool(t.get("counterfactual")))
        rec.seen.add(i)
        rec.nets[i] = t.get("net")
    for d in storage.fwd6_decisions(experiment_id):
        rec.decisions[(f"{d['bot_key']}@{d['symbol']}", int(d["signal_ts"]), d["side"])] = {
            k: d.get(k) for k in ("id", "final_action", "final_level", "risk_multiplier", "reason", "error_code",
                                  "p_support")}
    return rec


def recorded_for(rec: Any, key: str) -> Any:
    from app.live.continuity import Recorded
    mine = lambda k: k.split("@", 1)[0] == key  # noqa: E731
    return Recorded({i for i in rec.seen if mine(i[1])}, {i: v for i, v in rec.nets.items() if mine(i[1])},
                    {i: v for i, v in rec.decisions.items() if mine(i[0])})


def load_eliminated(storage: Any, experiment_id: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for e in storage.fwd6_events_all(experiment_id, ("eliminated",)):
        d = e["data"]
        if d.get("bot_key") and d["bot_key"] not in out:
            out[d["bot_key"]] = {"at": int(d.get("at") or e["ts"]), "reason": d.get("reason")}
    return out


def live_marks(row: dict[str, Any], market: Any) -> dict[str, Any]:
    """Display only: re-mark each open position at its own coin's live mid between 1m bars."""
    if not row.get("open_positions"):
        return row
    delta = 0.0
    out = []
    for p in row["open_positions"]:
        p = dict(p)
        mid = market.mid(p.get("symbol") or "")
        if mid:
            live = (mid - p["entry"]) * p["qty"] * (1 if p["side"] == "long" else -1)
            delta += live - (p.get("upnl") or 0.0)
            p["mark"], p["upnl"] = mid, live
        out.append(p)
    row["open_positions"] = out
    row["equity_live"] = (row.get("equity") or 0.0) + delta
    return row


def prices(market: Any, symbols: list[str]) -> dict[str, Any]:
    out = {}
    for sym in symbols:
        t = market.tickers.get(sym) or {}
        out[sym] = {"mid": market.mid(sym), "half_spread_bps": market.live_half_spread_bps(sym),
                    "mark": t.get("mark"), "index": t.get("index"), "funding_rate": t.get("funding_rate"),
                    "next_funding_ts": t.get("next_funding_ts"), "oi": t.get("oi"),
                    "last_bar_open": market.delivered.get(sym) or None}
    return out


def worker_main(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [v11] %(name)s: %(message)s")
    try:
        _run(cfg, out_q, stop_ev)
    except Exception as exc:
        log.exception("V11 scan worker failed")
        out_q.put(("status", {"status": "ERROR", "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}))


def _run(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    import threading

    from app.backtest import bybit_archive as bb
    from app.live.scan_engine import BE_COVER_BPS, ScanReplayEngine
    from app.competition import v11_config as v11
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6
    from app.core.storage import Storage
    from app.core.types import MarketRules
    from app.live.bybit_market import LiveFundingV6
    from app.live.scan_market import ScanMarket
    from app.live.v6_gates import Coverage, DecisionBoard
    from app.live.v6_runner import V6Bot
    from app.strategies.v11.ladder import ladder_class
    from app.strategies.v11.scan import ANCHOR, load_v11_scanners

    storage = Storage(cfg["db"])
    session_id = uuid.uuid4().hex[:12]
    boot_ms = int(time.time() * 1000)
    man = v11.load_freeze()
    diffs = v11.verify_freeze(man)
    if diffs:
        log.error("V11 is not frozen as it runs: %s", diffs[:5])
        out_q.put(("status", {"status": "FROZEN_MISMATCH", "session_id": session_id, "diffs": diffs[:20],
                              "detail": "the running V11 code / parameters / configuration differ from "
                                        "docs/V11_FREEZE.json: no bot trades until V11 is re-frozen (a new experiment)"}))
        stop_ev.wait()
        return
    import dataclasses
    import os

    from app.ai.jev.client import JevClient
    from app.ai.jev.models import JevConfig
    from app.ai.jev.v11 import ClientDeciderV11, JevStateBuilderV11
    jcfg = JevConfig.from_env()
    api_key = os.environ.get("OPENROUTER_API_KEY") or None
    jev_wanted = bool(cfg.get("jev", True))
    jev_state = ("READY" if (jev_wanted and jcfg.enabled and api_key) else
                 "DISABLED" if not jev_wanted or not jcfg.enabled else "NOT_CONFIGURED")
    experiment_id, identity = v11.experiment_identity(man, jcfg.model if jev_wanted else None)
    exp = storage.fwd6_experiment(experiment_id)
    universe = list(man["profile"]["universe"])
    rules = {s: MarketRules(**r) for s, r in (man.get("rules") or {}).items()}
    intervals = {s: int(v) for s, v in (man.get("funding_interval_min") or {}).items()}
    live_from = boot_ms // MINUTE * MINUTE + MINUTE           # every bar that closes after this minute is live
    warmup_from = int(exp["warmup_from_ms"]) if exp else (live_from - v11.WARMUP_DAYS * DAY) // MINUTE * MINUTE
    t0 = int(exp["forward_start_ms"]) if exp else live_from
    specs = v11.field_plan(jev=jev_wanted)
    config = {"program": "V11", "experiment_id": experiment_id, "identity": identity, "manifest": man.get("fingerprint"),
              "universe": universe, "coins": [s[:-4] for s in universe], "anchor": ANCHOR,
              "bots": [s.key for s in specs], "controls": sum(1 for s in specs if s.role == "CONTROL"),
              "jev_bots": sum(1 for s in specs if s.role == "JEV"),
              "jev": {"state": jev_state, "model": jcfg.model, "timeout_ms": v11.JEV_TIMEOUT_MS, "retries": v11.JEV_RETRIES},
              "warmup_from_ms": warmup_from, "resumed": exp is not None, "boot_ms": boot_ms,
              "venue": "BYBIT_LINEAR", "dry_run": True, "starting_balance": v11.STARTING_BALANCE,
              "market_data": "Bybit linear public REST: 1m klines + tickers every minute, one ordered tape"}
    if exp is None:
        storage.fwd6_experiment_create({"experiment_id": experiment_id, "created_ts": boot_ms, "forward_start_ms": t0,
                                        "warmup_from_ms": warmup_from, "manifest_fingerprint": man.get("fingerprint"),
                                        "identity": identity,
                                        "config": {k: config[k] for k in ("universe", "anchor", "bots", "venue",
                                                                          "market_data", "starting_balance")}})
    storage.fwd6_session_start(session_id, experiment_id, boot_ms, config)
    events: queue.Queue = queue.Queue()

    def emit(kind: str, data: dict[str, Any]) -> None:
        events.put(("event", kind, data))

    def store(kind: str, payload: dict[str, Any]) -> None:
        events.put(("store", kind, payload))

    def system(what: str, **data: Any) -> None:
        emit("system", {"event": what, "session_id": session_id, **data})

    system("SESSION_START", resumed=exp is not None, forward_start_ms=t0, warmup_from_ms=warmup_from)
    # funding: what earlier sessions charged, plus the settled history since the warm-up (REST, public)
    funding = LiveFundingV6(storage.fwd6_funding_charged(t0))
    for s in universe:
        try:
            funding.merge_settled(s, bb.funding(s, warmup_from, boot_ms))
        except Exception as exc:
            log.warning("funding history for %s unavailable: %s", s, str(exc)[:120])
    # the stored tape: replayed by the feed up to the last minute EVERY coin has, then REST catch-up from there
    lasts = [storage.fwd6_bar_last(s) for s in universe]
    until = min(lasts) if lasts and all(x is not None for x in lasts) else None
    local = threading.local()

    def history(a: int, b: int) -> dict[str, list[dict[str, Any]]]:
        st = getattr(local, "st", None)
        if st is None:
            st = local.st = Storage(cfg["db"])
        return {s: st.fwd6_bars(s, a, b) for s in universe}

    market = ScanMarket(universe, ANCHOR, store, funding, warmup_from, intervals, history=history,
                        history_until_ms=until, quotes_from_ms=t0)
    queues = {spec.key: market.subscribe() for spec in specs}
    bots: list[Any] = []
    state = {"status": "WARMING_UP"}
    batches: dict[str, list[dict[str, Any]]] = {"bar": [], "pos": [], "watermark": [], "funding_charged": []}
    eliminated = load_eliminated(storage, experiment_id)
    eliminated_at = {k: v["at"] for k, v in eliminated.items()}

    def status_row() -> dict[str, Any]:
        rows = []
        for b in bots:
            r = live_marks(dict(b.status), market)
            e = eliminated.get(r.get("key"))
            r["program_status"] = "ELIMINATED" if e else "ACTIVE"
            r["coins"] = len(universe)
            if e:
                r["eliminated"] = e
            rows.append(r)
        live = sum(1 for r in rows if r.get("live"))
        st = state["status"]
        if bots and live == len(bots):
            st = "LIVE"
        if any(r.get("error") for r in rows):
            st = "DEGRADED" if live else "ERROR"
        if bots and st == "LIVE" and not market.data_ok():
            st = "DEGRADED"
        if market.phase == "ERROR":
            st = "ERROR"
        return {"status": st, "session_id": session_id, "experiment_id": experiment_id, "forward_start_ms": t0,
                "live_from_ms": live_from, "boot_ms": boot_ms, "bots": rows, "live_bots": live,
                "total_bots": len(bots) or len(specs), "market": market.health(), "prices": prices(market, universe),
                "jev_state": jev_state, "config": config, "eliminated": len(eliminated),
                "error": market.stats.get("last_error") if market.phase == "ERROR" else None}

    def flush_store() -> None:
        try:
            if batches["bar"]:
                storage.fwd6_bars_save(batches["bar"])
            if batches["funding_charged"]:
                storage.fwd6_funding_charged_save(batches["funding_charged"])
        except Exception as exc:
            log.warning("could not store market inputs: %s", str(exc)[:160])
        for v in batches.values():
            v.clear()

    def drain(timeout: float) -> None:
        try:
            first = events.get(timeout=timeout) if timeout > 0 else events.get_nowait()
        except queue.Empty:
            return
        batch = [first]
        while len(batch) < 5000:
            try:
                batch.append(events.get_nowait())
            except queue.Empty:
                break
        for item in batch:
            if item[0] == "store":
                batches[item[1]].append(item[2])
                continue
            _, kind, data = item
            ts = int(time.time() * 1000)
            try:
                if kind == "decision":
                    storage.fwd6_decision_save(data)
                    storage.fwd6_event_add(experiment_id, session_id, ts, "decision", _public("decision", data))
                elif kind == "closed":
                    storage.fwd6_trade_save(experiment_id, session_id, data)
                    storage.fwd6_event_add(experiment_id, session_id, ts, kind, data)
                else:
                    storage.fwd6_event_add(experiment_id, session_id, ts, kind, data)
                out_q.put(("event", "v6", _public(kind, data), True))
            except Exception as exc:
                log.warning("could not record %s: %s", kind, str(exc)[:160])
        flush_store()

    def eliminate(now_ms: int) -> None:
        for b in bots:
            r = live_marks(dict(b.status), market)
            key = r.get("key")
            if key in eliminated or not r.get("live"):
                continue
            why = v11.should_eliminate(r, now_ms - t0)
            if why:
                eliminated[key] = {"at": now_ms, "reason": why}
                eliminated_at[key] = now_ms
                emit("eliminated", {"bot_key": key, "role": r.get("role"), "symbol": "", "at": now_ms, "reason": why,
                                    "trades": r.get("trades"), "trades_24h": r.get("trades_24h"),
                                    "equity": r.get("equity_live") if r.get("equity_live") is not None else r.get("equity")})

    recorded = load_recorded_v11(storage, experiment_id)
    coverage = Coverage(storage.fwd6_coverage(experiment_id))
    prev = {b["key"]: b for b in storage.fwd6_bots(experiment_id)}
    board = DecisionBoard(lambda sym, t: market.stream_ok(sym))
    classes = load_v11_scanners()
    settings = v11.settings_v11()
    client = JevClient(dataclasses.replace(jcfg, timeout_ms=v11.JEV_TIMEOUT_MS, max_retries=v11.JEV_RETRIES), api_key) \
        if jev_wanted else None
    decider = ClientDeciderV11(client) if client is not None else None
    for spec in specs:
        base_cls = classes[spec.strategy_id]
        cls = ladder_class(base_cls).for_universe(universe)          # the operator's 25 / 50 / 25 take-profit ladder
        if spec.role == "JEV":                                       # Jev reads the candidate coin's 5m / 15m / 1h
            cls = type(cls.__name__ + "Jev", (cls,), {"timeframes": tuple(dict.fromkeys((*cls.timeframes, "5m", "15m", "1h")))})
        bot_info = {**spec.to_dict(), "thesis": base_cls.thesis, "fails_when": base_cls.fails_when,
                    "family": base_cls.family}
        mine = recorded_for(recorded, spec.key)
        kw = dict(eliminated_at=eliminated_at, late_after_ms=v11.LATE_AFTER_MS, experiment_id=experiment_id,
                  session_id=session_id, bot=bot_info, emit=emit, board=board, coverage=coverage,
                  recorded=mine.decisions, live_from_ms=live_from)
        if spec.role == "JEV":
            gate = JevGateV11(decide=decider, model=jcfg.model, mid=market.mid,
                              spread=lambda s: ((round(market.live_half_spread_bps(s), 4), "observed")
                                                if market.live_half_spread_bps(s) is not None else (None, "modelled")),
                              builder=JevStateBuilderV11(), window_ms=v11.DECISION_WINDOW_MS,
                              margin_ms=v11.JEV_DEADLINE_MARGIN_MS, **kw)
        else:
            gate = ScanGateV11(**kw)
        eng = ScanReplayEngine(settings, universe, rules=rules, seed=7, funding=funding, execution=v11.EXECUTION_V11,
                           fees=FEES_V6, fee_source="schedule", sizing=SizingV6(rules, jev=False),
                           leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct, gate=gate,
                           cost_gate=None, quotes=market.quote_half_spread, be_cover_bps=BE_COVER_BPS,
                           maker_tp=True)              # take-profits rest on the book (docs/V11_ZAP_STUDY.json)
        bots.append(V6Bot(spec=spec, cls=cls, engine=eng, bars=queues[spec.key], since_ms=t0, live_from_ms=live_from,
                          emit=emit, recorded=mine, prev_snapshot=prev.get(spec.key), family=base_cls.family,
                          leverage=v11.LEVERAGE_CEILING, multi=True))
    for b in bots:
        b.start()
    market.start()
    storage.fwd6_session_update(session_id, live_ts=live_from, heartbeat_ts=int(time.time() * 1000), status="LIVE")
    system("RESUMED" if exp is not None else "FORWARD_START", forward_start_ms=t0, live_from_ms=live_from,
           sessions=len(storage.fwd6_sessions(experiment_id)))

    import multiprocessing as mp
    parent = mp.parent_process()
    last_status = last_hb = last_persist = last_elim = 0.0
    live_marked = False
    try:
        while not stop_ev.is_set():
            if parent is not None and not parent.is_alive():
                log.warning("parent process is gone; V11 worker stopping")
                break
            drain(0.5)
            now_s = time.time()
            if now_s - last_status >= STATUS_EVERY_S:
                st = status_row()
                out_q.put(("status", st))
                last_status = now_s
                if st["status"] == "LIVE" and not live_marked:
                    live_marked = True
                    checks = [b.continuity_check for b in bots if b.continuity_check is not None]
                    system("LIVE", bots=len(bots), continuity_checks=len(checks),
                           continuity_ok=sum(1 for c in checks if c.get("ok")))
            if live_marked and now_s - last_elim >= ELIMINATION_EVERY_S:
                eliminate(int(now_s * 1000))
                last_elim = now_s
            if now_s - last_hb >= HEARTBEAT_EVERY_S:
                storage.fwd6_session_update(session_id, heartbeat_ts=int(now_s * 1000))
                last_hb = now_s
            if now_s - last_persist >= PERSIST_BOTS_EVERY_S and bots:
                storage.fwd6_bots_save(experiment_id, session_id, [dict(b.status) for b in bots], int(now_s * 1000))
                last_persist = now_s
    finally:
        market.stop()
        for b in bots:
            b.stop()
        for b in bots:
            if b.thread is not None:
                b.thread.join(timeout=2.0)
        drain(0.0)
        if bots:
            storage.fwd6_bots_save(experiment_id, session_id, [dict(b.status) for b in bots], int(time.time() * 1000))
        storage.fwd6_session_update(session_id, status="ENDED", ended_ts=int(time.time() * 1000),
                                    heartbeat_ts=int(time.time() * 1000))
        storage.close()
