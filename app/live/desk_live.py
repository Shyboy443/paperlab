"""Desk LIVE executor: one desk's 4h rule on ONE linked Binance USD-M account, 1x (real orders when the account is mainnet).

Shared by every desk with a live switch (MACD, Donchian). A desk subclass supplies only:
- the identity (NAME, REF, CONFIRM, STATE_KEY, the symbols it may trade, whether it may go short),
- `decide(symbol, held)`: the open time of the newest COMPLETED 4h candle and the rule's action at its close --
  ("enter", +1 / -1), ("reverse", +1 / -1), ("exit", 0) or None -- given the position held (+1 long, -1 short, 0 flat).
Everything else is here, identical for every desk:
- switching on: private authenticated POST + the typed phrase; a linked account (app/live/accounts.py) no other strategy
  owns, dedicated and flat (no position on ANY symbol, no open orders on the traded symbol), holding the amount;
  mainnet also needs LIVE_MIRROR_MAINNET_ENABLED=true and a passed Binance testnet round trip; amount <=
  LIVE_MIRROR_MAX_AMOUNT_USDT. It starts FLAT and acts only on candles that close after it was switched on.
- orders: market entries at 1x (isolated, one-way) sized from the allocation (amount + realized P&L); exits and
  reversals close reduce-only first; an exchange-side STOP_MARKET DISASTER against every entry (below a long, above a
  short) -- a stop that cannot be placed closes the position at once.
- loss limit on the allocation marked to market; reconciliation every TICK_S: a vanished position -> OFF, a position
  that differs from what this desk opened -> HALTED (nothing traded automatically).
- states OFF / ARMED / CLOSING (close owed, stop re-placed, retried every tick) / HALTED; switching off closes the
  position and releases the account; the kill switch does the same. Telegram gets every event in every scope.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from types import SimpleNamespace
from typing import Any, Callable

log = logging.getLogger(__name__)

LEVERAGE, DISASTER, TAKER_EST, TICK_S, TF_MS = 1, 0.30, 0.0005, 15, 4 * 3_600_000
# maker execution (desks with MAKER = True): post-only limits at the best price, re-priced, market for the rest
MAKER_EST, MAKER_WINDOW_S, MAKER_REPRICE_S, MAKER_POLL_S = 0.0002, 180, 30, 5
LIVE_STATES = ("ARMED", "CLOSING", "HALTED")


class LiveError(ValueError):
    pass


class DeskLive:
    NAME = "Desk"
    KIND, REF = "desk", "Desk"
    CONFIRM = "GO LIVE"
    STATE_KEY = "live"
    SYMBOLS: tuple[str, ...] = ()
    SHORTS = False
    INTERVAL = "4h"                  # the rule's candle (the ZEC bots run some on 1h)
    SIZE = 1.0          # share of the allocation each position uses; the rest stays as cash on the account
    MAKER = False       # True: entries and exits as post-only limits first (maker fee), market after MAKER_WINDOW_S
    THREAD = "desk-live"

    def __init__(self, svc: Any, accounts: Any, providers: Any, checks: Callable[[], list], save_check: Callable[..., None],
                 telegram: Callable[[], Any] = lambda: None, clock: Callable[[], float] = time.time):
        self.svc, self.accounts, self.providers = svc, accounts, providers
        self.checks, self.save_check, self.telegram, self.clock = checks, save_check, telegram, clock
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.op_lock: asyncio.Lock | None = None
        self._clients: dict[str, Any] = {}          # account id -> exchange client, reused on the live loop (markets load once)
        self._abort = threading.Event()             # switch-off / kill switch: a maker fill stops waiting at once

    # -- the desk's rule (subclass) ----------------------------------------------------------------------------------------
    def decide(self, symbol: str, held: int) -> tuple[int | None, tuple[str, int] | None]:
        raise NotImplementedError

    def symbols(self) -> list[str]:
        return list(self.SYMBOLS)

    # -- state --------------------------------------------------------------------------------------------------------------
    def now(self) -> int:
        return int(self.clock() * 1000)

    def state(self) -> dict[str, Any]:
        return self.svc.store.get(self.STATE_KEY) or {"status": "OFF", "log": [], "trades": []}

    def _put(self, st: dict[str, Any]) -> None:
        st["log"] = (st.get("log") or [])[-300:]
        st["trades"] = (st.get("trades") or [])[-500:]
        self.svc.store.set(self.STATE_KEY, st)

    def _log(self, st: dict[str, Any], kind: str, **detail: Any) -> None:
        st.setdefault("log", []).append({"ts": self.now(), "kind": kind, **detail})

    def _alert(self, text: str) -> None:
        t = self.telegram()
        if t is not None and hasattr(t, "live_alert"):
            try:
                t.live_alert(text)
            except Exception:
                pass

    def _coin(self, st: dict[str, Any]) -> str:
        return (st.get("symbol") or "").replace("USDT", "")

    def view(self) -> dict[str, Any]:
        """Private view (authenticated dashboard only)."""
        st = self.state()
        acct = None
        if st.get("account_id"):
            try:
                a = self.accounts.get(st["account_id"])
                acct = {"id": a["id"], "label": a["label"], "network": a["network"]}
            except Exception:
                acct = {"id": st["account_id"], "label": "(removed)", "network": st.get("network")}
        return {"desk": self.NAME, "status": st.get("status", "OFF"), "account": acct, "symbol": st.get("symbol"),
                "amount": st.get("amount"), "max_loss": st.get("max_loss"), "realized": st.get("realized", 0.0),
                "position": st.get("position"), "armed_ts": st.get("armed_ts"), "stopped_ts": st.get("stopped_ts"),
                "reason": st.get("reason"), "last_signal_t": st.get("last_signal_t"),
                "trades": (st.get("trades") or [])[-50:], "log": (st.get("log") or [])[-60:], "confirm_phrase": self.CONFIRM,
                "rules": {"symbols": self.symbols(), "shorts": self.SHORTS, "leverage": LEVERAGE, "size": self.SIZE,
                          "disaster_stop_pct": 100 * DISASTER, "tick_s": TICK_S},
                "mainnet": self.mainnet_gate(), "max_amount": self.providers.max_amount(), "accounts": self.accounts.public(),
                "shared_keys": self.accounts.shared_keys()}

    def mainnet_gate(self) -> dict[str, Any]:
        tested = [c for c in self.checks() if c.get("ok") and c.get("network") == "testnet"]
        # Operator waiver (Railway variable, set on the operator's request 2026-10-18): the new server has no testnet
        # round trip on record; the old one passed it on 2026-10-13 with the same order code. The typed GO LIVE phrase,
        # the mainnet switch and every other check still apply.
        waived = os.environ.get("LIVE_TESTNET_GATE_WAIVED", "").strip().lower() in ("1", "true", "yes", "on")
        return {"switch": self.providers.mainnet_switch(), "testnet_passed": bool(tested) or waived,
                "testnet_ts": tested[0]["ts"] if tested else None, "testnet_waived": waived and not tested}

    # -- thread + loop --------------------------------------------------------------------------------------------------------
    def start(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name=self.THREAD, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.op_lock = asyncio.Lock()
        self.loop.run_until_complete(self._ticker())

    async def _ticker(self) -> None:
        while not self.stop_event.is_set():
            try:
                async with self.op_lock:
                    await self.tick()
            except Exception as exc:
                log.error("%s live tick: %s", self.NAME, self.providers.redact(f"{type(exc).__name__}: {exc}"))
            await asyncio.sleep(TICK_S)

    def stop(self) -> None:
        self.stop_event.set()

    async def call(self, coro_fn: Callable[[], Any]) -> Any:
        """Run an operation on the live loop (one at a time) from a FastAPI handler."""
        if self.loop is None:
            raise LiveError("the live executor is not running")

        async def locked():
            async with self.op_lock:
                return await coro_fn()
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(locked(), self.loop))

    # -- switching on / off ---------------------------------------------------------------------------------------------------
    async def arm(self, account_id: str, amount: float, max_loss: float, confirm: str, symbol: str | None = None) -> dict[str, Any]:
        st = self.state()
        if st.get("status") in LIVE_STATES:
            raise LiveError(f"the strategy is {st['status']}: switch it off first")
        self._abort.clear()                                             # a fresh start: maker fills wait normally
        if st.get("position"):
            raise LiveError("a previous live position is still recorded: resolve it before switching on again")
        if (confirm or "").strip() != self.CONFIRM:
            raise LiveError(f'type "{self.CONFIRM}" to confirm')
        symbol = symbol or (self.symbols()[0] if len(self.symbols()) == 1 else "")
        if symbol not in self.symbols():
            raise LiveError("choose one of: " + ", ".join(s.replace("USDT", "") for s in self.symbols()))
        acct = self.accounts.get(account_id)
        if acct["exchange"] != "binance":
            raise LiveError(f"the {self.NAME} trades Binance USD-M only")
        try:
            amount, max_loss = float(amount), float(max_loss)
        except (TypeError, ValueError):
            raise LiveError("amount and loss limit must be numbers") from None
        if not 0 < amount <= self.providers.max_amount():
            raise LiveError(f"amount must be above 0 and at most {self.providers.max_amount():g} USDT (LIVE_MIRROR_MAX_AMOUNT_USDT)")
        if not 0 < max_loss <= amount:
            raise LiveError("the loss limit must be above 0 and at most the amount")
        if acct["network"] == "mainnet":
            g = self.mainnet_gate()
            if not g["switch"]:
                raise LiveError("mainnet is locked on the server: set LIVE_MIRROR_MAINNET_ENABLED=true (operator)")
            if not g["testnet_passed"]:
                raise LiveError("mainnet is locked until a Binance testnet round trip has passed (link a testnet account and run it)")
        try:                                                            # before the account is claimed: nothing to undo
            last_t, _ = self.decide(symbol, 0)
        except Exception as exc:
            raise LiveError(f"the {symbol} {self.INTERVAL} candles are unavailable right now ({type(exc).__name__}); try again") from None
        if last_t is None:
            raise LiveError(f"no completed {symbol} {self.INTERVAL} candles yet; try again in a few minutes")
        c = self._client(account_id)
        try:
            bal = await c.fetch_balance()
            if float(bal.get("available") or 0) < amount:
                raise LiveError(f"available balance {float(bal.get('available') or 0):.2f} USDT is below the amount {amount:.2f}")
            held = await c.fetch_positions([symbol], all_symbols=True)
            busy = [s for s, p in held.items() if abs(float(p.qty or 0)) > 0]
            if busy:
                raise LiveError("the account is not flat (open positions: " + ", ".join(sorted(busy)[:5]) +
                                "). A live strategy needs a dedicated, flat account.")
            if await c.open_orders(symbol):
                raise LiveError(f"the account has open {symbol} orders; cancel them first")
            self.accounts.claim(account_id, self.KIND, self.REF)              # one strategy per account
            try:
                await c.ensure_account_mode([symbol], LEVERAGE)
            except Exception:
                self.accounts.release(account_id, self.KIND, self.REF)
                raise
        except Exception:
            await self._drop_client(account_id)
            raise
        st = {"status": "ARMED", "account_id": account_id, "network": acct["network"], "symbol": symbol, "amount": amount,
              "max_loss": max_loss, "realized": 0.0, "position": None, "armed_ts": self.now(), "stopped_ts": None,
              "reason": None, "last_signal_t": last_t, "trades": st.get("trades") or [], "log": st.get("log") or []}
        self._log(st, "armed", account=acct["label"], network=acct["network"], symbol=symbol, amount=amount, max_loss=max_loss)
        self._put(st)
        self._alert(f"🟢 <b>{self.NAME} LIVE ON</b> · {acct['label']} ({acct['network']}) · {symbol} perp 1x"
                    f"{' long & short' if self.SHORTS else ''} · {amount:g} USDT, loss limit {max_loss:g} USDT. "
                    "Waiting for the next signal.")
        return self.view()

    async def disarm(self, reason: str) -> dict[str, Any]:
        st = self.state()
        if st.get("status") not in LIVE_STATES and not st.get("position"):
            return self.view()
        if not await self._close_and_off(st, reason):
            raise LiveError("closing the position failed; the disaster stop is back in place and the close is retried")
        return self.view()

    async def _close_and_off(self, st: dict[str, Any], reason: str, c: Any | None = None) -> bool:
        """Flatten, then OFF. A failed close leaves the strategy CLOSING (retried every tick). True once flat + OFF."""
        if st.get("position"):
            await self._exit(st, reason, c)
            st = self.state()
            if st.get("position"):
                st.update(status="CLOSING", reason=reason)
                self._put(st)
                return False
        self._off(st, reason)
        return True

    def _off(self, st: dict[str, Any], reason: str) -> None:
        st.update(status="OFF", stopped_ts=self.now(), reason=reason)
        self._log(st, "off", reason=reason)
        self._put(st)
        if st.get("account_id"):
            self.accounts.release(st["account_id"], self.KIND, self.REF)
            if self.loop is not None and self.loop.is_running():
                self.loop.call_soon_threadsafe(lambda a=st["account_id"]: asyncio.ensure_future(self._drop_client(a)))
        self._alert(f"🔴 <b>{self.NAME} LIVE OFF</b> · {reason} · realized {st.get('realized', 0):+.2f} USDT.")

    # -- the loop -------------------------------------------------------------------------------------------------------------
    async def tick(self) -> None:
        st = self.state()
        if st.get("status") not in ("ARMED", "CLOSING"):                # OFF, or HALTED: the operator decides
            return
        c = self._client(st["account_id"])
        try:
            if st.get("position") and not await self._reconcile(st, c):
                return
            st = self.state()
            if st.get("status") == "CLOSING":                          # a close is owed: retry it
                await self._close_and_off(st, st.get("reason") or "closing", c)
                return
            if st.get("position") and await self._loss_hit(st, c):
                return
            held = int(st["position"]["dir"]) if st.get("position") else 0
            t, action = self.decide(st["symbol"], held)
            if t is None or t <= (st.get("last_signal_t") or 0):
                return
            st["last_signal_t"] = t
            self._put(st)
            if not action:
                return
            kind, side = action
            if kind in ("exit", "reverse") and held:
                await self._exit(st, "rule exit" if kind == "exit" else "reversal", c)
                st = self.state()
                if st.get("position"):                                 # the close failed: no new entry
                    st.update(status="CLOSING", reason="the rule's close failed")
                    self._put(st)
                    return
            if kind in ("enter", "reverse") and not st.get("position"):
                if side < 0 and not self.SHORTS:
                    return
                await self._enter(st, c, t, side)
        finally:
            await self._close(c)

    async def _enter(self, st: dict[str, Any], c: Any, signal_t: int, side: int) -> None:
        from app.core.types import OrderIntent
        from app.live.mirror import REJECTED
        sym = st["symbol"]
        rules = (await c.load_rules([sym]))[sym]
        px = float((await c.fetch_premium_index(sym)).mark)
        alloc = float(st["amount"]) + float(st.get("realized") or 0)
        bal = await c.fetch_balance()
        budget = min(alloc * self.SIZE, float(bal.get("available") or 0)) * (1 - 0.003)   # room for fee + slippage at 1x
        qty = rules.round_qty_down(budget / px)
        if qty < rules.min_order_qty(px):
            self._log(st, "skip", why=f"allocation {alloc:.2f} USDT is below the exchange minimum for {sym}")
            self._put(st)
            self._alert(f"⚠️ {self.NAME} live: signal skipped, the allocation ({alloc:.2f} USDT) is below Binance's minimum {sym} order.")
            return
        cid = f"dl{uuid.uuid4().hex[:12]}"
        fee_est = None
        if self.MAKER:
            got, entry, fee_est, how = await self._maker_fill(c, rules, sym, "BUY" if side > 0 else "SELL", qty, False, cid, px)
            self._log(st, "fill", how=how, qty=got, price=entry, client_id=cid)
            if got <= 0:
                self._log(st, "skip", why="entry not filled (switched off while waiting)", client_id=cid)
                self._put(st)
                return
            r = SimpleNamespace(status="FILLED")
        else:
            r = await c.market_order(OrderIntent(sym, "BUY" if side > 0 else "SELL", qty, False, "net_delta", cid, px))
            got, entry = float(r.executed_qty or 0), float(r.avg_price or 0)
        if str(r.status).upper() != "FILLED" or got <= 0:
            await asyncio.sleep(3)
            try:
                await c.cancel_all(sym)
            except Exception:
                pass
            pos = (await c.fetch_positions([sym])).get(sym)
            got = abs(float(getattr(pos, "qty", 0) or 0))
            entry = float(getattr(pos, "entry_price", 0) or 0) or px
            if got <= 0:
                self._log(st, "skip", why=f"entry not filled ({r.status})", client_id=cid)
                self._put(st)
                self._alert(f"⚠️ {self.NAME} live: the entry was not filled ({r.status}). Nothing is open.")
                return
            self._log(st, "adopted", qty=got, price=entry, client_id=cid)
        if entry <= 0:                  # Binance can answer FILLED without an average price (2026-10-14: ZEC, stop at 0)
            try:
                pos = (await c.fetch_positions([sym])).get(sym)
                entry = float(getattr(pos, "entry_price", 0) or 0)
            except Exception:
                entry = 0.0
            entry = entry if entry > 0 else px                          # the mark at the order, as a last resort
            self._log(st, "entry_price_filled_in", price=entry, client_id=cid)
        stop = entry * (1 - side * DISASTER)
        st["position"] = {"dir": side, "side": "long" if side > 0 else "short", "qty": got, "entry": entry,
                          "entry_ts": self.now(), "signal_t": signal_t, "client_id": cid, "stop": stop,
                          "stop_client_id": None, "fee_est": fee_est if fee_est is not None else got * entry * TAKER_EST}
        self._log(st, "entry", side=st["position"]["side"], qty=got, price=entry, client_id=cid)
        self._put(st)
        try:
            s = await c.place_backstop(sym, "SELL" if side > 0 else "BUY", stop, cid + "s", got)
            if str(s.status).upper() in REJECTED:
                raise RuntimeError(f"the exchange answered {s.status}")
            st["position"]["stop_client_id"] = cid + "s"
            self._log(st, "stop_placed", stop=stop)
            self._put(st)
        except Exception as exc:
            self._log(st, "error", step="disaster stop", error=self.providers.redact(f"{type(exc).__name__}: {exc}"))
            self._put(st)
            self._alert(f"⚠️ {self.NAME} live: the disaster stop could not be placed, closing the position now.")
            await self._close_and_off(st, "no exchange stop could be placed", c)
            return
        self._alert(f"{'📈' if side > 0 else '📉'} <b>{self.NAME} live {'BUY' if side > 0 else 'SELL SHORT'}</b> "
                    f"{got:g} {self._coin(st)} @ {entry:.6g} (1x) · disaster stop {stop:.6g}.")

    async def _maker_fill(self, c: Any, rules: Any, sym: str, side: str, qty: float, reduce_only: bool, cid: str,
                          ref_px: float) -> tuple[float, float, float, str]:
        """Fill `qty` as a maker: a post-only limit at the best bid (buy) / ask (sell), re-priced every MAKER_REPRICE_S,
        for up to MAKER_WINDOW_S; whatever is left then goes as one market order. A switch-off stops the waiting at
        once (an entry keeps what filled, an exit sends the rest at market). Returns (filled, average price, fee
        estimate, 'maker' / 'maker+market' / 'market')."""
        from app.core.types import OrderIntent
        filled = cost = fees = maker_q = 0.0
        deadline = time.monotonic() + MAKER_WINDOW_S
        k = 0

        async def left() -> float:
            want = rules.round_qty_down(qty - filled)
            if reduce_only:                                             # never more than is still held
                pos = (await c.fetch_positions([sym])).get(sym)
                want = min(want, rules.round_qty_down(abs(float(getattr(pos, "qty", 0) or 0))))
            return want

        while time.monotonic() < deadline and not self._abort.is_set():
            rest = await left()
            if rest <= 0 or (not reduce_only and rest < rules.min_order_qty(ref_px)):
                break
            bid, ask = await c.book_ticker(sym)
            price = bid if side == "BUY" else ask
            oid = f"{cid}m{k}"
            k += 1
            try:
                r = await c.post_only_order(OrderIntent(sym, side, rest, reduce_only, "maker", oid, price), price)
            except Exception as exc:
                log.warning("%s maker order: %s", self.NAME, self.providers.redact(f"{type(exc).__name__}: {exc}"))
                break
            status = str(r.status).upper()
            if status in ("EXPIRED", "REJECTED") and not r.executed_qty:   # it would have crossed the spread: again
                await asyncio.sleep(1)
                continue
            until = min(deadline, time.monotonic() + MAKER_REPRICE_S)
            while status != "FILLED" and time.monotonic() < until and not self._abort.is_set():
                await asyncio.sleep(MAKER_POLL_S)
                status = str((await c.get_order(sym, oid)).get("status", "")).upper()
            if status != "FILLED":
                try:
                    await c.cancel_order(sym, oid)
                except Exception:
                    pass
            o = await c.get_order(sym, oid)
            q = float(o.get("executedQty") or 0)
            if q > 0:
                ap = float(o.get("avgPrice") or 0) or price
                filled, cost, fees, maker_q = filled + q, cost + q * ap, fees + q * ap * MAKER_EST, maker_q + q
        rest = await left()
        if rest > 0 and (reduce_only or (not self._abort.is_set() and rest >= rules.min_order_qty(ref_px))):
            r = await c.market_order(OrderIntent(sym, side, rest, reduce_only, "flatten" if reduce_only else "net_delta",
                                                 f"{cid}t", ref_px))
            q = float(r.executed_qty or 0)
            if q > 0:
                ap = float(r.avg_price or 0) or ref_px
                filled, cost, fees = filled + q, cost + q * ap, fees + q * ap * TAKER_EST
        how = "maker" if filled and maker_q >= filled - 1e-12 else ("maker+market" if maker_q > 0 else "market")
        return filled, (cost / filled if filled else 0.0), fees, how

    def request_abort(self) -> None:
        """Called by the switch-off route and the kill switch BEFORE they queue the switch-off on the live loop."""
        self._abort.set()

    async def _exit(self, st: dict[str, Any], reason: str, c: Any | None = None) -> None:
        from app.core.types import OrderIntent
        own = c is None
        c = c or self._client(st["account_id"])
        p, sym = st["position"], st["symbol"]
        d = int(p.get("dir", 1))
        try:
            maker = self.MAKER and not self._abort.is_set()
            if not maker:
                await c.cancel_all(sym)                                 # a maker exit keeps the disaster stop until closed
            pos = (await c.fetch_positions([sym])).get(sym)
            held = max(0.0, d * float(getattr(pos, "qty", 0) or 0))         # this desk's side only
            qty = min(held, float(p["qty"]))
            px = float(getattr(pos, "mark", 0) or 0) or float(p["entry"])
            if qty > 0 and maker:
                rules = (await c.load_rules([sym]))[sym]
                got, avg, fee_x, how = await self._maker_fill(c, rules, sym, "SELL" if d > 0 else "BUY", qty, True,
                                                              f"dl{uuid.uuid4().hex[:12]}", px)
                self._log(st, "fill", how=how, qty=got, price=avg, reduce_only=True)
                await c.cancel_all(sym)                                 # the disaster stop goes once we are flat
                px = avg or px
                p["exit_fee_est"] = fee_x
            elif qty > 0:
                r = await c.market_order(OrderIntent(sym, "SELL" if d > 0 else "BUY", qty, True, "flatten",
                                                     f"dl{uuid.uuid4().hex[:12]}", px))
                if str(r.status).upper() != "FILLED":
                    raise RuntimeError(f"close answered {r.status}")
                px = float(r.avg_price or px)
            left = (await c.fetch_positions([sym])).get(sym)
            if abs(float(getattr(left, "qty", 0) or 0)) > 0:
                raise RuntimeError("the position is still open after the close")
            self._book(st, px, reason, qty or float(p["qty"]))
            self._alert(f"{'📉' if d > 0 else '📈'} <b>{self.NAME} live close</b> {qty:g} {self._coin(st)} @ {px:.6g} · {reason} · "
                        f"trade {st['trades'][-1]['pnl']:+.2f} USDT.")
        except Exception as exc:
            self._log(st, "error", step="close", error=self.providers.redact(f"{type(exc).__name__}: {exc}"))
            self._put(st)
            try:                                                        # never leave it unprotected
                held = abs(float(getattr((await c.fetch_positions([sym])).get(sym), "qty", 0) or 0))
                if held > 0:
                    await c.place_backstop(sym, "SELL" if d > 0 else "BUY", float(p["stop"]), f"dl{uuid.uuid4().hex[:12]}", held)
            except Exception:
                pass
            self._alert(f"🚨 {self.NAME} live: closing the {sym} position FAILED; the disaster stop was re-placed and the "
                        "close is retried. Check the account on Binance.")
        finally:
            if own:
                await self._close(c)

    def _book(self, st: dict[str, Any], exit_px: float, reason: str, qty: float) -> None:
        p = st["position"]
        d = int(p.get("dir", 1))
        fees = p.get("fee_est", 0.0) + (p["exit_fee_est"] if p.get("exit_fee_est") is not None else qty * exit_px * TAKER_EST)
        pnl = d * qty * (exit_px - float(p["entry"])) - fees
        st["realized"] = float(st.get("realized") or 0) + pnl
        st.setdefault("trades", []).append({"side": p.get("side", "long"), "entry_ts": p["entry_ts"], "entry": p["entry"],
                                            "exit_ts": self.now(), "exit": exit_px, "qty": qty, "fees_est": fees, "pnl": pnl,
                                            "reason": reason})
        st["position"] = None
        self._log(st, "exit", qty=qty, price=exit_px, pnl=pnl, reason=reason)
        self._put(st)

    async def _reconcile(self, st: dict[str, Any], c: Any) -> bool:
        """True when the exchange agrees with the desk's position."""
        p, sym = st["position"], st["symbol"]
        pos = (await c.fetch_positions([sym])).get(sym)
        held = float(getattr(pos, "qty", 0) or 0)                       # signed
        ours = int(p.get("dir", 1)) * float(p["qty"])
        if held == 0:
            self._book(st, float(p["stop"]), "position gone on the exchange (disaster stop or closed by hand)", float(p["qty"]))
            try:
                await c.cancel_all(sym)
            except Exception:
                pass
            self._off(self.state(), f"the {sym} position disappeared on the exchange (disaster stop or manual close)")
            return False
        if abs(held - ours) >= 1e-6:
            st["status"] = "HALTED"
            st["reason"] = f"the exchange holds {held:g} {sym} but this desk opened {ours:g}: someone else traded the account"
            self._log(st, "foreign", held=held, ours=ours)
            self._put(st)
            self._alert(f"🚨 {self.NAME} live STOPPED: the {sym} position changed outside this strategy. Nothing was traded; "
                        "check the account on Binance.")
            return False
        return True

    async def _loss_hit(self, st: dict[str, Any], c: Any) -> bool:
        p, sym = st["position"], st["symbol"]
        d = int(p.get("dir", 1))
        pos = (await c.fetch_positions([sym])).get(sym)
        mark = float(getattr(pos, "mark", 0) or 0) or float(p["entry"])
        equity = float(st["amount"]) + float(st.get("realized") or 0) + d * float(p["qty"]) * (mark - float(p["entry"])) \
            - p.get("fee_est", 0.0) - float(p["qty"]) * mark * TAKER_EST
        if float(st["amount"]) - equity >= float(st["max_loss"]):
            self._log(st, "loss_limit", equity=equity)
            self._put(st)
            await self._close_and_off(st, f"loss limit {st['max_loss']:g} USDT reached", c)
            return True
        return False

    # -- testnet round trip on a linked TESTNET account -----------------------------------------------------------------------
    async def verify(self, account_id: str) -> dict[str, Any]:
        from app.core.types import OrderIntent
        from app.live.mirror import REJECTED
        acct = self.accounts.get(account_id)
        if acct["network"] != "testnet":
            raise LiveError("the round trip runs on a linked TESTNET account")
        if acct.get("owner"):
            raise LiveError(f"in use by {acct['owner']['ref']}")
        detail: dict[str, Any] = {"account": acct["label"], "steps": []}
        ok, c = False, self.accounts.client(account_id)
        try:
            want = self.symbols()[0] if self.symbols() else "ETHUSDT"
            rules_all = await c.load_rules([want])
            sym = want if want in rules_all else "ETHUSDT"
            rules = rules_all.get(sym) or (await c.load_rules([sym]))[sym]
            detail["symbol"] = sym
            px = float((await c.fetch_premium_index(sym)).mark)
            qty = rules.min_order_qty(px, 1.2)
            await c.ensure_account_mode([sym], LEVERAGE)
            cid = f"vz{uuid.uuid4().hex[:10]}"
            r = await c.market_order(OrderIntent(sym, "BUY", qty, False, "net_delta", cid, px))
            detail["steps"].append({"open": r.status, "qty": r.executed_qty, "price": r.avg_price})
            if str(r.status).upper() == "FILLED":
                s = await c.place_backstop(sym, "SELL", px * (1 - DISASTER), cid + "s", r.executed_qty)
                detail["steps"].append({"stop": s.status})
                stop_ok = str(s.status).upper() not in REJECTED
                await c.cancel_all(sym)
                x = await c.market_order(OrderIntent(sym, "SELL", r.executed_qty, True, "flatten", cid + "c", px))
                detail["steps"].append({"close": x.status, "price": x.avg_price})
                left = await c.fetch_positions([sym])
                flat = abs(float(getattr(left.get(sym), "qty", 0.0) or 0.0)) == 0
                detail["steps"].append({"flat": flat})
                ok = stop_ok and str(x.status).upper() == "FILLED" and flat
        except Exception as exc:
            detail["error"] = self.providers.redact(f"{type(exc).__name__}: {exc}")
        finally:
            await self._close(c)
        self.save_check("binance", "testnet", self.now(), ok, detail)
        return {"ok": ok, **detail}

    def _client(self, account_id: str) -> Any:
        c = self._clients.get(account_id)
        if c is None:
            c = self._clients[account_id] = self.accounts.client(account_id)
        return c

    async def _drop_client(self, account_id: str | None) -> None:
        c = self._clients.pop(account_id, None) if account_id else None
        if c is not None:
            try:
                await c.close()
            except Exception:
                pass

    async def _close(self, c: Any) -> None:
        """Close a one-off client; the cached per-account clients stay open until the strategy switches off."""
        if any(c is x for x in self._clients.values()):
            return
        try:
            await c.close()
        except Exception:
            pass

    # -- helper for subclasses: completed 4h candles, refreshed on demand ------------------------------------------------------
    def completed_bars(self, key: str, since: int, fetch: Callable[[int, int], list], tf: int = TF_MS) -> list[dict[str, Any]]:
        bars = self.svc.store.bars(key, since)
        expect = (self.now() // tf - 1) * tf                            # open time of the newest COMPLETED candle
        if not bars or bars[-1]["t"] < expect:
            self.svc.store.save_bars(key, fetch(bars[-1]["t"] if bars else since, self.now()))
            bars = self.svc.store.bars(key, since)
        return bars
