"""Virtual book: per-strategy wallets and isolated virtual positions. Pure logic, no I/O.

* Every fill is a MARKET fill at `ref_price ± slippage_bps` and pays the taker fee (paper).
* Margin = notional / virtual leverage is reserved from the strategy wallet.
* The book is authoritative; the exchange only ever sees the NET per symbol (see paper_router).
* `replay()` rebuilds wallets and closed trades from persisted fills and open positions from snapshots.
"""
from __future__ import annotations

import random
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from app.core.types import Fill, MarketRules, RiskDecision, Signal, TakeProfit, VirtualPosition, Wallet


@dataclass(slots=True)
class ClosedTrade:
    position_id: str
    strategy_id: str
    symbol: str
    side: str
    qty: float
    entry_price: float
    exit_price: float
    entry_ts: int
    exit_ts: int
    pnl: float
    fees: float
    net: float
    r_multiple: float
    exit_kind: str
    leg: str | None = None
    simulated: bool = True

    @property
    def hold_s(self) -> int:
        return max(0, (self.exit_ts - self.entry_ts) // 1000)

    def to_dict(self) -> dict[str, Any]:
        return {"position_id": self.position_id, "strategy_id": self.strategy_id, "symbol": self.symbol,
                "side": self.side, "qty": self.qty, "entry_price": self.entry_price, "exit_price": self.exit_price,
                "entry_ts": self.entry_ts, "exit_ts": self.exit_ts, "pnl": self.pnl, "fees": self.fees,
                "net": self.net, "r": self.r_multiple, "exit_kind": self.exit_kind, "leg": self.leg,
                "hold_s": self.hold_s, "simulated": self.simulated}


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class Portfolio:
    def __init__(self, settings: Any, rules: dict[str, MarketRules], clock: Callable[[], int],
                 rng: random.Random | None = None):
        self.settings = settings
        self.rules = rules
        self.clock = clock
        self.wallets: dict[str, Wallet] = {}
        self.positions: dict[str, VirtualPosition] = {}
        self.closed_trades: deque[ClosedTrade] = deque(maxlen=5000)
        self.epoch: int = 1
        self.fixed_slippage_bps: float | None = None
        self._rng = rng or random.Random()

    # -- wallets -------------------------------------------------------------------------
    def ensure_wallet(self, strategy_id: str, allocation: float) -> Wallet:
        w = self.wallets.get(strategy_id)
        if w is None:
            w = Wallet(strategy_id=strategy_id, allocation=float(allocation))
            self.wallets[strategy_id] = w
        return w

    def set_allocation(self, strategy_id: str, allocation: float) -> Wallet:
        w = self.ensure_wallet(strategy_id, allocation)
        w.allocation = float(allocation)
        return w

    def rule(self, symbol: str) -> MarketRules:
        r = self.rules.get(symbol)
        if r is None:
            r = MarketRules(symbol, 0.0, 0.0, 0.0, 0.0)
        return r

    # -- pricing --------------------------------------------------------------------------
    def slippage_bps(self) -> float:
        if self.fixed_slippage_bps is not None:
            return self.fixed_slippage_bps
        lo, hi = self.settings.slippage_bps_min, self.settings.slippage_bps_max
        return self._rng.uniform(lo, hi) if hi > lo else lo

    @staticmethod
    def fill_price(order_side: str, ref_price: float, bps: float) -> float:
        return ref_price * (1 + bps / 1e4) if order_side == "BUY" else ref_price * (1 - bps / 1e4)

    def _conservative_min_notional(self) -> bool:
        """True when the operator opted into the old rule that partial exits clear minNotional too."""
        return float(getattr(self.settings, "min_notional_safety_multiplier", 1.0) or 1.0) > 1.0

    def fee_for(self, notional: float, maker: bool = False) -> float:
        rate = self.settings.maker_fee if maker else self.settings.taker_fee
        return abs(notional) * rate

    # -- open / close -------------------------------------------------------------------
    def open_position(self, sig: Signal, decision: RiskDecision, ref_price: float, simulated: bool,
                      ts: int | None = None, fill_price: float | None = None,
                      maker: bool | None = None, fill_meta: dict[str, Any] | None = None,
                      qty: float | None = None) -> tuple[VirtualPosition, Fill]:
        """`fill_price` lets an ExecutionModel own the pricing (order book / bid-ask / OHLCV model)
        instead of this module's uniform bps. When it is None the legacy internal model applies, so
        every existing caller is unchanged.

        `qty` is what actually FILLED when that is less than the approved size. A partially filled
        entry opens a position of the filled quantity, never the requested one -- and the filled
        part may legitimately be below minNotional, because the exchange checks the order, not its
        fills."""
        ts = self.clock() if ts is None else ts
        order_side = "BUY" if sig.side == "long" else "SELL"
        if fill_price is not None:
            price = fill_price
            bps = abs(price / ref_price - 1.0) * 1e4 if ref_price else 0.0
        else:
            bps = self.slippage_bps()
            price = self.fill_price(order_side, ref_price, bps)
        qty = decision.qty if qty is None else min(float(qty), decision.qty)
        notional = qty * price
        is_maker = bool(sig.meta.get("maker")) if maker is None else maker
        fee = self.fee_for(notional, maker=is_maker)
        lev = max(1, int(decision.leverage))
        wallet = self.ensure_wallet(sig.strategy_id, 0.0)
        wallet.fees += fee
        pos = VirtualPosition(
            id=new_id(), strategy_id=sig.strategy_id, symbol=sig.symbol, side=sig.side, qty=qty, qty_initial=qty,
            entry_price=price, entry_ts=ts, leverage=lev, margin=notional / lev, stop=sig.stop,
            take_profits=[TakeProfit(tp.price, tp.fraction) for tp in sig.take_profits], trail=sig.trail,
            be_at_r=sig.be_at_r, max_hold_deadline=(ts + sig.max_hold_s * 1000) if sig.max_hold_s else None,
            initial_risk_usd=qty * abs(price - sig.stop), extreme_price=price, signal_id=sig.id, leg=sig.leg,
            tf=sig.tf, meta={"reason": sig.reason, "stop_kind": "stop", "simulated": simulated, **{
                k: v for k, v in sig.meta.items() if k in ("vote", "grid_level", "proxy")}})
        pos.fees += fee
        self.positions[pos.id] = pos
        fill = Fill(id=new_id(), ts=ts, epoch=self.epoch, strategy_id=sig.strategy_id, symbol=sig.symbol,
                    position_id=pos.id, side=order_side, qty=qty, price=price, fee=fee, slippage_bps=bps,  # type: ignore[arg-type]
                    kind="entry", realized_pnl=0.0, simulated=simulated, signal_id=sig.id or None,
                    meta={"reason": sig.reason, "leg": sig.leg, "stop": sig.stop, "size_mult": sig.size_mult,
                          **(fill_meta or {})},
                    ref_price=ref_price, leverage=lev, position_side=sig.side, is_open=True)
        return pos, fill

    def close_position(self, position_id: str, fraction: float, ref_price: float, kind: str, simulated: bool,
                       reason: str = "", ts: int | None = None, fill_price: float | None = None,
                       maker: bool = False, fill_meta: dict[str, Any] | None = None) -> Fill:
        pos = self.positions[position_id]
        ts = self.clock() if ts is None else ts
        rules = self.rule(pos.symbol)
        if fraction >= 0.999:
            qty_close = pos.qty
        else:
            qty_close = rules.round_qty_nearest(pos.qty * fraction) if rules.step else pos.qty * fraction
            remainder = pos.qty - qty_close
            # A partial exit is a reduce-only order: LOT_SIZE applies to it, MIN_NOTIONAL does not
            # (Binance -4164 exempts reduce-only). The remainder is a position, not an order, and has
            # no minimum of its own -- it only has to stay closable, i.e. at least one lot.
            lot = max(rules.min_qty, rules.step or 0.0)
            if qty_close <= 0 or qty_close < lot - 1e-12 or remainder < lot - 1e-12:
                qty_close = pos.qty
            elif self._conservative_min_notional() and remainder * ref_price < rules.min_notional:
                qty_close = pos.qty       # opt-in PaperLab caution, not an exchange rule
        order_side = "SELL" if pos.side == "long" else "BUY"
        bps = self.slippage_bps()
        price = fill_price if fill_price is not None else self.fill_price(order_side, ref_price, bps)
        if fill_price is not None:
            bps = abs(price / ref_price - 1.0) * 1e4 if ref_price else 0.0
        pnl = (price - pos.entry_price) * qty_close * pos.sign
        fee = self.fee_for(qty_close * price, maker=maker)
        wallet = self.ensure_wallet(pos.strategy_id, 0.0)
        wallet.realized += pnl
        wallet.fees += fee
        pos.realized += pnl
        pos.fees += fee
        pos.qty = round(max(0.0, pos.qty - qty_close), rules.qty_precision if rules.step else 12)
        if rules.step and pos.qty < rules.step:
            pos.qty = 0.0
        pos.exit_qty += qty_close
        pos.exit_notional += qty_close * price
        pos.margin = pos.qty * pos.entry_price / pos.leverage
        pos.exit_fill_ids.append("pending")
        fill = Fill(id=new_id(), ts=ts, epoch=self.epoch, strategy_id=pos.strategy_id, symbol=pos.symbol,
                    position_id=pos.id, side=order_side, qty=qty_close, price=price, fee=fee, slippage_bps=bps,  # type: ignore[arg-type]
                    kind=kind, realized_pnl=pnl, simulated=simulated, signal_id=pos.signal_id or None,
                    meta={"reason": reason, "fraction": fraction, "leg": pos.leg, "remaining": pos.qty,
                          **(fill_meta or {})},
                    ref_price=ref_price, leverage=pos.leverage, position_side=pos.side, is_open=False)
        pos.exit_fill_ids[-1] = fill.id
        if pos.qty <= 0:
            self._finalize(pos, ts, kind, simulated)
        return fill

    def _finalize(self, pos: VirtualPosition, ts: int, kind: str, simulated: bool) -> ClosedTrade:
        exit_px = pos.exit_notional / pos.exit_qty if pos.exit_qty else pos.entry_price
        net = pos.realized - pos.fees
        r = net / pos.initial_risk_usd if pos.initial_risk_usd > 0 else 0.0
        trade = ClosedTrade(pos.id, pos.strategy_id, pos.symbol, pos.side, pos.qty_initial, pos.entry_price, exit_px,
                            pos.entry_ts, ts, pos.realized, pos.fees, net, r, kind, pos.leg, simulated)
        self.closed_trades.append(trade)
        self.positions.pop(pos.id, None)
        return trade

    def apply_funding(self, symbol: str, rate: float, mark: float, ts: int | None = None) -> list[Fill]:
        """Paper funding on every virtual position (v1: applied even when the exchange net is flat, tagged PAPER)."""
        ts = self.clock() if ts is None else ts
        out: list[Fill] = []
        for pos in list(self.positions.values()):
            if pos.symbol != symbol or pos.qty <= 0:
                continue
            pay = -rate * pos.notional(mark) * pos.sign
            wallet = self.ensure_wallet(pos.strategy_id, 0.0)
            wallet.funding += pay
            out.append(Fill(id=new_id(), ts=ts, epoch=self.epoch, strategy_id=pos.strategy_id, symbol=symbol,
                            position_id=pos.id, side="BUY" if pay >= 0 else "SELL", qty=0.0, price=mark, fee=0.0,
                            slippage_bps=0.0, kind="funding", realized_pnl=pay, simulated=True, signal_id=None,
                            meta={"paper": True, "rate": rate, "note": "paper funding; netted legs pay nothing on the exchange"},
                            ref_price=mark, leverage=pos.leverage, position_side=pos.side, is_open=False))
        return out

    # -- replay ------------------------------------------------------------------------------
    def replay(self, fills: Iterable[Fill], open_snapshots: Iterable[VirtualPosition],
               allocations: dict[str, float], lives: dict[str, int] | None = None) -> None:
        """Rebuild wallets from the fill ledger. `lives` restricts each strategy to its CURRENT life so a
        respawned book starts clean while the dead life stays on disk."""
        lives = lives or {}
        self.positions.clear()
        self.closed_trades.clear()
        self.wallets = {sid: Wallet(strategy_id=sid, allocation=float(a), life=lives.get(sid, 1))
                        for sid, a in allocations.items()}
        entries: dict[str, Fill] = {}
        exits: dict[str, list[Fill]] = {}
        for f in fills:
            if f.life != lives.get(f.strategy_id, 1):
                continue  # a previous life of this book: history only, not current equity
            w = self.ensure_wallet(f.strategy_id, 0.0)
            if f.kind == "funding":
                w.funding += f.realized_pnl
                continue
            w.realized += f.realized_pnl
            w.fees += f.fee
            if f.is_open:
                entries[f.position_id] = f
            else:
                exits.setdefault(f.position_id, []).append(f)
        open_ids = set()
        for pos in open_snapshots:
            self.positions[pos.id] = pos
            open_ids.add(pos.id)
        for pid, entry in entries.items():
            if pid in open_ids:
                continue
            ex = exits.get(pid, [])
            if not ex:
                continue
            qty = sum(x.qty for x in ex)
            exit_px = sum(x.qty * x.price for x in ex) / qty if qty else entry.price
            pnl = sum(x.realized_pnl for x in ex)
            fees = entry.fee + sum(x.fee for x in ex)
            stop = float(entry.meta.get("stop") or 0.0)
            risk = entry.qty * abs(entry.price - stop) if stop else 0.0
            net = pnl - fees
            self.closed_trades.append(ClosedTrade(
                pid, entry.strategy_id, entry.symbol, entry.position_side, entry.qty, entry.price, exit_px, entry.ts,
                ex[-1].ts, pnl, fees, net, net / risk if risk > 0 else 0.0, ex[-1].kind, entry.meta.get("leg"),
                entry.simulated))
        self.closed_trades = deque(sorted(self.closed_trades, key=lambda t: t.exit_ts), maxlen=5000)

    def restore_position(self, pos: VirtualPosition) -> None:
        self.positions[pos.id] = pos

    def respawn(self, strategy_id: str, balance: float, day: str) -> Wallet:
        """Start a new life for one book: bank the dead life's result, reset the wallet to `balance`.

        Only this wallet is touched — no cash moves between strategies, and nothing on disk is rewritten.
        """
        w = self.ensure_wallet(strategy_id, balance)
        w.realized_all_lives += w.net()
        w.life += 1
        w.lives_today = (w.lives_today + 1) if w.lives_day == day else 1
        w.lives_day = day
        w.allocation = float(balance)
        w.realized = 0.0
        w.fees = 0.0
        w.funding = 0.0
        self.closed_trades = deque([t for t in self.closed_trades if t.strategy_id != strategy_id], maxlen=5000)
        return w

    def reset(self, new_epoch: int, allocations: dict[str, float]) -> None:
        self.epoch = new_epoch
        self.positions.clear()
        self.closed_trades.clear()
        self.wallets = {sid: Wallet(strategy_id=sid, allocation=float(a)) for sid, a in allocations.items()}

    # -- queries ---------------------------------------------------------------------------
    def positions_of(self, strategy_id: str) -> list[VirtualPosition]:
        return [p for p in self.positions.values() if p.strategy_id == strategy_id]

    def positions_on(self, symbol: str) -> list[VirtualPosition]:
        return [p for p in self.positions.values() if p.symbol == symbol]

    def net_qty(self, symbol: str, only: set[str] | None = None) -> float:
        """Signed net quantity on a symbol. `only` restricts it to a set of strategies, which is how the
        router sends ONLY the live books to the exchange while the rest of the lab keeps paper trading."""
        return sum(p.signed_qty for p in self.positions.values()
                   if p.symbol == symbol and (only is None or p.strategy_id in only))

    def net_notional(self, symbol: str, price: float) -> float:
        return abs(self.net_qty(symbol)) * price

    def gross_notional(self, prices: dict[str, float]) -> float:
        return sum(p.notional(prices.get(p.symbol, p.entry_price)) for p in self.positions.values())

    def gross_notional_on(self, symbol: str, price: float) -> float:
        return sum(p.notional(price) for p in self.positions.values() if p.symbol == symbol)

    def margin_used(self, strategy_id: str) -> float:
        return sum(p.margin for p in self.positions.values() if p.strategy_id == strategy_id)

    def upnl(self, strategy_id: str, prices: dict[str, float]) -> float:
        return sum(p.upnl(prices.get(p.symbol, p.entry_price)) for p in self.positions.values()
                   if p.strategy_id == strategy_id)

    def total_upnl(self, prices: dict[str, float]) -> float:
        return sum(p.upnl(prices.get(p.symbol, p.entry_price)) for p in self.positions.values())

    def wallet_equity(self, strategy_id: str, prices: dict[str, float]) -> float:
        w = self.wallets.get(strategy_id)
        return 0.0 if w is None else w.equity(self.upnl(strategy_id, prices))

    def available(self, strategy_id: str, prices: dict[str, float]) -> float:
        return self.wallet_equity(strategy_id, prices) - self.margin_used(strategy_id)

    def total_equity(self, prices: dict[str, float]) -> float:
        return sum(w.equity(self.upnl(sid, prices)) for sid, w in self.wallets.items())

    def total_allocation(self) -> float:
        return sum(w.allocation for w in self.wallets.values())

    def totals(self, prices: dict[str, float]) -> dict[str, float]:
        return {"realized": sum(w.realized for w in self.wallets.values()),
                "fees": sum(w.fees for w in self.wallets.values()),
                "funding": sum(w.funding for w in self.wallets.values()),
                "upnl": self.total_upnl(prices), "equity": self.total_equity(prices),
                "allocation": self.total_allocation(), "gross_notional": self.gross_notional(prices)}

    def stats(self, strategy_id: str | None, day_start_ts: int) -> dict[str, Any]:
        trades = [t for t in self.closed_trades if strategy_id is None or t.strategy_id == strategy_id]
        n = len(trades)
        wins = [t for t in trades if t.net > 0]
        gp = sum(t.net for t in wins)
        gl = -sum(t.net for t in trades if t.net <= 0)
        pf: float | None
        if gl > 0:
            pf = gp / gl
        else:
            pf = None if gp <= 0 else float("inf")
        return {
            "trades_total": n, "trades_today": sum(1 for t in trades if t.exit_ts >= day_start_ts),
            "win_rate": (len(wins) / n) if n else None,
            "profit_factor": (None if pf is None else (999.0 if pf == float("inf") else pf)),
            "avg_r": (sum(t.r_multiple for t in trades) / n) if n else None,
            "expectancy": (sum(t.net for t in trades) / n) if n else None,
            "gross_profit": gp, "gross_loss": gl, "net": sum(t.net for t in trades),
        }

    def snapshot(self, prices: dict[str, float]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for sid, w in self.wallets.items():
            upnl = self.upnl(sid, prices)
            positions = self.positions_of(sid)
            out[sid] = {"allocation": w.allocation, "realized": w.realized, "fees": w.fees, "funding": w.funding,
                        "upnl": upnl, "equity": w.equity(upnl), "margin_used": self.margin_used(sid),
                        "available": w.equity(upnl) - self.margin_used(sid), "open_positions": len(positions),
                        "gross_notional": sum(p.notional(prices.get(p.symbol, p.entry_price)) for p in positions)}
        return out

    def trades_of(self, strategy_id: str | None, limit: int = 20) -> list[ClosedTrade]:
        items = [t for t in self.closed_trades if strategy_id is None or t.strategy_id == strategy_id]
        return items[-limit:][::-1]
