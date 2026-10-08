"""The V17 SWING worker (app/competition/v17_config.py): V6's forward worker for the swing/trend paper bots (the families
the V17 and V18 studies passed, on the 10 majors), in its own process and database (v17-forward.db). Adapted from
app/live/v16_worker.py; the V6 worker itself is pinned by V7's freeze and is not touched.

Differences from V6: the field, 1000 USDT books, instrument rules and funding intervals from V17's freeze, 4h signals,
and the daily bars, which come from Bybit's public daily klines (DailyBars: loaded at boot, refreshed every 10 minutes,
and fetched on demand when a decision needs the day that just ended). Paper only: no exchange client, key or order path.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
import uuid
from typing import Any

from app.live.v6_worker import _candle, _live_marks, _prices, _public, load_recorded_v6

log = logging.getLogger("paperlab.live.v17worker")

STATUS_EVERY_S = 2.0
HEARTBEAT_EVERY_S = 10.0
PERSIST_BOTS_EVERY_S = 30.0
DAILY_REFRESH_S = 600.0
MINUTE = 60_000
DAY = 86_400_000


def worker_main(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    from app.config import RedactFilter
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [v17] %(name)s: %(message)s")
    flt = RedactFilter([os.environ.get("OPENROUTER_API_KEY", "")])
    for h in logging.getLogger().handlers:
        h.addFilter(flt)
    try:
        _run(cfg, out_q, stop_ev)
    except Exception as exc:
        log.exception("V17 worker failed")
        out_q.put(("status", {"status": "ERROR", "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}))


def _run(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    from app.backtest.replay import ReplayEngine
    from app.competition import v17_config as v17
    from app.competition.v6_config import (AGGRESSIVE_V6, CONTEXT_HISTORY_DAYS, EXECUTION_V6, FEES_V6,
                                           FUNDING_HISTORY_DAYS, POSITIONING_HISTORY_DAYS, WARMUP_DAYS, SizingV6)
    from app.competition.v6_features import MarketContextV6, PositioningFeedV6
    from app.core.storage import Storage
    from app.core.types import MarketRules
    from app.live.bybit_market import BybitLiveMarket, LiveFundingV6
    from app.live.v6_gates import Coverage, DecisionBoard, ForwardGateV6
    from app.live.v6_runner import V6Bot

    storage = Storage(cfg["db"])
    session_id = uuid.uuid4().hex[:12]
    boot_ms = int(time.time() * 1000)
    man = v17.load_freeze()
    diffs = v17.verify_freeze(man)
    if diffs:
        log.error("V17 is not frozen as it runs: %s", diffs[:5])
        out_q.put(("status", {"status": "FROZEN_MISMATCH", "session_id": session_id, "diffs": diffs[:20],
                              "detail": "the running V17 code / configuration differ from docs/V17_FREEZE.json: no bot "
                                        "trades until V17 is re-frozen (a new experiment)"}))
        stop_ev.wait()
        return
    experiment_id, identity = v17.experiment_identity(man)
    exp = storage.fwd6_experiment(experiment_id)
    specs = v17.field_plan()
    traded = list(dict.fromkeys(s.symbol for s in specs))
    breadth = list(man["breadth_set"])
    rules = {s: MarketRules(**r) for s, r in man["rules"].items()}
    intervals = {s: int(v) for s, v in man["funding_interval_min"].items()}
    now = int(time.time() * 1000)
    warmup_from = int(exp["warmup_from_ms"]) if exp else (now - WARMUP_DAYS * DAY) // MINUTE * MINUTE
    t0 = int(exp["forward_start_ms"]) if exp else None
    config = {"program": "V17", "experiment_id": experiment_id, "identity": identity, "manifest": man.get("fingerprint"),
              "coins": [s.coin for s in specs], "breadth_set": breadth, "bots": [s.key for s in specs],
              "controls": len(specs), "jev_bots": 0, "jev": {"state": "DISABLED"},
              "warmup_from_ms": warmup_from, "resumed": exp is not None, "boot_ms": boot_ms,
              "market_data": "Bybit linear public: WS kline.1 + tickers; REST klines, open interest, premium index, "
                             "account ratio, funding history, daily klines (the swing rules' daily bars)",
              "venue": v17.VENUE, "dry_run": True, "starting_balance": v17.STARTING_BALANCE,
              "families": list(v17.FAMILIES), "daily_bars": man["daily_bars"]}
    storage.fwd6_session_start(session_id, experiment_id, boot_ms, config)
    events: queue.Queue = queue.Queue()

    def emit(kind: str, data: dict[str, Any]) -> None:
        events.put(("event", kind, data))

    def store(kind: str, payload: dict[str, Any]) -> None:
        events.put(("store", kind, payload))

    def system(what: str, **data: Any) -> None:
        emit("system", {"event": what, "session_id": session_id, **data})

    system("SESSION_START", resumed=exp is not None, forward_start_ms=t0, warmup_from_ms=warmup_from)
    # -- the daily bars the swing rules read ------------------------------------------------------------------------
    daily = v17.DailyBars(v17.bybit_daily)
    pending = list(traded)
    while not stop_ev.is_set() and pending:
        for s in list(pending):
            try:
                daily.load(s, v17.DAILY_HISTORY_DAYS)
                if daily.count(s) >= 100:
                    pending.remove(s)
            except Exception as exc:
                log.warning("%s daily klines unavailable: %s", s, str(exc)[:160])
        if pending:
            out_q.put(("status", {"status": "WARMING_UP", "session_id": session_id,
                                  "detail": f"waiting for daily klines: {', '.join(pending)}", "config": config}))
            stop_ev.wait(30)
    if stop_ev.is_set():
        return

    def refresh_daily() -> None:
        while not stop_ev.wait(DAILY_REFRESH_S):
            for s in traded:
                try:
                    daily.load(s, 5)
                except Exception as exc:
                    log.warning("%s daily refresh failed: %s", s, str(exc)[:160])
    threading.Thread(target=refresh_daily, name="v17-daily", daemon=True).start()
    # -- the experiment's stored inputs (as V6: the same anchor for every session) ---------------------------------
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
                "market": market.health(), "prices": _prices(market, traded), "jev_state": "DISABLED",
                "daily_bars": {s: daily.count(s) for s in traded}, "config": config}

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
        classes = v17.family_classes()
        settings = v17.settings_v17()
        for spec in specs:
            base_cls = classes[spec.strategy_id]
            cls = v17.live_class(spec.strategy_id, daily)
            bot_info = {**spec.to_dict(), "thesis": base_cls.thesis, "name": base_cls.name,
                        "fails_when": base_cls.fails_when, "family": base_cls.family}
            gate = ForwardGateV6(experiment_id=experiment_id, session_id=session_id, bot=bot_info, emit=emit,
                                 board=board, coverage=coverage, recorded=recorded.for_bot(spec.key).decisions,
                                 live_from_ms=live_from)
            eng = ReplayEngine(settings, [spec.symbol], rules={spec.symbol: rules[spec.symbol]}, seed=7,
                               funding=funding, execution=EXECUTION_V6, fees=FEES_V6, fee_source="schedule",
                               sizing=SizingV6(rules, jev=False), leverage_policy="needed",
                               max_risk_pct=AGGRESSIVE_V6.max_risk_pct, gate=gate, cost_gate=None,
                               quotes=market.quote_half_spread)
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
                log.warning("parent process is gone; V17 worker stopping")
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
