"""The V6 FORWARD worker (docs/V6_PROTOCOL.md §7-§8): a separate process, started automatically at every boot by
app/live/v6_service.py when V6_FORWARD_ENABLED=true. There is no manual step and no historical backtest in it.

    verify     the running V6 code, parameters and configuration must equal docs/V6_FREEZE.json -- otherwise the
               worker refuses to trade (FROZEN_MISMATCH): a changed V6 is a new, re-frozen experiment, never a
               silently mutated one
    identity   the frozen manifest + the Jev model = the FORWARD EXPERIMENT; an existing compatible experiment is
               RESUMED, otherwise a new one is CREATED
    warm up    only the recent history the features need (45 days of 1m bars, 95 days of funding, 35 days of open
               interest / basis / long-short ratio, 45 days of market-context 1h klines), from the experiment's own
               stored inputs first and Bybit REST for the rest. Warm-up never trades
    T0         a new experiment's V6_FORWARD_START is fixed once the market is healthy and warm, and never changes
    bots       every CONTROL and its +JEV twin on live Bybit data; a resumed experiment re-derives each book from T0
               on the stored inputs with every decision replayed, then continues live
    record     candidates, gate and Jev decisions, fills, closed trades, funding, bot snapshots, market inputs,
               session heartbeats -- in the shared SQLite file (fwd6_* tables)

Paper only: the engine books are simulated and this process holds no exchange client, key or order path.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import queue
import time
import uuid
from typing import Any

log = logging.getLogger("paperlab.live.v6worker")

STATUS_EVERY_S = 2.0
HEARTBEAT_EVERY_S = 10.0
PERSIST_BOTS_EVERY_S = 30.0
MINUTE = 60_000
DAY = 86_400_000


def load_recorded_v6(storage: Any, experiment_id: str) -> Any:
    """What earlier sessions of the experiment recorded: candidate / open / closed identities (checked, never
    re-recorded) and every gate or Jev decision (replayed on re-derivation)."""
    from app.live.continuity import Recorded, cand_ident, open_ident, trade_ident
    rec = Recorded()
    for e in storage.fwd6_events_all(experiment_id, ("candidate", "open")):
        d, key = e["data"], e.get("bot_key") or e["data"].get("bot_key")
        if not key or not d.get("side"):
            continue
        if e["kind"] == "candidate" and d.get("signal_ts") is not None:
            rec.seen.add(cand_ident(key, d["side"], d["signal_ts"]))
        elif e["kind"] == "open" and d.get("ts") is not None:
            rec.seen.add(open_ident(key, d["side"], d["ts"]))
    for t in storage.fwd6_trades(experiment_id, counterfactual=None):
        i = trade_ident(t["bot_key"], t["side"], t["entry_ts"], bool(t.get("counterfactual")))
        rec.seen.add(i)
        rec.nets[i] = t.get("net")
    for d in storage.fwd6_decisions(experiment_id):
        rec.decisions[(d["bot_key"], int(d["signal_ts"]), d["side"])] = {
            k: d.get(k) for k in ("id", "final_action", "final_level", "risk_multiplier", "reason", "error_code")}
    return rec


def _candle(r: dict[str, Any]) -> Any:
    from app.core.types import Candle
    o = int(r["open_time"])
    return Candle(r["symbol"], "1m", o, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                  float(r["volume"] or 0.0), o + MINUTE - 1, True, float(r.get("turnover") or 0.0), 0,
                  str(r.get("source") or "stored"), 0.0)


def _public(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in data.items() if k not in ("state_json",)}
    out["type"] = kind
    return out


def worker_main(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    from app.config import RedactFilter
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [v6] %(name)s: %(message)s")
    flt = RedactFilter([os.environ.get("OPENROUTER_API_KEY", "")])
    for h in logging.getLogger().handlers:
        h.addFilter(flt)
    try:
        _run(cfg, out_q, stop_ev)
    except Exception as exc:
        log.exception("V6 forward worker failed")
        out_q.put(("status", {"status": "ERROR", "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}))


def _run(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    from app.ai.jev.client import JevClient
    from app.ai.jev.models import JevConfig
    from app.ai.jev.v6 import ClientDeciderV6
    from app.backtest.replay import ReplayEngine
    from app.competition.v31_arena import _Probes
    from app.competition.v6_config import (AGGRESSIVE_V6, CONTEXT_HISTORY_DAYS, EXECUTION_V6, FEES_V6,
                                           FUNDING_HISTORY_DAYS, JEV_RETRIES, JEV_TIMEOUT_MS, POSITIONING_HISTORY_DAYS,
                                           WARMUP_DAYS, SizingV6, experiment_identity, field_plan, load_freeze,
                                           settings_v6, verify_freeze)
    from app.competition.v6_features import MarketContextV6, PositioningFeedV6
    from app.core.storage import Storage
    from app.core.types import MarketRules
    from app.live.bybit_market import BybitLiveMarket, LiveFundingV6
    from app.live.v6_gates import Coverage, DecisionBoard, ForwardGateV6, JevGateV6
    from app.live.v6_runner import V6Bot
    from app.strategies.registry import load_v6

    storage = Storage(cfg["db"])
    session_id = uuid.uuid4().hex[:12]
    boot_ms = int(time.time() * 1000)
    man = load_freeze()
    diffs = verify_freeze(man)
    if diffs:
        log.error("V6 is not frozen as it runs: %s", diffs[:5])
        out_q.put(("status", {"status": "FROZEN_MISMATCH", "session_id": session_id, "diffs": diffs[:20],
                              "detail": "the running V6 code / parameters / configuration differ from docs/V6_FREEZE.json: "
                                        "no bot trades until V6 is re-frozen (a new experiment)"}))
        stop_ev.wait()
        return
    jcfg = JevConfig.from_env()
    api_key = os.environ.get("OPENROUTER_API_KEY") or None
    jev_wanted = bool(cfg.get("jev", True))
    jev_state = ("READY" if (jev_wanted and jcfg.enabled and api_key) else
                 "DISABLED" if not jev_wanted or not jcfg.enabled else "NOT_CONFIGURED")
    experiment_id, identity = experiment_identity(man, jcfg.model)
    exp = storage.fwd6_experiment(experiment_id)
    coins = list(man["universe"]["traded"])
    traded = [f"{c}USDT" for c in coins]
    breadth = list(man["breadth_set"])
    rules = {s: MarketRules(**r) for s, r in (man.get("rules") or {}).items()}
    intervals = {s: int(v) for s, v in (man.get("funding_interval_min") or {}).items()}
    now = int(time.time() * 1000)
    warmup_from = int(exp["warmup_from_ms"]) if exp else (now - WARMUP_DAYS * DAY) // MINUTE * MINUTE
    t0 = int(exp["forward_start_ms"]) if exp else None
    specs = field_plan(coins, jev=jev_wanted)
    config = {"program": "V6", "experiment_id": experiment_id, "identity": identity, "manifest": man.get("fingerprint"),
              "coins": coins, "breadth_set": breadth, "bots": [s.key for s in specs], "controls": sum(1 for s in specs if s.role == "CONTROL"),
              "jev_bots": sum(1 for s in specs if s.role == "JEV"), "jev": {"state": jev_state, "model": jcfg.model,
                                                                            "timeout_ms": JEV_TIMEOUT_MS, "retries": JEV_RETRIES},
              "warmup_from_ms": warmup_from, "resumed": exp is not None, "boot_ms": boot_ms,
              "market_data": "Bybit linear public: WS kline.1 + tickers; REST klines, open interest, premium index, "
                             "account ratio, funding history", "venue": "BYBIT_LINEAR", "dry_run": True}
    storage.fwd6_session_start(session_id, experiment_id, boot_ms, config)
    events: queue.Queue = queue.Queue()

    def emit(kind: str, data: dict[str, Any]) -> None:
        events.put(("event", kind, data))

    def store(kind: str, payload: dict[str, Any]) -> None:
        events.put(("store", kind, payload))

    def system(what: str, **data: Any) -> None:
        emit("system", {"event": what, "session_id": session_id, **data})

    system("SESSION_START", resumed=exp is not None, forward_start_ms=t0, warmup_from_ms=warmup_from)
    # -- the experiment's stored inputs -----------------------------------------------------------------------
    # Every session loads history from the SAME anchor, the experiment's forward start (a new experiment: now, which
    # becomes its forward start minutes later), never from "now": a re-derivation weeks later must see exactly the
    # 90-day funding / 30-day OI and basis / 45-day market windows the live decision saw.
    base = t0 if t0 is not None else now
    wm = storage.fwd6_watermarks(warmup_from)
    feeds = {}
    for s in traded:
        feeds[s] = PositioningFeedV6(
            s, funding=storage.fwd6_positioning(s, "funding", base - FUNDING_HISTORY_DAYS * DAY),
            oi=storage.fwd6_positioning(s, "oi", base - POSITIONING_HISTORY_DAYS * DAY),
            premium=storage.fwd6_positioning(s, "premium", base - POSITIONING_HISTORY_DAYS * DAY),
            ratio=storage.fwd6_positioning(s, "ratio", base - POSITIONING_HISTORY_DAYS * DAY),
            caps=wm.get(s, {}), funding_interval_h=intervals.get(s, 480) / 60.0)
    market_ctx = MarketContextV6(breadth, caps=wm.get("*", {}))
    for s in breadth:
        market_ctx.close[s].extend(storage.fwd6_positioning(s, "ctx_close", base - CONTEXT_HISTORY_DAYS * DAY))
        market_ctx.oi[s].extend(storage.fwd6_positioning(s, "ctx_oi", base - 3 * DAY))
        market_ctx.funding[s].extend(storage.fwd6_positioning(s, "ctx_funding", base - 3 * DAY))
    funding = LiveFundingV6(storage.fwd6_funding_charged(t0 if t0 is not None else now))
    for s in traded:
        funding.merge_settled(s, list(zip(feeds[s].funding.t, feeds[s].funding.v)))
    preload, quotes = {}, {}
    for s in traded:
        rows = storage.fwd6_bars(s, warmup_from)
        preload[s] = [_candle(r) for r in rows]
        quotes[s] = [(int(r["open_time"]) + MINUTE - 1, float(r["half_spread_bps"])) for r in rows
                     if r.get("half_spread_bps") is not None]
    market = BybitLiveMarket(traded, breadth, feeds, market_ctx, funding, store, warmup_from, intervals,
                             preload=preload, quotes=quotes,
                             history_days={"funding": FUNDING_HISTORY_DAYS, "positioning": POSITIONING_HISTORY_DAYS,
                                           "context": CONTEXT_HISTORY_DAYS, "context_oi": 3})
    queues = {spec.key: market.subscribe(spec.symbol) for spec in specs}
    market.start()
    bots: list[Any] = []
    state = {"status": "WARMING_UP", "live_from_ms": None, "forward_start_ms": t0}
    batches: dict[str, list[dict[str, Any]]] = {"bar": [], "pos": [], "watermark": [], "funding_charged": []}

    def status_row() -> dict[str, Any]:
        rows = [_live_marks(dict(b.status), market) for b in bots]
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
                "config": config}

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

    import multiprocessing as mp
    parent = mp.parent_process()
    last_status = last_hb = last_persist = 0.0
    try:
        # -- warm up: stored inputs + REST, until the market is healthy and every coin is current ----------------
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

        def check(sym: str, t: int) -> tuple[bool, str | None]:
            ok, why = market.stream_ok(sym)
            if not ok:
                return ok, why
            return market.positioning_ok(sym, t)
        board = DecisionBoard(check)
        client = JevClient(dataclasses.replace(jcfg, timeout_ms=JEV_TIMEOUT_MS, max_retries=JEV_RETRIES), api_key) \
            if jev_wanted else None
        decider = ClientDeciderV6(client) if client is not None else None
        classes = load_v6()
        settings = settings_v6()
        for spec in specs:
            base_cls = classes[spec.strategy_id]
            cls = base_cls.for_class(spec.horizon, feeds[spec.symbol], market_ctx)
            bot_info = {**spec.to_dict(), "thesis": base_cls.thesis, "fails_when": base_cls.fails_when,
                        "family": base_cls.family}
            kw = dict(experiment_id=experiment_id, session_id=session_id, bot=bot_info, emit=emit, board=board,
                      coverage=coverage, recorded=recorded.for_bot(spec.key).decisions, live_from_ms=live_from)
            if spec.role == "JEV":
                gate = JevGateV6(decide=decider, model=jcfg.model, mid=market.mid, spread=market.spread_for_jev, **kw)
            else:
                gate = ForwardGateV6(**kw)
            eng = ReplayEngine(settings, [spec.symbol], rules={spec.symbol: rules[spec.symbol]}, seed=7,
                               funding=funding, execution=EXECUTION_V6, fees=FEES_V6, fee_source="schedule",
                               sizing=SizingV6(rules, jev=spec.role == "JEV"), leverage_policy="needed",
                               max_risk_pct=AGGRESSIVE_V6.max_risk_pct, gate=gate, cost_gate=None,
                               quotes=market.quote_half_spread)
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
                log.warning("parent process is gone; V6 forward worker stopping")
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


def _live_marks(row: dict[str, Any], market: Any) -> dict[str, Any]:
    """Display only: re-mark open positions at the live mid between 1m bars (the engine's own accounting -- fills,
    stops, targets, funding -- runs on closed bars exactly as in replay)."""
    mid = market.mid(row.get("symbol", ""))
    if not mid or not row.get("open_positions"):
        return row
    delta = 0.0
    out = []
    for p in row["open_positions"]:
        p = dict(p)
        live = (mid - p["entry"]) * p["qty"] * (1 if p["side"] == "long" else -1)
        delta += live - (p.get("upnl") or 0.0)
        p["mark"], p["upnl"] = mid, live
        out.append(p)
    row["open_positions"] = out
    row["equity_live"] = (row.get("equity") or 0.0) + delta
    return row


def _prices(market: Any, symbols: list[str]) -> dict[str, Any]:
    out = {}
    for sym in list(symbols) + ["BTCUSDT", "ETHUSDT"]:
        t = market.tickers.get(sym) or {}
        out[sym] = {"mid": market.mid(sym), "half_spread_bps": market.live_half_spread_bps(sym),
                    "mark": t.get("mark"), "index": t.get("index"), "funding_rate": t.get("funding_rate"),
                    "next_funding_ts": t.get("next_funding_ts"), "oi": t.get("oi"),
                    "last_bar_open": market.delivered.get(sym) or None}
    return out
