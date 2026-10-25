"""Ensemble LIVE switch: copies the Ensemble paper book (app/ensemble/service.py) onto ONE linked Binance USD-M account.

The operator asked for it on 2026-10-23 ("make it live now"). It ships OFF; nothing trades until the operator switches it
on from the private dashboard ("are you sure?" -> the dashboard sends the phrase), a linked account no other strategy owns
and an amount. A loss limit is optional (operator 2026-10-25: none by default, a stop-out can lock in a loss the research
book would have recovered).

How it trades (the research book, scaled to the account):
- every coin the book holds is held on the account with units x (allocation equity / book equity) x SCALE, isolated 1x
  (as the research modelled: a 1x short is liquidated by the exchange near +100 %, a 1x long cannot be);
- on switch-on it opens the book's current positions at once; afterwards it trades only the coins the book trades
  that day (the research's own rebalance days: rotation weekly, ML daily, momentum on flips + the 1st, all coins
  when the weekly weights change), just after 00:00 UTC, with market orders: reductions first, then additions;
- an order below the exchange minimum is skipped (closing a position is always allowed: reduce-only is exempt);
- allocation equity = amount + the account's equity change since switch-on (a dedicated account);
- every TICK_S: reconciliation (a position nobody recorded, or a different size -> HALTED, nothing traded; a recorded
  position that vanished is booked as closed on the exchange, e.g. a 1x short liquidated), the loss limit
  (only if one was set: allocation equity <= amount - loss limit -> close everything, OFF), the day's trades;
- switching off and the kill switch close every position (reduce-only market) and release the account.
Mainnet also needs LIVE_MIRROR_MAINNET_ENABLED=true and the testnet gate (app/live/desk_live.py mainnet_gate).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from app.live.desk_live import LIVE_STATES, DeskLive, LiveError

log = logging.getLogger(__name__)

SCALE = 0.95              # 5 % of the allocation stays free for fees, funding and price moves between rebalances
MIN_AMOUNT = 500.0        # below this many book positions fall under Binance's minimum order and the copy drifts
TAKER = 0.0005
QTY_TOL = 1e-9


class EnsembleLive(DeskLive):
    NAME = "Ensemble"
    KIND, REF = "bot", "Ensemble"
    CONFIRM = "GO LIVE ENSEMBLE"
    LABEL = "Worker 1 (Ensemble)"
    STATE_KEY = "live"
    SHORTS = True
    THREAD = "ensemble-live"
    MIN_AMOUNT = MIN_AMOUNT

    def __init__(self, book: Any, *a: Any, **k: Any):
        super().__init__(book, *a, **k)                    # DeskLive keeps its state in book.store (get / set)
        self.book = book

    def symbols(self) -> list[str]:
        t = self.book.latest() or {}
        return sorted(t.get("targets", {}))

    # -- view ------------------------------------------------------------------------------------------------------------------
    def view(self) -> dict[str, Any]:
        st = self.state()
        acct = None
        if st.get("account_id"):
            try:
                a = self.accounts.get(st["account_id"])
                acct = {"id": a["id"], "label": a["label"], "network": a["network"]}
            except Exception:
                acct = {"id": st["account_id"], "label": "(removed)", "network": st.get("network")}
        pos = st.get("positions") or {}
        today = self.book.latest() or {}
        return {"desk": self.NAME, "status": st.get("status", "OFF"), "account": acct, "amount": st.get("amount"),
                "max_loss": st.get("max_loss"), "equity": st.get("equity"), "armed_ts": st.get("armed_ts"),
                "stopped_ts": st.get("stopped_ts"), "reason": st.get("reason"), "last_day": st.get("last_day"),
                "positions": {s: q for s, q in pos.items() if q}, "n_positions": sum(1 for q in pos.values() if q),
                "skipped": st.get("skipped") or [], "trades": (st.get("trades") or [])[-80:],
                "log": (st.get("log") or [])[-60:], "confirm_phrase": self.CONFIRM, "book_day": today.get("date"),
                "book_positions": len(today.get("targets", {})),
                "rules": {"leverage": 1, "scale": SCALE, "min_amount": self.MIN_AMOUNT, "tick_s": 60, "isolated": True},
                "mainnet": self.mainnet_gate(), "max_amount": self.providers.max_amount(),
                "accounts": self.accounts.public(), "shared_keys": self.accounts.shared_keys()}

    def _book_ready(self, today: dict[str, Any] | None) -> bool:
        return bool(today and today.get("targets"))

    def _armed_note(self, today: dict[str, Any]) -> str:
        return f"opening the book's {len(today['targets'])} positions now (1x isolated)."

    # -- switching on / off --------------------------------------------------------------------------------------------------
    async def arm(self, account_id: str, amount: Any, max_loss: Any, confirm: str, symbol: str | None = None) -> dict[str, Any]:
        st = self.state()
        if st.get("status") in LIVE_STATES:
            raise LiveError(f"the Ensemble is {st['status']}: switch it off first")
        if any(st.get("positions", {}).values()):
            raise LiveError("live positions from a previous run are still recorded: resolve them first")
        self._abort.clear()
        if (confirm or "").strip() != self.CONFIRM:
            raise LiveError(f'type "{self.CONFIRM}" to confirm')
        today = self.book.latest()
        if not self._book_ready(today):
            raise LiveError("the paper book has not computed today's positions yet; try again in a few minutes")
        acct = self.accounts.get(account_id)
        if acct["exchange"] != "binance":
            raise LiveError("the Ensemble trades Binance USD-M only")
        try:
            amount = float(amount)
            max_loss = float(max_loss) if max_loss not in (None, "", 0) else None
        except (TypeError, ValueError):
            raise LiveError("amount and loss limit must be numbers") from None
        if not self.MIN_AMOUNT <= amount <= self.providers.max_amount():
            raise LiveError(f"amount must be between {self.MIN_AMOUNT:g} and {self.providers.max_amount():g} USDT "
                            "(LIVE_MIRROR_MAX_AMOUNT_USDT)")
        if max_loss is not None and not 0 < max_loss <= amount:
            raise LiveError("the loss limit must be above 0 and at most the amount")
        if acct["network"] == "mainnet":
            g = self.mainnet_gate()
            if not g["switch"]:
                raise LiveError("mainnet is locked on the server: set LIVE_MIRROR_MAINNET_ENABLED=true (operator)")
            if not g["testnet_passed"]:
                raise LiveError("mainnet is locked until a Binance testnet round trip has passed")
        c = self._client(account_id)
        try:
            bal = await c.fetch_balance()
            if float(bal.get("available") or 0) < amount:
                raise LiveError(f"available balance {float(bal.get('available') or 0):.2f} USDT is below the amount {amount:.2f}")
            held = await c.fetch_positions([], all_symbols=True)
            busy = [s for s, p in held.items() if abs(float(p.qty or 0)) > 0]
            if busy:
                raise LiveError("the account is not flat (open positions: " + ", ".join(sorted(busy)[:5]) +
                                "). The Ensemble needs a dedicated, flat account.")
            self.accounts.claim(account_id, self.KIND, self.REF)
        except Exception:
            await self._drop_client(account_id)
            raise
        base = float(bal.get("wallet") or 0) + float(bal.get("upnl") or 0)
        st = {"status": "ARMED", "account_id": account_id, "network": acct["network"], "amount": amount, "max_loss": max_loss,
              "base_equity": base, "equity": amount, "positions": {}, "modes": [], "armed_ts": self.now(), "stopped_ts": None,
              "reason": None, "last_day": None, "skipped": [], "trades": st.get("trades") or [], "log": st.get("log") or []}
        self._log(st, "armed", account=acct["label"], network=acct["network"], amount=amount, max_loss=max_loss)
        self._put(st)
        self._alert(f"🟢 <b>{self.LABEL} LIVE ON</b> · {acct['label']} ({acct['network']}) · {amount:g} USDT, "
                    + (f"loss limit {max_loss:g} USDT" if max_loss else "no loss limit") + f" · {self._armed_note(today)}")
        try:
            await self._rebalance(self.state(), c, today, full=True)
        except Exception as exc:
            st = self.state()
            self._log(st, "error", step="first rebalance", error=self.providers.redact(f"{type(exc).__name__}: {exc}"))
            self._put(st)
        return self.view()

    async def disarm(self, reason: str) -> dict[str, Any]:
        st = self.state()
        if st.get("status") not in LIVE_STATES and not any((st.get("positions") or {}).values()):
            return self.view()
        if not await self._close_and_off(st, reason):
            raise LiveError("closing some positions failed; the close is retried every minute (status CLOSING)")
        return self.view()

    async def _close_and_off(self, st: dict[str, Any], reason: str, c: Any | None = None) -> bool:
        c = c or self._client(st["account_id"])
        left = await self._flatten(st, c, reason)
        st = self.state()
        if left:
            st.update(status="CLOSING", reason=reason)
            self._put(st)
            self._alert(f"🚨 {self.LABEL} live: {len(left)} position(s) could not be closed ({', '.join(left[:5])}); retrying. "
                        "Check the account on Binance.")
            return False
        self._off(st, reason)
        return True

    async def _flatten(self, st: dict[str, Any], c: Any, reason: str) -> list[str]:
        from app.core.types import OrderIntent
        failed = []
        held = await c.fetch_positions([], all_symbols=True)
        for sym, q in list((st.get("positions") or {}).items()):
            ex = float(getattr(held.get(sym), "qty", 0) or 0)
            if not q or not ex:
                st["positions"][sym] = 0.0
                continue
            qty = min(abs(ex), abs(q)) if ex * q > 0 else 0.0
            if qty <= 0:
                st["positions"][sym] = 0.0
                continue
            try:
                px = float(getattr(held.get(sym), "mark", 0) or 0)
                r = await c.market_order(OrderIntent(sym, "SELL" if q > 0 else "BUY", qty, True, "flatten",
                                                     f"en{uuid.uuid4().hex[:12]}", px))
                if str(r.status).upper() != "FILLED":
                    raise RuntimeError(f"close answered {r.status}")
                self._fill(st, sym, -q, float(r.avg_price or px), reason)
                st["positions"][sym] = 0.0
            except Exception as exc:
                failed.append(sym)
                self._log(st, "error", step=f"close {sym}", error=self.providers.redact(f"{type(exc).__name__}: {exc}"))
            self._put(st)
        return failed

    # -- the loop ----------------------------------------------------------------------------------------------------------
    async def tick(self) -> None:
        st = self.state()
        if st.get("status") not in ("ARMED", "CLOSING"):
            return
        c = self._client(st["account_id"])
        if st["status"] == "CLOSING":
            await self._close_and_off(st, st.get("reason") or "closing", c)
            return
        if not await self._reconcile_all(st, c):
            return
        st = self.state()
        if await self._loss_hit_all(st, c):
            return
        today = self.book.latest()
        if today and today.get("day") and today["day"] != st.get("last_day"):
            await self._rebalance(self.state(), c, today, full=False)

    async def _equity(self, st: dict[str, Any], c: Any) -> float:
        bal = await c.fetch_balance()
        return float(st["amount"]) + float(bal.get("wallet") or 0) + float(bal.get("upnl") or 0) - float(st["base_equity"])

    async def _rebalance(self, st: dict[str, Any], c: Any, today: dict[str, Any], full: bool) -> None:
        """Move the account to the book's units for the coins the book trades today (all coins when `full`)."""
        from app.core.types import OrderIntent
        eq = await self._equity(st, c)
        st["equity"] = eq
        ratio = SCALE * eq / float(today["equity"])
        targets = today.get("targets", {})
        pos = st.setdefault("positions", {})
        coins = set(targets) | {s for s, q in pos.items() if q} if full else set(today.get("trades", {}))
        coins |= {s for s, q in pos.items() if q and s not in targets}          # the book no longer holds it
        if not coins:
            st["last_day"] = today["day"]
            self._put(st)
            return
        try:
            rules = await c.load_rules(sorted(coins))
        except ValueError:                                                      # a delisted coin: load what exists
            rules = {}
            for s in sorted(coins):
                try:
                    rules.update(await c.load_rules([s]))
                except ValueError:
                    pass
        plan = []
        for s in sorted(coins):
            t = targets.get(s)
            want = (t["units"] * ratio) if t else 0.0
            have = float(pos.get(s) or 0)
            r = rules.get(s)
            if r is None:
                continue
            want = (1 if want > 0 else -1) * r.round_qty_down(abs(want)) if want else 0.0
            plan.append((s, have, want, r, float(t["price"]) if t else 0.0))
        skipped = []
        # reductions / closes first (frees margin), then increases and new positions
        order = sorted(plan, key=lambda x: 0 if abs(x[2]) < abs(x[1]) or x[1] * x[2] < 0 else 1)
        for s, have, want, r, ref in order:
            delta = want - have
            if abs(delta) <= QTY_TOL:
                continue
            px = ref or float((await c.fetch_premium_index(s)).mark)
            legs: list[tuple[float, bool]] = []
            if have and (want == 0 or have * want < 0):                         # close (and maybe reverse)
                legs.append((-have, True))
                if want:
                    legs.append((want, False))
            elif abs(want) < abs(have):
                legs.append((delta, True))
            else:
                legs.append((delta, False))
            for q, reduce in legs:
                qty = r.round_qty_down(abs(q)) if not reduce else min(abs(q), abs(float(pos.get(s) or 0)))
                if qty <= 0 or not r.qty_ok(qty, px, reduce):
                    skipped.append({"symbol": s, "qty": round(q, 8), "why": "below the exchange minimum"})
                    continue
                if not reduce and s not in (st.get("modes") or []):
                    try:
                        await c.ensure_account_mode([s], 1)
                        st.setdefault("modes", []).append(s)
                    except Exception as exc:
                        skipped.append({"symbol": s, "qty": round(q, 8), "why": f"account mode: {type(exc).__name__}"})
                        continue
                cid = f"en{uuid.uuid4().hex[:12]}"
                try:
                    res = await c.market_order(OrderIntent(s, "BUY" if q > 0 else "SELL", qty, reduce,
                                                           "flatten" if reduce else "net_delta", cid, px))
                    got = float(res.executed_qty or 0)
                    if got <= 0:
                        raise RuntimeError(f"order answered {res.status}")
                    signed = got if q > 0 else -got
                    self._fill(st, s, signed, float(res.avg_price or px), "rebalance", cid)
                    pos[s] = round(float(pos.get(s) or 0) + signed, 12)
                except Exception as exc:
                    skipped.append({"symbol": s, "qty": round(q, 8), "why": self.providers.redact(f"{type(exc).__name__}: {exc}")[:160]})
                    self._log(st, "error", step=f"order {s}", error=self.providers.redact(f"{type(exc).__name__}: {exc}"))
                self._put(st)
        st["last_day"] = today["day"]
        st["skipped"] = skipped[-50:]
        self._log(st, "rebalance", day=today["date"], coins=len(coins), skipped=len(skipped), equity=round(eq, 2))
        self._put(st)
        n = sum(1 for q in pos.values() if q)
        self._alert(f"🔁 {self.LABEL} live {today['date']}: traded {len(coins) - len(skipped)} coin(s), {n} open, "
                    f"equity {eq:.2f} USDT" + (f" · {len(skipped)} skipped (below minimum / error)" if skipped else ""))

    def _fill(self, st: dict[str, Any], sym: str, signed: float, price: float, why: str, cid: str | None = None) -> None:
        st.setdefault("trades", []).append({"ts": self.now(), "symbol": sym, "qty": signed, "price": price,
                                            "fee_est": abs(signed) * price * TAKER, "why": why, "client_id": cid})

    async def _reconcile_all(self, st: dict[str, Any], c: Any) -> bool:
        held = await c.fetch_positions([], all_symbols=True)
        pos = st.get("positions") or {}
        foreign = []
        for s, p in held.items():
            q = float(p.qty or 0)
            mine = float(pos.get(s) or 0)
            if abs(q - mine) > max(QTY_TOL, 1e-6 * abs(mine)) and q:
                foreign.append((s, q, mine))
        if foreign:
            st["status"] = "HALTED"
            st["reason"] = "the account holds positions this bot did not open: " + ", ".join(
                f"{s} {q:g} (bot {m:g})" for s, q, m in foreign[:5])
            self._log(st, "foreign", rows=[[s, q, m] for s, q, m in foreign[:20]])
            self._put(st)
            self._alert(f"🚨 {self.LABEL} live STOPPED: the account changed outside this bot. Nothing was traded; check the "
                        "account on Binance.")
            return False
        gone = [s for s, q in pos.items() if q and s not in held]
        for s in gone:                                                          # liquidated / delisted / closed by hand
            self._log(st, "gone", symbol=s, qty=pos[s])
            self._alert(f"⚠️ {self.LABEL} live: the {s} position ({pos[s]:g}) closed on the exchange (liquidation, delisting "
                        "or a manual close). The bot carries on without it.")
            pos[s] = 0.0
        if gone:
            self._put(st)
        return True

    async def _loss_hit_all(self, st: dict[str, Any], c: Any) -> bool:
        eq = await self._equity(st, c)
        st["equity"] = eq
        self._put(st)
        if st.get("max_loss") and float(st["amount"]) - eq >= float(st["max_loss"]):
            self._log(st, "loss_limit", equity=eq)
            self._put(st)
            await self._close_and_off(st, f"loss limit {st['max_loss']:g} USDT reached", c)
            return True
        return False
