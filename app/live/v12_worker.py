"""The V12 BIZZY worker (docs/V12_PROTOCOL.md): a separate process started at boot by app/live/v6_service.py when
V12_FORWARD_ENABLED=true. Bizzy Bee (github.com/imikerussell/beebots) and her +JEV twin on live Bybit data with
simulated fills -- the V11 forward machinery, imported unchanged:

    verify     docs/V12_FREEZE.json must match the running V12 code (and the V11 files it imports) -- or nothing trades
    identity   the freeze fingerprint = the experiment (v12x-...); a compatible experiment is RESUMED
    tape       app/live/scan_market.py: stored history, REST catch-up, then live minute batches of BTC (the anchor,
               last), ETH, SOL, HYPE, so a restart re-derives the same decisions
    strategy   app/strategies/v12/bizzy.py: one long day breakout a day at full size (2x), stop = the day's open,
               exit 1 minute before the UTC close
    +JEV       Bizzy AI asks Jev V12 (app/ai/jev/v12.py) whether the breakout is real; CONTRADICT = WAIT
    gates      V11's per-coin rules: a decision later than 55 s, or stale data for that coin, is SKIP; decisions are
               recorded per (bot, coin, instant, side) and replayed on restart

Paper only: no exchange client, key or order path.
"""
from __future__ import annotations

import logging
import queue
import time
import uuid
from typing import Any

from app.live.v11_worker import (JevGateV11, ScanGateV11, live_marks, load_eliminated, load_recorded_v11, prices,
                                 recorded_for)
from app.live.v6_worker import _public

log = logging.getLogger("paperlab.live.v12worker")

STATUS_EVERY_S = 2.0
HEARTBEAT_EVERY_S = 10.0
PERSIST_BOTS_EVERY_S = 30.0
MINUTE = 60_000
DAY = 86_400_000


class JevGateV12(JevGateV11):
    """Bizzy AI: the V11 twin gate, asking Bizzy's breakout question (JEV_PROMPT_V12_BIZZY). CONTRADICT, a failure or
    an answer after H + 55 s is WAIT (SKIP); SUPPORT / STRONGLY_SUPPORT takes the breakout at full size. No ladder to
    stretch: Bizzy rides to the day close."""

    def _stretch(self, sig: Any, confidence: float | None) -> float:
        sig.meta["jev_confidence"] = confidence
        return 1.0

    def __call__(self, sig: Any, g: Any) -> Any:
        import json

        from app.ai.jev.models import JevOutcome
        from app.ai.jev.v12 import PROMPT_VERSION_V12
        from app.ai.jev.v2 import _series, health_features_v2
        from app.live.v6_gates import latency_move_bps
        t = int(g["ts"]) + 1
        past = self._replay(sig, g, t)
        if past is not None:
            return past
        row = self._base(sig, g, t)
        if int(g["ts"]) < self.live_from_ms:            # observed, never recorded: follow Bizzy
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
            except Exception as exc:                  # a failed call is WAIT, never Bizzy's own behaviour
                out = JevOutcome(False, error_code="CLIENT_ERROR", error_message=type(exc).__name__)
            t_response = self._now()
            if out.ok and t_response > deadline:
                out = JevOutcome(False, error_code="DEADLINE", error_message="answered after the decision window",
                                 latency_ms=out.latency_ms)
        dec = out.decision if out.ok else None
        probs = dict((dec.risk_probabilities or {}) if dec else {})
        conf = float(dec.take_probability) if dec else None
        if dec is None:
            level, reason = "SKIP", f"WAIT: JEV_ERROR {out.error_code or 'unknown'}"
        elif dec.risk_state == "SKIP":
            level, reason = "SKIP", f"WAIT: Jev not convinced ({conf:.0%})"
        else:
            level = "TAKE"
            self._stretch(sig, conf)
            reason = f"BREAKOUT: Jev {conf:.0%} sure, full size"
        row.update({"source": "api", "candidate_wall_ms": t_candidate, "request_start_ms": t_request,
                    "response_ms": t_response, "decision_ms": self._now(),
                    "latency_ms": (t_response - t_request) if out.ok else (out.latency_ms or (t_response - t_request)),
                    "mid_at_candidate": mid_then, "mid_at_response": self.mid(sig.symbol),
                    "move_bps": latency_move_bps(sig.side, mid_then, self.mid(sig.symbol)), "model_requested": self.model,
                    "model_resolved": dec.model_resolved if dec else "", "prompt_version": PROMPT_VERSION_V12,
                    "policy_version": "JEV_BREAKOUT_OR_WAIT_V12", "state_fingerprint": snap.fingerprint,
                    "state_json": json.dumps(dict(snap.state)), "p_support": conf,
                    "p_skip": probs.get("SKIP"), "p_take": probs.get("TAKE"), "p_attack": probs.get("ATTACK"),
                    "choice": dec.risk_state if dec else None, "final_action": level, "final_level": level,
                    "reason": reason, "error_code": out.error_code or None,
                    "timed_out": int(out.error_code in ("TIMEOUT", "DEADLINE", "LATE")),
                    "input_tokens": dec.input_tokens if dec else 0, "output_tokens": dec.output_tokens if dec else 0,
                    "cost_usd": dec.cost_usd if dec else 0.0, "notional": float(getattr(d, "notional", 0.0) or 0.0)})
        return self._verdict(row, 1.0 if level == "TAKE" else 0.0, error=not out.ok)


def worker_main(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [v12] %(name)s: %(message)s")
    try:
        _run(cfg, out_q, stop_ev)
    except Exception as exc:
        log.exception("V12 Bizzy worker failed")
        out_q.put(("status", {"status": "ERROR", "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}))


def _run(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    import dataclasses
    import os
    import threading

    from app.ai.jev.client import JevClient
    from app.ai.jev.models import JevConfig
    from app.ai.jev.v12 import ClientDeciderV12, JevStateBuilderV12
    from app.backtest import bybit_archive as bb
    from app.competition import v12_config as v12
    from app.competition.v6_config import FEES_V6
    from app.core.storage import Storage
    from app.core.types import MarketRules
    from app.live.bybit_market import LiveFundingV6
    from app.live.scan_market import ScanMarket
    from app.live.v6_gates import Coverage, DecisionBoard
    from app.live.v6_runner import V6Bot
    from app.strategies.v12.bizzy import ANCHOR, bizzy_engine, load_v12

    storage = Storage(cfg["db"])
    session_id = uuid.uuid4().hex[:12]
    boot_ms = int(time.time() * 1000)
    man = v12.load_freeze()
    diffs = v12.verify_freeze(man)
    if diffs:
        log.error("V12 is not frozen as it runs: %s", diffs[:5])
        out_q.put(("status", {"status": "FROZEN_MISMATCH", "session_id": session_id, "diffs": diffs[:20],
                              "detail": "the running V12 code / parameters / configuration differ from "
                                        "docs/V12_FREEZE.json: nothing trades until V12 is re-frozen (a new experiment)"}))
        stop_ev.wait()
        return
    jcfg = JevConfig.from_env()
    api_key = os.environ.get("OPENROUTER_API_KEY") or None
    jev_wanted = bool(cfg.get("jev", True))
    jev_state = ("READY" if (jev_wanted and jcfg.enabled and api_key) else
                 "DISABLED" if not jev_wanted or not jcfg.enabled else "NOT_CONFIGURED")
    experiment_id, identity = v12.experiment_identity(man, jcfg.model if jev_wanted else None)
    exp = storage.fwd6_experiment(experiment_id)
    universe = list(man["profile"]["universe"])
    rules = {s: MarketRules(**r) for s, r in (man.get("rules") or {}).items()}
    intervals = {s: int(v) for s, v in (man.get("funding_interval_min") or {}).items()}
    live_from = boot_ms // MINUTE * MINUTE + MINUTE
    warmup_from = int(exp["warmup_from_ms"]) if exp else (live_from - v12.WARMUP_DAYS * DAY) // DAY * DAY
    t0 = int(exp["forward_start_ms"]) if exp else live_from
    specs = v12.field_plan(jev=jev_wanted)
    config = {"program": "V12", "experiment_id": experiment_id, "identity": identity, "manifest": man.get("fingerprint"),
              "universe": universe, "coins": [s[:-4] for s in universe], "trade_coins": list(man["profile"]["trade_coins"]),
              "anchor": ANCHOR, "bots": [s.key for s in specs],
              "controls": sum(1 for s in specs if s.role == "CONTROL"), "jev_bots": sum(1 for s in specs if s.role == "JEV"),
              "jev": {"state": jev_state, "model": jcfg.model, "timeout_ms": v12.JEV_TIMEOUT_MS, "retries": v12.JEV_RETRIES},
              "warmup_from_ms": warmup_from, "resumed": exp is not None, "boot_ms": boot_ms,
              "venue": "BYBIT_LINEAR", "dry_run": True, "starting_balance": v12.STARTING_BALANCE,
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
    funding = LiveFundingV6(storage.fwd6_funding_charged(t0))
    for s in universe:
        try:
            funding.merge_settled(s, bb.funding(s, warmup_from, boot_ms))
        except Exception as exc:
            log.warning("funding history for %s unavailable: %s", s, str(exc)[:120])
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

    def status_row() -> dict[str, Any]:
        rows = []
        for b in bots:
            r = live_marks(dict(b.status), market)
            r["program_status"] = "ACTIVE"
            r["coins"] = len(man["profile"]["trade_coins"])
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
                "jev_state": jev_state, "config": config, "eliminated": 0,
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

    recorded = load_recorded_v11(storage, experiment_id)
    coverage = Coverage(storage.fwd6_coverage(experiment_id))
    prev = {b["key"]: b for b in storage.fwd6_bots(experiment_id)}
    board = DecisionBoard(lambda sym, t: market.stream_ok(sym))
    eliminated_at = {k: v["at"] for k, v in load_eliminated(storage, experiment_id).items()}
    classes = load_v12()
    settings = v12.settings_v12()
    client = JevClient(dataclasses.replace(jcfg, timeout_ms=v12.JEV_TIMEOUT_MS, max_retries=v12.JEV_RETRIES), api_key) \
        if jev_wanted else None
    decider = ClientDeciderV12(client) if client is not None else None
    Engine = bizzy_engine()
    for spec in specs:
        base_cls = classes[spec.strategy_id]
        cls = base_cls.for_universe(universe)
        if spec.role == "JEV":                                       # Jev reads the breakout coin's 5m / 15m / 1h
            cls = type(cls.__name__ + "Jev", (cls,), {"timeframes": tuple(dict.fromkeys((*cls.timeframes, "5m", "15m", "1h")))})
        bot_info = {**spec.to_dict(), "thesis": base_cls.thesis, "fails_when": base_cls.fails_when,
                    "family": base_cls.family}
        mine = recorded_for(recorded, spec.key)
        kw = dict(eliminated_at=eliminated_at, late_after_ms=v12.LATE_AFTER_MS, experiment_id=experiment_id,
                  session_id=session_id, bot=bot_info, emit=emit, board=board, coverage=coverage,
                  recorded=mine.decisions, live_from_ms=live_from)
        if spec.role == "JEV":
            gate = JevGateV12(decide=decider, model=jcfg.model, mid=market.mid,
                              spread=lambda s: ((round(market.live_half_spread_bps(s), 4), "observed")
                                                if market.live_half_spread_bps(s) is not None else (None, "modelled")),
                              builder=JevStateBuilderV12(), window_ms=v12.DECISION_WINDOW_MS,
                              margin_ms=v12.JEV_DEADLINE_MARGIN_MS, **kw)
        else:
            gate = ScanGateV11(**kw)
        eng = Engine(settings, universe, rules=rules, seed=7, funding=funding, execution=v12.EXECUTION_V12,
                     fees=FEES_V6, fee_source="schedule", sizing=None, leverage_policy="needed", max_risk_pct=None,
                     gate=gate, cost_gate=None, quotes=market.quote_half_spread)
        bots.append(V6Bot(spec=spec, cls=cls, engine=eng, bars=queues[spec.key], since_ms=t0, live_from_ms=live_from,
                          emit=emit, recorded=mine, prev_snapshot=prev.get(spec.key), family=base_cls.family,
                          leverage=v12.LEVERAGE_CEILING, multi=True))
    for b in bots:
        b.start()
    market.start()
    storage.fwd6_session_update(session_id, live_ts=live_from, heartbeat_ts=int(time.time() * 1000), status="LIVE")
    system("RESUMED" if exp is not None else "FORWARD_START", forward_start_ms=t0, live_from_ms=live_from,
           sessions=len(storage.fwd6_sessions(experiment_id)))

    import multiprocessing as mp
    parent = mp.parent_process()
    last_status = last_hb = last_persist = 0.0
    live_marked = False
    try:
        while not stop_ev.is_set():
            if parent is not None and not parent.is_alive():
                log.warning("parent process is gone; V12 worker stopping")
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
