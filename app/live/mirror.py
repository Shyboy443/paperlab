"""LIVE MIRROR: an operator-armed copy of ONE qualified paper bot on a real exchange account (testnet by default).

The paper bot keeps deciding exactly as before; the mirror only follows its events:

    open    the paper bot opened a position -> a MARKET order on the exchange, sized from the operator's amount and
            risk (risk_usd = amount x risk_pct, qty = risk_usd / stop distance, rounded DOWN to the exchange step),
            then an exchange-side reduce-only STOP at the paper stop (it protects the account even if this server
            is down)
    closed  the paper bot closed (target, stop, time stop) -> a reduce-only MARKET close and the stop cancelled

Guards (all enforced server-side, every one recorded in live_mirror_log):
    * only QUALIFIED bots; the operator types "GO LIVE <bot>"; amount <= LIVE_MIRROR_MAX_AMOUNT_USDT and <= balance
    * MAINNET needs LIVE_MIRROR_MAINNET_ENABLED=true and a recorded testnet round trip on that exchange
    * risk per trade <= 2%; daily and total loss limits stop the mirror and close its position
    * STOP per mirror; the global kill switch stops every mirror and closes their positions
    * re-derived / replayed paper events are ignored: only live decisions are mirrored
    * reconciliation every 30 s: a stop that fired on the exchange is booked; a live position the paper bot no longer
      holds is closed; a position this mirror did not open is never touched
    * an entry the exchange did not confirm is settled against the account (adopted and protected if it filled,
      cancelled otherwise); a stop the exchange rejects closes the position; a close that fails puts the stop back
    * loss limits are checked on realized PnL after each close AND mark-to-market every reconciliation
    * SAFE MODE: while the followed paper program is unhealthy (feed stale, worker down) or the exchange keeps failing,
      no new entry is mirrored; open positions stay protected by their exchange stop and keep being managed
    * every one of these events also goes to the operator's Telegram (alert hook)
    * the bot must STILL be QUALIFIED at every new entry (not only when armed): a bot that loses its qualification or
      is eliminated stops opening live trades (its open position keeps being managed) until it qualifies again
    * portfolio limits across mirrors on one account: the armed amounts together never exceed the account's equity,
      and at most MAX_SAME_DIRECTION live positions point the same way at once (crypto moves together: two longs on
      two coins are close to one bigger long)

The mirror never enlarges a position, never averages, never trades another symbol.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from typing import Any, Callable

log = logging.getLogger("paperlab.live.mirror")

MAX_RISK_PCT = 0.02
RECONCILE_S = 30.0
MAX_LEVERAGE = 10
TAKER_FEE_EST = 0.0006
UNCONFIRMED_RECHECK_S = 1.5             # let a late fill land before reading the account back
EXCHANGE_FAILS_FOR_SAFE = 3             # consecutive failed reconciliations on one exchange -> SAFE MODE there
REJECTED = {"REJECTED", "CANCELED", "CANCELLED", "EXPIRED", "DEACTIVATED"}
MAX_SAME_DIRECTION = 2                  # live positions in one direction on one account at a time (correlated exposure)


class MirrorError(ValueError):
    pass


class MirrorService:
    def __init__(self, storage: Any, providers: Any, bot_view: Callable[[str, str], dict[str, Any] | None],
                 clock: Callable[[], float] = time.time, alert: Callable[[str], None] | None = None):
        self.storage = storage
        self.alert_fn = alert                        # the operator's Telegram (main sets it: telegram.alert)
        self.safe: dict[str, str] = {}               # program -> why no new entries are mirrored
        self.exchange_safe: dict[tuple[str, str], str] = {}
        self._fails: dict[tuple[str, str], int] = {}
        self.recheck_s = UNCONFIRMED_RECHECK_S
        self.providers = providers
        self.bot_view = bot_view                     # (program, bot_key) -> the bot's arena row incl. program_status
        self.clock = clock
        self.loop: asyncio.AbstractEventLoop | None = None
        self.mirrors: dict[str, dict[str, Any]] = {}
        self._clients: dict[tuple[str, str], Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._task: asyncio.Task | None = None

    # -- lifecycle -------------------------------------------------------------------------------------------------
    def load(self) -> None:
        self.mirrors = {m["id"]: m for m in self.storage.mirrors() if m["status"] == "ARMED" or m.get("position")}

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.load()
        self._task = asyncio.create_task(self._reconcile_loop(), name="live-mirror")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
        for c in self._clients.values():
            try:
                await c.close()
            except Exception:
                pass
        self._clients.clear()

    def _now(self) -> int:
        return int(self.clock() * 1000)

    def _log(self, m: dict[str, Any], kind: str, **detail: Any) -> None:
        self.storage.mirror_log(m["id"], self._now(), kind, {k: (self.providers.redact(v) if k == "error" else v)
                                                             for k, v in detail.items()})

    def _save(self, m: dict[str, Any]) -> None:
        self.storage.mirror_save(m)

    def _alert(self, text: str) -> None:
        if self.alert_fn is not None:
            try:
                self.alert_fn(self.providers.redact(text))
            except Exception:
                pass

    def _armed(self, program: str | None = None) -> list[dict[str, Any]]:
        return [m for m in self.mirrors.values() if m["status"] == "ARMED" and (program is None or m["program"] == program)]

    def set_program_safe(self, program: str, reason: str | None) -> None:
        """SAFE MODE for one followed program (called by the watchdog): no new mirrored entries while it is unhealthy."""
        was = self.safe.get(program)
        if reason == was:
            return
        if reason:
            self.safe[program] = reason
        else:
            self.safe.pop(program, None)
        for m in self._armed(program):
            self._log(m, "safe_mode", on=bool(reason), reason=reason or was)
        if self._armed(program):
            self._alert(f"🛡 <b>SAFE MODE {'ON' if reason else 'OFF'}</b> for live {program.upper()} mirrors: "
                        + (f"{reason}. No new live entries; open positions keep their exchange stop." if reason
                           else "the program is healthy again; new live entries resume."))

    def safe_reason(self, m: dict[str, Any]) -> str | None:
        return self.safe.get(m["program"]) or self.exchange_safe.get((m["exchange"], m["network"]))

    def health(self) -> dict[str, Any]:
        return {"armed": len(self._armed()), "with_position": sum(1 for m in self.mirrors.values() if m.get("position")),
                "safe_mode": {**self.safe, **{f"{e}:{n}": r for (e, n), r in self.exchange_safe.items()}}}

    async def _client(self, exchange: str, network: str, symbol: str) -> Any:
        key = (exchange, network)
        c = self._clients.get(key)
        if c is None:
            c = self.providers.client(exchange, network)
            self._clients[key] = c
        if symbol not in (getattr(c, "rules", None) or {}):
            await c.load_rules([symbol])
        return c

    # -- arming --------------------------------------------------------------------------------------------------
    async def arm(self, req: dict[str, Any]) -> dict[str, Any]:
        program, bot_key = str(req.get("program") or "v8"), str(req.get("bot_key") or "")
        exchange, network = str(req.get("exchange") or ""), str(req.get("network") or "testnet")
        amount = float(req.get("amount_usdt") or 0)
        risk = float(req.get("risk_pct") or 0.01)
        max_daily = float(req.get("max_daily_loss") or amount * 0.1)
        max_total = float(req.get("max_total_loss") or amount * 0.25)
        if str(req.get("confirm") or "").strip() != f"GO LIVE {bot_key}":
            raise MirrorError(f'type exactly "GO LIVE {bot_key}" to confirm')
        if (exchange, network) not in {("bybit", "testnet"), ("bybit", "mainnet"), ("binance", "testnet"), ("binance", "mainnet")}:
            raise MirrorError("choose Bybit or Binance, testnet or mainnet")
        if program == "v11":
            raise MirrorError("V11 scanner bots trade many coins from one book; the live mirror follows a single coin, "
                              "so scanners cannot go live yet")
        row = self.bot_view(program, bot_key)
        if not row:
            raise MirrorError(f"no {program} bot {bot_key}")
        if row.get("program_status") != "QUALIFIED":
            raise MirrorError(f"{bot_key} is {row.get('program_status') or 'not qualified'}: only QUALIFIED bots can go live")
        if bot_key.endswith("+LADDER"):
            raise MirrorError("+LADDER bots take partial profits and move their stop; the live mirror copies whole "
                              "positions with a fixed stop, so ladder bots cannot go live yet")
        if any(m["bot_key"] == bot_key and m["status"] == "ARMED" for m in self.mirrors.values()):
            raise MirrorError(f"{bot_key} is already live")
        if not 0 < risk <= MAX_RISK_PCT:
            raise MirrorError("risk per trade must be above 0 and at most 2%")
        if not 0 < amount <= self.providers.max_amount():
            raise MirrorError(f"amount must be above 0 and at most {self.providers.max_amount():g} USDT (LIVE_MIRROR_MAX_AMOUNT_USDT)")
        if not 0 < max_daily <= amount or not 0 < max_total <= amount:
            raise MirrorError("loss limits must be above 0 and at most the amount")
        if self.providers.keys(exchange, network) is None:
            raise MirrorError(f"{exchange} {network} keys are not configured on the server")
        if network == "mainnet":
            if not self.providers.mainnet_switch():
                raise MirrorError("mainnet is locked: the operator must set LIVE_MIRROR_MAINNET_ENABLED=true")
            if not any(c["ok"] and c["network"] == "testnet" for c in self.storage.provider_checks(exchange)):
                raise MirrorError(f"mainnet is locked until a {exchange} testnet round trip has passed")
        bal = await self.providers.balance(exchange, network, max_age_s=0)
        if not bal.get("ok"):
            raise MirrorError("could not read the balance: " + str(bal.get("error")))
        if bal["available"] < amount:
            raise MirrorError(f"available balance {bal['available']:.2f} USDT is below the amount {amount:.2f}")
        committed = sum(float(x["amount_usdt"]) for x in self._armed() if (x["exchange"], x["network"]) == (exchange, network))
        equity = float(bal.get("wallet") or bal["available"])
        if committed + amount > equity:
            raise MirrorError(f"{committed:.2f} USDT is already committed to live mirrors on this account; with "
                              f"{amount:.2f} more it would exceed the account equity {equity:.2f}")
        symbol = row.get("symbol") or (row.get("coin", "") + "USDT")
        m = {"id": "m" + uuid.uuid4().hex[:10], "program": program, "bot_key": bot_key, "symbol": symbol,
             "exchange": exchange, "network": network, "amount_usdt": amount, "risk_pct": risk, "max_daily_loss": max_daily,
             "max_total_loss": max_total, "status": "ARMED", "created_ts": self._now(), "stopped_ts": None, "reason": None,
             "position": None, "realized": 0.0, "daily": {}, "trades": 0}
        self.mirrors[m["id"]] = m
        self._save(m)
        self._log(m, "armed", exchange=exchange, network=network, amount=amount, risk_pct=risk, balance=bal)
        self._alert(f"🟢 <b>LIVE MIRROR ARMED</b> {bot_key} on {exchange} {network}: {amount:g} USDT, risk {risk:.2%} "
                    f"per trade, daily loss limit {max_daily:g}, total {max_total:g}")
        return self.public(m)

    async def disarm(self, mirror_id: str, reason: str = "operator STOP", flatten: bool = True) -> dict[str, Any]:
        m = self.mirrors.get(mirror_id)
        if m is None:
            raise MirrorError("no such live mirror")
        async with self._lock(m):
            if flatten and m.get("position"):
                await self._close_live(m, "stopped: " + reason)
            m.update(status="STOPPED", stopped_ts=self._now(), reason=reason)
            self._save(m)
            self._log(m, "stopped", reason=reason)
        self._alert(f"⏹ <b>LIVE MIRROR STOPPED</b> {m['bot_key']} ({m['exchange']} {m['network']}): {reason}"
                    + (" — a live position is STILL OPEN, check the exchange" if m.get("position") else ""))
        return self.public(m)

    async def disarm_all(self, reason: str) -> int:
        if self._armed() or any(m.get("position") for m in self.mirrors.values()):
            self._alert(f"🚨 <b>KILL SWITCH</b>: stopping every live mirror ({reason}).")
        n = 0
        for mid, m in list(self.mirrors.items()):
            if m["status"] == "ARMED" or m.get("position"):
                try:
                    await self.disarm(mid, reason)
                    n += 1
                except Exception as exc:
                    log.error("could not stop mirror %s: %s", mid, self.providers.redact(exc))
        return n

    def _lock(self, m: dict[str, Any]) -> asyncio.Lock:
        return self._locks.setdefault(m["id"], asyncio.Lock())

    # -- following the paper bot ------------------------------------------------------------------------------------
    def on_event(self, program: str, data: dict[str, Any]) -> None:
        """Called from the forward service's pump thread for every published event."""
        if self.loop is None or data.get("rederived") or data.get("counterfactual"):
            return
        if data.get("type") not in ("open", "closed"):
            return
        if not any(m["status"] == "ARMED" and m["program"] == program and m["bot_key"] == data.get("bot_key")
                   for m in self.mirrors.values()):
            return
        asyncio.run_coroutine_threadsafe(self.handle(program, dict(data)), self.loop)

    async def handle(self, program: str, ev: dict[str, Any]) -> None:
        for m in list(self.mirrors.values()):
            if m["status"] != "ARMED" or m["program"] != program or m["bot_key"] != ev.get("bot_key"):
                continue
            async with self._lock(m):
                try:
                    if ev["type"] == "open":
                        await self._open_live(m, ev)
                    elif ev["type"] == "closed" and m.get("position"):
                        await self._close_live(m, "paper exit: " + str(ev.get("exit_kind") or "close"))
                except Exception as exc:
                    self._log(m, "error", step=ev["type"], error=f"{type(exc).__name__}: {exc}")

    async def _held(self, c: Any, symbol: str) -> float:
        """The account's SIGNED position on the symbol (+long / -short)."""
        live = await c.fetch_positions([symbol])
        return float(getattr(live.get(symbol), "qty", 0.0) or 0.0)

    async def _open_live(self, m: dict[str, Any], ev: dict[str, Any]) -> None:
        from app.core.types import OrderIntent
        if m.get("position"):
            self._log(m, "skip", why="a live position is already open", event_ts=ev.get("ts"))
            return
        why = self.safe_reason(m)
        if why:
            self._log(m, "skip", why="SAFE MODE: " + why, event_ts=ev.get("ts"))
            return
        row = self.bot_view(m["program"], m["bot_key"]) or {}
        state = row.get("program_status") or "unknown"
        if state != "QUALIFIED":
            self._log(m, "skip", why=f"the paper bot is {state}, no longer QUALIFIED", event_ts=ev.get("ts"))
            if m.get("unqualified_alerted") != state:
                m["unqualified_alerted"] = state
                self._save(m)
                self._alert(f"⏸ <b>{m['bot_key']}</b> is {state} on paper, no longer QUALIFIED: its live mirror takes no "
                            "new entries until it qualifies again.")
            return
        m.pop("unqualified_alerted", None)
        same = [x for x in self.mirrors.values() if x is not m and x.get("position") and x["position"].get("side") == ev.get("side")
                and (x["exchange"], x["network"]) == (m["exchange"], m["network"])]
        if len(same) >= MAX_SAME_DIRECTION:
            self._log(m, "skip", why=f"correlated exposure cap: {len(same)} live {ev.get('side')} positions already open "
                                     f"on this account ({', '.join(x['symbol'] for x in same)})", event_ts=ev.get("ts"))
            self._alert(f"⛔ <b>{m['bot_key']}</b>: live entry skipped — {len(same)} {ev.get('side')} positions are already "
                        "open on this account (correlated exposure cap).")
            return
        price, stop, side = float(ev.get("price") or 0), float(ev.get("stop") or 0), ev.get("side")
        dist = abs(price - stop)
        if price <= 0 or dist <= 0 or side not in ("long", "short"):
            self._log(m, "skip", why="the paper open carries no usable price / stop", event=ev.get("ts"))
            return
        c = await self._client(m["exchange"], m["network"], m["symbol"])
        rules = c.rules[m["symbol"]]
        qty = rules.round_qty_down((m["amount_usdt"] * m["risk_pct"]) / dist)
        notional = qty * price
        if qty < rules.min_qty or notional < rules.min_notional:
            self._log(m, "skip", why="the risk-sized order is below the exchange minimum", qty=qty, notional=round(notional, 4),
                      min_qty=rules.min_qty, min_notional=rules.min_notional)
            return
        lev = min(MAX_LEVERAGE, max(1, math.ceil(notional / m["amount_usdt"] * 1.25)))
        try:
            await c.ensure_account_mode([m["symbol"]], lev)
        except Exception as exc:
            self._log(m, "warn", step="account mode", error=f"{type(exc).__name__}: {exc}")
        cid = f"{m['id']}o{int(ev.get('ts') or self._now()) % 10**10}"
        before = await self._held(c, m["symbol"])
        sent_ms = self._now()
        res, err = None, None
        try:
            res = await c.market_order(OrderIntent(m["symbol"], "BUY" if side == "long" else "SELL", qty, False, "net_delta", cid, price))
        except Exception as exc:                     # a timeout can hide an order the exchange DID accept
            err = f"{type(exc).__name__}: {exc}"
        if res is None or res.status != "FILLED" or res.executed_qty <= 0:
            await self._settle_unconfirmed(m, c, side, cid, stop, price, before, err or (res.error or res.status))
            return
        fill = float(res.avg_price or price)
        m["position"] = {"side": side, "qty": res.executed_qty, "entry": fill, "stop": stop, "entry_ts": self._now(),
                         "client_id": cid, "paper_ts": ev.get("ts"), "stop_client_id": None}
        self._save(m)
        slip_bps = (fill - price) / price * 1e4 * (1 if side == "long" else -1)
        self._log(m, "open", side=side, qty=res.executed_qty, price=fill, ref=price, stop=stop, leverage=lev, client_id=cid,
                  exchange_id=res.exchange_id, slippage_bps=round(slip_bps, 2), order_ms=self._now() - sent_ms,
                  signal_to_fill_ms=(self._now() - int(ev["ts"])) if ev.get("ts") else None)
        await self._protect(m, c, side, stop, cid + "s", res.executed_qty)

    async def _protect(self, m: dict[str, Any], c: Any, side: str, stop: float, scid: str, qty: float) -> bool:
        """Place the exchange-side stop; a rejected or failed stop closes the position (never left unprotected)."""
        try:
            sres = await c.place_backstop(m["symbol"], "SELL" if side == "long" else "BUY", stop, scid, qty)
            if str(sres.status).upper() in REJECTED:
                raise RuntimeError(f"the exchange answered {sres.status} to the stop order")
            m["position"]["stop_client_id"] = scid
            self._save(m)
            self._log(m, "stop_placed", stop=stop, client_id=scid, status=sres.status)
            return True
        except Exception as exc:
            # an unprotected live position is not acceptable: close it at once
            self._log(m, "error", step="stop", error=f"{type(exc).__name__}: {exc}")
            self._alert(f"⚠️ <b>{m['bot_key']}</b>: the exchange stop could not be placed ({type(exc).__name__}); "
                        "closing the live position")
            await self._close_live(m, "no exchange stop could be placed")
            return False

    async def _settle_unconfirmed(self, m: dict[str, Any], c: Any, side: str, cid: str, stop: float, ref: float,
                                  before: float, err: str) -> None:
        """The entry was not confirmed: read the account back. A fill that landed anyway is ADOPTED and protected;
        otherwise any resting remainder is cancelled. Never leaves an untracked live position."""
        self._log(m, "error", step="open", client_id=cid, error=err)
        await asyncio.sleep(self.recheck_s)
        try:
            await c.cancel_all(m["symbol"])
        except Exception as exc:
            self._log(m, "warn", step="cancel unconfirmed", error=f"{type(exc).__name__}: {exc}")
        try:
            after = await self._held(c, m["symbol"])
        except Exception as exc:
            self._log(m, "error", step="read back", error=f"{type(exc).__name__}: {exc}")
            self._alert(f"🚨 <b>{m['bot_key']}</b>: an entry was not confirmed and the account could not be read back. "
                        f"CHECK {m['symbol']} ON {m['exchange'].upper()} {m['network'].upper()} NOW.")
            return
        got = round((after - before) * (1 if side == "long" else -1), 12)
        if got <= 0:
            self._log(m, "skip", why="the entry did not fill (account unchanged)", client_id=cid)
            self._alert(f"⚠️ <b>{m['bot_key']}</b>: a live entry was not confirmed and did not fill ({str(err)[:80]}). "
                        "Nothing is open.")
            return
        entry = 0.0
        if before == 0:
            live = await c.fetch_positions([m["symbol"]])
            entry = float(getattr(live.get(m["symbol"]), "entry_price", 0.0) or 0.0)
        m["position"] = {"side": side, "qty": got, "entry": entry or ref, "stop": stop, "entry_ts": self._now(),
                         "client_id": cid, "paper_ts": None, "stop_client_id": None, "adopted": True}
        self._save(m)
        self._log(m, "adopted", side=side, qty=got, price=entry or ref, client_id=cid)
        self._alert(f"⚠️ <b>{m['bot_key']}</b>: an unconfirmed entry DID fill ({got:g} {m['symbol']}); "
                    "adopted and protecting it with the exchange stop.")
        await self._protect(m, c, side, stop, cid + "s", got)

    async def _restore_stop(self, m: dict[str, Any], c: Any, why: str) -> None:
        """A close failed after its stop was cancelled: put a stop back on whatever is still held."""
        p = m.get("position") or {}
        try:
            held = abs(await self._held(c, m["symbol"]))
            if held <= 0:
                return
            p["qty"] = held
            scid = f"{m['id']}r{self._now() % 10**10}"
            sres = await c.place_backstop(m["symbol"], "SELL" if p["side"] == "long" else "BUY", float(p["stop"]), scid, held)
            if str(sres.status).upper() in REJECTED:
                raise RuntimeError(f"the exchange answered {sres.status}")
            p["stop_client_id"] = scid
            self._save(m)
            self._log(m, "stop_restored", stop=p["stop"], qty=held, client_id=scid)
            self._alert(f"⚠️ <b>{m['bot_key']}</b>: closing the live position failed ({why[:80]}); the exchange stop is "
                        "back in place and the close is retried every 30 s.")
        except Exception as exc:
            self._log(m, "error", step="restore stop", error=f"{type(exc).__name__}: {exc}")
            self._alert(f"🚨 <b>{m['bot_key']}</b>: the close failed AND the stop could not be restored. The live "
                        f"{m['symbol']} position on {m['exchange'].upper()} {m['network'].upper()} may be UNPROTECTED. "
                        "Act on the exchange now.")

    async def _close_live(self, m: dict[str, Any], why: str) -> None:
        from app.core.types import OrderIntent
        p = m.get("position")
        if not p:
            return
        c = await self._client(m["exchange"], m["network"], m["symbol"])
        try:
            await c.cancel_all(m["symbol"])
        except Exception as exc:
            self._log(m, "warn", step="cancel stop", error=f"{type(exc).__name__}: {exc}")
        try:
            live = await c.fetch_positions([m["symbol"]])
            held = abs(float(getattr(live.get(m["symbol"]), "qty", 0.0) or 0.0))
            qty = min(float(p["qty"]), held) if held else 0.0
            exit_price = None
            if qty > 0:
                cid = f"{m['id']}c{self._now() % 10**10}"
                res = await c.market_order(OrderIntent(m["symbol"], "SELL" if p["side"] == "long" else "BUY", qty, True,
                                                       "flatten", cid, float(p["entry"])))
                if res.status != "FILLED" or res.executed_qty < qty * 0.999:
                    raise RuntimeError(f"close order {res.status} ({res.executed_qty:g} of {qty:g}) {res.error or ''}")
                exit_price = float(res.avg_price or p["entry"])
            else:
                mark = getattr(live.get(m["symbol"]), "mark", 0.0) if live.get(m["symbol"]) else 0.0
                exit_price = float(p["stop"]) if not mark else float(mark)
                why += " (the exchange stop had already closed it)"
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            self._log(m, "error", step="close", error=err)
            await self._restore_stop(m, c, err)
            return
        sign = 1.0 if p["side"] == "long" else -1.0
        gross = (exit_price - float(p["entry"])) * float(p["qty"]) * sign
        fees = TAKER_FEE_EST * float(p["qty"]) * (float(p["entry"]) + exit_price)
        net = gross - fees
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        m["realized"] = float(m.get("realized") or 0) + net
        m["daily"] = {**(m.get("daily") or {}), day: float((m.get("daily") or {}).get(day, 0.0)) + net}
        m["trades"] = int(m.get("trades") or 0) + 1
        m["position"] = None
        self._save(m)
        self._log(m, "close", why=why, exit=exit_price, gross=round(gross, 6), fees_est=round(fees, 6), net=round(net, 6),
                  realized=round(m["realized"], 6))
        if (-m["daily"][day] >= m["max_daily_loss"] or -m["realized"] >= m["max_total_loss"]) and m["status"] == "ARMED":
            m.update(status="STOPPED", stopped_ts=self._now(), reason="loss limit reached")
            self._save(m)
            self._log(m, "stopped", reason="loss limit reached", daily=round(m["daily"][day], 6), total=round(m["realized"], 6))
            self._alert(f"🛑 <b>LOSS LIMIT</b> {m['bot_key']} live mirror STOPPED: today {m['daily'][day]:+.2f}, "
                        f"total {m['realized']:+.2f} USDT")

    # -- reconciliation ------------------------------------------------------------------------------------------------
    async def _reconcile_loop(self) -> None:
        while True:
            await asyncio.sleep(RECONCILE_S)
            try:
                await self.reconcile()
            except Exception as exc:
                log.warning("mirror reconciliation failed: %s", self.providers.redact(exc))

    async def reconcile(self) -> None:
        for m in list(self.mirrors.values()):
            p = m.get("position")
            if not p:
                continue
            venue = (m["exchange"], m["network"])
            try:
                async with self._lock(m):
                    await self._reconcile_one(m, p)
                self._venue_ok(venue)
            except Exception as exc:                 # one mirror's failure never skips the others
                log.warning("mirror %s reconciliation failed: %s", m["id"], self.providers.redact(exc))
                self._venue_failed(venue, f"{type(exc).__name__}: {exc}")

    async def _reconcile_one(self, m: dict[str, Any], p: dict[str, Any]) -> None:
        c = await self._client(m["exchange"], m["network"], m["symbol"])
        live = await c.fetch_positions([m["symbol"]])
        pos = live.get(m["symbol"])
        held = abs(float(getattr(pos, "qty", 0.0) or 0.0))
        row = self.bot_view(m["program"], m["bot_key"]) or {}
        paper_open = bool(row.get("open_positions"))
        if held == 0:
            await self._close_live(m, "reconcile: flat on the exchange")
            return
        if not paper_open or m["status"] != "ARMED":
            await self._close_live(m, "reconcile: the paper bot is flat")
            return
        mark = float(getattr(pos, "mark", 0.0) or 0.0)
        if mark > 0:                                 # loss limits, mark-to-market (the fees to close included)
            sign = 1.0 if p["side"] == "long" else -1.0
            upnl = (mark - float(p["entry"])) * held * sign - TAKER_FEE_EST * held * (float(p["entry"]) + mark)
            day = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
            today = float((m.get("daily") or {}).get(day, 0.0)) + upnl
            total = float(m.get("realized") or 0.0) + upnl
            if -today >= m["max_daily_loss"] or -total >= m["max_total_loss"]:
                self._log(m, "loss_limit_mtm", mark=mark, upnl=round(upnl, 6), today=round(today, 6), total=round(total, 6))
                await self._close_live(m, "loss limit reached (mark-to-market)")
                if m["status"] == "ARMED" and not m.get("position"):
                    m.update(status="STOPPED", stopped_ts=self._now(), reason="loss limit reached (mark-to-market)")
                    self._save(m)
                    self._log(m, "stopped", reason="loss limit reached (mark-to-market)")
                    self._alert(f"🛑 <b>LOSS LIMIT</b> {m['bot_key']} live mirror STOPPED at mark: today {today:+.2f}, "
                                f"total {total:+.2f} USDT")

    def _venue_ok(self, venue: tuple[str, str]) -> None:
        self._fails[venue] = 0
        if self.exchange_safe.pop(venue, None):
            self._alert(f"🛡 <b>SAFE MODE OFF</b> on {venue[0]} {venue[1]}: the exchange answers normally again.")

    def _venue_failed(self, venue: tuple[str, str], err: str) -> None:
        self._fails[venue] = self._fails.get(venue, 0) + 1
        if self._fails[venue] >= EXCHANGE_FAILS_FOR_SAFE and venue not in self.exchange_safe:
            self.exchange_safe[venue] = f"{venue[0]} {venue[1]} failed {self._fails[venue]} checks in a row"
            self._alert(f"🛡 <b>SAFE MODE ON</b> on {venue[0]} {venue[1]}: the exchange failed {self._fails[venue]} "
                        f"reconciliations in a row ({self.providers.redact(err)[:100]}). No new live entries; open "
                        "positions keep their exchange stop.")

    # -- testnet verification -------------------------------------------------------------------------------------------
    async def verify_testnet(self, exchange: str, symbol: str = "ETHUSDT") -> dict[str, Any]:
        """A real TESTNET round trip: smallest legal long, an exchange-side stop, a reduce-only close, then flat."""
        from app.core.types import OrderIntent
        detail: dict[str, Any] = {"symbol": symbol, "steps": []}
        ok = False
        c = None
        try:
            c = self.providers.client(exchange, "testnet")
            rules = (await c.load_rules([symbol]))[symbol]
            px = float((await c.fetch_premium_index(symbol)).mark)
            qty = rules.min_order_qty(px, 1.2)
            await c.ensure_account_mode([symbol], 5)
            cid = f"vt{uuid.uuid4().hex[:10]}"
            r = await c.market_order(OrderIntent(symbol, "BUY", qty, False, "net_delta", cid, px))
            detail["steps"].append({"open": r.status, "qty": r.executed_qty, "price": r.avg_price})
            if r.status == "FILLED":
                s = await c.place_backstop(symbol, "SELL", px * 0.9, cid + "s", r.executed_qty)
                detail["steps"].append({"stop": s.status})
                await c.cancel_all(symbol)
                x = await c.market_order(OrderIntent(symbol, "SELL", r.executed_qty, True, "flatten", cid + "c", px))
                detail["steps"].append({"close": x.status, "price": x.avg_price})
                left = await c.fetch_positions([symbol])
                flat = abs(float(getattr(left.get(symbol), "qty", 0.0) or 0.0)) == 0
                detail["steps"].append({"flat": flat})
                ok = x.status == "FILLED" and flat
        except Exception as exc:
            detail["error"] = self.providers.redact(f"{type(exc).__name__}: {exc}")
        finally:
            if c is not None:
                try:
                    await c.close()
                except Exception:
                    pass
        self.storage.provider_check_save(exchange, "testnet", self._now(), ok, detail)
        return {"ok": ok, **detail}

    # -- reads ---------------------------------------------------------------------------------------------------------
    @staticmethod
    def public(m: dict[str, Any]) -> dict[str, Any]:
        keys = ("id", "program", "bot_key", "symbol", "exchange", "network", "amount_usdt", "risk_pct", "max_daily_loss",
                "max_total_loss", "status", "created_ts", "stopped_ts", "reason", "position", "realized", "daily", "trades")
        return {k: m.get(k) for k in keys}

    def overview(self) -> list[dict[str, Any]]:
        return [self.public(m) for m in self.storage.mirrors()]
