"""Program-agnostic arena payload helpers for the dashboard: candlesticks from a forward experiment's stored 1m bars
with its trades as markers and position boxes, and the V8 bot statuses (ACTIVE / QUALIFIED / ELIMINATED). Read-only;
nothing secret."""
from __future__ import annotations

import json
import time
from typing import Any

TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def ladder_rungs(program: str | None, bot_key: str) -> tuple[float, ...] | None:
    """The take-profit ladder (in R) a bot trades with, when it has one: the V11 scanners and V8's +LADDER twins."""
    try:
        if program == "v11":
            from app.strategies.v11.ladder import RUNGS
            return tuple(RUNGS.get(bot_key.split("-")[0]) or ()) or None
        if program == "v8" and bot_key.endswith("+LADDER"):
            from app.strategies.v8.ladder import LADDER
            return tuple(r for r, _ in LADDER)
    except Exception:
        return None
    return None


def fill_ladder(box: dict[str, Any], rungs: tuple[float, ...] | None) -> None:
    """A position recorded before its open event carried the whole ladder: rebuild TP1..TPn from its stop and TP1. The
    signal price P satisfies TP1 = P + r1 (P - stop), so every rung is P + r (P - stop)."""
    if box.get("tps") or not rungs or box.get("stop") is None or box.get("target") is None:
        return
    stop, tp1, r1 = float(box["stop"]), float(box["target"]), float(rungs[0])
    p = (tp1 + r1 * stop) / (1.0 + r1)
    box["tps"] = [p + r * (p - stop) for r in rungs]


def candles(storage: Any, experiment: dict[str, Any] | None, symbol: str, tf: str = "5m", limit: int = 300,
            now_ms: int | None = None, program: str | None = None) -> dict[str, Any]:
    """OHLCV buckets of `tf` aggregated from the experiment's stored 1m bars, plus that program's closed and open-entry
    markers on the coin. Bars are the ones the bots actually traded on (live, or backfilled at warm-up)."""
    step = TF_MS.get(tf)
    if step is None:
        return {"ok": False, "error": "tf must be one of " + ", ".join(TF_MS)}
    limit = max(10, min(int(limit), 1000))
    now_ms = now_ms or int(time.time() * 1000)
    start = (now_ms // step - limit + 1) * step
    out = _buckets(storage.fwd6_bars(symbol, start), step)
    if len(out) < limit:            # a market that closes (stocks): the last `limit` buckets that exist, not a clock window
        out = _buckets(storage.fwd6_bars(symbol, start - 14 * 86_400_000), step)[-limit:]
        if out:
            start = out[0][0]
    markers: list[dict[str, Any]] = []
    if experiment:
        for t in storage.fwd6_trades(experiment["experiment_id"], counterfactual=False, since_ts=start):
            if t.get("symbol") != symbol:
                continue
            markers.append({"ts": int(t["entry_ts"]), "kind": "entry", "side": t.get("side"), "price": t.get("entry_price"),
                            "bot_key": t.get("bot_key")})
            markers.append({"ts": int(t["exit_ts"]), "kind": "exit", "side": t.get("side"), "price": t.get("exit_price"),
                            "bot_key": t.get("bot_key"), "net": t.get("net"), "exit_kind": t.get("exit_kind")})
    markers.sort(key=lambda m: m["ts"])
    boxes = closed_boxes(storage, experiment, symbol, start) if experiment else []
    for b in boxes:
        fill_ladder(b, ladder_rungs(program, (b.get("bots") or [""])[0]))
    res = {"ok": True, "symbol": symbol, "tf": tf, "candles": out, "markers": markers[-300:], "positions": boxes[-150:],
           "note": "aggregated from the 1m bars the bots traded on; markers and position boxes are paper fills"}
    if program == "v11":                            # how much each TP closes, per scanner (the chart's TP labels)
        from app.strategies.v11.ladder import SHARES
        res["ladder_shares"] = {k: list(v) for k, v in SHARES.items()}
    return res


def _buckets(rows: Any, step: int) -> list[list[float]]:
    out: list[list[float]] = []
    for r in rows:
        b = int(r["open_time"]) // step * step
        o, hi, lo, c, v = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"] or 0)
        if out and out[-1][0] == b:
            k = out[-1]
            k[2], k[3], k[4], k[5] = max(k[2], hi), min(k[3], lo), c, k[5] + v
        else:
            out.append([b, o, hi, lo, c, v])
    return out


def _open_levels(storage: Any, experiment_id: str, since_ms: int) -> dict[str, dict[str, Any]]:
    """The stop and target each position opened with (from its recorded "open" event), by position id and by
    (bot, side, fill time)."""
    out: dict[str, dict[str, Any]] = {}
    rows = storage.conn.execute("SELECT data_json FROM fwd6_events WHERE experiment_id=? AND kind='open' AND ts>=?",
                                (experiment_id, int(since_ms))).fetchall()
    for (raw,) in rows:
        try:
            d = json.loads(raw or "{}")
        except ValueError:
            continue
        lv = {"stop": d.get("stop"), "target": d.get("target"), "qty": d.get("qty"), "risk_usd": d.get("risk_usd"),
              "tps": d.get("tps")}
        if d.get("position_id"):
            out["pid:" + str(d["position_id"])] = lv
        if d.get("bot_key") and d.get("ts"):
            out[f"{d['bot_key']}|{d.get('side')}|{int(d['ts'])}"] = lv
    return out


def closed_boxes(storage: Any, experiment: dict[str, Any], symbol: str, since_ms: int) -> list[dict[str, Any]]:
    """One box per closed paper position on the coin (entry to exit, stop and target), a CONTROL and its +JEV twin
    merged when they took the same fill."""
    eid = experiment["experiment_id"]
    levels = _open_levels(storage, eid, since_ms - 7 * 86_400_000)
    boxes: dict[tuple, dict[str, Any]] = {}
    for t in storage.fwd6_trades(eid, counterfactual=False, since_ts=since_ms):
        if t.get("symbol") != symbol or t.get("entry_price") is None:
            continue
        try:
            extra = json.loads(t.get("data_json") or "{}")
        except ValueError:
            extra = {}
        entry, side = float(t["entry_price"]), t.get("side")
        lv = levels.get("pid:" + str(extra.get("position_id"))) or \
            levels.get(f"{t.get('bot_key')}|{side}|{int(t['entry_ts'])}") or {}
        stop, target = lv.get("stop"), lv.get("target")
        sp, tr = extra.get("stop_pct"), extra.get("target_r")
        if stop is None and sp:                      # older rows: rebuild from the recorded stop % and target R
            stop = entry * (1 - float(sp)) if side == "long" else entry * (1 + float(sp))
        if target is None and stop is not None and tr:
            target = entry + (entry - float(stop)) * float(tr)
        key = (side, int(t["entry_ts"]), round(entry, 10), int(t["exit_ts"]))
        b = boxes.get(key)
        if b is None:
            boxes[key] = {"side": side, "entry_ts": int(t["entry_ts"]), "exit_ts": int(t["exit_ts"]), "entry": entry,
                          "stop": stop, "target": target, "tps": lv.get("tps") or None,
                          "exit": t.get("exit_price"), "exit_kind": t.get("exit_kind"),
                          "bots": [t.get("bot_key")], "net": [t.get("net")], "r": t.get("r")}
        else:
            b["bots"].append(t.get("bot_key"))
            b["net"].append(t.get("net"))
    missing = [b for b in boxes.values() if b.get("stop") is None]
    if missing:                                      # recorded without levels: the decision's own stop and first target
        cands = _candidate_levels(storage, eid, symbol, since_ms - 86_400_000)
        for b in missing:
            for bot in b["bots"]:
                best = None
                for sig_ts, stop, target in cands.get((bot, b["side"]), ()):
                    if sig_ts <= b["entry_ts"] and b["entry_ts"] - sig_ts <= 10 * 60_000:
                        best = (stop, target)
                if best:
                    b["stop"], b["target"] = best
                    break
    return sorted(boxes.values(), key=lambda b: b["entry_ts"])


def _candidate_levels(storage: Any, experiment_id: str, symbol: str, since_ms: int) -> dict[tuple, list]:
    """(bot, side) -> [(signal_ts, stop, first target)] from the recorded candidates on the coin, oldest first."""
    out: dict[tuple, list] = {}
    rows = storage.conn.execute("SELECT data_json FROM fwd6_events WHERE experiment_id=? AND kind='candidate' AND symbol=? "
                                "AND ts>=? ORDER BY id", (experiment_id, symbol, int(since_ms))).fetchall()
    for (raw,) in rows:
        try:
            d = json.loads(raw or "{}")
        except ValueError:
            continue
        if d.get("stop") is None or d.get("signal_ts") is None:
            continue
        out.setdefault((d.get("bot_key"), d.get("side")), []).append((int(d["signal_ts"]), d["stop"], d.get("target")))
    return out


def v8_enrich(payload: dict[str, Any], storage: Any, now_ms: int | None = None) -> dict[str, Any]:
    """Add each V8 bot's status (ACTIVE / QUALIFIED / ELIMINATED) and the counts to the arena payload."""
    from app.competition import v8_config as v8
    from app.live.v8_worker import load_eliminated
    x = payload.get("experiment")
    if not x:
        return payload
    now_ms = now_ms or int(time.time() * 1000)
    age = max(0, now_ms - int(x.get("forward_start_ms") or now_ms))
    gone = load_eliminated(storage, x["experiment_id"])
    counts = {"ACTIVE": 0, "QUALIFIED": 0, "ELIMINATED": 0}
    for r in payload.get("leaderboard") or []:
        status, detail = v8.bot_status(r, age, gone.get(r.get("key")))
        r["program_status"], r["status_detail"] = status, detail
        counts[status] += 1
    hero = payload.get("hero") or {}
    hero.update({"eliminated": counts["ELIMINATED"], "qualified": counts["QUALIFIED"], "active": counts["ACTIVE"]})
    payload["qualification"] = {"rule": v8.QUALIFY, "elimination": {"after_h": v8.ELIMINATE_AFTER_H,
                                "min_trades_24h": v8.ELIMINATE_MIN_TRADES_24H,
                                "min_trades_24h_by_family": v8.ELIMINATE_MIN_TRADES_24H_BY_FAMILY,
                                "drawdown": v8.ELIMINATE_DRAWDOWN}}
    return payload


def v11_enrich(payload: dict[str, Any], storage: Any, now_ms: int | None = None) -> dict[str, Any]:
    """V11's bot statuses (ACTIVE / QUALIFIED / ELIMINATED), the universe, and the coins each scanner has traded."""
    from app.competition import v11_config as v11
    from app.live.v11_worker import load_eliminated
    x = payload.get("experiment")
    if not x:
        return payload
    now_ms = now_ms or int(time.time() * 1000)
    age = max(0, now_ms - int(x.get("forward_start_ms") or now_ms))
    gone = load_eliminated(storage, x["experiment_id"])
    counts = {"ACTIVE": 0, "QUALIFIED": 0, "ELIMINATED": 0}
    traded: dict[str, dict[str, int]] = {}
    for t in storage.fwd6_trades(x["experiment_id"], counterfactual=False):
        c = (t.get("symbol") or "").replace("USDT", "")
        if c:
            per = traded.setdefault(t.get("bot_key") or "", {})
            per[c] = per.get(c, 0) + 1
    for r in payload.get("leaderboard") or []:
        status, detail = v11.bot_status(r, age, gone.get(r.get("key")))
        r["program_status"], r["status_detail"] = status, detail
        r["coins_traded"] = dict(sorted(traded.get(r.get("key") or "", {}).items(), key=lambda kv: -kv[1]))
        counts[status] += 1
    hero = payload.get("hero") or {}
    hero.update({"eliminated": counts["ELIMINATED"], "qualified": counts["QUALIFIED"], "active": counts["ACTIVE"]})
    payload["universe"] = [s.replace("USDT", "") for s in v11.UNIVERSE]
    payload["qualification"] = {"rule": v11.QUALIFY, "elimination": {"after_h": v11.ELIMINATE_AFTER_H,
                                "min_trades_24h": v11.ELIMINATE_MIN_TRADES_24H, "drawdown": v11.ELIMINATE_DRAWDOWN}}
    return payload


def v12_enrich(payload: dict[str, Any], storage: Any, now_ms: int | None = None) -> dict[str, Any]:
    """V12 Bizzy: bot statuses (ACTIVE / QUALIFIED; never eliminated), her coins, and the coins each bot traded."""
    from app.competition import v12_config as v12
    x = payload.get("experiment")
    if not x:
        return payload
    now_ms = now_ms or int(time.time() * 1000)
    age = max(0, now_ms - int(x.get("forward_start_ms") or now_ms))
    counts = {"ACTIVE": 0, "QUALIFIED": 0}
    traded: dict[str, dict[str, int]] = {}
    for t in storage.fwd6_trades(x["experiment_id"], counterfactual=False):
        c = (t.get("symbol") or "").replace("USDT", "")
        if c:
            per = traded.setdefault(t.get("bot_key") or "", {})
            per[c] = per.get(c, 0) + 1
    for r in payload.get("leaderboard") or []:
        status, detail = v12.bot_status(r, age)
        r["program_status"], r["status_detail"] = status, detail
        r["coins_traded"] = dict(sorted(traded.get(r.get("key") or "", {}).items(), key=lambda kv: -kv[1]))
        counts[status] += 1
    hero = payload.get("hero") or {}
    hero.update({"eliminated": 0, "qualified": counts["QUALIFIED"], "active": counts["ACTIVE"]})
    payload["universe"] = [s.replace("USDT", "") for s in v12.TRADE_COINS]
    payload["qualification"] = {"rule": v12.QUALIFY, "elimination": "none"}
    return payload


def v14_enrich(payload: dict[str, Any], storage: Any, now_ms: int | None = None) -> dict[str, Any]:
    """V14 HTF: bot statuses (ACTIVE / QUALIFIED; never eliminated), the coins traded, and each bot's original."""
    from app.competition import v14_config as v14
    x = payload.get("experiment")
    if not x:
        return payload
    now_ms = now_ms or int(time.time() * 1000)
    age = max(0, now_ms - int(x.get("forward_start_ms") or now_ms))
    counts = {"ACTIVE": 0, "QUALIFIED": 0}
    traded: dict[str, dict[str, int]] = {}
    for t in storage.fwd6_trades(x["experiment_id"], counterfactual=False):
        c = (t.get("symbol") or "").replace("USDT", "")
        if c:
            per = traded.setdefault(t.get("bot_key") or "", {})
            per[c] = per.get(c, 0) + 1
    specs = {s.key: s for s in v14.field_plan()}
    for r in payload.get("leaderboard") or []:
        status, detail = v14.bot_status(r, age)
        r["program_status"], r["status_detail"] = status, detail
        r["coins_traded"] = dict(sorted(traded.get(r.get("key") or "", {}).items(), key=lambda kv: -kv[1]))
        spec = specs.get(r.get("key") or "")
        if spec is not None:
            r["copies"] = dict(zip(("program", "key"), v14.original_key(spec)))
        counts[status] += 1
    hero = payload.get("hero") or {}
    hero.update({"eliminated": 0, "qualified": counts["QUALIFIED"], "active": counts["ACTIVE"]})
    payload["universe"] = [s.replace("USDT", "") for s in v14.UNIVERSE if s != "BTCUSDT"]
    payload["qualification"] = {"rule": v14.QUALIFY, "elimination": "none"}
    return payload


def v13_enrich(payload: dict[str, Any], storage: Any, now_ms: int | None = None) -> dict[str, Any]:
    """V13 Snapback: bot statuses (ACTIVE / QUALIFIED; never eliminated), the coins it can trade, the coins it traded."""
    from app.competition import v13_config as v13
    x = payload.get("experiment")
    if not x:
        return payload
    now_ms = now_ms or int(time.time() * 1000)
    age = max(0, now_ms - int(x.get("forward_start_ms") or now_ms))
    counts = {"ACTIVE": 0, "QUALIFIED": 0}
    traded: dict[str, dict[str, int]] = {}
    for t in storage.fwd6_trades(x["experiment_id"], counterfactual=False):
        c = (t.get("symbol") or "").replace("USDT", "")
        if c:
            per = traded.setdefault(t.get("bot_key") or "", {})
            per[c] = per.get(c, 0) + 1
    for r in payload.get("leaderboard") or []:
        status, detail = v13.bot_status(r, age)
        r["program_status"], r["status_detail"] = status, detail
        r["coins_traded"] = dict(sorted(traded.get(r.get("key") or "", {}).items(), key=lambda kv: -kv[1]))
        counts[status] += 1
    hero = payload.get("hero") or {}
    hero.update({"eliminated": 0, "qualified": counts["QUALIFIED"], "active": counts["ACTIVE"]})
    payload["universe"] = [s.replace("USDT", "") for s in v13.UNIVERSE if s != "BTCUSDT"]
    payload["qualification"] = {"rule": v13.QUALIFY, "elimination": "none"}
    return payload


def v9_enrich(payload: dict[str, Any], storage: Any, svc: Any = None) -> dict[str, Any]:
    """V9's bot statuses (ACTIVE / QUALIFIED / ELIMINATED, age counted in completed sessions) and the market session."""
    from app.competition import v9_config as v9
    from app.live.v8_worker import load_eliminated
    x = payload.get("experiment")
    status = (getattr(svc, "status", None) or {}) if svc is not None else {}
    hero = payload.get("hero") or {}
    session = status.get("session") or {}
    hero.update({"currency": "USD", "market_open": session.get("open"), "session_close_ms": session.get("close_ms"),
                 "next_open_ms": session.get("next_open_ms")})
    if not x:
        return payload
    n = int(status.get("sessions_live") or 0)
    gone = load_eliminated(storage, x["experiment_id"])
    counts = {"ACTIVE": 0, "QUALIFIED": 0, "ELIMINATED": 0}
    for r in payload.get("leaderboard") or []:
        st, detail = v9.bot_status(r, n, gone.get(r.get("key")))
        r["program_status"], r["status_detail"] = st, detail
        counts[st] += 1
    hero.update({"eliminated": counts["ELIMINATED"], "qualified": counts["QUALIFIED"], "active": counts["ACTIVE"],
                 "sessions_live": n})
    if session.get("open"):
        now_ms = int(time.time() * 1000)
        hero["next_decision_ms"] = (now_ms // 300_000 + 1) * 300_000
    payload["qualification"] = {"rule": v9.QUALIFY, "elimination": {"window_sessions": v9.ELIMINATE_WINDOW_SESSIONS,
                                "min_trades_window": v9.ELIMINATE_MIN_TRADES_WINDOW, "drawdown": v9.ELIMINATE_DRAWDOWN}}
    return payload
