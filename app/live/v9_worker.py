"""The V9 STOCKS worker (docs/V9_PROTOCOL.md): a separate process started at boot by app/live/v6_service.py when
V9_FORWARD_ENABLED=true. It runs the V9 stock scalpers on live Alpaca IEX data (regular sessions only) with
simulated fills -- the V8 worker's design on a stock feed:

    verify     docs/V9_FREEZE.json must match the running V9 code and the shared V6 dependencies -- or no bot trades
    keys       Alpaca market data needs the account's keys: the dashboard's encrypted vault (Alpaca paper), else the
               ALPACA_PAPER_API_KEY / ALPACA_PAPER_API_SECRET variables. Used for market data ONLY
    identity   the freeze fingerprint + the Jev model = the experiment (v9x-...); a compatible experiment is RESUMED
    gates      the V8 CONTROL gate (55 s lateness, stale data, ELIMINATED) and, for every +JEV twin, the V8 Jev gate
               asking Jev V9 (JEV_PROMPT_V9_STOCKS)
    eliminate  a bot 25% below its start leaves at once; after each session's close, a CONTROL with fewer than 3
               closed trades over its last 3 sessions leaves (a twin leaves with its control)

Paper only: no order path. Positions are never carried overnight (the strategies stop entering 50 minutes before the
close and the time stop is 45 minutes).
"""
from __future__ import annotations

import dataclasses
import logging
import os
import queue
import time
import uuid
from typing import Any

from app.live.v6_worker import _candle, _live_marks, _prices, _public, load_recorded_v6
from app.live.v8_worker import ForwardGateV8, JevGateV8, load_eliminated

log = logging.getLogger("paperlab.live.v9worker")

STATUS_EVERY_S = 2.0
HEARTBEAT_EVERY_S = 10.0
PERSIST_BOTS_EVERY_S = 30.0
ELIMINATION_EVERY_S = 30.0
JUDGE_AFTER_CLOSE_MS = 2 * 60_000        # the session's last time-stop exits are recorded by then
JUDGE_WINDOW_MS = 3 * 3_600_000
MINUTE = 60_000
DAY = 86_400_000


class JevGateV9(JevGateV8):
    """The V8 Jev gate (same deadline, lateness, ELIMINATED and ATTACK rules) with Jev V9's stock state and question.
    The recorded prompt version is V9's."""

    def __init__(self, **k: Any):
        from app.ai.jev.v8 import PROMPT_VERSION_V8
        from app.ai.jev.v9 import PROMPT_VERSION_V9
        emit = k.pop("emit")

        def relabel(kind: str, data: dict[str, Any]) -> None:
            if kind == "decision" and data.get("prompt_version") == PROMPT_VERSION_V8:
                data["prompt_version"] = PROMPT_VERSION_V9
            emit(kind, data)
        super().__init__(emit=relabel, **k)


def alpaca_keys() -> tuple[str, str] | None:
    """Alpaca PAPER keys for market data: the dashboard's encrypted vault first, then the environment."""
    from app.live.key_vault import VAULT_FILE, KeyVault
    data_dir = os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or "data"
    saved = KeyVault(os.path.join(data_dir, VAULT_FILE), os.environ.get("DASHBOARD_PASSWORD")).get("alpaca:testnet")
    if saved:
        return saved
    k, s = (os.environ.get("ALPACA_PAPER_API_KEY") or "").strip(), (os.environ.get("ALPACA_PAPER_API_SECRET") or "").strip()
    return (k, s) if k and s else None


def worker_main(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    from app.config import RedactFilter
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [v9] %(name)s: %(message)s")
    keys = alpaca_keys() or ()
    flt = RedactFilter([os.environ.get("OPENROUTER_API_KEY", ""), *keys])
    for h in logging.getLogger().handlers:
        h.addFilter(flt)
    try:
        _run(cfg, out_q, stop_ev)
    except Exception as exc:
        log.exception("V9 stock worker failed")
        msg = f"{exc.__class__.__name__}: {str(exc)[:200]}"
        for v in keys:
            msg = msg.replace(v, "***")
        out_q.put(("status", {"status": "ERROR", "error": msg}))


def _run(cfg: dict[str, Any], out_q: Any, stop_ev: Any) -> None:
    from app.ai.jev.client import JevClient
    from app.ai.jev.models import JevConfig
    from app.ai.jev.v9 import ClientDeciderV9, JevStateBuilderV9
    from app.live.stock_engine import StockReplayEngine
    from app.competition import v9_config as v9
    from app.competition.v31_arena import _Probes
    from app.competition.v6_config import AGGRESSIVE_V6, SizingV6
    from app.core.storage import Storage
    from app.core.types import MarketRules
    from app.exchange.alpaca_client import AlpacaClient
    from app.live.alpaca_market import AlpacaLiveMarket
    from app.live.v6_gates import Coverage, DecisionBoard
    from app.live.v6_runner import V6Bot
    from app.strategies.v9.stocks import load_v9_stock_scalpers as load_v9

    storage = Storage(cfg["db"])
    session_id = uuid.uuid4().hex[:12]
    boot_ms = int(time.time() * 1000)
    man = v9.load_freeze()
    diffs = v9.verify_freeze(man)
    if diffs:
        log.error("V9 is not frozen as it runs: %s", diffs[:5])
        out_q.put(("status", {"status": "FROZEN_MISMATCH", "session_id": session_id, "diffs": diffs[:20],
                              "detail": "the running V9 code / parameters / configuration differ from docs/V9_FREEZE.json: "
                                        "no bot trades until V9 is re-frozen (a new experiment)"}))
        stop_ev.wait()
        return
    keys = alpaca_keys()
    while keys is None and not stop_ev.is_set():
        out_q.put(("status", {"status": "NO_DATA_KEYS", "session_id": session_id,
                              "detail": "enter the Alpaca paper keys in System -> Providers & Live (market data needs them)"}))
        stop_ev.wait(30)
        keys = alpaca_keys()
    if keys is None:
        return
    jcfg = JevConfig.from_env()
    api_key = os.environ.get("OPENROUTER_API_KEY") or None
    jev_wanted = bool(cfg.get("jev", True))
    jev_state = ("READY" if (jev_wanted and jcfg.enabled and api_key) else
                 "DISABLED" if not jev_wanted or not jcfg.enabled else "NOT_CONFIGURED")
    experiment_id, identity = v9.experiment_identity(man, jcfg.model if jev_wanted else None)
    exp = storage.fwd6_experiment(experiment_id)
    symbols = list(man["profile"]["symbols"])
    rules = {s: MarketRules(**r) for s, r in (man.get("rules") or {}).items()}
    now = int(time.time() * 1000)
    warmup_from = int(exp["warmup_from_ms"]) if exp else (now - v9.WARMUP_DAYS * DAY) // MINUTE * MINUTE
    t0 = int(exp["forward_start_ms"]) if exp else None
    specs = v9.field_plan(symbols, jev=jev_wanted)
    config = {"program": "V9", "experiment_id": experiment_id, "identity": identity, "manifest": man.get("fingerprint"),
              "coins": symbols, "breadth_set": [], "bots": [s.key for s in specs],
              "controls": sum(1 for x in specs if x.role == "CONTROL"), "jev_bots": sum(1 for x in specs if x.role == "JEV"),
              "jev": {"state": jev_state, "model": jcfg.model, "timeout_ms": v9.JEV_TIMEOUT_MS, "retries": v9.JEV_RETRIES},
              "warmup_from_ms": warmup_from, "resumed": exp is not None, "boot_ms": boot_ms, "venue": v9.VENUE,
              "dry_run": True, "currency": "USD", "starting_balance": v9.STARTING_BALANCE,
              "market_data": "Alpaca IEX: WS minute bars + quotes; REST bars and the trading calendar"}
    storage.fwd6_session_start(session_id, experiment_id, boot_ms, config)
    events: queue.Queue = queue.Queue()

    def emit(kind: str, data: dict[str, Any]) -> None:
        events.put(("event", kind, data))

    def store(kind: str, payload: dict[str, Any]) -> None:
        events.put(("store", kind, payload))

    def system(what: str, **data: Any) -> None:
        emit("system", {"event": what, "session_id": session_id, **data})

    system("SESSION_START", resumed=exp is not None, forward_start_ms=t0, warmup_from_ms=warmup_from)
    preload, quotes = {}, {}
    for s in symbols:
        rows = storage.fwd6_bars(s, warmup_from)
        preload[s] = [_candle(r) for r in rows]
        quotes[s] = [(int(r["open_time"]) + MINUTE - 1, float(r["half_spread_bps"])) for r in rows
                     if r.get("half_spread_bps") is not None]
    market = AlpacaLiveMarket(symbols, AlpacaClient("testnet", *keys), store, warmup_from, preload=preload, quotes=quotes)
    queues = {spec.key: market.subscribe(spec.symbol) for spec in specs}
    market.start()
    bots: list[Any] = []
    state = {"status": "WARMING_UP", "live_from_ms": None, "forward_start_ms": t0}
    batches: dict[str, list[dict[str, Any]]] = {"bar": []}
    eliminated = load_eliminated(storage, experiment_id)
    eliminated_at = {k: v["at"] for k, v in eliminated.items()}

    def sessions_live(now_ms: int) -> int:
        if t0 is None or market.sessions is None:
            return 0
        return market.sessions.closed_between(t0, now_ms)

    def status_row() -> dict[str, Any]:
        sess = market.market_state()
        rows = []
        for b in bots:
            r = _live_marks(dict(b.status), market)
            if not sess["open"] and b.q.empty() and not b.evaluating and b.warm_bars + b.live_bars > 0:
                r["live"] = True                      # caught up; the next bar comes at the next open
            e = eliminated.get(r.get("key"))
            r["program_status"] = "ELIMINATED" if e else "ACTIVE"
            if e:
                r["eliminated"] = e
            rows.append(r)
        live = sum(1 for r in rows if r.get("live"))
        st = state["status"]
        if bots and live == len(bots):
            st = "LIVE" if sess["open"] else "MARKET_CLOSED"
        if any(r.get("error") for r in rows):
            st = "DEGRADED" if live else "ERROR"
        if bots and st == "LIVE" and not market.data_ok():
            st = "DEGRADED"
        now_ms = int(time.time() * 1000)
        return {"status": st, "session_id": session_id, "experiment_id": experiment_id,
                "forward_start_ms": state["forward_start_ms"], "live_from_ms": state["live_from_ms"],
                "boot_ms": boot_ms, "bots": rows, "live_bots": live, "total_bots": len(bots) or len(specs),
                "market": market.health(), "prices": _prices(market, symbols), "jev_state": jev_state,
                "config": config, "eliminated": len(eliminated), "session": sess,
                "sessions_live": sessions_live(now_ms)}

    def flush_store() -> None:
        try:
            if batches["bar"]:
                storage.fwd6_bars_save(batches["bar"])
        except Exception as exc:
            log.warning("could not store market inputs: %s", str(exc)[:160])
        batches["bar"].clear()

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
                batches.setdefault(item[1], []).append(item[2])
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

    def out(key: str, r: dict[str, Any], now_ms: int, why: str) -> None:
        eliminated[key] = {"at": now_ms, "reason": why}
        eliminated_at[key] = now_ms
        emit("eliminated", {"bot_key": key, "role": r.get("role"), "symbol": r.get("symbol"), "at": now_ms,
                            "reason": why, "trades": r.get("trades"), "trades_24h": r.get("trades_24h"),
                            "equity": r.get("equity_live") if r.get("equity_live") is not None else r.get("equity")})

    def eliminate(now_ms: int) -> None:
        """25% down: out at once. Right after a session closes: CONTROLs that traded too little over the last sessions
        are out (their twins with them). Recorded as events, so a restart re-applies them."""
        if t0 is None or market.sessions is None:
            return
        done = [s for s in market.sessions.iv if s[0] >= t0 and s[1] <= now_ms]
        last = done[-1] if done else None
        judge = last is not None and last[1] + JUDGE_AFTER_CLOSE_MS <= now_ms <= last[1] + JUDGE_WINDOW_MS
        since = done[-v9.ELIMINATE_WINDOW_SESSIONS][0] if len(done) >= v9.ELIMINATE_WINDOW_SESSIONS else None
        n_live = sessions_live(now_ms)
        rows = [_live_marks(dict(b.status), market) for b in bots]
        for r in rows:                                   # controls first, then their twins
            key = r.get("key")
            if key in eliminated:
                continue
            n = None
            if judge and since is not None and r.get("role") != "JEV":
                n = sum(1 for t in storage.fwd6_trades(experiment_id, bot_key=key, since_ts=since)
                        if int(t.get("exit_ts") or 0) <= last[1] + JUDGE_AFTER_CLOSE_MS)
            why = v9.should_eliminate(r, n_live, n)
            if not why and r.get("role") == "JEV" and r.get("control_key") in eliminated:
                why = "CONTROL_ELIMINATED"
            if why:
                out(key, r, now_ms, why)

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
        client = JevClient(dataclasses.replace(jcfg, timeout_ms=v9.JEV_TIMEOUT_MS, max_retries=v9.JEV_RETRIES), api_key) \
            if jev_wanted else None
        decider = ClientDeciderV9(client) if client is not None else None
        classes = load_v9()
        settings = v9.settings_v9()
        session_at = lambda t: market.sessions.at(t) if market.sessions is not None else None  # noqa: E731
        for spec in specs:
            base_cls = classes[spec.strategy_id]
            cls = base_cls.for_class(spec.horizon, session_at=session_at)
            bot_info = {**spec.to_dict(), "thesis": base_cls.thesis, "fails_when": base_cls.fails_when,
                        "family": base_cls.family}
            kw = dict(experiment_id=experiment_id, session_id=session_id, bot=bot_info, emit=emit, board=board,
                      coverage=coverage, recorded=recorded.for_bot(spec.key).decisions, live_from_ms=live_from)
            if spec.role == "JEV":
                gate = JevGateV9(eliminated_at=eliminated_at, late_after_ms=v9.LATE_AFTER_MS,
                                 window_ms=v9.DECISION_WINDOW_MS, margin_ms=v9.JEV_DEADLINE_MARGIN_MS,
                                 decide=decider, model=jcfg.model, mid=market.mid, spread=market.spread_for_jev,
                                 builder=JevStateBuilderV9(), **kw)
            else:
                gate = ForwardGateV8(eliminated_at=eliminated_at, late_after_ms=v9.LATE_AFTER_MS, **kw)
            eng = StockReplayEngine(settings, [spec.symbol], rules={spec.symbol: rules[spec.symbol]}, seed=7, funding=None,
                               execution=v9.EXECUTION_V9, fees=v9.FEES_V9, fee_source="schedule",
                               sizing=SizingV6(rules, jev=spec.role == "JEV"), leverage_policy="needed",
                               max_risk_pct=AGGRESSIVE_V6.max_risk_pct, gate=gate, cost_gate=None,
                               quotes=market.quote_half_spread)
            if spec.role == "JEV":
                gate.probe = _Probes(eng).resize
            bots.append(V6Bot(spec=spec, cls=cls, engine=eng, bars=queues[spec.key], since_ms=t0,
                              live_from_ms=live_from, emit=emit, recorded=recorded.for_bot(spec.key),
                              prev_snapshot=prev.get(spec.key), family=base_cls.family, leverage=v9.LEVERAGE_CAP))
        for b in bots:
            b.start()
        storage.fwd6_session_update(session_id, live_ts=live_from, heartbeat_ts=int(time.time() * 1000),
                                    status="LIVE")
        state["status"] = "WARMING_UP"
        live_marked = False
        while not stop_ev.is_set():
            if parent is not None and not parent.is_alive():
                log.warning("parent process is gone; V9 worker stopping")
                break
            drain(0.5)
            now_s = time.time()
            if now_s - last_status >= STATUS_EVERY_S:
                st = status_row()
                out_q.put(("status", st))
                last_status = now_s
                if st["status"] in ("LIVE", "MARKET_CLOSED") and not live_marked:
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
