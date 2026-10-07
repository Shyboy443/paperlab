"""PaperRouter: mirrors the virtual book's NET position per symbol onto the testnet account.

* plan() is pure and unit-tested: delta = target_net - exchange_net (+ sub-step residual), rounded toward
  zero to the lot step; reduceOnly only when the order shrinks |exchange_net| without flipping; a flip is
  split into [reduceOnly to zero, open remainder]. Only orders that open or add exposure must clear
  MIN_NOTIONAL; reduce-only orders are exempt on Binance USD-M and go out at any lot-legal size.
* Virtual fills are NEVER rolled back: an exchange rejection becomes `desync[symbol]` (red badge, retried).
* One exchange-level backstop STOP_MARKET (closePosition) per symbol at a leverage-aware distance.
* DRY_RUN: every method is a no-op; the GUI shows exchange_net = 0.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Callable

from app.core.types import ExchangePosition, MarketRules, OrderIntent, UserEvent

log = logging.getLogger("paperlab.router")

CID_NET = "plb-nd-"
CID_BACKSTOP = "plb-bs-"
CID_FLATTEN = "plb-fl-"


def new_cid(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:12]


def backstop_distance(lev_exchange: int, mmr: float, factor: float = 0.70, min_pct: float = 0.004) -> float:
    """Fraction below/above the exchange entry: 70% of the way to isolated liquidation, floor 0.4%."""
    lev = max(1, int(lev_exchange))
    return max(min_pct, (1.0 / lev - mmr) * factor)


def plan(symbol: str, target_net: float, exchange_net: float, ref_price: float, rules: MarketRules,
         residual: float = 0.0, cid_factory: Callable[[], str] | None = None) -> tuple[list[OrderIntent], float]:
    """Pure net-delta planner. Returns (intents in send order, new residual)."""
    cid = cid_factory or (lambda: new_cid(CID_NET))
    step = rules.step or 1e-8
    raw = (target_net - exchange_net) + residual
    units = int(abs(raw) / step + 1e-9)
    delta = (units * step) * (1 if raw > 0 else -1)
    delta = round(delta, rules.qty_precision)
    new_residual = raw - delta
    if units == 0 or abs(delta) < rules.min_qty - 1e-12:
        return [], raw
    side = "BUY" if delta > 0 else "SELL"
    qty = abs(delta)
    intents: list[OrderIntent] = []
    # MIN_NOTIONAL is checked on the submitted order and reduce-only orders are exempt (Binance
    # -4164), so only an order that OPENS or ADDS exposure has to clear it. A shrinking delta goes
    # out reduce-only at any lot-legal size instead of waiting for more to accumulate.
    if abs(exchange_net) < step or (delta > 0) == (exchange_net > 0):
        if qty * ref_price < rules.min_notional:
            return [], raw  # an opening order below minNotional would be rejected; keep accumulating
        intents.append(OrderIntent(symbol, side, qty, False, "net_delta", cid(), ref_price))  # type: ignore[arg-type]
    elif qty <= abs(exchange_net) + 1e-12:
        intents.append(OrderIntent(symbol, side, qty, True, "net_delta", cid(), ref_price))  # type: ignore[arg-type]
    else:
        first = round(abs(exchange_net), rules.qty_precision)
        rest = round(qty - first, rules.qty_precision)
        intents.append(OrderIntent(symbol, side, first, True, "net_delta", cid(), ref_price))  # type: ignore[arg-type]
        # the opening remainder of a flip is an ordinary order and must clear both minimums
        if rest >= rules.min_qty - 1e-12 and rest * ref_price >= rules.min_notional:
            intents.append(OrderIntent(symbol, side, rest, False, "net_delta", cid(), ref_price))  # type: ignore[arg-type]
        else:
            new_residual += rest * (1 if delta > 0 else -1)
    return intents, new_residual


def backstop_plan(symbol: str, net_qty: float, exchange_entry: float | None, mark: float | None,
                  lev_exchange: int, mmr: float, rules: MarketRules, factor: float = 0.70,
                  min_pct: float = 0.004) -> OrderIntent | None:
    """STOP_MARKET closePosition at `backstop_distance` from the exchange entry (fallback: mark)."""
    if abs(net_qty) < (rules.step or 1e-8):
        return None
    ref = exchange_entry if exchange_entry and exchange_entry > 0 else (mark or 0.0)
    if ref <= 0:
        return None
    pct = backstop_distance(lev_exchange, mmr, factor, min_pct)
    if net_qty > 0:
        stop = rules.round_price(ref * (1 - pct), "down")
        side = "SELL"
    else:
        stop = rules.round_price(ref * (1 + pct), "up")
        side = "BUY"
    return OrderIntent(symbol, side, abs(net_qty), True, "backstop", new_cid(CID_BACKSTOP), ref,  # type: ignore[arg-type]
                       stop_price=stop, close_position=True)


class PaperRouter:
    def __init__(self, settings: Any, client: Any | None, portfolio: Any, storage: Any, rules: dict[str, MarketRules],
                 clock: Callable[[], int], price_provider: Callable[[str], float | None]):
        self.settings = settings
        self.client = client
        self.portfolio = portfolio
        self.storage = storage
        self.rules = rules
        self.clock = clock
        self.price_of = price_provider
        # A LIVE venue ALWAYS boots on paper fills, whatever DRY_RUN says. Real orders require an explicit
        # arm from the dashboard, which is the only path that runs the preflight checks (one strategy,
        # paper book sized to the real wallet, flat, not halted). DRY_RUN=false must never be enough on
        # its own to start trading real money at boot.
        self.dry_run: bool = bool(settings.dry_run) or client is None or bool(settings.venue.is_live)
        # Which strategies are allowed to reach the exchange. None = all (legacy / testnet behaviour);
        # a set = only those books are netted onto the real account. Set by Engine.arm_live.
        self.live_strategies: set[str] | None = None
        self.exchange_net: dict[str, ExchangePosition] = {}
        self.exchange_wallet: dict[str, float] = {}
        self.residual: dict[str, float] = {}
        self.desync: dict[str, str] = {}
        self.backstops: dict[str, dict[str, Any]] = {}
        self.last_drift_bps: dict[str, float] = {}
        self.mmr_by_symbol: dict[str, float] = {}
        self.exchange_leverage: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._dirty: set[str] = set()
        self._last_refresh: float = 0.0
        self.orders_sent: int = 0
        self.orders_failed: int = 0

    # -- helpers ------------------------------------------------------------------------
    def _lock(self, symbol: str) -> asyncio.Lock:
        if symbol not in self._locks:
            self._locks[symbol] = asyncio.Lock()
        return self._locks[symbol]

    def mark_dirty(self, symbol: str) -> None:
        self._dirty.add(symbol)

    def exchange_qty(self, symbol: str) -> float:
        p = self.exchange_net.get(symbol)
        return p.qty if p else 0.0

    def mmr(self, symbol: str) -> float:
        r = self.rules.get(symbol)
        return self.mmr_by_symbol.get(symbol, r.maint_margin_rate if r else 0.025)

    def lev(self, symbol: str) -> int:
        if not self.settings.venue.caps.leverage:
            return 1  # spot: no leverage on the exchange side
        return self.exchange_leverage.get(symbol, self.settings.default_leverage)

    def backstop_pct(self, symbol: str) -> float | None:
        if not self.settings.venue.caps.backstop:
            return None
        return backstop_distance(self.lev(symbol), self.mmr(symbol), self.settings.backstop_factor,
                                 self.settings.backstop_min_pct)

    # -- exchange state ------------------------------------------------------------------
    async def refresh_exchange(self, symbols: list[str], force: bool = False) -> None:
        if self.dry_run:
            return
        now = time.monotonic()
        if not force and now - self._last_refresh < 10.0:
            return
        positions = await self.client.fetch_positions(symbols)
        for sym in symbols:
            pos = positions.get(sym)
            if pos is None:
                pos = ExchangePosition(sym, 0.0, 0.0, None, 0.0, 0.0, self.lev(sym), 0.0, self.clock())
            self.exchange_net[sym] = pos
            if pos.leverage:
                self.exchange_leverage[sym] = pos.leverage
        try:
            self.exchange_wallet = await self.client.fetch_balance()
        except Exception as exc:  # balance is informational
            log.warning("balance fetch failed: %s", str(exc)[:120])
        self._last_refresh = now

    # -- sync --------------------------------------------------------------------------------
    async def sync_dirty(self, reason: str = "fills") -> None:
        if self.dry_run:
            self._dirty.clear()
            return
        symbols = sorted(self._dirty)
        self._dirty.clear()
        for sym in symbols:
            await self.sync(sym, reason)

    async def retry_desync(self, symbols: list[str]) -> None:
        for sym in [s for s in symbols if s in self.desync]:
            await self.sync(sym, "retry")

    async def sync(self, symbol: str, reason: str) -> None:
        """Bring the exchange net to the virtual net for one symbol, then re-place the backstop."""
        if self.dry_run:
            return
        async with self._lock(symbol):
            try:
                await self.refresh_exchange([symbol], force=reason in ("boot", "retry", "backstop_fired"))
                ref = self.price_of(symbol) or (self.exchange_net.get(symbol).mark if symbol in self.exchange_net else 0.0)
                if not ref:
                    self.desync[symbol] = "no reference price"
                    return
                rules = self.rules[symbol]
                # ONLY the live books reach the exchange. Every other strategy keeps trading on paper
                # in the same process, so the bake-off continues alongside a live book without its
                # positions ever being netted onto the real account.
                target = self.portfolio.net_qty(symbol, only=self.live_strategies)
                intents, self.residual[symbol] = plan(symbol, target, self.exchange_qty(symbol), ref, rules,
                                                      self.residual.get(symbol, 0.0))
                for intent in intents:
                    ok = await self._send(intent, reason)
                    if not ok:
                        return
                self.desync.pop(symbol, None)
                await self._update_backstop(symbol)
            except Exception as exc:
                self._fail(symbol, f"{reason}: {str(exc)[:200]}")

    def _fail(self, symbol: str, detail: str) -> None:
        self.desync[symbol] = detail
        self.orders_failed += 1
        log.error("router desync %s: %s", symbol, detail)
        self.storage.insert_event("exchange_desync", {"symbol": symbol, "detail": detail}, self.portfolio.epoch)
        try:
            self.storage.insert_note(self.portfolio.epoch, "desync", f"DESYNC {symbol}: {detail}", symbol=symbol)
        except Exception:  # the journal must never break the router
            pass

    async def _send(self, intent: OrderIntent, reason: str) -> bool:
        self.storage.insert_order({"client_id": intent.client_id, "ts": self.clock(), "epoch": self.portfolio.epoch,
                                   "symbol": intent.symbol,
                                   "side": intent.side, "qty": intent.qty, "reduce_only": intent.reduce_only,
                                   "purpose": intent.purpose, "status": "NEW", "ref_price": intent.ref_price})
        self.orders_sent += 1
        try:
            res = await self.client.market_order(intent)
        except Exception as exc:
            self.storage.update_order(intent.client_id, status="REJECTED", error=str(exc)[:300])
            self._fail(intent.symbol, f"order {intent.side} {intent.qty} rejected: {str(exc)[:160]}")
            return False
        drift = None
        if res.avg_price and intent.ref_price:
            signed = (res.avg_price - intent.ref_price) / intent.ref_price * 1e4
            drift = signed if intent.side == "BUY" else -signed  # positive = paid more than the virtual fill
            self.last_drift_bps[intent.symbol] = drift
        self.storage.update_order(intent.client_id, status=res.status, exchange_order_id=res.exchange_id,
                                  avg_price=res.avg_price, executed_qty=res.executed_qty, drift_bps=drift,
                                  raw=res.raw, error=res.error)
        if res.status != "FILLED":
            self._fail(intent.symbol, f"order {intent.client_id} status {res.status}")
            return False
        pos = self.exchange_net.get(intent.symbol) or ExchangePosition(intent.symbol, 0.0, 0.0, None, 0.0, 0.0,
                                                                        self.lev(intent.symbol), 0.0, self.clock())
        signed_qty = res.executed_qty if intent.side == "BUY" else -res.executed_qty
        new_qty = round(pos.qty + signed_qty, self.rules[intent.symbol].qty_precision)
        if abs(new_qty) > 0 and (pos.qty == 0 or (pos.qty > 0) != (new_qty > 0)):
            pos.entry_price = res.avg_price or intent.ref_price
        elif abs(new_qty) > abs(pos.qty) and res.avg_price:
            pos.entry_price = (abs(pos.qty) * pos.entry_price + res.executed_qty * res.avg_price) / abs(new_qty)
        pos.qty = new_qty
        pos.ts = self.clock()
        self.exchange_net[intent.symbol] = pos
        self._last_refresh = 0.0  # force a positionRisk refresh on the next sync
        return True

    async def _update_backstop(self, symbol: str) -> None:
        if not self.settings.venue.caps.backstop:
            return
        pos = self.exchange_net.get(symbol)
        net = pos.qty if pos else 0.0
        existing = self.backstops.get(symbol)
        if abs(net) < (self.rules[symbol].step or 1e-8):
            if existing:
                await self._cancel_backstop(symbol)
            return
        mark = self.price_of(symbol) or (pos.mark if pos else None)
        intent = backstop_plan(symbol, net, pos.entry_price if pos else None, mark, self.lev(symbol), self.mmr(symbol),
                               self.rules[symbol], self.settings.backstop_factor, self.settings.backstop_min_pct)
        if intent is None:
            return
        if existing and existing.get("side") == intent.side and existing.get("qty") == intent.qty and \
                abs(existing.get("price", 0) - (intent.stop_price or 0)) / (intent.stop_price or 1) < 0.001:
            return
        if existing:
            await self._cancel_backstop(symbol)
        self.storage.insert_order({"client_id": intent.client_id, "ts": self.clock(), "epoch": self.portfolio.epoch,
                                   "symbol": symbol,
                                   "side": intent.side, "qty": intent.qty, "reduce_only": True, "purpose": "backstop",
                                   "status": "NEW", "ref_price": intent.ref_price, "stop_price": intent.stop_price})
        res = await self.client.place_backstop(symbol, intent.side, intent.stop_price or 0.0, intent.client_id,
                                               qty=abs(net))
        self.storage.update_order(intent.client_id, status=res.status, exchange_order_id=res.exchange_id, raw=res.raw)
        self.backstops[symbol] = {"id": intent.client_id, "price": intent.stop_price, "side": intent.side,
                                  "qty": intent.qty, "pct": self.backstop_pct(symbol), "ts": self.clock()}

    async def _cancel_backstop(self, symbol: str) -> None:
        existing = self.backstops.pop(symbol, None)
        if existing and self.client is not None:
            try:
                await self.client.cancel_order(symbol, existing["id"])
                self.storage.update_order(existing["id"], status="CANCELED")
            except Exception as exc:
                log.warning("cancel backstop %s failed: %s", existing["id"], str(exc)[:120])

    # -- boot / kill helpers -----------------------------------------------------------------
    async def cancel_orphans(self, symbols: list[str]) -> None:
        if self.dry_run:
            return
        for sym in symbols:
            await self.client.cancel_all(sym)
        self.backstops.clear()
        self.storage.insert_event("orphans_cancelled", {"symbols": symbols}, self.portfolio.epoch)

    async def flatten_exchange(self, symbols: list[str], reason: str = "flatten") -> None:
        """Close every exchange net position (used by kill / margin halt); virtual book handled by the engine."""
        if self.dry_run:
            return
        for sym in symbols:
            async with self._lock(sym):
                try:
                    await self.refresh_exchange([sym], force=True)
                    net = self.exchange_qty(sym)
                    if abs(net) >= (self.rules[sym].step or 1e-8):
                        ref = self.price_of(sym) or 0.0
                        intent = OrderIntent(sym, "SELL" if net > 0 else "BUY", abs(net), True, "flatten",
                                             new_cid(CID_FLATTEN), ref)
                        await self._send(intent, reason)
                    await self._cancel_backstop(sym)
                    self.residual[sym] = 0.0
                except Exception as exc:
                    self._fail(sym, f"{reason}: {str(exc)[:200]}")

    def on_user_event(self, ev: UserEvent) -> list[str]:
        """Returns symbols whose exchange backstop just FILLED; keeps exchange_net fresh from ACCOUNT_UPDATE."""
        fired: list[str] = []
        p = ev.payload
        if ev.kind == "ORDER_TRADE_UPDATE":
            o = p.get("o", {})
            cid = str(o.get("c", ""))
            if cid.startswith(CID_BACKSTOP) and o.get("X") == "FILLED":
                sym = str(o.get("s"))
                self.backstops.pop(sym, None)
                fired.append(sym)
        elif ev.kind == "ACCOUNT_UPDATE":
            for row in p.get("a", {}).get("P", []):
                sym = str(row.get("s"))
                if sym in self.rules:
                    pos = self.exchange_net.get(sym) or ExchangePosition(sym, 0.0, 0.0, None, 0.0, 0.0, self.lev(sym))
                    try:
                        pos.qty = float(row.get("pa", pos.qty))
                        pos.entry_price = float(row.get("ep", pos.entry_price))
                        pos.upnl = float(row.get("up", pos.upnl))
                    except (TypeError, ValueError):
                        pass
                    pos.ts = self.clock()
                    self.exchange_net[sym] = pos
        return fired

    # -- status ----------------------------------------------------------------------------
    def status(self, symbols: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for sym in symbols:
            price = self.price_of(sym) or 0.0
            ex = self.exchange_net.get(sym)
            out[sym] = {
                "virtual_net": self.portfolio.net_qty(sym), "exchange_net": ex.qty if ex else 0.0,
                "gross": self.portfolio.gross_notional_on(sym, price), "residual": self.residual.get(sym, 0.0),
                "desync": self.desync.get(sym), "backstop": self.backstops.get(sym),
                "backstop_pct": self.backstop_pct(sym), "last_drift_bps": self.last_drift_bps.get(sym),
                "exchange_entry": ex.entry_price if ex else None, "exchange_liq": ex.liq_price if ex else None,
                "lev_exchange": self.lev(sym), "mmr": self.mmr(sym),
            }
        return out
