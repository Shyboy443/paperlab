"""V14 rechecks completed HTF context before queued market or resting limit fills.

The signal-time filter cannot authorize an entry indefinitely. Cancel the order
if the regime changes or the hourly feed becomes stale while it waits. The fill
bar's close/high/low never enters the HTF decision. Original engines are untouched.
"""
from app.strategies.v14.htf import allows, htf_view, rule_profile
from app.live.scan_engine import ScanReplayEngine
from app.live.v8_engine import LevelMakerEngineV8
from app.live.v13_engine import LimitEntryEngineV13


class HTFFillGuard:
    def _execute_pending(self, bar, meta, res):
        keep = []
        for order, sig, decision in self._pending:
            if order.symbol != bar.symbol or bar.close_time < order.execute_at:
                keep.append((order, sig, decision))
                continue
            policy = rule_profile()["policies"].get(sig.strategy_id)
            # The prior bar has completed at the order's earliest fill instant.
            asof = bar.open_time - 1
            view = htf_view(self.ctx, sig.symbol, bar.open, asof)
            if policy is None or not allows(view, sig.side, policy):
                reason = "htf_fill_missing_or_stale" if view is None else "htf_fill_regime_veto"
                order.reject(reason)
                res.rejects[reason] = res.rejects.get(reason, 0) + 1
                continue
            order.meta.setdefault("sizing", {}).update(
                htf=sig.meta.get("htf"), htf_fill={**view, "policy": policy, "decision_ms": asof})
            keep.append((order, sig, decision))
        self._pending = keep
        super()._execute_pending(bar, meta, res)
        self.ctx._v14_pending_symbols = frozenset(order.symbol for order, _, _ in self._pending)


class HTFScalpEngine(HTFFillGuard, LevelMakerEngineV8):
    pass


class HTFScanEngine(HTFFillGuard, ScanReplayEngine):
    pass


class HTFLimitEngine(HTFFillGuard, LimitEntryEngineV13):
    pass
