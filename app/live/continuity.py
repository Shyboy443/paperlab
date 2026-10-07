"""FORWARD EXPERIMENT continuity: one continuous book per bot, however often the server restarts.

A live shadow session used to start every bot at 20 USDT with nothing open. A deploy therefore
dropped in-flight positions without an exit (they were neither closed nor carried), and reset each
book's peak, drawdown and halts -- so the frozen 30% drawdown halt and the 25% floor could never
bind across a restart.

Now a session that belongs to an existing experiment RE-DERIVES the book instead of resetting it:

    warmup     REST 1m history from the experiment's own backfill start (the first session's), so
               every indicator window is exactly what the first session had
    re-derive  the same engine trades the closed 1m tape from the experiment's forward start T0 up
               to this session's go-live: positions opened before the restart are re-opened at the
               same fills and managed on, halts and drawdown carry over
    live       from this session's go-live on, live bars exactly as before

The engine is deterministic on the canonical closed 1m tape (bar-open fills after latency, modelled
spread; the live book is display only), so re-derivation reproduces what earlier sessions recorded.
It is CHECKED, not assumed: every re-derived candidate, open and closed trade is matched against
what earlier sessions recorded (by bot, side and timestamp); a match is not recorded twice, a net
that differs beyond funding noise counts as DIVERGED, and anything recorded that the replay does not
reproduce counts as UNREPRODUCED. Events no session observed (the minutes the server was down) are
recorded once, flagged `rederived`.

Jev is never asked about the past: a re-derived +JEV candidate reuses the decision recorded for it;
one no session observed follows the control (flagged NOT_OBSERVED, not counted as a Jev decision), so
the gap moves neither side of the CONTROL vs +JEV comparison.
"""
from __future__ import annotations

import dataclasses
from typing import Any

MINUTE = 60_000
MODE = "CONTINUOUS_BOOK_V1"
NET_TOLERANCE = 0.005           # USDT: funding estimate vs settled rate on a 20 USDT book


def cand_ident(bot_key: str, side: str, signal_ts: int) -> tuple:
    return ("candidate", bot_key, side, int(signal_ts))


def open_ident(bot_key: str, side: str, ts: int) -> tuple:
    return ("open", bot_key, side, int(ts))


def trade_ident(bot_key: str, side: str, entry_ts: int, counterfactual: bool) -> tuple:
    return ("closed", bot_key, side, int(entry_ts), bool(counterfactual))


@dataclasses.dataclass
class Recorded:
    """What earlier sessions of this experiment recorded, for one bot or for all of them."""
    seen: set = dataclasses.field(default_factory=set)
    nets: dict = dataclasses.field(default_factory=dict)            # trade ident -> recorded net
    decisions: dict = dataclasses.field(default_factory=dict)       # (bot_key, signal_ts, side) -> row

    def for_bot(self, key: str) -> "Recorded":
        return Recorded({i for i in self.seen if i[1] == key},
                        {i: v for i, v in self.nets.items() if i[1] == key},
                        {i: v for i, v in self.decisions.items() if i[0] == key})

    def counts(self) -> dict[str, int]:
        kinds = [i[0] for i in self.seen]
        return {"candidates": kinds.count("candidate"), "opens": kinds.count("open"),
                "trades": kinds.count("closed"), "decisions": len(self.decisions)}


def load_recorded(storage: Any, session_ids: list[str]) -> Recorded:
    rec = Recorded()
    if not session_ids:
        return rec
    for e in storage.shadow_events_for(session_ids, ("candidate", "open")):
        d, key = e["data"], e.get("bot_key") or e["data"].get("bot_key")
        if not key or not d.get("side"):
            continue
        if e["kind"] == "candidate" and d.get("signal_ts") is not None:
            rec.seen.add(cand_ident(key, d["side"], d["signal_ts"]))
        elif e["kind"] == "open" and d.get("ts") is not None:
            rec.seen.add(open_ident(key, d["side"], d["ts"]))
    for t in storage.shadow_trades(counterfactual=None, session_ids=session_ids):
        if t.get("exit_kind") == "session_end":       # a pre-continuity forced exit, not a trade
            continue
        i = trade_ident(t["bot_key"], t["side"], t["entry_ts"], bool(t.get("counterfactual")))
        rec.seen.add(i)
        rec.nets[i] = t.get("net")
    for d in storage.shadow_decisions(session_ids=session_ids):
        rec.decisions[(d["bot_key"], int(d["signal_ts"]), d["side"])] = {
            k: d.get(k) for k in ("id", "final_action", "final_level", "risk_multiplier", "take_probability",
                                  "error_code")}
    return rec


def plan(storage: Any, experiment_id: str, since_ms: int, warmup_ms: int) -> dict[str, Any]:
    """Where this session's book starts. A new experiment starts now; a continuing one starts at
    the experiment's forward start T0, warmed up from the first session's own backfill start."""
    from app.core.shadow_view import experiments
    prior = sorted((s for s in experiments(storage) if s.get("experiment_id") == experiment_id),
                   key=lambda s: s.get("created_ts") or 0)
    if not prior:
        return {"mode": MODE, "forward_from_ms": since_ms,
                "backfill_from_ms": (since_ms - warmup_ms) // MINUTE * MINUTE,
                "resumed_from_sessions": [], "recorded": Recorded()}
    first = prior[0]
    cfg = first.get("config") or {}
    cont = cfg.get("continuity") or {}
    t0 = int(cont.get("forward_from_ms") or cfg.get("go_live_ms") or first.get("created_ts") or since_ms)
    first_warmup = int(float(cfg.get("warmup_hours") or warmup_ms / 3_600_000) * 3_600_000)
    backfill = int(cont.get("backfill_from_ms") or (t0 - first_warmup) // MINUTE * MINUTE)
    sids = [s["session_id"] for s in prior]
    return {"mode": MODE, "forward_from_ms": t0, "backfill_from_ms": backfill,
            "resumed_from_sessions": sids, "recorded": load_recorded(storage, sids)}


def public(plan_: dict[str, Any]) -> dict[str, Any]:
    """What the session config records about its continuity (no Recorded object)."""
    rec = plan_.get("recorded")
    return {"mode": plan_["mode"], "forward_from_ms": plan_["forward_from_ms"],
            "backfill_from_ms": plan_["backfill_from_ms"],
            "resumed_from_sessions": list(plan_.get("resumed_from_sessions") or []),
            "recorded": rec.counts() if rec is not None else {}}


class Tracker:
    """One bot's re-derivation bookkeeping: decides, per event, whether it is live, already
    recorded (then checked, not re-recorded) or re-derived from the tape (recorded once, flagged)."""

    def __init__(self, recorded: Recorded | None, live_from_ms: int):
        self.rec = recorded or Recorded()
        self.live_from_ms = int(live_from_ms)
        self.matched: set = set()
        self.stats = {"rederived": 0, "matched": 0, "diverged": 0, "unreproduced": None}

    def route(self, ident: tuple, ts: int | None, net: float | None = None) -> str:
        """'live' | 'rederived' | 'recorded'."""
        if ts is None or ts >= self.live_from_ms:
            return "live"
        if ident in self.rec.seen:
            if ident not in self.matched:
                self.matched.add(ident)
                self.stats["matched"] += 1
                was = self.rec.nets.get(ident)
                if net is not None and was is not None and abs(float(was) - float(net)) > NET_TOLERANCE:
                    self.stats["diverged"] += 1
            return "recorded"
        self.stats["rederived"] += 1
        return "rederived"

    def handoff(self) -> None:
        """Called once the re-derivation reaches live bars: what was recorded but never reproduced."""
        if self.stats["unreproduced"] is None:
            self.stats["unreproduced"] = len(self.rec.seen - self.matched)
