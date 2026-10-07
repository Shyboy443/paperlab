"""Adapt the existing breakout paper book to the Competition UI contracts."""
from __future__ import annotations

import time

KEY = "BTC-4H-BREAKOUT"
PROGRAM = "video"


def payload(service):
    if service is None:
        return {"ok": True, "experiment": None, "leaderboard": [], "read_only": True}
    b, s = service.bot, service.summary()
    now = int(time.time() * 1000)
    mark = (s.get("quote") or {}).get("price") or (b.history[-1].close if b.history else 0)
    equity = b.cash + b.qty * mark
    positions = []
    if b.entry and b.qty:
        e = b.entry
        positions.append({"bot_key": KEY, "coin": "BTC", "symbol": "BTCUSDT", "side": "long",
                          "qty": b.qty, "entry": e["price"], "entry_ts": e["fill_time"], "mark": mark,
                          "upnl": b.qty * (mark - e["price"]), "stop": None, "target": None})
    trades = [{"symbol": "BTCUSDT", "side": "long", "entry_ts": t["entry_time"],
               "exit_ts": t["exit_time"], "entry_price": t["entry_price"], "exit_price": t["exit_price"],
               "net": t["pnl"], "fees": t["fees"], "qty": t["qty"], "exit_kind": t["exit_reason"]}
              for t in reversed(b.trades)]
    wins = sum(t["pnl"] > 0 for t in b.trades)
    gain = sum(max(0, t["pnl"]) for t in b.trades)
    loss = -sum(min(0, t["pnl"]) for t in b.trades)
    row = {"key": KEY, "strategy_id": KEY, "coin": "BTC", "symbol": "BTCUSDT", "horizon": "4h",
           "role": "CONTROL", "program_status": s["status"], "risk_state": s["status"], "live": s["enabled"],
           "equity_now": equity, "start_equity": b.config.initial_cash, "net_now": equity - b.config.initial_cash,
           "trades": len(b.trades), "trades_24h": sum(t["exit_time"] >= now - 86_400_000 for t in b.trades),
           "win_rate": wins / len(b.trades) if b.trades else None,
           "profit_factor": gain / loss if loss else 999 if gain else None, "max_dd": b.max_dd,
           "fees": s["total_fees"], "funding_net": 0, "open_positions": positions,
           "curve": [[p["ts"], p["equity"]] for p in b.curve[-300:]]}
    # Explicit allow-list: no journal, file paths, credentials, or provider settings in the public payload.
    controls = {k: s[k] for k in ("config", "enabled", "status", "qty", "cash", "can_start", "has_fills",
                                  "entry_high", "exit_low", "quote", "quote_age_seconds", "rules", "execution")}
    signals = [{k: v.get(k) for k in ("signal_time", "side", "expired", "canceled", "suppressed", "reason")}
               for v in s["signals"]]
    fills = [{k: f.get(k) for k in ("fill_time", "observed_ms", "side", "qty", "price", "fee")}
             for f in s["fills"]]
    return {"ok": True, "read_only": True, "experiment": {"experiment_id": KEY}, "program": "Bitcoin breakout",
            "leaderboard": [row], "positions": positions, "activity": [], "trades": trades[:200],
            "hero": {"forward_start_ms": b.curve[0]["ts"] if b.curve else None},
            "breakout": controls, "signals": signals, "fills": fills}


def roster_row(service):
    p = payload(service)
    if not p["leaderboard"]:
        return None
    r = p["leaderboard"][0]
    signals = p["signals"]
    last = signals[0] if signals else None
    return {"program": PROGRAM, "program_label": "Video breakout", "experiment_id": KEY,
            "key": KEY, "strategy_id": KEY, "name": "Bitcoin breakout", "persona": "7-day breakout · 3-day exit",
            "job": p["breakout"]["rules"], "where": "BTCUSDT", "timeframe": "4h", "currency": "USDT",
            "role": "CONTROL", "status": r["program_status"], "live": r["live"],
            "equity": r["equity_now"], "start_equity": r["start_equity"], "net": r["net_now"],
            "return": r["net_now"] / r["start_equity"], "trades": r["trades"], "trades_24h": r["trades_24h"],
            "win_rate": r["win_rate"], "profit_factor": r["profit_factor"], "max_dd": r["max_dd"],
            "fees": r["fees"], "funding": 0, "risk_state": r["risk_state"], "positions": r["open_positions"],
            "curve": r["curve"], "_st": None,
            "last_call": {"signal_ts": last["signal_time"], "symbol": "BTCUSDT", "side": "long",
                          "final_level": "SKIP" if last["expired"] or last["canceled"] or last["suppressed"] else last["side"],
                          "reason": last["suppressed"] or last["reason"] or last["side"]} if last else None}


def candles(service, until=0):
    if service is None:
        return {"ok": True, "candles": [], "positions": [], "markers": []}
    bars = [c for c in service.bot.history if not until or c.open_time <= until]
    p = payload(service)
    boxes = [{"bots": [KEY], "side": "long", "entry_ts": t["entry_ts"], "exit_ts": t["exit_ts"],
              "entry": t["entry_price"], "exit": t["exit_price"], "net": [t["net"]], "stop": None, "target": None}
             for t in p["trades"]]
    return {"ok": True, "candles": [[c.open_time, c.open, c.high, c.low, c.close, c.volume] for c in bars],
            "positions": boxes, "markers": [{"ts": t["exit_ts"], "price": t["exit_price"], "kind": "exit",
                                             "side": "long", "net": t["net"]} for t in p["trades"]]}
