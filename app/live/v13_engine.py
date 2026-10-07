"""V13's engine: the V11 scan engine (level exits, maker take-profits) plus RESTING LIMIT ENTRIES (post-only, maker).

A V13 signal carries meta["limit"] (its entry price) and meta["expire_ms"]. The engine queues it like any order (the
decision's 60 s latency still applies: the order reaches the book at the first 1m bar after the window) and then:

    placement   on the first bar it is live, a buy limit at or above that bar's OPEN (a sell limit at or below it)
                would cross the spread and fill as a TAKER -- a post-only order is REJECTED instead
                (reject "post_only_would_cross"): the trade is missed, never bought at market
    fill        on any bar while it is live, when the price trades THROUGH the limit by TRADE_THROUGH (0.5 bp; a
                touch is not a fill, the order may be behind the queue): filled AT THE LIMIT, at the maker fee, no
                spread or slippage; a bar that opens beyond the limit (a gap) also fills at the limit
    expiry      not filled by expire_ms: cancelled (reject "limit_expired")
    slots       when it fills, the coin must still be free and the book under its max positions, else it is cancelled
                (reject "limit_slot_taken") -- resting orders are not positions when they are placed

On the bar it fills, the exits are walked from the FILL, not from the bar's open: the price passed the limit on its way
to the adverse extreme, so the walk is limit -> adverse extreme -> favourable extreme -> close (a stop on the way down
the same bar is hit; a take-profit before the fill is not). Every other position, and every later bar, uses the scan
engine's level-exact walk unchanged. This module is new: no frozen module changes (V13's freeze pins it).
"""
from __future__ import annotations

from typing import Any

from app.live.scan_engine import MAKER_TP_TRADE_THROUGH, ScanReplayEngine

TRADE_THROUGH = 0.00005


class LimitEntryEngineV13(ScanReplayEngine):
    def __init__(self, *args: Any, trade_through: float = TRADE_THROUGH, **kw: Any):
        super().__init__(*args, **kw)
        self.trade_through = float(trade_through)

    def _reject(self, order: Any, res: Any, why: str) -> None:
        res.rejects[why] = res.rejects.get(why, 0) + 1
        order.reject(why)

    def _execute_pending(self, bar: Any, meta: Any, res: Any) -> None:
        resting, market = [], []
        if self._pending:
            for item in self._pending:
                order, sig, decision = item
                limit = (sig.meta or {}).get("limit")
                if limit is None:
                    market.append(item)                  # a market order: the scan engine's own path below
                    continue
                if order.symbol != bar.symbol or bar.close_time < order.execute_at:
                    resting.append(item)
                    continue
                limit = float(limit)
                long = sig.side == "long"
                if bar.open_time >= int(sig.meta.get("expire_ms") or 0):
                    self._reject(order, res, "limit_expired")
                    continue
                if not order.meta.get("placed"):
                    order.meta["placed"] = True
                    if (long and bar.open <= limit) or (not long and bar.open >= limit):
                        self._reject(order, res, "post_only_would_cross")
                        continue
                hit = bar.low <= limit * (1 - self.trade_through) if long else bar.high >= limit * (1 + self.trade_through)
                if not hit:
                    resting.append(item)
                    continue
                held = list(self.portfolio.positions_of(meta.id))
                if any(p.symbol == sig.symbol for p in held) or len(held) >= meta.max_positions:
                    self._reject(order, res, "limit_slot_taken")
                    continue
                self._now = max(bar.open_time, order.execute_at)
                if not self._entry_at_fill(order, sig, decision, meta, res, limit, maker=True):
                    continue
                self._fill_limit(order, sig, decision, bar, limit, res)
        self._pending = market                           # resting limits never reach the market-order path
        super()._execute_pending(bar, meta, res)
        self._pending = resting + self._pending

    def _fill_limit(self, order: Any, sig: Any, decision: Any, bar: Any, limit: float, res: Any) -> None:
        ts = max(bar.open_time, order.execute_at)
        self._now = ts
        sizing = order.meta.get("sizing") or {}
        pos, fill = self.portfolio.open_position(
            sig, decision, limit, True, ts=ts, fill_price=limit, maker=True, qty=decision.qty,
            fill_meta={**sizing, "requested_qty": order.qty, "order_state": "FILLED", "execution_level": "resting_limit_entry",
                       "level_reason": "post-only limit traded through", "liquidity_role": "MAKER", "latency_cost": 0.0,
                       "spread_cost": 0.0, "impact_cost": 0.0, "latency_ms": ts - order.signal_ts})
        self._fills.append(fill)
        did = sizing.get("decision_id")
        if did:
            res.entry_links[pos.id] = did
        if pos.take_profits and "tp1" not in pos.meta:
            pos.meta["tp1"] = pos.take_profits[0].price
            pos.meta["n_tps"] = len(pos.take_profits)
            if self.maker_tp:
                d = 1 if pos.side == "long" else -1
                for tp in pos.take_profits:
                    tp.price *= 1 + d * MAKER_TP_TRADE_THROUGH
            pos.meta["tp1_lock"] = float((sig.meta or {}).get("tp1_lock") or 0.0)
        pos.meta["limit_fill_bar"] = bar.open_time
        pos.meta["limit_fill_ts"] = ts

    # -- replay-only closes, priced on the position's OWN coin --------------------------------------------------------
    # The shared engine closes what is still open at a book reset or at the end of a replay with the LAST bar it saw.
    # On a multi-coin tape that is the anchor's (BTC's) bar, and the execution model then prices an alt's exit with
    # BTC-sized slippage (found 2026-10-03: a USELESS long "closed" at -1.31). Live trading never takes these paths;
    # studies do. Here each position is closed against its own coin's last bar, and the end of a replay is labelled
    # end_of_run (not "time") so a study can leave it out.
    def _own_bar(self, symbol: str) -> Any:
        return super()._own_bar(symbol)

    def _flatten(self, sid: str) -> None:
        for pos in list(self.portfolio.positions_of(sid)):
            price, bar = self.ctx.prices.get(pos.symbol), self._own_bar(pos.symbol)
            if price and bar is not None:
                self._fills.append(self._close(pos, pos.id, 1.0, price, "end_of_run", "end of replay", bar, self._now))

    def _reset_book(self, sid: str, balance: float, res: Any, ts: int) -> None:
        for pos in list(self.portfolio.positions_of(sid)):
            price, bar = self.ctx.prices.get(pos.symbol), self._own_bar(pos.symbol)
            if price and bar is not None:
                self._fills.append(self._close(pos, pos.id, 1.0, price, "reset", "walk-forward window reset", bar, ts))
        super()._reset_book(sid, balance, res, ts)

    def _walk(self, bar: Any) -> None:
        fresh = [p for p in self.portfolio.positions_on(bar.symbol) if p.meta.get("limit_fill_bar") == bar.open_time]
        if not fresh or not self.level_fills:
            super()._walk(bar)
            return
        for pos in self.portfolio.positions_on(bar.symbol):
            path = self._path(bar, pos.side)
            if pos.meta.get("limit_fill_bar") == bar.open_time:   # start at the fill, not at the bar's open
                path = [(float(pos.entry_price), int(pos.meta.get("limit_fill_ts") or bar.open_time))] + path[1:]
                pos.meta.pop("limit_fill_bar", None)
            cur, t0 = path[0]
            self._step(pos, bar, cur, t0)
            for nxt, t1 in path[1:]:
                for _ in range(8):
                    lv = self._level_between(pos, cur, nxt) if pos.qty > 0 else None
                    if lv is None:
                        break
                    ts = t0 + int((t1 - t0) * abs(lv - cur) / abs(nxt - cur)) if nxt != cur else t0
                    self._step(pos, bar, lv, ts)
                    cur, t0 = lv, ts
                if pos.qty <= 0:
                    break
                self._step(pos, bar, nxt, t1)
                cur, t0 = nxt, t1
        self._now = bar.close_time
