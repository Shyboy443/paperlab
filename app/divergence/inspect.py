"""Chart-inspectable backtest of the frozen divergence rule on long history (read-only, paper only).

Every CONFIRMED signal is replayed on its own with app/divergence/backtest.backtest: entry at the next 15m open with
0.02% adverse slippage, 0.04% fees per side, real funding, stop first when a candle touches both, target = 2 x the
actual entry-to-stop distance (1:2 reward/risk), open trades marked at the last close. R = net P&L / planned risk.
"""
from __future__ import annotations

import time

from .backtest import backtest
from .model import RULE_VERSION, _study, clean_bars

KEEP = ("id", "symbol", "side", "divergence_type", "regular", "price_relation", "rsi_relation", "divergence_tf",
        "trigger_tf", "divergence_at", "pivot_at", "pivot1", "pivot2", "zone", "stop", "atr", "swing_gap_atr", "checks",
        "trigger_level", "trigger_candle", "confirmed_at", "entry_reference", "target", "status", "reason", "volume_ratio")


def backtest_report(bars, funding, symbol, days=None):
    rows = clean_bars(bars)
    if not rows:
        return {"status": "NO_DATA", "symbol": symbol, "trades": []}
    _, signals, _ = _study(rows, symbol)
    trades, skipped = [], []
    for s in signals:
        out = backtest(rows, [s], funding, start_equity=1000, start_ms=s["confirmed_at"])
        if not out["trades"]:
            skipped.append({"id": s["id"], "reason": out["skipped"][0]["reason"] if out["skipped"] else "no fill"})
            continue
        t = out["trades"][0]
        risk = t["quantity"] * abs(t["entry"] - t["stop"])
        outcome = {"entry_t": t["entry_t"], "entry": t["entry"], "stop": t["stop"], "target": t["target"],
                   "exit_t": t["exit_t"], "exit": t["exit"],
                   "reason": "open" if t["reason"] == "period_end" else t["reason"],
                   "r": t["net"] / risk if risk else None, "fees": t["fees"], "funding": t["funding"],
                   "holding_hours": t["holding_hours"]}
        trades.append({**{k: s.get(k) for k in KEEP}, "outcome": outcome})
    closed = [t["outcome"]["r"] for t in trades if t["outcome"]["reason"] != "open" and t["outcome"]["r"] is not None]
    allr = [t["outcome"]["r"] for t in trades if t["outcome"]["r"] is not None]
    peak = cum = dd = 0.0
    for r in allr:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    wins, losses = sum(r for r in allr if r > 0), -sum(r for r in allr if r < 0)
    by_type = {}
    for t in trades:
        k = f"{t['divergence_tf']} {t['divergence_type']}"
        b = by_type.setdefault(k, {"trades": 0, "wins": 0, "total_r": 0.0})
        b["trades"] += 1
        b["wins"] += (t["outcome"]["r"] or 0) > 0
        b["total_r"] += t["outcome"]["r"] or 0
    return {"status": "READY", "symbol": symbol, "rule_version": RULE_VERSION, "reward_risk": 2.0,
            "start": rows[0]["t"], "end": rows[-1]["t"] + 15 * 60_000, "bars_15m": len(rows), "days": days,
            "computed_ms": int(time.time() * 1000),
            "summary": {"trades": len(trades), "closed": len(closed), "open": len(trades) - len(closed),
                        "wins": sum(r > 0 for r in allr), "win_rate": sum(r > 0 for r in allr) / len(allr) if allr else None,
                        "total_r": sum(allr), "avg_r": sum(allr) / len(allr) if allr else None,
                        "profit_factor": wins / losses if losses else None, "max_drawdown_r": dd,
                        "best_r": max(allr, default=None), "worst_r": min(allr, default=None),
                        "by_type": by_type, "skipped": len(skipped)},
            "trades": trades, "skipped": skipped,
            "method": "Every confirmed signal replayed on its own: next 15m open, 0.02% slippage and 0.04% fee per side, "
                      "real funding, stop first, 2R target from the actual entry, open trades marked at the last close."}
