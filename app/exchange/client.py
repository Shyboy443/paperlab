"""ExchangeClient: thin, venue-native wrappers over the guarded ccxt instance.

Only fapi-native (or spot api-native) implicit methods are used - never ccxt's unified
fetch_balance/fetch_positions, which are the calls that mis-routed to live in sandbox mode.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.config import Settings
from app.core.types import Candle, ExchangePosition, FundingInfo, MarketRules, OrderIntent, OrderResult, tf_ms
from app.exchange.guard import (AccountModeError, GuardedBinanceSpot, GuardedBinanceUSDM, binance_code,
                                build_exchange, with_retry)

log = logging.getLogger("paperlab.exchange")


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def fmt_num(value: float, precision: int) -> str:
    s = f"{value:.{max(0, precision)}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


class ExchangeClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.venue = settings.venue
        self.kind = settings.venue.caps.kind
        self.ex: GuardedBinanceUSDM | GuardedBinanceSpot = build_exchange(settings)
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
        if self.kind == "futures":
            info = await with_retry(self.ex.fapiPublicGetExchangeInfo, label="exchangeInfo", exchange=self.ex)
        else:
            info = await with_retry(self.ex.publicGetExchangeInfo, label="exchangeInfo", exchange=self.ex)
        wanted = set(symbols)
        out: dict[str, MarketRules] = {}
        for s in info.get("symbols", []):
            sym = s.get("symbol")
            if sym not in wanted:
                continue
            f = {x["filterType"]: x for x in s.get("filters", [])}
            tick = _f(f.get("PRICE_FILTER", {}).get("tickSize"))
            lot = f.get("MARKET_LOT_SIZE") or f.get("LOT_SIZE") or {}
            step = _f(lot.get("stepSize")) or _f(f.get("LOT_SIZE", {}).get("stepSize"))
            min_qty = _f(lot.get("minQty")) or _f(f.get("LOT_SIZE", {}).get("minQty"))
            mn = f.get("MIN_NOTIONAL") or f.get("NOTIONAL") or {}
            min_notional = _f(mn.get("notional") or mn.get("minNotional"))
            mmr = _f(s.get("maintMarginPercent"), 2.5) / 100.0 if self.kind == "futures" else 0.0
            out[sym] = MarketRules(sym, tick, step, min_qty, min_notional, mmr,
                                   int(s.get("pricePrecision", 8)), int(s.get("quantityPrecision", 8)))
        missing = wanted - set(out)
        if missing:
            raise ValueError(f"symbols not found on {self.venue.mode}: {sorted(missing)}")
        self.rules.update(out)
        return out

    async def fetch_leverage_brackets(self, symbols: list[str]) -> dict[str, float]:
        """First-bracket maintenance margin ratio per symbol (signed endpoint; empty dict without keys)."""
        if self.kind != "futures" or not self.settings.has_keys:
            return {}
        out: dict[str, float] = {}
        try:
            rows = await with_retry(self.ex.fapiPrivateGetLeverageBracket, label="leverageBracket", exchange=self.ex)
        except Exception as exc:
            log.warning("leverageBracket unavailable: %s", str(exc)[:160])
            return {}
        for row in rows if isinstance(rows, list) else [rows]:
            sym = row.get("symbol")
            brackets = row.get("brackets") or []
            if sym in symbols and brackets:
                out[sym] = _f(brackets[0].get("maintMarginRatio"), 0.025)
        return out

    async def fetch_klines(self, symbol: str, tf: str, start_ms: int | None = None, end_ms: int | None = None,
                           limit: int = 500) -> list[Candle]:
        params: dict[str, Any] = {"symbol": symbol, "interval": tf, "limit": min(1500, max(1, limit))}
        if start_ms is not None:
            params["startTime"] = int(start_ms)
        if end_ms is not None:
            params["endTime"] = int(end_ms)
        method = self.ex.fapiPublicGetKlines if self.kind == "futures" else self.ex.publicGetKlines
        rows = await with_retry(lambda: method(params), label=f"klines {symbol} {tf}", exchange=self.ex)
        out: list[Candle] = []
        for r in rows:
            out.append(Candle(symbol, tf, int(r[0]), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), _f(r[5]), int(r[6]),
                              True, _f(r[7]), int(_f(r[8])), "backfill"))
        return out

    async def backfill(self, symbol: str, tf: str, since_ms: int | None, now_ms: int, max_bars: int = 600) -> list[Candle]:
        """Closed bars only, oldest first, paging forward from since_ms (or the last max_bars bars)."""
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
        seen: dict[int, Candle] = {}
        for c in out:
            seen[c.open_time] = c
        return [seen[k] for k in sorted(seen)][-max_bars:]

    async def fetch_premium_index(self, symbol: str) -> FundingInfo:
        if self.kind != "futures":
            raise RuntimeError("funding is futures-only")
        r = await with_retry(lambda: self.ex.fapiPublicGetPremiumIndex({"symbol": symbol}),
                             label=f"premiumIndex {symbol}", exchange=self.ex)
        return FundingInfo(symbol, _f(r.get("lastFundingRate")), int(_f(r.get("nextFundingTime"))),
                           _f(r.get("markPrice")), _f(r.get("indexPrice")), int(_f(r.get("time"), time.time() * 1000)))

    # -- account ------------------------------------------------------------------------
    async def ensure_account_mode(self, symbols: list[str], leverage: int) -> dict[str, Any]:
        """One-way mode, isolated margin and the exchange leverage per symbol. Hedge mode aborts."""
        if self.kind != "futures":
            return {"mode": "spot"}
        report: dict[str, Any] = {"dual": False, "isolated": [], "leverage": {}}
        dual = await with_retry(self.ex.fapiPrivateGetPositionSideDual, label="positionSideDual", exchange=self.ex)
        is_dual = str(dual.get("dualSidePosition")).lower() == "true"
        if is_dual:
            positions = await self.fetch_positions(symbols, all_symbols=True)
            if any(abs(p.qty) > 0 for p in positions.values()):
                raise AccountModeError("dual-side (hedge) mode not supported - switch the account to one-way mode")
            try:
                await with_retry(lambda: self.ex.fapiPrivatePostPositionSideDual({"dualSidePosition": "false"}),
                                 label="positionSideDual=false", exchange=self.ex)
                report["dual"] = "switched_to_one_way"
            except Exception as exc:
                raise AccountModeError(f"dual-side (hedge) mode not supported - switch the account to one-way mode "
                                       f"({str(exc)[:120]})") from exc
        for sym in symbols:
            try:
                await with_retry(lambda: self.ex.fapiPrivatePostMarginType({"symbol": sym, "marginType": "ISOLATED"}),
                                 label=f"marginType {sym}", exchange=self.ex)
                report["isolated"].append(sym)
            except Exception as exc:
                if binance_code(exc) == -4046:  # "No need to change margin type"
                    report["isolated"].append(sym)
                else:
                    raise
            r = await with_retry(lambda: self.ex.fapiPrivatePostLeverage({"symbol": sym, "leverage": int(leverage)}),
                                 label=f"leverage {sym}", exchange=self.ex)
            report["leverage"][sym] = int(_f(r.get("leverage"), leverage))
        return report

    async def fetch_positions(self, symbols: list[str], all_symbols: bool = False) -> dict[str, ExchangePosition]:
        if self.kind != "futures":
            return {}
        rows = await with_retry(self.ex.fapiPrivateV2GetPositionRisk, label="positionRisk", exchange=self.ex)
        out: dict[str, ExchangePosition] = {}
        now = int(time.time() * 1000)
        for r in rows:
            sym = r.get("symbol")
            if not all_symbols and sym not in symbols:
                continue
            qty = _f(r.get("positionAmt"))
            if all_symbols and qty == 0:
                continue
            out[sym] = ExchangePosition(
                symbol=sym, qty=qty, entry_price=_f(r.get("entryPrice")), liq_price=_f(r.get("liquidationPrice")) or None,
                margin=_f(r.get("isolatedMargin")) or _f(r.get("isolatedWallet")), upnl=_f(r.get("unRealizedProfit")),
                leverage=int(_f(r.get("leverage"), 1)), mark=_f(r.get("markPrice")), ts=now)
        return out

    async def fetch_balance(self) -> dict[str, float]:
        if self.kind == "futures":
            rows = await with_retry(self.ex.fapiPrivateV2GetBalance, label="balance", exchange=self.ex)
            for r in rows:
                if r.get("asset") == "USDT":
                    return {"wallet": _f(r.get("balance")), "available": _f(r.get("availableBalance")),
                            "upnl": _f(r.get("crossUnPnl"))}
            return {"wallet": 0.0, "available": 0.0, "upnl": 0.0}
        acct = await with_retry(self.ex.privateGetAccount, label="account", exchange=self.ex)
        for b in acct.get("balances", []):
            if b.get("asset") == "USDT":
                free = _f(b.get("free"))
                return {"wallet": free + _f(b.get("locked")), "available": free, "upnl": 0.0}
        return {"wallet": 0.0, "available": 0.0, "upnl": 0.0}

    # -- orders --------------------------------------------------------------------------
    def _qty_str(self, symbol: str, qty: float) -> str:
        rules = self.rules.get(symbol)
        return fmt_num(qty, rules.qty_precision if rules else 8)

    def _price_str(self, symbol: str, price: float) -> str:
        rules = self.rules.get(symbol)
        return fmt_num(price, rules.price_precision if rules else 8)

    @staticmethod
    def _result(client_id: str, r: dict[str, Any]) -> OrderResult:
        status = str(r.get("status", "UNKNOWN"))
        executed = _f(r.get("executedQty"))
        avg = _f(r.get("avgPrice")) or None
        if avg is None and executed > 0 and _f(r.get("cumQuote")):
            avg = _f(r.get("cumQuote")) / executed
        return OrderResult(client_id, str(r.get("orderId", "")), status, avg, executed, dict(r))

    async def market_order(self, intent: OrderIntent) -> OrderResult:
        params: dict[str, Any] = {"symbol": intent.symbol, "side": intent.side, "type": "MARKET",
                                  "quantity": self._qty_str(intent.symbol, intent.qty),
                                  "newClientOrderId": intent.client_id, "newOrderRespType": "RESULT"}
        if self.kind == "futures":
            if intent.reduce_only:
                params["reduceOnly"] = "true"
            method = self.ex.fapiPrivatePostOrder
        else:
            method = self.ex.privatePostOrder
        r = await with_retry(lambda: method(params), label=f"order {intent.client_id}", attempts=1, exchange=self.ex)
        res = self._result(intent.client_id, r)
        if res.status != "FILLED" or res.executed_qty <= 0:
            # confirmed-fill guard: never assume a fill from an ACK; poll the order a few times
            for _ in range(3):
                await self._sleep(0.5)
                try:
                    r2 = await self.get_order(intent.symbol, intent.client_id)
                except Exception as exc:
                    log.warning("order poll failed for %s: %s", intent.client_id, str(exc)[:120])
                    continue
                res = self._result(intent.client_id, r2)
                if res.status in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
                    break
            if res.status not in ("FILLED",):
                res.status = "UNCONFIRMED" if res.status in ("NEW", "PARTIALLY_FILLED", "UNKNOWN") else res.status
        return res

    async def _sleep(self, s: float) -> None:
        import asyncio
        await asyncio.sleep(s)

    async def get_order(self, symbol: str, client_id: str) -> dict[str, Any]:
        params = {"symbol": symbol, "origClientOrderId": client_id}
        method = self.ex.fapiPrivateGetOrder if self.kind == "futures" else self.ex.privateGetOrder
        return await with_retry(lambda: method(params), label=f"getOrder {client_id}", exchange=self.ex)

    async def place_backstop(self, symbol: str, side: str, stop_price: float, client_id: str,
                             qty: float | None = None) -> OrderResult:
        """An exchange-side STOP_MARKET that closes the position.

        Binance USD-M moved conditional orders to its Algo Order API: POST /fapi/v1/order now answers -4120 ("Order type
        not supported for this endpoint. Please use the Algo Order API endpoints instead.") for STOP_MARKET -- found by
        the live mirror's first testnet trade, 2026-10-01. The Algo endpoint is used first (closePosition, then
        reduceOnly + quantity); the legacy endpoint stays as the fallback for a venue that has not migrated."""
        if self.kind != "futures":
            raise RuntimeError("backstop is futures-only")
        algo = getattr(self.ex, "fapiPrivatePostAlgoOrder", None)
        if algo is None:
            return await self._legacy_backstop(symbol, side, stop_price, client_id, qty)
        base = {"algoType": "CONDITIONAL", "symbol": symbol, "side": side, "type": "STOP_MARKET",
                "triggerPrice": self._price_str(symbol, stop_price), "workingType": "MARK_PRICE", "clientAlgoId": client_id}
        try:
            try:
                r = await with_retry(lambda: algo({**base, "closePosition": "true"}),
                                     label=f"algo backstop {client_id}", attempts=1, exchange=self.ex)
            except Exception as exc:
                if qty is None:
                    raise
                log.warning("closePosition algo backstop rejected (%s); falling back to reduceOnly+quantity", str(exc)[:100])
                r = await with_retry(lambda: algo({**base, "reduceOnly": "true", "quantity": self._qty_str(symbol, qty)}),
                                     label=f"algo backstop-ro {client_id}", attempts=1, exchange=self.ex)
        except Exception as algo_exc:
            log.warning("algo backstop failed (%s); trying the legacy order endpoint", str(algo_exc)[:120])
            try:
                return await self._legacy_backstop(symbol, side, stop_price, client_id, qty)
            except Exception:
                raise algo_exc from None
        return OrderResult(client_id, str(r.get("algoId", "")), str(r.get("algoStatus") or "NEW"), None, 0.0,
                           {**dict(r), "algo": True})

    async def _legacy_backstop(self, symbol: str, side: str, stop_price: float, client_id: str,
                               qty: float | None = None) -> OrderResult:
        base = {"symbol": symbol, "side": side, "type": "STOP_MARKET", "stopPrice": self._price_str(symbol, stop_price),
                "workingType": "MARK_PRICE", "newClientOrderId": client_id, "newOrderRespType": "RESULT"}
        try:
            r = await with_retry(lambda: self.ex.fapiPrivatePostOrder({**base, "closePosition": "true"}),
                                 label=f"backstop {client_id}", attempts=1, exchange=self.ex)
        except Exception as exc:
            if qty is None or binance_code(exc) not in (-4120, -1106, -4136, -1102):
                raise
            log.warning("closePosition backstop rejected (%s); falling back to reduceOnly+quantity", str(exc)[:100])
            r = await with_retry(lambda: self.ex.fapiPrivatePostOrder(
                {**base, "reduceOnly": "true", "quantity": self._qty_str(symbol, qty)}),
                label=f"backstop-ro {client_id}", attempts=1, exchange=self.ex)
        return self._result(client_id, r)

    async def cancel_all(self, symbol: str) -> None:
        """Every open order on `symbol`, including the Algo (conditional) orders a backstop now lives in."""
        method = self.ex.fapiPrivateDeleteAllOpenOrders if self.kind == "futures" else self.ex.privateDeleteOpenOrders
        try:
            await with_retry(lambda: method({"symbol": symbol}), label=f"cancelAll {symbol}", exchange=self.ex)
        except Exception as exc:
            if binance_code(exc) not in (-2011,):  # -2011: nothing to cancel
                raise
        algo = getattr(self.ex, "fapiPrivateDeleteAlgoOpenOrders", None) if self.kind == "futures" else None
        if algo is not None:
            try:
                await with_retry(lambda: algo({"symbol": symbol}), label=f"cancelAllAlgo {symbol}", exchange=self.ex)
            except Exception as exc:
                if binance_code(exc) not in (-2011, -2013):
                    raise

    async def cancel_order(self, symbol: str, client_id: str) -> None:
        params = {"symbol": symbol, "origClientOrderId": client_id}
        method = self.ex.fapiPrivateDeleteOrder if self.kind == "futures" else self.ex.privateDeleteOrder
        try:
            await with_retry(lambda: method(params), label=f"cancel {client_id}", attempts=1, exchange=self.ex)
            return
        except Exception as exc:
            if binance_code(exc) not in (-2011, -2013):  # unknown order / already gone
                raise
        algo = getattr(self.ex, "fapiPrivateDeleteAlgoOrder", None) if self.kind == "futures" else None
        if algo is not None:                    # not a regular order: it may be an Algo (conditional) one
            try:
                await with_retry(lambda: algo({"clientAlgoId": client_id}), label=f"cancelAlgo {client_id}",
                                 attempts=1, exchange=self.ex)
            except Exception as exc:
                if binance_code(exc) not in (-2011, -2013):
                    raise

    async def open_orders(self, symbol: str) -> list[dict[str, Any]]:
        method = self.ex.fapiPrivateGetOpenOrders if self.kind == "futures" else self.ex.privateGetOpenOrders
        rows = await with_retry(lambda: method({"symbol": symbol}), label=f"openOrders {symbol}", exchange=self.ex)
        return list(rows)

    # -- user data stream ------------------------------------------------------------
    async def create_listen_key(self) -> str:
        if self.kind == "futures":
            r = await with_retry(self.ex.fapiPrivatePostListenKey, label="listenKey", exchange=self.ex)
        else:
            r = await with_retry(self.ex.publicPostUserDataStream, label="listenKey", exchange=self.ex)
        return str(r["listenKey"])

    async def keepalive_listen_key(self, key: str) -> None:
        if self.kind == "futures":
            await with_retry(self.ex.fapiPrivatePutListenKey, label="listenKey keepalive", exchange=self.ex)
        else:
            await with_retry(lambda: self.ex.publicPutUserDataStream({"listenKey": key}),
                             label="listenKey keepalive", exchange=self.ex)


def make_client(settings: Settings) -> Any:
    """The venue picks its own client. Everything downstream only sees this common surface."""
    if settings.venue.exchange == "bybit":
        from app.exchange.bybit_client import BybitClient   # imported lazily: binance runs never load it
        return BybitClient(settings)
    return ExchangeClient(settings)
