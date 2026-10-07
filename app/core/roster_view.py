"""THE ROSTER (the home screen): the named contenders across every forward program, and everyone else retired from view.

    contender   not eliminated, at least CONTENDER_MIN_TRADES closed trades, in profit after every cost (fees, spread,
                funding); ranked by return on its own book; the best ROSTER_SIZE. A +JEV / +LADDER twin that matches
                its control trade for trade is shown once (the control). Free places go to bots in profit with
                fewer closed trades (a fresh experiment), flagged "rising"; still fewer than 3: the closest bots
                (>= the minimum trades) fill the stage, flagged "chasing"
    scanners    the V11 multi-coin scanners (PINNED, operator's request 2026-09-30): always on screen in their own
                section, winning or not, ranked among themselves
    retired     every other bot: still paper trading in the background (their experiments are frozen and keep their
                history), just not on the screen; a bot that climbs into profit comes back by itself

Read-only and public-safe: built from the same payloads as /api/public/competition/{program}, cached CACHE_S seconds.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Mapping

from app.core.bot_names import assign, control_of, describe

log = logging.getLogger("paperlab.roster")

CONTENDER_MIN_TRADES = 3
ROSTER_SIZE = 6
CACHE_S = 15.0
PROGRAMS = {"video": "Video breakout", "v14": "V14 HTF", "v13": "V13 Snapback", "v12": "V12 Bizzy", "v11": "V11 Scan", "v8": "V8 Scalp", "v9": "V9 Stocks", "v7": "V7", "v6": "V6"}
CURRENCY = {"v9": "USD"}
PINNED = ("video", "v11", "v12", "v13", "v14")              # always on screen, in their own section
POSITION_KEYS = ("symbol", "side", "qty", "entry", "mark", "upnl", "stop", "target", "entry_ts", "time_stop_ts", "tps")

_cache: dict[str, Any] = {"at": 0.0, "payload": None}
_lock = threading.Lock()


def invalidate():
    """Operator controls must refresh the roster immediately, including a balance change."""
    with _lock:
        _cache.update(at=0.0, payload=None)


def _sources(state: Any) -> dict[str, tuple[Any, Any]]:
    comp = getattr(state, "competition", None)
    return {"v6": (getattr(comp, "storage", None), getattr(state, "v6", None)),
            "v7": (getattr(state, "v7_storage", None), getattr(state, "v7", None)),
            "v8": (getattr(state, "v8_storage", None), getattr(state, "v8", None)),
            "v9": (getattr(state, "v9_storage", None), getattr(state, "v9", None)),
            "v11": (getattr(state, "v11_storage", None), getattr(state, "v11", None)),
            "v12": (getattr(state, "v12_storage", None), getattr(state, "v12", None)),
            "v13": (getattr(state, "v13_storage", None), getattr(state, "v13", None)),
            "v14": (getattr(state, "v14_storage", None), getattr(state, "v14", None))}


def program_payloads(state: Any) -> dict[str, tuple[dict[str, Any], Any]]:
    from app.core.arena_live_view import v8_enrich, v9_enrich, v11_enrich, v12_enrich, v13_enrich, v14_enrich
    from app.core.v6_view import v6_payload
    out = {}
    for prog, (st, svc) in _sources(state).items():
        if st is None:
            continue
        try:
            p = v6_payload(st, svc)
            if prog == "v8":
                p = v8_enrich(p, st)
            elif prog == "v9":
                p = v9_enrich(p, st, svc)
            elif prog == "v11":
                p = v11_enrich(p, st)
            elif prog == "v12":
                p = v12_enrich(p, st)
            elif prog == "v13":
                p = v13_enrich(p, st)
            elif prog == "v14":
                p = v14_enrich(p, st)
        except Exception as exc:                     # one program failing never blanks the roster
            log.warning("roster: %s payload failed: %s", prog, str(exc)[:160])
            continue
        if p.get("experiment"):
            out[prog] = (p, st)
    return out


def _ret(r: Mapping[str, Any]) -> float:
    start = float(r.get("start_equity") or 20.0)
    return float(r.get("net_now") or 0.0) / start if start else 0.0


def _last_call(storage: Any, eid: str, key: str) -> dict[str, Any] | None:
    row = storage.conn.execute(
        "SELECT signal_ts, side, symbol, final_level, reason, p_skip, p_take, p_attack, latency_ms, source, role "
        "FROM fwd6_decisions WHERE experiment_id=? AND bot_key=? ORDER BY signal_ts DESC LIMIT 1", (eid, key)).fetchone()
    return dict(row) if row else None


def _events(storage: Any, eid: str, keys: list[str], limit: int = 160) -> list[dict[str, Any]]:
    if not keys:
        return []
    marks = ",".join("?" for _ in keys)
    rows = storage.conn.execute(
        f"SELECT ts, kind, bot_key, data_json FROM fwd6_events WHERE experiment_id=? AND bot_key IN ({marks}) "
        f"AND kind IN ('open','closed','decision') ORDER BY id DESC LIMIT ?", (eid, *keys, limit)).fetchall()
    out = []
    for r in rows:
        try:
            d = json.loads(r["data_json"] or "{}")
        except ValueError:
            d = {}
        if r["kind"] == "closed" and d.get("counterfactual"):
            continue
        out.append({"ts": int(r["ts"]), "kind": r["kind"], "bot_key": r["bot_key"], "symbol": d.get("symbol"),
                    "side": d.get("side"), "price": d.get("price") or d.get("entry_price"), "net": d.get("net"),
                    "r_net": d.get("r_net"), "exit_kind": d.get("exit_kind"), "final_level": d.get("final_level"),
                    "reason": d.get("reason"), "p_take": d.get("p_take"), "p_skip": d.get("p_skip"),
                    "p_attack": d.get("p_attack"), "p_support": d.get("p_support"), "latency_ms": d.get("latency_ms"),
                    "pair_id": d.get("pair_id"), "signal_ts": d.get("signal_ts"), "open_ts": d.get("ts"),
                    "entry_ts": d.get("entry_ts"), "exit_ts": d.get("exit_ts"), "exit_price": d.get("exit_price")})
    return out


def trade_ideas(events: list[dict[str, Any]], limit: int = 14) -> list[dict[str, Any]]:
    """One row per TRADE IDEA instead of one line per event: a candidate, the decisions every bot of its pair took on
    it (a scanner and its +JEV twin see the same idea), each one's fill and its result. Without this, one idea read as
    three trades on one coin: "Zap TAKE XPL", "Zap AI PASS XPL", "Zap OPEN XPL"."""
    ideas: dict[tuple, dict[str, Any]] = {}

    def slot(e: dict[str, Any], t: int) -> dict[str, Any]:
        k = (e["program"], e.get("pair_id") or e["bot_key"], e.get("symbol"), e.get("side"), t)
        idea = ideas.setdefault(k, {"ts": t, "program": e["program"], "symbol": e.get("symbol"), "side": e.get("side"),
                                    "bots": {}, "last_ts": t})
        return idea

    def bot(idea: dict[str, Any], e: dict[str, Any]) -> dict[str, Any]:
        return idea["bots"].setdefault(e["bot_key"], {"key": e["bot_key"], "name": e.get("name")})

    by_kind = {k: sorted((e for e in events if e["kind"] == k), key=lambda e: e["ts"]) for k in ("decision", "open", "closed")}
    for e in by_kind["decision"]:
        t = int(e.get("signal_ts") or e["ts"]) + 1
        b = bot(slot(e, t), e)
        b.update(verdict=e.get("final_level"), reason=e.get("reason"), p_support=e.get("p_support"))
    opened: dict[tuple, dict[str, Any]] = {}
    for e in by_kind["open"]:
        ts = int(e.get("open_ts") or e["ts"])
        cands = [i for k, i in ideas.items() if k[0] == e["program"] and k[1] == (e.get("pair_id") or e["bot_key"])
                 and k[2] == e.get("symbol") and k[3] == e.get("side") and 0 <= ts - k[4] <= 10 * 60_000]
        idea = max(cands, key=lambda i: i["ts"]) if cands else slot(e, ts)
        b = bot(idea, e)
        b.update(open_price=e.get("price"), open_ts=ts, verdict=b.get("verdict") or "TAKE")
        idea["last_ts"] = max(idea["last_ts"], ts)
        opened[(e["bot_key"], e.get("symbol"), e.get("side"), ts)] = idea
    for e in by_kind["closed"]:
        ets = int(e.get("entry_ts") or 0)
        idea = opened.get((e["bot_key"], e.get("symbol"), e.get("side"), ets)) or slot(e, ets or e["ts"])
        b = bot(idea, e)
        b.update(net=e.get("net"), r_net=e.get("r_net"), exit_kind=e.get("exit_kind"), exit_ts=e.get("exit_ts"),
                 exit_price=e.get("exit_price"), verdict=b.get("verdict") or "TAKE")
        idea["last_ts"] = max(idea["last_ts"], int(e.get("exit_ts") or e["ts"]))
    out = []
    for i in sorted(ideas.values(), key=lambda i: -i["last_ts"])[:limit]:
        bots = sorted(i["bots"].values(), key=lambda b: (b["key"].count("+"), b["key"]))   # the scanner, then its twin
        out.append({**i, "bots": bots})
    return out


def build(state: Any, now_ms: int | None = None) -> dict[str, Any]:
    now_ms = now_ms or int(time.time() * 1000)
    payloads = program_payloads(state)
    from app.video_breakout.competition import roster_row
    breakout = roster_row(getattr(state, "video_breakout", None))
    names = assign([*((prog, control_of(str(r.get("key") or ""))[0]) for prog, (p, _) in payloads.items()
                      for r in p.get("leaderboard") or []),
                    *([("video", str(breakout["key"]))] if breakout else [])])      # sorts last: shifts no other name
    rows: list[dict[str, Any]] = []
    for prog, (p, st) in payloads.items():
        eid = p["experiment"]["experiment_id"]
        for r in p.get("leaderboard") or []:
            rows.append({"program": prog, "program_label": PROGRAMS[prog], "experiment_id": eid,
                         "currency": CURRENCY.get(prog, "USDT"), **describe(prog, r, names),
                         "key": r.get("key"), "role": r.get("role") or "CONTROL", "strategy_id": r.get("strategy_id"),
                         "status": r.get("program_status") or "ACTIVE", "live": bool(r.get("live")),
                         "equity": r.get("equity_now"), "start_equity": r.get("start_equity"), "net": r.get("net_now"),
                         "return": round(_ret(r), 5), "trades": int(r.get("trades") or 0),
                         "trades_24h": int(r.get("trades_24h") or 0), "win_rate": r.get("win_rate"),
                         "profit_factor": r.get("profit_factor"), "max_dd": r.get("max_dd"), "fees": r.get("fees"),
                         "funding": r.get("funding_net"), "risk_state": r.get("risk_state"),
                         "positions": [{k: q.get(k) for k in POSITION_KEYS} for q in r.get("open_positions") or []],
                         "curve": r.get("curve") or [], "_st": st})
    if breakout:
        breakout = {**breakout, "name": names.get(("video", str(breakout["key"]))) or breakout.get("name")}
        rows.append(breakout)
    by_key = {(x["program"], x["key"]): x for x in rows}

    def twin_of_control(x: dict[str, Any]) -> bool:
        ctl, role = control_of(str(x["key"]))
        c = by_key.get((x["program"], ctl))
        return role != "CONTROL" and c is not None and c["trades"] == x["trades"] and \
            abs(float(c["net"] or 0) - float(x["net"] or 0)) < 1e-9
    pool = [x for x in rows if x["status"] != "ELIMINATED" and x["trades"] >= CONTENDER_MIN_TRADES
            and not twin_of_control(x) and x["program"] not in PINNED]
    pool.sort(key=lambda x: (-x["return"], -x["trades"], x["key"]))
    winners = [x for x in pool if (x["net"] or 0) > 0]
    stage = winners[:ROSTER_SIZE]
    for x in stage:
        x["chasing"] = x["rising"] = False
    # A fresh experiment (a re-freeze restarts every program) has almost no bot with the minimum trades yet: bots in
    # profit with fewer closed trades take the free places, after the real contenders, flagged "rising".
    rising = [x for x in rows if x["status"] != "ELIMINATED" and 0 < x["trades"] < CONTENDER_MIN_TRADES
              and (x["net"] or 0) > 0 and not twin_of_control(x) and x["program"] not in PINNED]
    rising.sort(key=lambda x: (-x["return"], -x["trades"], x["key"]))
    for x in rising[:max(0, ROSTER_SIZE - len(stage))]:
        x["chasing"] = x["rising"] = True
        stage.append(x)
    if len(stage) < 3:
        for x in pool:
            if len(stage) >= 3:
                break
            if x not in stage:
                x["chasing"], x["rising"] = True, False
                stage.append(x)
    leader = stage[0]["return"] if stage else 0.0
    for i, x in enumerate(stage, 1):
        x["rank"] = i
        x["behind"] = round(leader - x["return"], 5)
        if x["_st"] is not None:
            x["last_call"] = _last_call(x["_st"], x["experiment_id"], x["key"])
    scanners = sorted((x for x in rows if x["program"] in PINNED), key=lambda x: (-x["return"], x["key"]))
    top = scanners[0]["return"] if scanners else 0.0
    for i, x in enumerate(scanners, 1):
        x["rank"], x["chasing"], x["rising"], x["pinned"] = i, False, False, True
        x["behind"] = round(top - x["return"], 5)
        if x["_st"] is not None:
            x["last_call"] = _last_call(x["_st"], x["experiment_id"], x["key"])
    # V6.2 / V6.6 bots whose two-year backtest made money: the ones the operator may take live (app/live/v6_golive.py),
    # always on screen in their own section, best backtest first, so they can be found by name.
    from app.live.v6_golive import eligible as golive_eligible, backtest as golive_backtest
    ready = []
    for x in rows:
        if x["program"] == "v6" and (x["program"], x["key"]) not in {(y["program"], y["key"]) for y in stage}:
            ok, _ = golive_eligible({"key": x["key"], "strategy_id": x["strategy_id"], "role": x["role"],
                                     "risk_state": x["risk_state"]})
            if ok:
                x["backtest"] = {k: (golive_backtest(x["key"]) or {}).get(k) for k in
                                 ("return_pct", "net", "trades", "profit_factor", "max_dd_pct", "year1", "year2")}
                ready.append(x)
    ready.sort(key=lambda x: (-(x["backtest"]["return_pct"] or 0.0), x["key"]))
    for i, x in enumerate(ready, 1):
        x["rank"], x["chasing"], x["rising"], x["pinned"], x["ready"] = i, False, False, True, True
        x["behind"] = 0.0
        x["last_call"] = _last_call(x["_st"], x["experiment_id"], x["key"])
    shown = stage + scanners + ready
    on_stage = {(x["program"], x["key"]) for x in shown}
    stream: list[dict[str, Any]] = []
    for prog in {x["program"] for x in shown}:
        mine = [x for x in shown if x["program"] == prog]
        if prog == "video":
            from app.video_breakout.competition import payload as breakout_payload
            book = breakout_payload(getattr(state, "video_breakout", None))
            for t in book["trades"]:
                stream.append({"program": prog, "name": breakout["name"], "bot_key": breakout["key"],
                               "kind": "closed", "ts": t["exit_ts"], "price": t["entry_price"],
                               "exit_price": t["exit_price"], "exit_kind": t["exit_kind"], **t})
            for f in book["fills"]:
                if f["side"] == "BUY":
                    stream.append({"program": prog, "name": breakout["name"], "bot_key": breakout["key"],
                                   "kind": "open", "ts": f["fill_time"], "open_ts": f["fill_time"],
                                   "symbol": "BTCUSDT", "side": "long", "price": f["price"]})
            continue
        for e in _events(mine[0]["_st"], mine[0]["experiment_id"], [x["key"] for x in mine]):
            who = by_key[(prog, e["bot_key"])]
            stream.append({**e, "program": prog, "name": who["name"]})
    stream.sort(key=lambda e: -e["ts"])
    ideas = trade_ideas(stream)
    retired = [{k: x.get(k) for k in ("program", "program_label", "key", "name", "persona", "where", "status", "net",
                                      "return", "trades", "currency")}
               for x in sorted(rows, key=lambda x: (list(PROGRAMS).index(x["program"]), -x["return"], x["key"]))
               if (x["program"], x["key"]) not in on_stage]
    for x in rows:
        x.pop("_st", None)
    usd = lambda xs, f: round(sum(float(x.get(f) or 0.0) for x in xs), 4)  # noqa: E731
    return {"ok": True, "as_of_ms": now_ms, "roster": stage, "scanners": scanners, "ready": ready, "retired": retired,
            "stream": stream[:40], "ideas": ideas,
            "names": {f"{x['program']}|{x['key']}": {"name": x["name"], "persona": x["persona"]} for x in rows},
            "kpis": {"bots_total": len(rows), "on_stage": len(stage), "scanners": len(scanners), "retired": len(retired),
                     "in_profit": len(winners) + len(rising), "roster_net": usd(stage, "net"), "roster_fees": usd(stage, "fees"),
                     "roster_funding": usd(stage, "funding"), "roster_trades_24h": sum(x["trades_24h"] for x in stage),
                     "positions_open": sum(len(x["positions"]) for x in stage),
                     "all_net": usd(rows, "net"), "all_trades": sum(x["trades"] for x in rows),
                     "programs": {p: PROGRAMS[p] for p in [*payloads, *(["video"] if breakout else [])]}},
            "rule": {"min_trades": CONTENDER_MIN_TRADES, "size": ROSTER_SIZE,
                     "text": f"On stage: bots in profit after every cost with at least {CONTENDER_MIN_TRADES} closed "
                             f"trades, best {ROSTER_SIZE} by return; free places go to newer bots in profit "
                             "(\"new\"). Everyone else is retired from view but keeps "
                             "paper trading, and comes back on stage if it climbs into profit."},
            "paper": "simulated fills on live market data; no real orders"}


def roster_payload(state: Any) -> dict[str, Any]:
    with _lock:
        if _cache["payload"] is not None and time.time() - _cache["at"] < CACHE_S:
            return _cache["payload"]
    payload = build(state)
    with _lock:
        _cache.update(at=time.time(), payload=payload)
    return payload
