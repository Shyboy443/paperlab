"""V6 FORWARD ARENA payloads: the home screen, /api/public/competition/v6, the V6 block of /public/inspection.json and
the V6 section of the static report (docs/V6_PROTOCOL.md §10).

Everything is rebuilt from allow-listed fields of the live worker's status and the fwd6_* tables. The Jev state
payload never leaves the server here, and there is nothing secret in these tables: no key, no account, no order id.
All results are PAPER (live Bybit data, simulated fills).
"""
from __future__ import annotations

import math
import time
from typing import Any, Iterable, Mapping, Sequence

from app.competition.v6_config import STARTING_BALANCE, maturity

HOUR = 3_600_000
DAY = 24 * HOUR
BOT_FIELDS = ("key", "role", "pair_id", "control_key", "strategy_id", "family", "coin", "symbol", "horizon", "timeframe",
              "live", "last_bar_ts", "equity", "equity_live", "start_equity", "net", "return_pct", "peak", "max_dd",
              "dd_now", "trades", "wins", "win_rate", "expectancy_r", "profit_factor", "gross", "fees", "slippage",
              "funding_paid", "funding_received", "funding_net", "net_closed", "trades_24h", "candidates",
              "candidates_24h", "min_notional_skips", "risk_state", "evaluating", "error")
POSITION_FIELDS = ("symbol", "side", "qty", "entry", "mark", "upnl", "stop", "target", "entry_ts", "time_stop_ts", "leverage",
                   "risk_usd", "risk_pct", "tier", "jev_level", "funding_paid", "funding_received", "tps")
EVENT_FIELDS = ("type", "event", "bot_key", "role", "pair_id", "symbol", "coin", "horizon", "strategy_id", "side",
                "signal_ts", "decision_ms", "price", "stop", "target", "stop_pct", "quality", "setup", "regime", "outcome",
                "tier", "gate", "reason", "qty", "fee", "ts", "risk_pct", "jev_level", "latency_ms", "entry_ts",
                "exit_ts", "entry_price", "exit_price", "gross", "fees", "slippage", "funding", "net", "r", "r_net",
                "exit_kind", "counterfactual", "final_action", "final_level", "p_support", "choice", "move_bps",
                "error_code", "rederived", "forward_start_ms", "live_from_ms", "warmup_s", "bots", "continuity_ok",
                "continuity_checks", "sessions", "error", "hold_s", "resumed", "source", "exit_code", "session_id")


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _r(x: Any, nd: int = 4) -> Any:
    v = _f(x)
    return round(v, nd) if v is not None else None


def _pick(d: Mapping[str, Any] | None, fields: Sequence[str]) -> dict[str, Any]:
    d = d or {}
    return {k: d.get(k) for k in fields if k in d}


def _pct(vals: Iterable[float], q: float) -> float | None:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def age_text(ms: int | float | None) -> str:
    if not ms or ms <= 0:
        return "0m"
    m = int(ms // 60_000)
    d, rem = divmod(m, 1440)
    h, mm = divmod(rem, 60)
    if d >= 3:
        return f"{d}d"
    if d:
        return f"{d}d {h}h"
    return f"{h}h {mm:02d}m" if h else f"{mm}m"


def current_experiment(storage: Any, svc: Any = None) -> dict[str, Any] | None:
    st = (getattr(svc, "status", None) or {}) if svc is not None else {}
    eid = st.get("experiment_id")
    if eid:
        exp = storage.fwd6_experiment(eid)
        if exp:
            return exp
    exps = storage.fwd6_experiments(5)
    return exps[0] if exps else None


def trade_stats(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(trades)
    nets = [float(t.get("net") or 0.0) for t in trades]
    rs = [float(t["r_net"]) for t in trades if t.get("r_net") is not None] if trades and "r_net" in trades[0] else \
        [float(t.get("r") or 0.0) for t in trades]
    win, loss = sum(x for x in nets if x > 0), -sum(x for x in nets if x < 0)
    return {"trades": n, "net": _r(sum(nets)), "gross": _r(sum(float(t.get("gross") or 0.0) for t in trades)),
            "fees": _r(sum(float(t.get("fees") or 0.0) for t in trades)),
            "slippage": _r(sum(float(t.get("slippage") or 0.0) for t in trades)),
            "funding": _r(sum(float(t.get("funding") or 0.0) for t in trades)),
            "wins": sum(1 for x in nets if x > 0), "win_rate": _r(sum(1 for x in nets if x > 0) / n, 3) if n else None,
            "expectancy_r": _r(sum(rs) / len(rs)) if rs else None,
            "profit_factor": _r(win / loss, 3) if loss else (None if not win else 999.0)}


def _r_of(t: Mapping[str, Any]) -> float | None:
    import json
    try:
        d = json.loads(t.get("data_json") or "{}")
    except (TypeError, ValueError):
        d = {}
    v = d.get("r_net")
    return _f(v) if v is not None else _f(t.get("r"))


def jev_block(decisions: Sequence[Mapping[str, Any]], trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Live Jev analytics: actions, latency, market move during latency, selection alpha (candidate-level: the mean
    outcome of what Jev let through minus the mean outcome of everything it judged, taken or shadow) and the ATTACK
    increment (the part of each ATTACK trade's result due to sizing up)."""
    import json
    jd = [d for d in decisions if d.get("role") == "JEV" and d.get("source") == "api"]
    n = len(jd)
    acts: dict[str, int] = {}
    for d in jd:
        acts[d.get("final_level") or "?"] = acts.get(d.get("final_level") or "?", 0) + 1
    lat = [d["latency_ms"] for d in jd if d.get("latency_ms") is not None and not d.get("error_code")]
    moves = [d["move_bps"] for d in jd if d.get("move_bps") is not None]
    errors = [d for d in jd if d.get("error_code")]
    resolved = [d for d in jd if d.get("outcome_r") is not None]
    taken = [d for d in resolved if d.get("final_level") in ("TAKE", "ATTACK")]
    all_r = [float(d["outcome_r"]) for d in resolved]
    tk_r = [float(d["outcome_r"]) for d in taken]
    attack = [t for t in trades if t.get("role") == "JEV" and not t.get("counterfactual") and t.get("jev_level") == "ATTACK"]
    inc = 0.0
    for t in attack:
        try:
            mult = float(json.loads(t.get("data_json") or "{}").get("jev_multiplier") or 0.0)
        except (TypeError, ValueError):
            mult = 0.0
        if mult > 1.0:
            inc += float(t.get("net") or 0.0) * (1.0 - 1.0 / mult)
    return {"decisions": n, "actions": acts, "skip_rate": _r(acts.get("SKIP", 0) / n, 3) if n else None,
            "take_rate": _r(acts.get("TAKE", 0) / n, 3) if n else None,
            "attack_rate": _r(acts.get("ATTACK", 0) / n, 3) if n else None,
            "accepted": acts.get("TAKE", 0) + acts.get("ATTACK", 0), "accepted_resolved": len(taken),
            "errors": len(errors), "error_rate": _r(len(errors) / n, 3) if n else None,
            "error_codes": {c: sum(1 for d in errors if d.get("error_code") == c) for c in {d.get("error_code") for d in errors}},
            "latency_ms": {"p50": _r(_pct(lat, 0.5), 0), "p95": _r(_pct(lat, 0.95), 0), "p99": _r(_pct(lat, 0.99), 0), "n": len(lat)},
            "move_during_latency_bps": {"mean": _r(sum(moves) / len(moves), 3) if moves else None,
                                        "p95": _r(_pct(moves, 0.95), 3)},
            "resolved": len(resolved),
            "selection_alpha_r": _r((sum(tk_r) / len(tk_r)) - (sum(all_r) / len(all_r)), 4) if tk_r and all_r else None,
            "attack_trades": len(attack), "attack_increment_usdt": _r(inc),
            "cost_usd": _r(sum(float(d.get("cost_usd") or 0.0) for d in jd), 5)}


def equity_curve(trades: Sequence[Mapping[str, Any]], start: float, t0: int, now_ms: int,
                 live_equity: float | None, max_points: int = 400) -> list[list[float]]:
    """Realized equity after each closed trade (by exit time) from the forward start, net of every booked cost; the
    last point is the live equity (booked costs plus open positions marked at the mid). Thinned evenly past
    `max_points`, keeping the first and last realized points."""
    pts: list[list[float]] = [[int(t0), round(float(start), 4)]]
    eq = float(start)
    for t in sorted(trades, key=lambda x: int(x.get("exit_ts") or 0)):
        eq += float(t.get("net") or 0.0)
        pts.append([int(t.get("exit_ts") or 0), round(eq, 4)])
    if len(pts) > max_points:
        step = (len(pts) - 1) / (max_points - 1)
        pts = [pts[round(i * step)] for i in range(max_points)]
    if live_equity is not None:
        pts.append([int(now_ms), round(float(live_equity), 4)])
    return pts


def v6_payload(storage: Any, svc: Any = None, now_ms: int | None = None, activity: int = 60) -> dict[str, Any]:
    now_ms = now_ms or int(time.time() * 1000)
    health = svc.health() if svc is not None else {"status": "DISABLED", "enabled": False}
    status = (getattr(svc, "status", None) or {}) if svc is not None else {}
    exp = current_experiment(storage, svc)
    if exp is None:
        return {"ok": True, "status": health, "experiment": None,
                "note": "no V6 forward experiment yet: it starts automatically once V6_FORWARD_ENABLED=true and the "
                        "market is warm"}
    eid = exp["experiment_id"]
    t0 = int(exp.get("forward_start_ms") or 0)
    bots = status.get("bots") if status.get("experiment_id") == eid else None
    from_store = not bots
    if from_store:                                  # the worker is not reporting (yet): the last saved books, not live
        bots = [{**b, "live": False, "evaluating": False} for b in storage.fwd6_bots(eid)]
    trades = storage.fwd6_trades(eid, counterfactual=False)
    for t in trades:
        t["r_net"] = _r_of(t)
    decisions = storage.fwd6_decisions(eid)
    by_bot: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        by_bot.setdefault(t.get("bot_key") or "", []).append(t)
    rows = []
    for b in bots:
        r = _pick(b, BOT_FIELDS)
        eq = _f(b.get("equity_live")) if b.get("equity_live") is not None else _f(b.get("equity"))
        mine = by_bot.get(b.get("key") or "", [])
        if not b.get("live") and mine:
            # warming up after a restart, the book is being re-derived: show the recorded trades, not a half-replayed book
            nets = [float(t.get("net") or 0.0) for t in mine]
            gain, loss = sum(x for x in nets if x > 0), -sum(x for x in nets if x < 0)
            eq = float(b.get("start_equity") or STARTING_BALANCE) + sum(nets)
            r.update(trades=len(nets), wins=sum(1 for x in nets if x > 0), win_rate=sum(1 for x in nets if x > 0) / len(nets),
                     profit_factor=(gain / loss) if loss > 0 else (999.0 if gain > 0 else None))
        r["equity_now"] = _r(eq)
        r["net_now"] = _r((eq or 0.0) - float(b.get("start_equity") or STARTING_BALANCE)) if eq is not None else None
        r["open_positions"] = [_pick(p, POSITION_FIELDS) for p in b.get("open_positions") or []]
        r["maturity"] = maturity(now_ms - t0, int(b.get("trades") or 0))
        # capital growth: this bot's equity after each of its closed trades, ending at its live equity
        r["curve"] = equity_curve(by_bot.get(b.get("key") or "", []), float(b.get("start_equity") or STARTING_BALANCE),
                                  t0, now_ms, eq, max_points=60)
        rows.append(r)
    rows.sort(key=lambda r: (-(r.get("net_now") if r.get("net_now") is not None else -9e9), r["key"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    controls = [r for r in rows if r.get("role") == "CONTROL"]
    jevs = [r for r in rows if r.get("role") == "JEV"]
    positions = []
    for r in rows:
        for p in r["open_positions"]:
            coin = r.get("coin")
            if coin == "ALL" and p.get("symbol"):          # a multi-coin scanner book: the position's own coin
                coin = p["symbol"][:-4] if p["symbol"].endswith("USDT") else p["symbol"]
            positions.append({"bot_key": r["key"], "coin": coin, "horizon": r.get("horizon"),
                              "role": r.get("role"), **p, "hold_s": int((now_ms - int(p.get("entry_ts") or now_ms)) / 1000)})
    positions.sort(key=lambda p: p.get("entry_ts") or 0)
    by_ctl = {r["key"]: r for r in controls}
    pairs = []
    for j in jevs:
        c = by_ctl.get(j.get("control_key") or "")
        pd = [d for d in decisions if d.get("pair_id") == j.get("pair_id")]
        pt = [t for t in trades if t.get("pair_id") == j.get("pair_id")]
        jb = jev_block(pd, pt)
        pairs.append({"pair_id": j.get("pair_id"), "control_key": (c or {}).get("key"), "jev_key": j["key"],
                      "coin": j.get("coin"), "horizon": j.get("horizon"), "strategy_id": j.get("strategy_id"),
                      "control_net": (c or {}).get("net_now"), "jev_net": j.get("net_now"),
                      "delta": _r((j.get("net_now") or 0.0) - ((c or {}).get("net_now") or 0.0)),
                      "control_trades": (c or {}).get("trades"), "jev_trades": j.get("trades"),
                      "decisions": jb["decisions"], "actions": jb["actions"], "selection_alpha_r": jb["selection_alpha_r"],
                      "attack_increment_usdt": jb["attack_increment_usdt"], "latency_p50_ms": jb["latency_ms"]["p50"],
                      "maturity": maturity(now_ms - t0, int(j.get("trades") or 0))})
    ctl_trades = [t for t in trades if t.get("role") == "CONTROL"]
    jev_trades = [t for t in trades if t.get("role") == "JEV"]
    tiers: dict[str, int] = {}
    for t in trades:
        tiers[t.get("tier") or "?"] = tiers.get(t.get("tier") or "?", 0) + 1
    skips: dict[str, int] = {}
    for b in bots:
        for k, v in (b.get("rejects") or {}).items():
            if k.startswith("min_notional") or k.startswith("below_min"):
                skips[k] = skips.get(k, 0) + int(v)
    eq_total = sum(float(r.get("equity_now") or 0.0) for r in rows)
    start_total = sum(float(b.get("start_equity") or STARTING_BALANCE) for b in bots)
    sess = storage.fwd6_sessions(eid)
    fam: dict[str, dict[str, Any]] = {}
    for r in controls:
        k = f"{r.get('strategy_id')} {r.get('horizon')}"
        f = fam.setdefault(k, {"family": k, "bots": 0, "trades": 0, "net": 0.0})
        f["bots"] += 1
        f["trades"] += int(r.get("trades") or 0)
        f["net"] += float(r.get("net_now") or 0.0)
    for f in fam.values():
        f["net"] = _r(f["net"])
        f["maturity"] = maturity(now_ms - t0, f["trades"])
    market = status.get("market") or {}
    hero = {"status": health.get("status"), "live": health.get("status") == "LIVE", "forward_start_ms": t0,
            "forward_age_ms": max(0, now_ms - t0), "forward_age": age_text(now_ms - t0),
            "active_bots": sum(1 for r in rows if r.get("live")), "bots": len(rows), "controls": len(controls),
            "jev_bots": len(jevs), "positions_open": len(positions),
            "trades_24h": sum(1 for t in trades if int(t.get("exit_ts") or 0) >= now_ms - DAY),
            "candidates_24h": storage.fwd6_event_count(eid, "candidate", now_ms - DAY),
            "total_virtual_equity": _r(eq_total, 2), "start_equity_total": _r(start_total, 2),
            "net_pnl": _r(eq_total - start_total, 4),
            "jev_edge": _r(sum(p["delta"] or 0.0 for p in pairs), 4) if pairs else None,
            "maturity": maturity(now_ms - t0, len(ctl_trades)),
            "next_1h_decision_ms": (now_ms // HOUR + 1) * HOUR, "next_4h_decision_ms": (now_ms // (4 * HOUR) + 1) * 4 * HOUR}
    events = storage.fwd6_events(eid, limit=max(1, min(activity, 300)))
    return {
        "ok": True, "status": health, "hero": hero,
        "experiment": {"experiment_id": eid, "forward_start_ms": t0, "created_ts": exp.get("created_ts"),
                       "warmup_from_ms": exp.get("warmup_from_ms"), "manifest": exp.get("manifest_fingerprint"),
                       "identity": exp.get("identity"), "coins": (exp.get("config") or {}).get("coins"),
                       "sessions": len(sess), "sessions_detail": [{k: s.get(k) for k in ("session_id", "created_ts",
                                                                                        "live_ts", "heartbeat_ts",
                                                                                        "ended_ts", "status")}
                                                                  for s in sess[-10:]]},
        "totals": {"controls": trade_stats(ctl_trades), "jev": trade_stats(jev_trades),
                   "fees": _r(sum(float(b.get("fees") or 0.0) for b in bots)),
                   "slippage": _r(sum(float(b.get("slippage") or 0.0) for b in bots)),
                   "unrealized_pnl": _r(sum(float(p.get("upnl") or 0.0) for p in positions)),
                   "funding_paid": _r(sum(float(b.get("funding_paid") or 0.0) for b in bots)),
                   "funding_received": _r(sum(float(b.get("funding_received") or 0.0) for b in bots)),
                   "maker_fees": 0.0, "note": "every V6 order is a MARKET (taker) order: maker fees are 0 by construction"},
        "equity_curve": {
            "controls": equity_curve(ctl_trades, sum(float(b.get("start_equity") or STARTING_BALANCE) for b in bots
                                                     if b.get("role") == "CONTROL"), t0, now_ms,
                                     sum(float(r.get("equity_now") or 0.0) for r in controls) if controls else None),
            "jev": equity_curve(jev_trades, sum(float(b.get("start_equity") or STARTING_BALANCE) for b in bots
                                                if b.get("role") == "JEV"), t0, now_ms,
                                sum(float(r.get("equity_now") or 0.0) for r in jevs)) if jevs else [],
            "note": "equity after each closed trade, net of fees, slippage and funding; the last point is live equity "
                    "including open positions"},
        "leaderboard": rows, "positions": positions, "pairs": pairs, "families": sorted(fam.values(), key=lambda f: f["family"]),
        "jev": {**jev_block(decisions, trades), "state": status.get("jev_state")},
        "risk_distribution": {"trades_by_tier": tiers, "min_notional_skips": skips,
                              "risk_states": {s: sum(1 for r in rows if r.get("risk_state") == s)
                                              for s in {r.get("risk_state") for r in rows}}},
        "stream": {"ws": market.get("ws"), "klines_age_s": market.get("klines_age_s"),
                   "bars_live": market.get("bars_live"), "gaps_repaired": market.get("gaps_repaired"),
                   "rest_errors": market.get("rest_errors"), "barrier_last": market.get("barrier_last"),
                   "barrier_timeouts": market.get("barrier_timeouts"), "positioning_ready": market.get("positioning_ready")},
        "prices": status.get("prices") or {},
        "activity": [{"id": e["id"], "ts": e["ts"], "kind": e["kind"], **_pick(e.get("data"), EVENT_FIELDS)} for e in events],
        "rules": {"maturity": "TOO EARLY / COLLECTING (>= 1 day, 3 trades) / EARLY SIGNAL (>= 7 days, 15 trades) / "
                              "MATURE SAMPLE (>= 30 days, 30 trades); never WINNER",
                  "frozen": "V6 is frozen: no threshold, entry, exit, Jev prompt or sizing changes during the test",
                  "paper": "live Bybit market data, simulated fills; DRY_RUN=true; no real orders"},
    }


def v6_activity(storage: Any, svc: Any = None, limit: int = 100, before_id: int | None = None) -> dict[str, Any]:
    exp = current_experiment(storage, svc)
    if exp is None:
        return {"ok": True, "events": []}
    rows = storage.fwd6_events(exp["experiment_id"], limit=max(1, min(limit, 500)), before_id=before_id)
    return {"ok": True, "experiment_id": exp["experiment_id"],
            "events": [{"id": r["id"], "ts": r["ts"], "kind": r["kind"], **_pick(r.get("data"), EVENT_FIELDS)} for r in rows]}


def v6_bot(storage: Any, key: str, svc: Any = None) -> dict[str, Any]:
    exp = current_experiment(storage, svc)
    if exp is None:
        return {"ok": False, "error": "no V6 experiment"}
    eid = exp["experiment_id"]
    status = (getattr(svc, "status", None) or {}) if svc is not None else {}
    bot = next((b for b in (status.get("bots") or []) if b.get("key") == key), None) or \
        next((b for b in storage.fwd6_bots(eid) if b.get("key") == key), None)
    if bot is None:
        return {"ok": False, "error": f"no V6 bot {key}"}
    trades = storage.fwd6_trades(eid, counterfactual=None, bot_key=key)
    ds = [d for d in storage.fwd6_decisions(eid) if d.get("bot_key") == key]
    return {"ok": True, "experiment_id": eid, "bot": {**_pick(bot, BOT_FIELDS),
                                                       "open_positions": [_pick(p, POSITION_FIELDS) for p in bot.get("open_positions") or []],
                                                       "continuity": bot.get("continuity")},
            "trades": [{k: t.get(k) for k in ("symbol", "side", "entry_ts", "exit_ts", "entry_price", "exit_price", "qty", "gross",
                                              "fees", "slippage", "funding", "funding_paid", "funding_received", "net",
                                              "r", "exit_kind", "risk_pct", "tier", "jev_level", "counterfactual",
                                              "rederived")} for t in trades[-200:]][::-1],
            "decisions": [{k: d.get(k) for k in ("signal_ts", "side", "final_level", "reason", "p_support", "choice",
                                                 "latency_ms", "move_bps", "tier", "legal_min_risk_pct", "quality",
                                                 "error_code", "outcome_kind", "outcome_r", "source")} for d in ds[-200:]][::-1]}


def inspection_block(storage: Any, svc: Any = None) -> dict[str, Any] | None:
    """The V6 block of /public/inspection.json: live state for an outside reviewer."""
    try:
        p = v6_payload(storage, svc, activity=20)
    except Exception as exc:
        return {"error": type(exc).__name__}
    if not p.get("experiment"):
        return {"status": p.get("status"), "note": p.get("note")}
    h = p["hero"]
    lb = p["leaderboard"]
    tot = p["totals"]
    return {"program": "V6 FORWARD ARENA (live Bybit data, paper fills)", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": h["status"], "experiment_id": p["experiment"]["experiment_id"],
            "forward_start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(h["forward_start_ms"] / 1000)),
            "forward_start_ms": h["forward_start_ms"], "forward_age": h["forward_age"], "forward_age_ms": h["forward_age_ms"],
            "maturity": h["maturity"], "active_bots": h["active_bots"], "bots": h["bots"], "controls": h["controls"],
            "jev_bots": h["jev_bots"], "open_positions": p["positions"], "candidates_24h": h["candidates_24h"],
            "trades_24h": h["trades_24h"], "total_virtual_equity": h["total_virtual_equity"], "net_pnl": h["net_pnl"],
            "jev_edge": h["jev_edge"], "closed_trades": {"controls": tot["controls"], "jev": tot["jev"]},
            "gross_pnl": _r((tot["controls"]["gross"] or 0) + (tot["jev"]["gross"] or 0)),
            "fees": tot["fees"], "maker_fees": 0.0,
            "slippage": tot["slippage"], "unrealized_pnl": tot["unrealized_pnl"],
            "cost_scope": "all executed fills, including entries in positions still open; gross_pnl is closed trades only",
            "funding_paid": tot["funding_paid"], "funding_received": tot["funding_received"],
            "top_bots": [{k: r.get(k) for k in ("rank", "key", "role", "coin", "horizon", "trades", "net_now", "expectancy_r",
                                                "profit_factor", "max_dd", "risk_state", "maturity")} for r in lb[:10]],
            "jev_pairs": p["pairs"], "jev": {k: p["jev"].get(k) for k in ("decisions", "actions", "accepted", "latency_ms",
                                                                         "move_during_latency_bps", "selection_alpha_r",
                                                                         "attack_increment_usdt", "error_rate", "state")},
            "risk_distribution": p["risk_distribution"], "stream_health": p["stream"],
            "sessions": p["experiment"]["sessions"], "rules": p["rules"]}
