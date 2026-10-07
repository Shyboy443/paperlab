"""The V8 SCALP worker (docs/V8_PROTOCOL.md): a separate process started at boot by app/live/v6_service.py when
V8_FORWARD_ENABLED=true. It runs the V8 scalpers on live Bybit data with simulated fills, exactly like the V6/V7
worker (which V7's freeze pins, so V8 has this adapted copy):

    verify     docs/V8_FREEZE.json must match the running V8 code and the shared V6 dependencies -- or no bot trades
    identity   the freeze fingerprint = the experiment (v8x-...); a compatible experiment is RESUMED
    warm up    5 days of 1m bars (plus short funding / positioning windows for the engine's funding charge)
    gates      live-actionability (stale data, a decision later than 55 s) and ELIMINATION, recorded and replayed;
               every CONTROL has a matched +JEV twin whose Jev V8 answer (SKIP / TAKE / ATTACK) must land by H + 55 s,
               and a +LADDER twin that takes the control's candidates with TP1/TP2/TP3 ladder exits (the control gate)
    eliminate  after 24 h live, a bot with < 5 closed trades in the last 24 h, or 25% below its start, stops opening
               trades; the elimination is an event with its instant, re-applied on every restart. A +JEV twin is
               never eliminated for inactivity (Jev may skip); it leaves with its CONTROL or at its own 25% loss

Paper only: no exchange client, key or order path (the only outbound call is Jev, to OpenRouter).
"""
from __future__ import annotations

import dataclasses
import logging
import os
import queue
import time
import uuid
from typing import Any

from app.live.v6_gates import ForwardGateV6, JevGateV6, latency_move_bps
from app.live.v6_worker import _candle, _live_marks, _prices, _public, load_recorded_v6

log = logging.getLogger("paperlab.live.v8worker")

STATUS_EVERY_S = 2.0
HEARTBEAT_EVERY_S = 10.0
PERSIST_BOTS_EVERY_S = 30.0
ELIMINATION_EVERY_S = 30.0
MINUTE = 60_000
DAY = 86_400_000


class ForwardGateV8(ForwardGateV6):
    """The V8 CONTROL gate: V6's recorded-replay / MISSED / NOT_RECORDED rules, a 55 s lateness limit for the 60 s
    scalp window, and ELIMINATED once the bot has left the field."""

    def __init__(self, *, eliminated_at: dict[str, int], late_after_ms: int, **k: Any):
        super().__init__(**k)
        self.eliminated_at = eliminated_at
        self.late_after_ms = int(late_after_ms)

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
        ok, why = self.board.verdict(self.bot["symbol"], t)
        return None if ok else f"DATA_STALE:{why}"


class JevGateV8(JevGateV6):
    """The +JEV twin of a V8 scalper: the same recorded-replay / MISSED / NOT_RECORDED rules and live-actionability
    checks as its CONTROL (55 s lateness, stale data, ELIMINATED), then Jev V8 inside the 60 s window: an answer after
    H + 55 s is SKIP, a failure is SKIP, ATTACK sizes to 2% only if the RiskManager accepts it."""

    def __init__(self, *, eliminated_at: dict[str, int], late_after_ms: int, window_ms: int, margin_ms: int, **k: Any):
        super().__init__(**k)
        self.eliminated_at = eliminated_at
        self.late_after_ms = int(late_after_ms)
        self.window_ms, self.margin_ms = int(window_ms), int(margin_ms)

    def _live_block(self, sig: Any, g: Any, t: int) -> str | None:
        if self._now() - t > self.late_after_ms:
            return "LATE_DECISION"
        ok, why = self.board.verdict(self.bot["symbol"], t)
        return None if ok else f"DATA_STALE:{why}"

    def __call__(self, sig: Any, g: Any) -> Any:
        import json

        from app.ai.jev.models import JevOutcome
        from app.ai.jev.v2 import _series, health_features_v2
        from app.ai.jev.v6 import decide_v6
        from app.ai.jev.v8 import PROMPT_VERSION_V8
        from app.competition.v6_config import health_state
        t = int(g["ts"]) + 1
        attack_only = bool((g.get("sizing") or {}).get("attack_only"))
        past = self._replay(sig, g, t)
        if past is not None:
            return past
        row = self._base(sig, g, t)
        at = self.eliminated_at.get(self.bot["key"])
        if at is not None and int(g["ts"]) >= int(at):
            row.update(final_action="SKIP", final_level="SKIP", reason="ELIMINATED", source="rule", decision_ms=self._now())
            return self._verdict(row, 0.0)
        if int(g["ts"]) < self.live_from_ms:            # observed, never recorded: follow the control
            act = "SKIP" if attack_only else "TAKE"
            row.update(final_action=act, final_level=act, reason="NOT_RECORDED", source="rederived", rederived=True)
            return self._verdict(row, 0.0 if attack_only else 1.0)
        block = self._live_block(sig, g, t)
        if block:
            row.update(final_action="SKIP", final_level="SKIP", reason=block, source="rule", decision_ms=self._now())
            return self._verdict(row, 0.0)
        deadline = t + self.window_ms - self.margin_ms
        state_h, _ = health_state(g["health"])
        eq = float(g["equity"])
        d = g["decision"]
        h = g["health"]
        health = health_features_v2(eq, float(h.get("peak") or eq), float(g["start_equity"]),
                                    list(g.get("trades") or []), int(g["ts"]))
        half, source = self.spread(self.bot["symbol"])
        position = {"proposed_risk_pct": round(d.risk_usd / eq, 5) if eq > 0 else None,
                    "proposed_leverage": d.leverage,
                    "available_margin_pct": round(g["available"] / eq, 4) if eq > 0 else None}
        execution = {"taker_fee": g["taker_fee"], "half_spread_bps": half if half is not None else g["half_spread_bps"],
                     "spread_source": source}
        snap = self.builder.build(t=t, bot=self.bot, sig=sig, series=_series(g, self.bot["symbol"], ["5m", "15m", "1h"]),
                                  execution=execution, sizing=g.get("sizing") or {}, health=health, position=position)
        t_candidate = self._now()
        mid_then = self.mid(self.bot["symbol"])
        if t_candidate > deadline - 1000:
            out = JevOutcome(False, error_code="LATE", error_message="no time left in the decision window")
            t_request = t_response = t_candidate
        else:
            t_request = self._now()
            try:
                out = self.decide_fn(dict(snap.state))
            except Exception as exc:                  # a failed call is SKIP, never control behaviour
                out = JevOutcome(False, error_code="CLIENT_ERROR", error_message=type(exc).__name__)
            t_response = self._now()
            if out.ok and t_response > deadline:
                out = JevOutcome(False, error_code="DEADLINE", error_message="answered after the decision window",
                                 latency_ms=out.latency_ms)
        mid_now = self.mid(self.bot["symbol"])
        dec = out.decision if out.ok else None
        pol = decide_v6(dec, state_h, attack_only, self.policy, out.error_code)
        level, mult, why = pol.level, pol.multiplier, (pol.extra or {}).get("downgrade")
        if mult < 0:                                  # ATTACK: 2% risk if the RiskManager accepts it
            mult = self._attack_mult(g)
            if self.probe is not None:
                ok, reason = self.probe(sig, mult)
                if not ok:
                    level, why = ("SKIP", f"ATTACK_NOT_LEGAL:{reason}") if attack_only else ("TAKE", f"ATTACK_NOT_LEGAL:{reason}")
                    mult = 0.0 if attack_only else 1.0
        probs = dict((dec.risk_probabilities or {}) if dec else {})
        row.update({"source": "api", "candidate_wall_ms": t_candidate, "request_start_ms": t_request,
                    "response_ms": t_response, "decision_ms": self._now(),
                    "latency_ms": (t_response - t_request) if out.ok else (out.latency_ms or (t_response - t_request)),
                    "mid_at_candidate": mid_then, "mid_at_response": mid_now,
                    "move_bps": latency_move_bps(sig.side, mid_then, mid_now), "model_requested": self.model,
                    "model_resolved": dec.model_resolved if dec else "", "prompt_version": PROMPT_VERSION_V8,
                    "policy_version": self.policy.version, "state_fingerprint": snap.fingerprint,
                    "state_json": json.dumps(dict(snap.state)), "p_support": dec.take_probability if dec else None,
                    "p_skip": probs.get("SKIP"), "p_take": probs.get("TAKE"), "p_attack": probs.get("ATTACK"),
                    "choice": dec.risk_state if dec else None,
                    "final_action": "SKIP" if level == "SKIP" else "TAKE", "final_level": level,
                    "reason": why or pol.reason, "error_code": out.error_code or None,
                    "timed_out": int(out.error_code in ("TIMEOUT", "DEADLINE", "LATE")),
                    "input_tokens": dec.input_tokens if dec else 0, "output_tokens": dec.output_tokens if dec else 0,
                    "cost_usd": dec.cost_usd if dec else 0.0, "notional": float(getattr(d, "notional", 0.0) or 0.0),
                    "health": state_h})
        return self._verdict(row, 0.0 if level == "SKIP" else float(mult), error=not out.ok)


def load_eliminated(storage: Any, experiment_id: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for e in storage.fwd6_events_all(experiment_id, ("eliminated",)):
        d = e["data"]
        if d.get("bot_key") and d["bot_key"] not in out:
            out[d["bot_key"]] = {"at": int(d.get("at") or e["ts"]), "reason": d.get("reason")}
    return out


def worker_main(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [v8] %(name)s: %(message)s")
    try:
        _run(cfg, out_q, stop_ev)
    except Exception as exc:
        log.exception("V8 scalp worker failed")
        out_q.put(("status", {"status": "ERROR", "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}))


def _run(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    from app.live.v8_engine import LevelMakerEngineV8
    from app.competition import v8_config as v8
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6, settings_v6
    from app.competition.v6_features import MarketContextV6, PositioningFeedV6
    from app.core.storage import Storage
    from app.core.types import MarketRules
    from app.live.bybit_market import BybitLiveMarket, LiveFundingV6
    from app.live.v6_gates import Coverage, DecisionBoard
    from app.live.v6_runner import V6Bot
    from app.strategies.v8.arena import load_v8_scalpers as load_v8
    from app.strategies.v8.ladder import ladder_class
    from app.ai.jev.client import JevClient
    from app.ai.jev.models import JevConfig
    from app.ai.jev.v8 import ClientDeciderV8, JevStateBuilderV8
    from app.competition.v31_arena import _Probes

    storage = Storage(cfg["db"])
    session_id = uuid.uuid4().hex[:12]
    boot_ms = int(time.time() * 1000)
    man = v8.load_freeze()
    diffs = v8.verify_freeze(man)
    if diffs:
        log.error("V8 is not frozen as it runs: %s", diffs[:5])
        out_q.put(("status", {"status": "FROZEN_MISMATCH", "session_id": session_id, "diffs": diffs[:20],
                              "detail": "the running V8 code / parameters / configuration differ from docs/V8_FREEZE.json: "
                                        "no bot trades until V8 is re-frozen (a new experiment)"}))
        stop_ev.wait()
        return
    jcfg = JevConfig.from_env()
    api_key = os.environ.get("OPENROUTER_API_KEY") or None
    jev_wanted = bool(cfg.get("jev", True))
    jev_state = ("READY" if (jev_wanted and jcfg.enabled and api_key) else
                 "DISABLED" if not jev_wanted or not jcfg.enabled else "NOT_CONFIGURED")
    experiment_id, identity = v8.experiment_identity(man, jcfg.model if jev_wanted else None)
    exp = storage.fwd6_experiment(experiment_id)
    coins = list(man["profile"]["coins"])
    traded = [f"{c}USDT" for c in coins]
    context = list(man["profile"]["context_symbols"])
    rules = {s: MarketRules(**r) for s, r in (man.get("rules") or {}).items()}
    intervals = {s: int(v) for s, v in (man.get("funding_interval_min") or {}).items()}
    hist = dict(v8.HISTORY_DAYS)
    now = int(time.time() * 1000)
    warmup_from = int(exp["warmup_from_ms"]) if exp else (now - v8.WARMUP_DAYS * DAY) // MINUTE * MINUTE
    t0 = int(exp["forward_start_ms"]) if exp else None
    specs = v8.field_plan(coins, jev=jev_wanted)
    config = {"program": "V8", "experiment_id": experiment_id, "identity": identity, "manifest": man.get("fingerprint"),
              "coins": coins, "breadth_set": context, "bots": [s.key for s in specs],
              "controls": sum(1 for x in specs if x.role == "CONTROL"), "jev_bots": sum(1 for x in specs if x.role == "JEV"),
              "jev": {"state": jev_state, "model": jcfg.model, "timeout_ms": v8.JEV_TIMEOUT_MS, "retries": v8.JEV_RETRIES},
              "warmup_from_ms": warmup_from, "resumed": exp is not None,
              "boot_ms": boot_ms, "venue": "BYBIT_LINEAR", "dry_run": True,
              "market_data": "Bybit linear public: WS kline.1 + tickers; REST klines and funding"}
    storage.fwd6_session_start(session_id, experiment_id, boot_ms, config)
    events: queue.Queue = queue.Queue()

    def emit(kind: str, data: dict[str, Any]) -> None:
        events.put(("event", kind, data))

    def store(kind: str, payload: dict[str, Any]) -> None:
        events.put(("store", kind, payload))

    def system(what: str, **data: Any) -> None:
        emit("system", {"event": what, "session_id": session_id, **data})

    system("SESSION_START", resumed=exp is not None, forward_start_ms=t0, warmup_from_ms=warmup_from)
    base = t0 if t0 is not None else now          # every session loads history from the same anchor
    wm = storage.fwd6_watermarks(warmup_from)
    feeds = {s: PositioningFeedV6(
        s, funding=storage.fwd6_positioning(s, "funding", base - hist["funding"] * DAY),
        oi=storage.fwd6_positioning(s, "oi", base - hist["positioning"] * DAY),
        premium=storage.fwd6_positioning(s, "premium", base - hist["positioning"] * DAY),
        ratio=storage.fwd6_positioning(s, "ratio", base - hist["positioning"] * DAY),
        caps=wm.get(s, {}), funding_interval_h=intervals.get(s, 480) / 60.0) for s in traded}
    market_ctx = MarketContextV6(context, caps=wm.get("*", {}))
    for s in context:
        market_ctx.close[s].extend(storage.fwd6_positioning(s, "ctx_close", base - hist["context"] * DAY))
        market_ctx.oi[s].extend(storage.fwd6_positioning(s, "ctx_oi", base - hist["context_oi"] * DAY))
        market_ctx.funding[s].extend(storage.fwd6_positioning(s, "ctx_funding", base - hist["context_oi"] * DAY))
    funding = LiveFundingV6(storage.fwd6_funding_charged(t0 if t0 is not None else now))
    for s in traded:
        funding.merge_settled(s, list(zip(feeds[s].funding.t, feeds[s].funding.v)))
    preload, quotes = {}, {}
    for s in traded:
        rows = storage.fwd6_bars(s, warmup_from)
        preload[s] = [_candle(r) for r in rows]
        quotes[s] = [(int(r["open_time"]) + MINUTE - 1, float(r["half_spread_bps"])) for r in rows
                     if r.get("half_spread_bps") is not None]
    market = BybitLiveMarket(traded, context, feeds, market_ctx, funding, store, warmup_from, intervals,
                             preload=preload, quotes=quotes, history_days=hist)
    queues = {spec.key: market.subscribe(spec.symbol) for spec in specs}
    market.start()
    bots: list[Any] = []
    state = {"status": "WARMING_UP", "live_from_ms": None, "forward_start_ms": t0}
    batches: dict[str, list[dict[str, Any]]] = {"bar": [], "pos": [], "watermark": [], "funding_charged": []}
    eliminated = load_eliminated(storage, experiment_id)
    eliminated_at = {k: v["at"] for k, v in eliminated.items()}

    def status_row() -> dict[str, Any]:
        rows = []
        for b in bots:
            r = _live_marks(dict(b.status), market)
            e = eliminated.get(r.get("key"))
            r["program_status"] = "ELIMINATED" if e else "ACTIVE"
            if e:
                r["eliminated"] = e
            rows.append(r)
        live = sum(1 for r in rows if r.get("live"))
        st = state["status"]
        if bots and live == len(bots):
            st = "LIVE"
        if any(r.get("error") for r in rows):
            st = "DEGRADED" if live else "ERROR"
        if bots and st == "LIVE" and not market.stats["ws"]["connected"]:
            st = "DEGRADED"
        return {"status": st, "session_id": session_id, "experiment_id": experiment_id,
                "forward_start_ms": state["forward_start_ms"], "live_from_ms": state["live_from_ms"],
                "boot_ms": boot_ms, "bots": rows, "live_bots": live, "total_bots": len(bots) or len(specs),
                "market": market.health(), "prices": _prices(market, traded), "jev_state": jev_state,
                "config": config, "eliminated": len(eliminated)}

    def flush_store() -> None:
        try:
            if batches["bar"]:
                storage.fwd6_bars_save(batches["bar"])
            if batches["pos"]:
                storage.fwd6_positioning_save(batches["pos"])
            if batches["watermark"]:
                storage.fwd6_watermark_save(batches["watermark"])
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
        while len(batch) < 2000:
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
        """Take bots that stopped trading (or lost 25%) out of the field; recorded, so a restart re-applies it."""
        if t0 is None:
            return
        for b in bots:
            r = _live_marks(dict(b.status), market)
            key = r.get("key")
            if key in eliminated or not r.get("live"):
                continue
            why = v8.should_eliminate(r, now_ms - t0)
            if not why and r.get("role") == "JEV" and r.get("control_key") in eliminated:
                why = "CONTROL_ELIMINATED"
            if why:
                eliminated[key] = {"at": now_ms, "reason": why}
                eliminated_at[key] = now_ms
                emit("eliminated", {"bot_key": key, "role": r.get("role"), "symbol": r.get("symbol"), "at": now_ms,
                                    "reason": why, "trades": r.get("trades"), "trades_24h": r.get("trades_24h"),
                                    "equity": r.get("equity_live") if r.get("equity_live") is not None else r.get("equity")})

    import multiprocessing as mp
    parent = mp.parent_process()
    last_status = last_hb = last_persist = last_elim = 0.0
    try:
        while not stop_ev.is_set() and not market.ready():
            if parent is not None and not parent.is_alive():
                return
            drain(0.5)
            if time.time() - last_status >= STATUS_EVERY_S:
                out_q.put(("status", status_row()))
                last_status = time.time()
        if stop_ev.is_set():
            return
        live_from = int(time.time() * 1000) // MINUTE * MINUTE
        if t0 is None:
            storage.fwd6_experiment_create({"experiment_id": experiment_id, "created_ts": live_from,
                                            "forward_start_ms": live_from, "warmup_from_ms": warmup_from,
                                            "manifest_fingerprint": man.get("fingerprint"), "identity": identity,
                                            "config": {k: config[k] for k in ("coins", "breadth_set", "bots", "jev",
                                                                              "venue", "market_data")}})
            t0 = int((storage.fwd6_experiment(experiment_id) or {}).get("forward_start_ms") or live_from)
            system("FORWARD_START", forward_start_ms=t0, warmup_s=round((live_from - boot_ms) / 1000.0, 1))
        else:
            system("RESUMED", forward_start_ms=t0, live_from_ms=live_from,
                   sessions=len(storage.fwd6_sessions(experiment_id)))
        state.update(forward_start_ms=t0, live_from_ms=live_from, status="STARTING_BOTS")
        recorded = load_recorded_v6(storage, experiment_id)
        coverage = Coverage(storage.fwd6_coverage(experiment_id))
        prev = {b["key"]: b for b in storage.fwd6_bots(experiment_id)}
        board = DecisionBoard(lambda sym, t: market.stream_ok(sym))
        client = JevClient(dataclasses.replace(jcfg, timeout_ms=v8.JEV_TIMEOUT_MS, max_retries=v8.JEV_RETRIES), api_key) \
            if jev_wanted else None
        decider = ClientDeciderV8(client) if client is not None else None
        classes = load_v8()
        settings = settings_v6()
        for spec in specs:
            base_cls = classes[spec.strategy_id]
            family_cls = ladder_class(base_cls) if spec.role == "LADDER" else base_cls     # +LADDER: same entries, TP ladder
            cls = family_cls.for_class(spec.horizon, feeds[spec.symbol], market_ctx)
            bot_info = {**spec.to_dict(), "thesis": base_cls.thesis, "fails_when": base_cls.fails_when,
                        "family": base_cls.family}
            kw = dict(experiment_id=experiment_id, session_id=session_id, bot=bot_info, emit=emit, board=board,
                      coverage=coverage, recorded=recorded.for_bot(spec.key).decisions, live_from_ms=live_from)
            if spec.role == "JEV":
                gate = JevGateV8(eliminated_at=eliminated_at, late_after_ms=v8.LATE_AFTER_MS,
                                 window_ms=v8.DECISION_WINDOW_MS, margin_ms=v8.JEV_DEADLINE_MARGIN_MS,
                                 decide=decider, model=jcfg.model, mid=market.mid, spread=market.spread_for_jev,
                                 builder=JevStateBuilderV8(), **kw)
            else:
                gate = ForwardGateV8(eliminated_at=eliminated_at, late_after_ms=v8.LATE_AFTER_MS, **kw)
            eng = LevelMakerEngineV8(settings, [spec.symbol], rules={spec.symbol: rules[spec.symbol]}, seed=7,
                                     funding=funding, execution=v8.EXECUTION_V8, fees=FEES_V6, fee_source="schedule",
                                     sizing=SizingV6(rules, jev=spec.role == "JEV"), leverage_policy="needed",
                                     max_risk_pct=AGGRESSIVE_V6.max_risk_pct, gate=gate, cost_gate=None,
                                     quotes=market.quote_half_spread, maker_tp=True)   # docs/V8_COST_STUDY.json
            if spec.role == "JEV":
                gate.probe = _Probes(eng).resize
            bots.append(V6Bot(spec=spec, cls=cls, engine=eng, bars=queues[spec.key], since_ms=t0,
                              live_from_ms=live_from, emit=emit, recorded=recorded.for_bot(spec.key),
                              prev_snapshot=prev.get(spec.key), family=base_cls.family))
        for b in bots:
            b.start()
        storage.fwd6_session_update(session_id, live_ts=live_from, heartbeat_ts=int(time.time() * 1000),
                                    status="LIVE")
        state["status"] = "WARMING_UP"
        live_marked = False
        while not stop_ev.is_set():
            if parent is not None and not parent.is_alive():
                log.warning("parent process is gone; V8 worker stopping")
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
