"""The V11 scanners' engine: the shared ReplayEngine plus one pre-trade check (operator's request, 2026-09-30).

A scanner decides on a bar's close and its market order fills at the first 1m open after the 60 s window. A scanner
trades violent moves (capitulations, breakouts), and in that minute the price can run a long way. When it has already
run PAST TP1 (the trade's first profit target), the engine would open the position and hand back TP1 / TP2 at once for
nothing but fees. When it has already run THROUGH THE STOP, the trade would open and stop out at once. Either way the
trade's premise is gone.

So, immediately before a queued order fills, the reference price the fill would use (that bar's open) is compared with
the signal's own levels:

    long   open >= TP1  or  open <= stop    -> skipped: gap_past_tp1 / gap_past_stop
    short  open <= TP1  or  open >= stop    -> skipped

A real bot can run the same check on the live price just before sending its order. A skipped order never fills and is
counted in the engine's rejects.

Exits fill AT THEIR LEVEL (operator, 2026-10-01: "use the best settings and do it"). The shared engine walks each 1m bar
as four prices -- open, adverse extreme, favourable extreme, close -- and fills a stop or a take-profit at whichever of
those prices it is first found at. A stop passed on the way down to the bar's low was filled AT THE LOW (worse than a
real stop order), and a take-profit passed on the way up to the high was filled AT THE HIGH (better than a real
take-profit order). Here, when the price moves between two of those points through the stop or the next take-profit,
the exit engine sees that level itself, so the exit is priced from the level -- and then pays the execution model's
spread and slippage like any exit. A bar that OPENS beyond a level still fills at the open (a gap is a gap). The order
of the walk (adverse before favourable: a bar that touches both is a stop) is unchanged.

The ladder's stop moves the moment a take-profit fills, as a real bot would move it: after TP1 to the scanner's lock
(app/strategies/v11/ladder.tp1_lock: past entry + the fees, or further), after TP2 onto TP1.

Take-profits as RESTING LIMIT orders (`maker_tp`, operator 2026-10-02 "improve Zap"; scripts/v11_zap_study.py): a TP
sits on the book at its price, so it fills at that price at the MAKER fee (0.020%, no spread) instead of a market order
at the taker fee (0.055%) -- but only once price trades THROUGH it by 0.5 bp (a touch is not a fill: the order may be
behind the queue). Stops and time exits stay market orders. Over 90 days this improved every scanner's net R per trade
in both halves (docs/V11_ZAP_STUDY.json).

Everything else is the shared engine, unchanged: its module stays byte-identical, so the V6-V9 freezes that pin it are
untouched; this subclass is pinned by V11's freeze.
"""
from __future__ import annotations

from typing import Any

from app.backtest.replay import ReplayEngine
from app.strategies.v11.ladder import tp1_lock

# Break-even that PAYS THE FEES (operator, 2026-10-01: "these hit TP but show in losses"). After TP1 the stop moves to
# entry + 0.15% (2 x 0.055% taker + spread) instead of entry + 6 bp, so a trade that has hit TP1 cannot end below zero
# after fees (barring slippage through the stop). 90 days, the chosen ladders: R per trade about unchanged
# (docs/V11_EXIT_FIXES.json, BEF), more trades end in profit.
BE_COVER_BPS = 15.0
MAKER_TP_TRADE_THROUGH = 0.00005    # 0.5 bp beyond a resting take-profit before it counts as filled
# Each position remembers, from the signal it opened on, its TP1, how many TPs it has and its after-TP1 lock
# (app/strategies/v11/ladder.LOCK_AFTER_TP1, chosen by scripts/v11_tp1_study.py), so the stop can move the moment a TP
# fills -- not 5 minutes to an hour later at the scanner's decision bar.


def gap_verdict(side: str, open_price: float, sig: Any) -> str | None:
    """Why an order may not fill at `open_price` any more (None: it may)."""
    tps = getattr(sig, "take_profits", None) or []
    stop = getattr(sig, "stop", None)
    long = side == "long"
    if tps:
        tp1 = float(tps[0].price)
        if (long and open_price >= tp1) or (not long and open_price <= tp1):
            return "gap_past_tp1"
    if stop:
        if (long and open_price <= float(stop)) or (not long and open_price >= float(stop)):
            return "gap_past_stop"
    return None


class ScanReplayEngine(ReplayEngine):
    level_fills = True      # False: the shared engine's path-point fills, the lock at the end of the bar (studies compare)

    def __init__(self, *args: Any, be_cover_bps: float | None = None, maker_tp: bool = False, **kw: Any):
        super().__init__(*args, **kw)
        self.maker_tp = bool(maker_tp)
        if be_cover_bps is not None:            # the engine's own break-even move, the instant TP1 prints
            for ex in (self.exits, self.shadow_exits):
                if ex is not None:
                    ex.fee_buffer_bps = float(be_cover_bps)

    def _execute_pending(self, bar: Any, meta: Any, res: Any) -> None:
        if self._pending:
            keep = []
            for order, sig, decision in self._pending:
                due = order.symbol == bar.symbol and bar.close_time >= order.execute_at
                why = gap_verdict(sig.side, float(bar.open), sig) if due else None
                if why:
                    res.rejects[why] = res.rejects.get(why, 0) + 1
                    order.reject(why)
                    continue
                keep.append((order, sig, decision))
            self._pending = keep
        sigs: dict[Any, Any] = {}               # a signal may carry no id: then its coin and side find it
        for _, sig, _ in self._pending:
            sigs[sig.id or (sig.symbol, sig.side)] = sig
        before = len(res.fills)
        super()._execute_pending(bar, meta, res)
        for f in res.fills[before:]:
            pos = self.portfolio.positions.get(f.position_id) if f.kind == "entry" else None
            if pos is not None and pos.take_profits and "tp1" not in pos.meta:
                sig = sigs.get(f.signal_id) if f.signal_id else sigs.get((f.symbol, f.position_side))
                pos.meta["tp1"] = pos.take_profits[0].price
                pos.meta["n_tps"] = len(pos.take_profits)
                if self.maker_tp:                      # a resting limit fills on a trade-THROUGH, not a touch
                    d = 1 if pos.side == "long" else -1
                    for tp in pos.take_profits:
                        tp.price *= 1 + d * MAKER_TP_TRADE_THROUGH
                pos.meta["tp1_lock"] = float(((sig.meta if sig is not None else None) or {}).get("tp1_lock") or 0.0)

    def _walk(self, bar: Any) -> None:
        if not self.level_fills:
            super()._walk(bar)
            for pos in self.portfolio.positions_on(bar.symbol):
                self._ladder_lock(pos)
            return
        for pos in self.portfolio.positions_on(bar.symbol):
            path = self._path(bar, pos.side)
            cur, t0 = path[0]
            self._step(pos, bar, cur, t0)               # the open: a gap through a level fills here
            for nxt, t1 in path[1:]:
                for _ in range(8):                      # every level passed on the way there, nearest first
                    lv = self._level_between(pos, cur, nxt) if pos.qty > 0 else None
                    if lv is None:
                        break
                    ts = t0 + int((t1 - t0) * abs(lv - cur) / abs(nxt - cur))
                    self._step(pos, bar, lv, ts)
                    cur, t0 = lv, ts
                if pos.qty <= 0:
                    break
                self._step(pos, bar, nxt, t1)
                cur, t0 = nxt, t1
        self._now = bar.close_time

    def _close(self, pos: Any, position_id: str, fraction: float, ref_price: float, kind: str, reason: str, bar: Any,
               ts: int) -> Any:
        if self.maker_tp and kind == "tp":         # the resting limit fills at its own price, at the maker fee
            d = 1 if pos.side == "long" else -1
            px = ref_price / (1 + d * MAKER_TP_TRADE_THROUGH)
            return self.portfolio.close_position(position_id, fraction, px, kind, True, reason, ts=ts, fill_price=px,
                                                 maker=True, fill_meta={"liquidity_role": "MAKER",
                                                                        "execution_level": "resting_limit_tp"})
        return super()._close(pos, position_id, fraction, ref_price, kind, reason, bar, ts)

    def _step(self, pos: Any, bar: Any, price: float, ts: int) -> None:
        """One price on the bar's walk: the shared exit engine decides, exits route through the execution model."""
        self._now = ts
        self.ctx.set_price(bar.symbol, price, ts)
        for intent in self.exits.on_price(pos, price, None, ts):
            if pos.qty <= 0:
                break
            self._fills.append(self._close(pos, intent.position_id, intent.fraction, intent.ref_price, intent.kind,
                                           intent.reason, bar, ts))
        self._ladder_lock(pos)
        self._check_halts(pos.strategy_id)

    @staticmethod
    def _level_between(pos: Any, a: float, b: float) -> float | None:
        """The stop or next take-profit the price passes strictly between `a` and `b` (the nearer one), if any."""
        long = pos.side == "long"
        down = b < a
        found = None
        if pos.stop and pos.stop > 0 and down == long and min(a, b) < pos.stop < max(a, b):
            found = pos.stop
        if pos.take_profits:
            tp = float(pos.take_profits[0].price)
            if down != long and min(a, b) < tp < max(a, b) and (found is None or abs(tp - a) < abs(found - a)):
                found = tp
        return found

    @staticmethod
    def _ladder_lock(pos: Any) -> None:
        """After TP1: the stop to the scanner's lock (at least entry + the fees). After TP2: the stop onto TP1."""
        n = pos.meta.get("n_tps")
        if pos.qty <= 0 or not pos.tp1_done or not n or pos.meta.get("tp1") is None:
            return
        done = int(n) - len(pos.take_profits)
        if done < 1 or not pos.take_profits:
            return
        lock = float(pos.meta["tp1"]) if done >= 2 else \
            tp1_lock(pos.entry_price, pos.side, pos.meta["tp1"], float(pos.meta.get("tp1_lock") or 0.0))
        if (lock - pos.stop) * (1 if pos.side == "long" else -1) > 0:
            pos.stop = lock
            pos.meta["stop_kind"] = "be"
