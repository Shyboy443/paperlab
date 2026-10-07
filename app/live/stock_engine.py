"""V9 stock paper execution with level crossings, real opening gaps and limit targets.

The shared crypto replay intentionally walks OHLC extremes. Stock stops must not
fill at a later adverse extreme, and resting targets must not earn the later high.
This isolated subclass preserves the conservative adverse-first ordering, charges
market friction on stops/time exits and requires trade-through for a limit fill.
"""
from typing import Any

from app.backtest.replay import ReplayEngine

TRADE_THROUGH = 0.00005  # A touch alone is insufficient evidence of a limit fill.


class StockReplayEngine(ReplayEngine):
    def _execute_pending(self, bar: Any, meta: Any, res: Any) -> None:
        keep = []
        for order, sig, decision in self._pending:
            close_ms = (sig.meta or {}).get('stock_session_close_ms')
            if order.symbol == bar.symbol and close_ms and bar.open_time >= close_ms - 60_000:
                order.reject('stock_session_ended')
                res.rejects['stock_session_ended'] = res.rejects.get('stock_session_ended', 0) + 1
            else:
                keep.append((order, sig, decision))
        self._pending = keep
        signals = {sig.id or (sig.symbol, sig.side): sig for _, sig, _ in self._pending}
        before = len(res.fills)
        super()._execute_pending(bar, meta, res)
        for fill in res.fills[before:]:
            pos = self.portfolio.positions.get(fill.position_id) if fill.kind == 'entry' else None
            if pos is None:
                continue
            sig = signals.get(fill.signal_id) if fill.signal_id else signals.get((fill.symbol, fill.position_side))
            close_ms = (sig.meta or {}).get('stock_session_close_ms') if sig is not None else None
            if close_ms:
                pos.meta['stock_session_close_ms'] = close_ms
                deadline = int(close_ms) - 60_000
                pos.max_hold_deadline = min(pos.max_hold_deadline or deadline, deadline)
            if pos.take_profits:
                pos.meta['stock_target_price'] = pos.take_profits[0].price
                d = 1 if pos.side == 'long' else -1
                pos.take_profits[0].price *= 1 + d * TRADE_THROUGH

    @staticmethod
    def _level_between(pos: Any, a: float, b: float) -> float | None:
        if a == b:
            return None
        long = pos.side == 'long'
        levels = []
        if pos.stop > 0 and (b < a) == long and min(a, b) < pos.stop < max(a, b):
            levels.append(pos.stop)
        if pos.take_profits:
            tp = pos.take_profits[0].price
            if (b < a) != long and min(a, b) < tp < max(a, b):
                levels.append(tp)
        return min(levels, key=lambda p: abs(p - a)) if levels else None

    def _step(self, pos: Any, bar: Any, price: float, ts: int) -> None:
        self._now = ts
        self.ctx.set_price(bar.symbol, price, ts)
        for intent in self.exits.on_price(pos, price, None, ts):
            if pos.qty <= 0:
                break
            self._fills.append(self._close(pos, intent.position_id, intent.fraction,
                                           intent.ref_price, intent.kind, intent.reason, bar, ts))
        self._check_halts(pos.strategy_id)

    def _walk(self, bar: Any) -> None:
        for pos in self.portfolio.positions_on(bar.symbol):
            path = self._path(bar, pos.side)
            cur, t0 = path[0]
            self._step(pos, bar, cur, t0)  # A stop gapped at the open fills at that open.
            for nxt, t1 in path[1:]:
                for _ in range(8):
                    level = self._level_between(pos, cur, nxt) if pos.qty > 0 else None
                    if level is None:
                        break
                    ts = t0 + int((t1 - t0) * abs(level - cur) / abs(nxt - cur))
                    self._step(pos, bar, level, ts)
                    cur, t0 = level, ts
                if pos.qty <= 0:
                    break
                self._step(pos, bar, nxt, t1)
                cur, t0 = nxt, t1
        self._now = bar.close_time

    def _close(self, pos: Any, position_id: str, fraction: float, ref_price: float,
               kind: str, reason: str, bar: Any, ts: int) -> Any:
        if kind == 'tp' and pos.meta.get('stock_target_price') is not None:
            px = float(pos.meta['stock_target_price'])
            return self.portfolio.close_position(position_id, fraction, px, kind, True, reason,
                                                 ts=ts, fill_price=px, maker=True,
                                                 fill_meta={'liquidity_role': 'MAKER',
                                                            'execution_level': 'stock_resting_limit_tp'})
        return super()._close(pos, position_id, fraction, ref_price, kind, reason, bar, ts)

    def _shadow_execute(self, bar: Any) -> None:
        if self.shadow is None:
            return
        keep = []
        for order, sig, decision, did in self._shadow_pending:
            close_ms = (sig.meta or {}).get('stock_session_close_ms')
            if order.symbol == bar.symbol and close_ms and bar.open_time >= close_ms - 60_000:
                order.reject('stock_session_ended')
                rejects = self._res.shadow_rejects
                rejects['stock_session_ended'] = rejects.get('stock_session_ended', 0) + 1
            else:
                keep.append((order, sig, decision, did))
        self._shadow_pending = keep
        signals = {sig.id or (sig.symbol, sig.side): sig for _, sig, _, _ in self._shadow_pending}
        previous = set(self.shadow.positions)
        super()._shadow_execute(bar)
        for pid in set(self.shadow.positions) - previous:
            pos = self.shadow.positions[pid]
            sig = signals.get(pos.signal_id) if pos.signal_id else signals.get((pos.symbol, pos.side))
            close_ms = (sig.meta or {}).get('stock_session_close_ms') if sig is not None else None
            if close_ms:
                pos.max_hold_deadline = min(pos.max_hold_deadline or close_ms - 60_000, close_ms - 60_000)
            if pos.take_profits:
                pos.meta['stock_target_price'] = pos.take_profits[0].price
                d = 1 if pos.side == 'long' else -1
                pos.take_profits[0].price *= 1 + d * TRADE_THROUGH

    def _shadow_step(self, pos: Any, bar: Any, price: float, ts: int) -> None:
        for intent in self.shadow_exits.on_price(pos, price, None, ts):
            if pos.qty <= 0:
                break
            self._shadow_close(pos, intent.position_id, intent.fraction, intent.ref_price,
                               intent.kind, intent.reason, bar, ts)

    def _shadow_walk(self, bar: Any) -> None:
        if self.shadow is None:
            return
        for pos in self.shadow.positions_on(bar.symbol):
            path = self._path(bar, pos.side)
            cur, t0 = path[0]
            self._shadow_step(pos, bar, cur, t0)
            for nxt, t1 in path[1:]:
                for _ in range(8):
                    level = self._level_between(pos, cur, nxt) if pos.qty > 0 else None
                    if level is None:
                        break
                    ts = t0 + int((t1 - t0) * abs(level - cur) / abs(nxt - cur))
                    self._shadow_step(pos, bar, level, ts)
                    cur, t0 = level, ts
                if pos.qty <= 0:
                    break
                self._shadow_step(pos, bar, nxt, t1)
                cur, t0 = nxt, t1

    def _shadow_close(self, pos: Any, position_id: str, fraction: float, ref_price: float,
                      kind: str, reason: str, bar: Any, ts: int) -> None:
        if kind == 'tp' and pos.meta.get('stock_target_price') is not None:
            px = float(pos.meta['stock_target_price'])
            self.shadow.close_position(position_id, fraction, px, kind, True, reason,
                                       ts=ts, fill_price=px, maker=True)
            return
        super()._shadow_close(pos, position_id, fraction, ref_price, kind, reason, bar, ts)
