"""BybitClient: the same surface as ExchangeClient, spoken in Bybit v5.

The engine, router and risk manager only ever see this class through the ExchangeClient interface, so
every method here returns the same types (MarketRules, Candle, ExchangePosition, OrderResult, ...) that
the Binance client returns. Differences Bybit forces on us, all handled here and nowhere else:

  * category="linear" on every call (USDT perpetuals);
  * intervals are bare minutes ("1", "5", "15"), not "1m";
  * klines come back NEWEST FIRST and must be reversed;
  * sides are "Buy"/"Sell", not "BUY"/"SELL"; order type is "Market", not "MARKET";
  * a client id is `orderLinkId`, not `newClientOrderId`;
  * errors carry `retCode` rather than a negative `code`;
  * there is no listenKey - the private websocket authenticates with the API key directly;
  * one-way vs hedge is `positionIdx` (0 = one-way) set per symbol, not an account-wide flag.

!! UNVERIFIED !! Written but never executed: the test suite could not be run in the session that wrote
this (permission block). Treat every method as unproven until tests pass against Bybit TESTNET.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from app.config import Settings
from app.core.types import Candle, ExchangePosition, FundingInfo, MarketRules, OrderIntent, OrderResult, tf_ms
from app.exchange.guard import AccountModeError, GuardedBybit, build_exchange, with_retry

log = logging.getLogger("paperlab.exchange")

CATEGORY = "linear"
_TF = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D"}
_RET_RE = re.compile(r'"retCode"\s*:\s*(\d+)')

# Bybit retCodes that a retry cannot fix.
NON_RETRYABLE_BYBIT = {
    10001,   # parameter error
    10003,   # invalid api key
    10004,   # sign error
    10005,   # permission denied
    110007,  # insufficient available balance
    110012,  # insufficient available balance
    110017,  # reduce-only rule not satisfied
    110025,  # position mode not modified
    110043,  # leverage not modified
    110045,  # insufficient wallet balance
    170131,  # insufficient balance
    110004,  # wallet balance insufficient
}

# Statuses that mean "this order is finished, stop polling".
_TERMINAL = {"Filled", "Cancelled", "Rejected", "Deactivated"}


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def bybit_code(exc: BaseException) -> int | None:
    m = _RET_RE.search(str(exc))
    return int(m.group(1)) if m else None


def _decimals(step: str | float) -> int:
    """Decimal places carried by a Bybit tick/step string ("0.010" -> 2, "1" -> 0, "1e-05" -> 5)."""
    s = str(step).strip()
    if "e" in s.lower() or "E" in s:
        s = f"{float(s):.12f}"
    if "." not in s:
        return 0
    return len(s.split(".")[1].rstrip("0"))


def fmt_num(value: float, precision: int) -> str:
    s = f"{value:.{max(0, precision)}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


class BybitClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.venue = settings.venue
        self.kind = "futures"                      # linear perpetuals only
        self.ex: GuardedBybit = build_exchange(settings)  # type: ignore[assignment]
        self.rules: dict[str, MarketRules] = {}
        self.time_offset_ms: int = 0

    async def close(self) -> None:
        try:
            await self.ex.close()
        except Exception:  # pragma: no cover - best effort
            pass

    # -- public ------------------------------------------------------------------------
    async def ping(self) -> float:
        t0 = time.perf_counter()
        await with_retry(self.ex.fetch_time, label="time", exchange=self.ex)
        return (time.perf_counter() - t0) * 1000.0

    async def fetch_time(self) -> int:
        return int(await with_retry(self.ex.fetch_time, label="time", exchange=self.ex))

    async def load_rules(self, symbols: list[str]) -> dict[str, MarketRules]:
        out: dict[str, MarketRules] = {}
        for sym in symbols:
            r = await with_retry(
                lambda s=sym: self.ex.publicGetV5MarketInstrumentsInfo({"category": CATEGORY, "symbol": s}),
                label=f"instrumentsInfo {sym}", exchange=self.ex)
            rows = (r.get("result") or {}).get("list") or []
            if not rows:
                continue
            it = rows[0]
            lot, pf = it.get("lotSizeFilter", {}), it.get("priceFilter", {})
            tick = _f(pf.get("tickSize"))
            step = _f(lot.get("qtyStep"))
            min_qty = _f(lot.get("minOrderQty"))
            # Bybit publishes a notional floor (usually 5 USDT); fall back to minQty x tick if absent.
            min_notional = _f(lot.get("minNotionalValue"), 5.0)
            out[sym] = MarketRules(sym, tick, step, min_qty, min_notional,
                                   _f(it.get("maintenanceMargin"), 0.005) or 0.005,
                                   _decimals(pf.get("tickSize", "0.01")),
                                   _decimals(lot.get("qtyStep", "0.001")))
        missing = set(symbols) - set(out)
        if missing:
            raise ValueError(f"symbols not found on {self.venue.mode}: {sorted(missing)}")
        self.rules.update(out)
        return out

    async def fetch_leverage_brackets(self, symbols: list[str]) -> dict[str, float]:
        """Bybit exposes the maintenance margin on the instrument itself, so load_rules already has it."""
        return {s: r.maint_margin_rate for s, r in self.rules.items()
                if s in symbols and r.maint_margin_rate}

    async def fetch_klines(self, symbol: str, tf: str, start_ms: int | None = None, end_ms: int | None = None,
                           limit: int = 500) -> list[Candle]:
        interval = _TF.get(tf)
        if interval is None:
            raise ValueError(f"unsupported timeframe for bybit: {tf}")
        params: dict[str, Any] = {"category": CATEGORY, "symbol": symbol, "interval": interval,
                                  "limit": min(1000, max(1, limit))}
        if start_ms is not None:
            params["start"] = int(start_ms)
        if end_ms is not None:
            params["end"] = int(end_ms)
        r = await with_retry(lambda: self.ex.publicGetV5MarketKline(params),
                             label=f"kline {symbol} {tf}", exchange=self.ex)
        rows = (r.get("result") or {}).get("list") or []
        step = tf_ms(tf)
        out: list[Candle] = []
        for row in reversed(rows):            # Bybit returns newest first
            ot = int(_f(row[0]))
            out.append(Candle(symbol, tf, ot, _f(row[1]), _f(row[2]), _f(row[3]), _f(row[4]), _f(row[5]),
                              ot + step, True, _f(row[6]) if len(row) > 6 else 0.0, 0, "backfill"))
        return out

    async def backfill(self, symbol: str, tf: str, since_ms: int | None, now_ms: int,
                       max_bars: int = 600) -> list[Candle]:
        """Closed bars only, oldest first. Same contract as the Binance client."""
        step = tf_ms(tf)
        if since_ms is None:
            since_ms = now_ms - step * (max_bars + 2)
        out: list[Candle] = []
        cursor = since_ms
        while len(out) < max_bars * 2:
            batch = await self.fetch_klines(symbol, tf, start_ms=cursor, limit=min(1000, max_bars))
            if not batch:
                break
            fresh = [c for c in batch if c.close_time < now_ms and c.open_time >= cursor]
            out.extend(fresh)
            if len(batch) < 2 or batch[-1].close_time >= now_ms:
                break
            cursor = batch[-1].open_time + step
            if cursor >= now_ms:
                break
        seen: dict[int, Candle] = {c.open_time: c for c in out}
        return [seen[k] for k in sorted(seen)][-max_bars:]

    async def fetch_premium_index(self, symbol: str) -> FundingInfo:
        r = await with_retry(lambda: self.ex.publicGetV5MarketTickers({"category": CATEGORY, "symbol": symbol}),
                             label=f"tickers {symbol}", exchange=self.ex)
        rows = (r.get("result") or {}).get("list") or []
        t = rows[0] if rows else {}
        return FundingInfo(symbol, _f(t.get("fundingRate")), int(_f(t.get("nextFundingTime"))),
                           _f(t.get("markPrice")), _f(t.get("indexPrice")), int(time.time() * 1000))

    # -- account ------------------------------------------------------------------------
    async def ensure_account_mode(self, symbols: list[str], leverage: int) -> dict[str, Any]:
        """One-way mode (positionIdx 0), isolated margin and per-symbol leverage. Hedge mode aborts."""
        report: dict[str, Any] = {"dual": False, "isolated": [], "leverage": {}}
        lev = str(int(leverage))
        for sym in symbols:
            # one-way mode, per symbol. mode=0 is one-way, 3 is hedge.
            try:
                await with_retry(lambda s=sym: self.ex.privatePostV5PositionSwitchMode(
                    {"category": CATEGORY, "symbol": s, "mode": 0}), label=f"switchMode {sym}", exchange=self.ex)
            except Exception as exc:
                code = bybit_code(exc)
                if code == 110025:            # "position mode not modified" - already one-way
                    pass
                elif code in (110024,):       # cannot switch with an open position/order
                    raise AccountModeError(
                        "dual-side (hedge) mode not supported - switch the account to one-way mode "
                        f"({str(exc)[:120]})") from exc
                else:
                    log.warning("switchMode %s: %s", sym, str(exc)[:140])
            try:
                await with_retry(lambda s=sym: self.ex.privatePostV5PositionSwitchIsolated(
                    {"category": CATEGORY, "symbol": s, "tradeMode": 1, "buyLeverage": lev, "sellLeverage": lev}),
                    label=f"switchIsolated {sym}", exchange=self.ex)
                report["isolated"].append(sym)
            except Exception as exc:
                if bybit_code(exc) in (110026, 110043):   # already isolated / leverage unchanged
                    report["isolated"].append(sym)
                else:
                    log.warning("switchIsolated %s: %s", sym, str(exc)[:140])
            try:
                await with_retry(lambda s=sym: self.ex.privatePostV5PositionSetLeverage(
                    {"category": CATEGORY, "symbol": s, "buyLeverage": lev, "sellLeverage": lev}),
                    label=f"setLeverage {sym}", exchange=self.ex)
            except Exception as exc:
                if bybit_code(exc) != 110043:             # "leverage not modified"
                    raise
            report["leverage"][sym] = int(leverage)
        return report

    async def fetch_positions(self, symbols: list[str], all_symbols: bool = False) -> dict[str, ExchangePosition]:
        params: dict[str, Any] = {"category": CATEGORY, "settleCoin": "USDT"}
        r = await with_retry(lambda: self.ex.privateGetV5PositionList(params), label="positionList", exchange=self.ex)
        rows = (r.get("result") or {}).get("list") or []
        out: dict[str, ExchangePosition] = {}
        now = int(time.time() * 1000)
        for p in rows:
            sym = p.get("symbol")
            if not all_symbols and sym not in symbols:
                continue
            size = _f(p.get("size"))
            qty = -size if str(p.get("side")) == "Sell" else size     # signed, like positionAmt
            if all_symbols and qty == 0:
                continue
            out[sym] = ExchangePosition(
                symbol=sym, qty=qty, entry_price=_f(p.get("avgPrice")),
                liq_price=_f(p.get("liqPrice")) or None, margin=_f(p.get("positionIM")),
                upnl=_f(p.get("unrealisedPnl")), leverage=int(_f(p.get("leverage"), 1)),
                mark=_f(p.get("markPrice")), ts=now)
        return out

    async def fetch_balance(self) -> dict[str, float]:
        r = await with_retry(lambda: self.ex.privateGetV5AccountWalletBalance({"accountType": "UNIFIED"}),
                             label="walletBalance", exchange=self.ex)
        for acct in (r.get("result") or {}).get("list") or []:
            for c in acct.get("coin", []):
                if c.get("coin") == "USDT":
                    wallet = _f(c.get("walletBalance"))
                    avail = _f(c.get("availableToWithdraw")) or _f(acct.get("totalAvailableBalance")) or wallet
                    return {"wallet": wallet, "available": avail, "upnl": _f(c.get("unrealisedPnl"))}
        return {"wallet": 0.0, "available": 0.0, "upnl": 0.0}

    # -- orders --------------------------------------------------------------------------
    def _qty_str(self, symbol: str, qty: float) -> str:
        rules = self.rules.get(symbol)
        return fmt_num(qty, rules.qty_precision if rules else 3)

    def _price_str(self, symbol: str, price: float) -> str:
        rules = self.rules.get(symbol)
        return fmt_num(price, rules.price_precision if rules else 2)

    @staticmethod
    def _side(side: str) -> str:
        return "Buy" if str(side).upper() in ("BUY", "LONG") else "Sell"

    @staticmethod
    def _norm_status(s: str) -> str:
        """Map Bybit's status vocabulary onto the one the router already understands."""
        return {"Filled": "FILLED", "New": "NEW", "PartiallyFilled": "PARTIALLY_FILLED",
                "Cancelled": "CANCELED", "Rejected": "REJECTED", "Deactivated": "CANCELED",
                "Untriggered": "NEW", "Triggered": "NEW"}.get(str(s), str(s).upper() or "UNKNOWN")

    def _result(self, client_id: str, r: dict[str, Any]) -> OrderResult:
        executed = _f(r.get("cumExecQty"))
        avg = _f(r.get("avgPrice")) or None
        if avg is None and executed > 0 and _f(r.get("cumExecValue")):
            avg = _f(r.get("cumExecValue")) / executed
        return OrderResult(client_id, str(r.get("orderId", "")), self._norm_status(r.get("orderStatus", "UNKNOWN")),
                           avg, executed, dict(r))

    async def market_order(self, intent: OrderIntent) -> OrderResult:
        params: dict[str, Any] = {
            "category": CATEGORY, "symbol": intent.symbol, "side": self._side(intent.side),
            "orderType": "Market", "qty": self._qty_str(intent.symbol, intent.qty),
            "orderLinkId": intent.client_id, "timeInForce": "IOC", "positionIdx": 0,
        }
        if intent.reduce_only:
            params["reduceOnly"] = True
        await with_retry(lambda: self.ex.privatePostV5OrderCreate(params),
                         label=f"order {intent.client_id}", attempts=1, exchange=self.ex)
        # v5 order/create returns only ids - the fill must be read back. Never assume a fill from an ACK.
        res = OrderResult(intent.client_id, "", "UNKNOWN", None, 0.0, {})
        for _ in range(4):
            await asyncio.sleep(0.4)
            try:
                r2 = await self.get_order(intent.symbol, intent.client_id)
            except Exception as exc:
                log.warning("order poll failed for %s: %s", intent.client_id, str(exc)[:120])
                continue
            res = self._result(intent.client_id, r2)
            if r2.get("orderStatus") in _TERMINAL:
                break
        if res.status != "FILLED":
            res.status = "UNCONFIRMED" if res.status in ("NEW", "PARTIALLY_FILLED", "UNKNOWN") else res.status
        return res

    async def get_order(self, symbol: str, client_id: str) -> dict[str, Any]:
        params = {"category": CATEGORY, "symbol": symbol, "orderLinkId": client_id}
        r = await with_retry(lambda: self.ex.privateGetV5OrderRealtime(params),
                             label=f"getOrder {client_id}", exchange=self.ex)
        rows = (r.get("result") or {}).get("list") or []
        if rows:
            return rows[0]
        # a finished order drops off the realtime endpoint; fall back to history
        h = await with_retry(lambda: self.ex.privateGetV5OrderHistory(params),
                             label=f"orderHistory {client_id}", exchange=self.ex)
        hrows = (h.get("result") or {}).get("list") or []
        return hrows[0] if hrows else {}

    async def place_backstop(self, symbol: str, side: str, stop_price: float, client_id: str,
                             qty: float | None = None) -> OrderResult:
        """Catastrophic stop: a mark-price-triggered reduce-only market close."""
        params: dict[str, Any] = {
            "category": CATEGORY, "symbol": symbol, "side": self._side(side), "orderType": "Market",
            "triggerPrice": self._price_str(symbol, stop_price), "triggerBy": "MarkPrice",
            "triggerDirection": 2 if self._side(side) == "Buy" else 1,   # 1 = rises to, 2 = falls to
            "orderLinkId": client_id, "reduceOnly": True, "closeOnTrigger": True, "positionIdx": 0,
            "qty": self._qty_str(symbol, qty) if qty else "0",
        }
        r = await with_retry(lambda: self.ex.privatePostV5OrderCreate(params),
                             label=f"backstop {client_id}", attempts=1, exchange=self.ex)
        return self._result(client_id, (r.get("result") or {}))

    async def cancel_all(self, symbol: str) -> None:
        try:
            await with_retry(lambda: self.ex.privatePostV5OrderCancelAll({"category": CATEGORY, "symbol": symbol}),
                             label=f"cancelAll {symbol}", exchange=self.ex)
        except Exception as exc:
            if bybit_code(exc) in (110001,):   # nothing to cancel
                return
            raise

    async def cancel_order(self, symbol: str, client_id: str) -> None:
        try:
            await with_retry(lambda: self.ex.privatePostV5OrderCancel(
                {"category": CATEGORY, "symbol": symbol, "orderLinkId": client_id}),
                label=f"cancel {client_id}", attempts=1, exchange=self.ex)
        except Exception as exc:
            if bybit_code(exc) in (110001, 170213):   # order does not exist / already gone
                return
            raise

    async def open_orders(self, symbol: str) -> list[dict[str, Any]]:
        r = await with_retry(lambda: self.ex.privateGetV5OrderRealtime({"category": CATEGORY, "symbol": symbol}),
                             label=f"openOrders {symbol}", exchange=self.ex)
        return list((r.get("result") or {}).get("list") or [])

    # -- user data stream ------------------------------------------------------------
    async def create_listen_key(self) -> str:
        """Bybit has no listenKey: the private socket authenticates with the API key itself."""
        raise NotImplementedError("bybit private stream authenticates with the api key, not a listenKey")

    async def keepalive_listen_key(self, key: str) -> None:
        return None
