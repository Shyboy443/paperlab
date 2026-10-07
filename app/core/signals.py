"""SignalBoard: the last signal per strategy and the per-symbol vote board used by S20.

Every signal (approved / rejected / shadow / stale / warmup / exit) is posted here by the engine.
Votes are per symbol: the newest ENTRY signal per contributor within a TTL counts, an EXIT voids it.
Each vote is stamped `source = live|shadow` from the contributor's CURRENT enabled flag; S20 applies
the live-vote gate itself (see s20_ensemble_vote.py).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from app.core.types import Signal


@dataclass(slots=True)
class Vote:
    strategy_id: str
    symbol: str
    side: str
    ts: int
    stop: float
    tf: str
    signal_id: str
    source: str  # live | shadow
    status: str

    def to_dict(self, now_ms: int) -> dict[str, Any]:
        return {"strategy_id": self.strategy_id, "side": self.side, "age_s": max(0, (now_ms - self.ts) // 1000),
                "stop": self.stop, "source": self.source, "status": self.status, "tf": self.tf}


@dataclass(slots=True)
class SignalRecord:
    signal: Signal
    status: str
    reason: str
    ts: int

    def to_dict(self) -> dict[str, Any]:
        s = self.signal
        return {"id": s.id, "ts": self.ts, "strategy_id": s.strategy_id, "symbol": s.symbol, "kind": s.kind,
                "side": s.side, "tf": s.tf, "entry_price": s.entry_price, "stop": s.stop,
                "tps": [[tp.price, tp.fraction] for tp in s.take_profits], "size_mult": s.size_mult,
                "leg": s.leg, "status": self.status, "reason": self.reason or s.reason, "meta": s.meta}


class SignalBoard:
    def __init__(self, keep: int = 500) -> None:
        self._recent: deque[SignalRecord] = deque(maxlen=keep)
        self._last: dict[str, SignalRecord] = {}
        self._last_entry: dict[tuple[str, str], SignalRecord] = {}  # (strategy, symbol) -> newest entry
        self._voided: dict[tuple[str, str], int] = {}  # (strategy, symbol) -> ts of the exit that voided it
        self._enabled: dict[str, bool] = {}

    # -- state maintained by the engine ---------------------------------------------
    def set_enabled(self, strategy_id: str, enabled: bool) -> None:
        self._enabled[strategy_id] = enabled

    def is_enabled(self, strategy_id: str) -> bool:
        return self._enabled.get(strategy_id, False)

    def reset(self) -> None:
        self._recent.clear()
        self._last.clear()
        self._last_entry.clear()
        self._voided.clear()

    # -- posting -----------------------------------------------------------------------
    def post(self, sig: Signal, status: str, reason: str = "", ts: int | None = None) -> SignalRecord:
        rec = SignalRecord(signal=sig, status=status, reason=reason, ts=ts if ts is not None else sig.ts)
        self._recent.append(rec)
        self._last[sig.strategy_id] = rec
        key = (sig.strategy_id, sig.symbol)
        if sig.kind == "entry":
            self._last_entry[key] = rec
            self._voided.pop(key, None)
        else:
            self._voided[key] = rec.ts
        return rec

    # -- queries -------------------------------------------------------------------
    def last(self, strategy_id: str) -> SignalRecord | None:
        return self._last.get(strategy_id)

    def last_entry(self, strategy_id: str, symbol: str) -> SignalRecord | None:
        return self._last_entry.get((strategy_id, symbol))

    def votes(self, symbol: str, contributors: Sequence[str] | Iterable[str], ttl_ms: int, now_ms: int) -> list[Vote]:
        out: list[Vote] = []
        for sid in contributors:
            rec = self._last_entry.get((sid, symbol))
            if rec is None:
                continue
            voided_at = self._voided.get((sid, symbol))
            if voided_at is not None and voided_at >= rec.ts:
                continue
            if now_ms - rec.ts > ttl_ms:
                continue
            s = rec.signal
            out.append(Vote(strategy_id=sid, symbol=symbol, side=s.side, ts=rec.ts, stop=s.stop, tf=s.tf,
                            signal_id=s.id, source="live" if self.is_enabled(sid) else "shadow",
                            status=rec.status))
        return out

    def recent(self, n: int = 200, strategy_id: str | None = None) -> list[SignalRecord]:
        items = [r for r in self._recent if strategy_id is None or r.signal.strategy_id == strategy_id]
        return items[-n:][::-1]
