"""Which V6 bots the operator may take live: the V6.2 momentum and V6.6 trend-follower CONTROL bots whose two-year
backtest made money (operator, 2026-10-06: "add go live button for V6.6 and V6.2 bots").

V6 has no QUALIFIED status of its own: its forward test trades a few times a month, so a live record takes many months.
The evidence here is the two-year replay of each bot through its own frozen code (scripts/backtest_2y.py,
docs/BACKTEST_2Y_SUMMARY.json): fees, funding, the live sizing and the live 30%-below-peak stop.

A bot is eligible only while all of these hold:
- its strategy is V6.2 or V6.6;
- it is a CONTROL bot (no +JEV twin);
- its backtest ended in profit;
- its live paper book is not halted.

The live mirror re-checks this at every entry, exactly as it re-checks a V8 bot's QUALIFIED status. Going live still
needs the operator: a typed "GO LIVE <bot>", an amount and loss limits. Nothing here places an order, and the V6
paper experiment itself is unchanged (frozen, DRY_RUN).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

STRATEGIES = ("V6.2", "V6.6")
SUMMARY = Path(__file__).resolve().parents[2] / "docs" / "BACKTEST_2Y_SUMMARY.json"
HALTED = ("HALTED", "FLOOR_HALT", "DAILY_HALT")
_cache: dict[str, Any] = {}


def backtest(key: str) -> dict[str, Any] | None:
    """The bot's two-year backtest under the live rules (None: not backtested)."""
    if "bots" not in _cache:
        try:
            data = json.loads(SUMMARY.read_text(encoding="utf-8"))
            _cache["bots"] = {f"{b['program']}|{b['key']}": b for b in data.get("bots") or []}
        except (OSError, ValueError):
            _cache["bots"] = {}
    return _cache["bots"].get(f"v6|{key}")


def eligible(row: Mapping[str, Any]) -> tuple[bool, str]:
    key = str(row.get("key") or "")
    if row.get("strategy_id") not in STRATEGIES:
        return False, "only V6.2 and V6.6 bots can go live"
    if (row.get("role") or "CONTROL") != "CONTROL":
        return False, "only CONTROL bots can go live (not the +JEV twin)"
    bt = backtest(key)
    if bt is None:
        return False, "no two-year backtest for this bot"
    if (bt.get("net") or 0.0) <= 0:
        return False, f"its two-year backtest lost money ({bt.get('return_pct'):+.1f}%)"
    if row.get("risk_state") in HALTED:
        return False, f"its paper book is {row.get('risk_state')}"
    return True, f"two-year backtest {bt.get('return_pct'):+.1f}%"


def annotate(row: dict[str, Any]) -> dict[str, Any]:
    """Adds `golive` (eligible, why, the backtest numbers) to a V6 leaderboard row. Read-only and public-safe."""
    ok, why = eligible(row)
    bt = backtest(str(row.get("key") or "")) or {}
    row["golive"] = {"eligible": ok, "why": why,
                     "backtest": {k: bt.get(k) for k in ("return_pct", "net", "trades", "win_rate", "profit_factor",
                                                         "max_dd_pct", "year1", "year2")} if bt else None}
    return row


def mirror_row(storage: Any, svc: Any, key: str) -> dict[str, Any] | None:
    """The live mirror's view of one V6 bot: its row of the V6 payload (built from the main paperlab.db storage, as the
    public V6 page builds it), marked QUALIFIED only when eligible."""
    from app.core.v6_view import v6_payload
    rows = v6_payload(storage, svc).get("leaderboard") or []
    return mirror_view(next((r for r in rows if r.get("key") == key), None))


def mirror_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """The row the live mirror checks: an eligible bot reads as QUALIFIED, anything else as ACTIVE."""
    if row is None:
        return None
    ok, _ = eligible(row)
    return {**row, "program_status": "QUALIFIED" if ok else "ACTIVE"}
