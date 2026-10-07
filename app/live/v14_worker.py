"""The V14 HTF worker (docs/V14_PROTOCOL.md): a separate process started at boot by app/live/v6_service.py when
V14_FORWARD_ENABLED=true. Copies of the best current strategies that check the higher timeframes before every entry, on
live Bybit data with simulated fills -- the V11 forward machinery, imported unchanged:

    verify     docs/V14_FREEZE.json must match the running V14 code (and the copied objects it runs) -- or nothing trades
    identity   the freeze fingerprint = the experiment (v14x-...); a compatible experiment is RESUMED
    tape       app/live/scan_market.py: stored history, REST catch-up, then live minute batches of all 30 coins (BTC,
               the anchor, last); 25 days of warm-up for complete UTC 4h/daily context
    bots       V14.1 = V8.3 + HTF on each of V8's 6 coins (LevelMakerEngineV8); V14.2 / V14.3 = V11.1 / V11.2 + HTF over
               30 coins (ScanReplayEngine, the 25/50/25 ladder); V14.4 = V13 + HTF over 29 coins (LimitEntryEngineV13)
    gates      V11's per-coin rules: a decision later than 55 s, or stale data for that coin, is SKIP; decisions are
               recorded per (bot, coin, instant, side) and replayed on restart

No Jev twin. Paper only: no exchange client, key or order path.
"""
from __future__ import annotations

import logging
import queue
import time
import uuid
from typing import Any

from app.live.v11_worker import ScanGateV11, live_marks, load_eliminated, load_recorded_v11, prices, recorded_for
from app.live.v6_worker import _public

log = logging.getLogger("paperlab.live.v14worker")

STATUS_EVERY_S = 2.0
HEARTBEAT_EVERY_S = 10.0
PERSIST_BOTS_EVERY_S = 30.0
MINUTE = 60_000
DAY = 86_400_000


def worker_main(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    import os

    from app.config import RedactFilter
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [v14] %(name)s: %(message)s")
    flt = RedactFilter([os.environ.get("OPENROUTER_API_KEY", "")])      # no key is used here; never log one anyway
    for h in logging.getLogger().handlers:
        h.addFilter(flt)
    try:
        _run(cfg, out_q, stop_ev)
    except Exception as exc:
        log.exception("V14 HTF worker failed")
        out_q.put(("status", {"status": "ERROR", "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}))


def _run(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    import threading

    from app.backtest import bybit_archive as bb
    from app.competition import v14_config as v14
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6
    from app.core.storage import Storage
    from app.core.types import MarketRules
    from app.live.bybit_market import LiveFundingV6
    from app.live.scan_market import ScanMarket
    from app.live.v6_gates import Coverage, DecisionBoard
    from app.live.v6_runner import V6Bot
    from app.live.scan_engine import BE_COVER_BPS
    from app.live.v14_engine import HTFScalpEngine, HTFScanEngine, HTFLimitEngine
    from app.strategies.v14.htf import SOURCES, base_class, build
    ANCHOR = v14.ANCHOR

    storage = Storage(cfg["db"])
    session_id = uuid.uuid4().hex[:12]
    boot_ms = int(time.time() * 1000)
    man = v14.load_freeze()
    diffs = v14.verify_freeze(man)
    if diffs:
        log.error("V14 is not frozen as it runs: %s", diffs[:5])
        out_q.put(("status", {"status": "FROZEN_MISMATCH", "session_id": session_id, "diffs": diffs[:20],
                              "detail": "the running V14 code / parameters / configuration differ from "
                                        "docs/V14_FREEZE.json: nothing trades until V14 is re-frozen (a new experiment)"}))
        stop_ev.wait()
        return
    jev_state = "DISABLED"
    experiment_id, identity = v14.experiment_identity(man)
    exp = storage.fwd6_experiment(experiment_id)
    universe = list(man["profile"]["universe"])
    rules = {s: MarketRules(**r) for s, r in (man.get("rules") or {}).items()}
    intervals = {s: int(v) for s, v in (man.get("funding_interval_min") or {}).items()}
    live_from = boot_ms // MINUTE * MINUTE + MINUTE
    warmup_from = int(exp["warmup_from_ms"]) if exp else (live_from - v14.WARMUP_DAYS * DAY) // DAY * DAY
    t0 = int(exp["forward_start_ms"]) if exp else live_from
    specs = v14.field_plan()
    config = {"program": "V14", "experiment_id": experiment_id, "identity": identity, "manifest": man.get("fingerprint"),
              "universe": universe, "coins": [s[:-4] for s in universe], "anchor": ANCHOR, "bots": [s.key for s in specs],
              "controls": len(specs), "jev_bots": 0, "jev": {"state": jev_state},
              "warmup_from_ms": warmup_from, "resumed": exp is not None, "boot_ms": boot_ms,
              "venue": "BYBIT_LINEAR", "dry_run": True, "starting_balance": v14.STARTING_BALANCE,
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
            r["coins"] = 1 if r.get("symbol") else len(universe) - 1
            r["htf_filter"] = {
                "entry_rejects": dict(getattr(b.eng.ctx, "_v14_htf_rejects", {})),
                "fill_rejects": {k: v for k, v in dict(getattr(getattr(b.eng, "_res", None), "rejects", {})).items()
                                 if k.startswith("htf_fill_")},
                "latest_context": {sym: value[1] for sym, value in
                                   dict(getattr(b.eng.ctx, "_v14_htf_cache", {})).items()}}
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
    settings = v14.settings_v14()
    for spec in specs:
        base_cls = base_class(spec.strategy_id)
        cls = build(spec.strategy_id, universe)
        bot_info = {**spec.to_dict(), "thesis": base_cls.thesis, "fails_when": base_cls.fails_when,
                    "family": base_cls.family}
        mine = recorded_for(recorded, spec.key)
        gate = ScanGateV11(eliminated_at=eliminated_at, late_after_ms=v14.LATE_AFTER_MS, experiment_id=experiment_id,
                           session_id=session_id, bot=bot_info, emit=emit, board=board, coverage=coverage,
                           recorded=mine.decisions, live_from_ms=live_from)
        kind = SOURCES[spec.strategy_id]["kind"]
        syms = universe if spec.multi else [spec.symbol]
        kw = dict(rules=rules if spec.multi else {spec.symbol: rules[spec.symbol]}, seed=7, funding=funding,
                  execution=v14.EXECUTION_V14, fees=FEES_V6, fee_source="schedule", sizing=SizingV6(rules, jev=False),
                  leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct, gate=gate, cost_gate=None,
                  quotes=market.quote_half_spread, maker_tp=True)          # every copied strategy rests its targets
        if kind == "single":                                             # V8.3's own engine (no pre-trade gap check)
            eng = HTFScalpEngine(settings, syms, **kw)
        elif kind == "scanner":                                          # V11's engine: level exits + ladder locks
            eng = HTFScanEngine(settings, syms, be_cover_bps=BE_COVER_BPS, **kw)
        else:                                                            # V13's engine: resting post-only entries
            eng = HTFLimitEngine(settings, syms, **kw)
        bots.append(V6Bot(spec=spec, cls=cls, engine=eng, bars=queues[spec.key], since_ms=t0, live_from_ms=live_from,
                          emit=emit, recorded=mine, prev_snapshot=prev.get(spec.key), family=base_cls.family,
                          leverage=v14.LEVERAGE_CEILING, multi=spec.multi))
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
                log.warning("parent process is gone; V14 worker stopping")
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
