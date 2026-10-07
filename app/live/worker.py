"""The live shadow worker: a separate process, so warmup and 30+ bots never compete with the web
server for the GIL. Started and stopped by app/live/service.py.

    field    every bot of the frozen v2 TEST arena run (dataset_role TEST), as CONTROL
    pairs    for each JEV_ELIGIBLE_CONTROL among them, a +JEV twin on the same live bars
    feed     one LiveMarket (Binance USD-M public data) shared by every bot
    output   shadow_* tables in the shared SQLite file, plus events and status to the parent

Jev is only ever asked about candidates of eligible controls, only when JEV_ENABLED is true and a
key is configured, with the frozen V1 prompt and policy. With fewer than MIN_PAIRS eligible
controls the status says INSUFFICIENT JEV-ELIGIBLE CONTROLS; the eligible pairs still run and
collect evidence, and no live Jev verdict is issued until the minimum is met.

A session that continues an existing FORWARD EXPERIMENT does not start its books from scratch: it
re-derives them from the experiment's forward start on the closed 1m tape and carries on (open
positions, drawdown and halts included) -- see app/live/continuity.py.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import queue
import time
import uuid
from typing import Any

log = logging.getLogger("paperlab.live.worker")

MIN_PAIRS = 10
STATUS_EVERY_S = 2.0
PERSIST_BOTS_EVERY_S = 30.0


def plan_field(storage: Any, source_run: str | None = None, max_bots: int = 40) -> dict[str, Any]:
    """Which frozen bots go live, and which of them are JEV-eligible controls. Pure storage reads."""
    from app.competition.cost_efficiency import cost_efficiency, jev_eligible
    from app.core.arena_view import window_days
    run = None
    if source_run:
        run = storage.arena_run(source_run, heavy=True)
    else:
        for r in storage.arena_runs(limit=50):
            full = storage.arena_run(r["run_id"], heavy=True) or {}
            cfg = full.get("config") or {}
            if str(cfg.get("dataset_role", "")).upper() == "TEST" and cfg.get("params_version") == "v2" \
                    and str(full.get("status", "")).upper() == "COMPLETE":
                run = full
                break
    if run is None:
        return {"run": None, "controls": [], "eligible": [], "reasons": {}}
    days = window_days(run.get("first_month"), run.get("last_month"))
    controls, eligible, reasons = [], [], {}
    for b in storage.arena_bots(run["run_id"]):
        if not b.get("entered", True) or b.get("metrics") is None:
            continue
        controls.append(b)
        ce = cost_efficiency(b["metrics"], None, days)
        ok, why = jev_eligible(b["metrics"], ce)
        reasons[b["key"]] = why
        if ok:
            eligible.append(b["key"])
    controls.sort(key=lambda b: b["key"])
    return {"run": run, "controls": controls[:max_bots], "eligible": sorted(eligible), "reasons": reasons}


def _spec(b: dict[str, Any]) -> Any:
    from app.competition.bots import BotSpec
    return BotSpec(b["strategy_id"], b["symbol"], b["timeframe"], int(b.get("max_leverage") or 20),
                   b.get("profile") or "AGGRESSIVE", b.get("params_version") or "v2",
                   b.get("name") or b["strategy_id"])


def _public(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """What may leave the process on the public stream: never the Jev state payload."""
    out = {k: v for k, v in data.items() if k not in ("state_json",)}
    out["type"] = kind
    return out


def worker_main(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    """Child-process entry point. Everything the parent needs arrives on `out_q` as
    ("event", channel, data, public) or ("status", dict)."""
    from app.config import RedactFilter
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [shadow] %(name)s: %(message)s")
    key = os.environ.get("OPENROUTER_API_KEY", "")
    flt = RedactFilter([key])
    for h in logging.getLogger().handlers:
        h.addFilter(flt)
    try:
        _run(cfg, out_q, stop_ev)
    except Exception as exc:
        log.exception("live shadow worker failed")
        out_q.put(("status", {"status": "ERROR", "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}))


def _run(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    from app.ai.jev.client import JevClient
    from app.ai.jev.models import JevConfig, PROMPT_VERSION
    from app.ai.jev.policy import POLICY_V1
    from app.backtest import brackets as bracket_mod
    from app.competition.arena import Arena
    from app.competition.jev_experiment import _settings, arena_config_from, pair_id
    from app.core.storage import Storage
    from app.core.types import MarketRules
    from app.live.jev_live import LiveJevGate
    from app.live.market import LiveMarket
    from app.live.runner import ShadowBot, attack_profile
    from app.strategies.registry import load_all, load_v2

    storage = Storage(cfg["db"])
    session_id = uuid.uuid4().hex[:12]
    field = plan_field(storage, cfg.get("source_run") or None, int(cfg.get("max_bots") or 40))
    run = field["run"]
    if run is None:
        out_q.put(("status", {"status": "NO_SOURCE_RUN", "session_id": session_id,
                              "detail": "no COMPLETE v2 TEST arena run in storage"}))
        stop_ev.wait()
        return
    acfg = arena_config_from(run.get("config") or {})
    settings = _settings(acfg.starting_balance)
    rules = {s: MarketRules(**r) for s, r in ((run.get("config") or {}).get("rules") or {}).items()}
    specs = [_spec(b) for b in field["controls"]]
    symbols = sorted({s.symbol for s in specs})
    arena = Arena(settings, acfg, [], rules, brackets=bracket_mod.load(symbols, settings.data_dir))
    classes = load_v2() if acfg.params_version == "v2" else load_all(strict=True)
    jcfg = JevConfig.from_env()
    api_key = os.environ.get("OPENROUTER_API_KEY") or None
    jev_wanted = bool(cfg.get("jev", True))
    jev_ready = jev_wanted and jcfg.enabled and bool(api_key)
    jev_state = ("READY" if jev_ready else "DISABLED" if not jev_wanted or not jcfg.enabled
                 else "NOT_CONFIGURED")
    eligible = [k for k in field["eligible"] if any(s.key == k for s in specs)]
    insufficient = len(eligible) < MIN_PAIRS
    client = JevClient(dataclasses.replace(jcfg, max_retries=0), api_key) if jev_ready else None

    events: queue.Queue = queue.Queue()

    def emit(kind: str, data: dict[str, Any]) -> None:
        events.put((kind, data))

    since = int(time.time() * 1000)
    warmup_ms = int(float(cfg.get("warmup_hours") or 504) * 3_600_000)
    order_latency = int(acfg.execution.signal_latency_ms + acfg.execution.order_latency_ms)
    profile = attack_profile(acfg)
    config = {"source_run_id": run["run_id"], "source_label": run.get("label"),
              "dataset_role": (run.get("config") or {}).get("dataset_role"),
              "params_version": acfg.params_version, "strategy_fingerprints": dict(acfg.strategy_fingerprints),
              "fees": acfg.fees.to_dict(), "execution": dataclasses.asdict(acfg.execution),
              "cost_gate_min_ratio": acfg.cost_gate_min_ratio, "profile": acfg.profile,
              "starting_balance": acfg.starting_balance, "leverage_ceiling": acfg.leverage_ceiling,
              "symbols": symbols, "controls": [s.key for s in specs], "eligible_controls": eligible,
              "min_pairs": MIN_PAIRS, "jev": {"state": jev_state, "model": jcfg.model, "prompt_version": PROMPT_VERSION,
                                              "policy_version": POLICY_V1.version, "timeout_ms": jcfg.timeout_ms,
                                              "retries": 0},
              "warmup_hours": float(cfg.get("warmup_hours") or 504), "go_live_ms": since,
              "market_data": "Binance USD-M public: /market kline_1m, /public bookTicker, premiumIndex",
              "venue": "BINANCE_USDM", "arena_config_fingerprint": run.get("config_fingerprint")}
    from app.live import continuity
    from app.live.analytics import forward_experiment
    experiment_id, experiment = forward_experiment(config, run.get("config_fingerprint"))
    config["experiment_id"] = experiment_id
    # One continuous book per experiment: a resumed session re-derives [T0, now) before going live.
    cont = continuity.plan(storage, experiment_id, since, warmup_ms)
    config["continuity"] = continuity.public(cont)
    t0, recorded = cont["forward_from_ms"], cont["recorded"]
    market = LiveMarket(symbols, warmup_ms, start_ms=cont["backfill_from_ms"],
                        funding_from_ms=t0 if cont["resumed_from_sessions"] else None)
    bots: list[ShadowBot] = []
    for spec in specs:
        cls = arena.bind(spec, classes[spec.strategy_id])
        pid = pair_id(spec.key) if (spec.key in eligible and jev_ready) else None
        bots.append(ShadowBot(key=spec.key, role="CONTROL", pair_id=pid, spec=spec, cls=cls,
                              engine=arena.build_engine(spec, funding=market.funding),
                              bars=market.subscribe(spec.symbol), since_ms=t0, emit=emit, profile=profile,
                              live_from_ms=since, recorded=recorded.for_bot(spec.key)))
        if pid is not None:
            bot = {"key": spec.key + "+JEV", "strategy_id": spec.strategy_id,
                   "strategy_name": getattr(cls, "name", spec.strategy_id), "params_version": spec.params_version,
                   "symbol": spec.symbol, "timeframe": spec.timeframe, "control_version": spec.version()}
            twin = recorded.for_bot(bot["key"])
            gate = LiveJevGate(client.decide, bot, pid, session_id, jcfg.model, emit, market.mid, order_latency,
                               live_from_ms=since, recorded=twin.decisions)
            bots.append(ShadowBot(key=bot["key"], role="JEV", pair_id=pid, spec=spec, cls=cls,
                                  engine=arena.build_engine(spec, gate=gate, funding=market.funding),
                                  bars=market.subscribe(spec.symbol), since_ms=t0, emit=emit, profile=profile,
                                  live_from_ms=since, recorded=twin))
    storage.shadow_session_start(session_id, since, run["run_id"], config,
                                 experiment_id=experiment_id, experiment=experiment)
    if cont["resumed_from_sessions"]:
        log.info("live shadow session %s resumes %s: re-deriving from %s (%d sessions, recorded %s)",
                 session_id, experiment_id, t0, len(cont["resumed_from_sessions"]), recorded.counts())
    log.info("live shadow session %s: %d controls, %d eligible, jev %s", session_id, len(specs),
             len(eligible), jev_state)

    def status(extra: dict[str, Any] | None = None) -> dict[str, Any]:
        rows = [_live_marks(dict(b.status, control_key=b.spec.key), market) for b in bots]
        live = sum(1 for r in rows if r.get("live"))
        cs = [r.get("continuity") or {} for r in rows]
        unrep = [c.get("unreproduced") for c in cs]
        cont_now = {**config["continuity"], "live_from_ms": since,
                    **{k: sum(int(c.get(k) or 0) for c in cs) for k in ("rederived", "matched", "diverged")},
                    "unreproduced": None if not cs or any(u is None for u in unrep) else sum(unrep)}
        st = "LIVE" if bots and live == len(bots) else "WARMING_UP"
        if any(r.get("error") for r in rows):
            st = "DEGRADED" if live else "ERROR"
        return {"status": st, "session_id": session_id, "experiment_id": experiment_id,
                "source_run_id": run["run_id"], "go_live_ms": since,
                "bots": rows, "live_bots": live, "total_bots": len(bots), "controls": len(specs),
                "eligible_controls": eligible, "pairs": sum(1 for b in bots if b.role == "JEV"),
                "insufficient_controls": insufficient, "min_pairs": MIN_PAIRS, "jev_state": jev_state,
                "market": market.health(), "prices": _prices(market), "config": config,
                "continuity": cont_now, **(extra or {})}

    for b in bots:
        b.start()
    market.start()
    last_status = last_persist = 0.0
    live_marked = False
    import multiprocessing as mp
    parent = mp.parent_process()
    try:
        while not stop_ev.is_set():
            if parent is not None and not parent.is_alive():
                log.warning("parent process is gone; live shadow worker stopping")
                break
            _drain(events, storage, session_id, out_q, timeout=0.5)
            now = time.time()
            if now - last_status >= STATUS_EVERY_S:
                st = status()
                out_q.put(("status", st))
                last_status = now
                if st["status"] == "LIVE" and not live_marked:
                    storage.shadow_session_update(session_id, status="LIVE", live_ts=int(now * 1000))
                    out_q.put(("event", "shadow", _public("session", {"status": "LIVE", "session_id": session_id}), True))
                    live_marked = True
                if now - last_persist >= PERSIST_BOTS_EVERY_S:
                    storage.shadow_bots_save(session_id, st["bots"], int(now * 1000))
                    last_persist = now
    finally:
        market.stop()
        for b in bots:
            b.stop()
        for b in bots:
            if b.thread is not None:
                b.thread.join(timeout=2.0)
        _drain(events, storage, session_id, out_q, timeout=0.0)
        st = status()
        storage.shadow_bots_save(session_id, st["bots"], int(time.time() * 1000))
        storage.shadow_session_update(session_id, status="ENDED", ended_ts=int(time.time() * 1000),
                                      summary_json={"bots": len(bots), "live_bots": st["live_bots"]})
        storage.close()


def _live_marks(row: dict[str, Any], market: Any) -> dict[str, Any]:
    """Display only: re-mark open positions at the live mid between 1m bars. The engine's own
    accounting (fills, stops, exits) still runs on closed bars exactly as in replay."""
    mid = market.mid(row.get("symbol", ""))
    if not mid or not row.get("open_positions"):
        return row
    delta = 0.0
    for p in row["open_positions"]:
        live = (mid - p["entry"]) * p["qty"] * (1 if p["side"] == "long" else -1)
        delta += live - (p.get("upnl") or 0.0)
        p["mark"], p["upnl"] = mid, live
    row["equity"] = (row.get("equity") or 0.0) + delta
    row["net"] = row["equity"] - (row.get("start_equity") or 0.0)
    return row


def _prices(market: Any) -> dict[str, Any]:
    out = {}
    for sym in market.symbols:
        f = market.funding_info.get(sym) or {}
        out[sym] = {"mid": market.mid(sym), "half_spread_bps": market.half_spread_bps(sym),
                    "funding_rate": f.get("rate"), "next_funding_ts": f.get("next_ts"),
                    "last_bar_open": market.delivered.get(sym) or None}
    return out


def _drain(events: queue.Queue, storage: Any, session_id: str, out_q: Any, timeout: float) -> None:
    """Persist and forward everything the bots emitted. One writer thread for the database."""
    try:
        first = events.get(timeout=timeout) if timeout > 0 else events.get_nowait()
    except queue.Empty:
        return
    batch = [first]
    while len(batch) < 500:
        try:
            batch.append(events.get_nowait())
        except queue.Empty:
            break
    for kind, data in batch:
        ts = int(time.time() * 1000)
        try:
            if kind == "decision":
                storage.shadow_decision_save(data)
                storage.shadow_event_add(session_id, ts, "decision", _public("decision", data))
                out_q.put(("event", "jev", _public("decision", data), True))
                continue
            if kind == "closed":
                storage.shadow_trade_save(session_id, data)
            if kind != "evaluating":
                storage.shadow_event_add(session_id, ts, kind, data)
            out_q.put(("event", "shadow", _public(kind, data), True))
        except Exception as exc:
            log.warning("could not record %s: %s", kind, str(exc)[:160])
