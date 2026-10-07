"""Binance USD-M conditional orders live in the Algo Order API (2026-10-01: the live mirror's first testnet trade got
-4120 from POST /fapi/v1/order for its STOP_MARKET). The client places, and cancels, backstops there."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.exchange.client import ExchangeClient


class FakeEx:
    def __init__(self, algo_fails: bool = False, legacy_fails: bool = True):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.algo_fails, self.legacy_fails = algo_fails, legacy_fails

    async def fapiPrivatePostAlgoOrder(self, params):
        self.calls.append(("algo", params))
        if self.algo_fails:
            raise RuntimeError('binanceusdm {"code":-1102,"msg":"bad param"}')
        return {"algoId": 77, "clientAlgoId": params["clientAlgoId"], "algoStatus": "NEW", "triggerPrice": params["triggerPrice"]}

    async def fapiPrivatePostOrder(self, params):
        self.calls.append(("order", params))
        if self.legacy_fails:
            raise RuntimeError('binanceusdm {"code":-4120,"msg":"Order type not supported for this endpoint."}')
        return {"orderId": 5, "status": "NEW", "executedQty": "0"}

    async def fapiPrivateDeleteAllOpenOrders(self, params):
        self.calls.append(("cancel_all", params))
        raise RuntimeError('binanceusdm {"code":-2011,"msg":"Unknown order sent."}')

    async def fapiPrivateDeleteAlgoOpenOrders(self, params):
        self.calls.append(("cancel_all_algo", params))
        return {"code": 200, "msg": "The operation of cancel all open order is done."}

    async def fapiPrivateDeleteOrder(self, params):
        self.calls.append(("cancel", params))
        raise RuntimeError('binanceusdm {"code":-2011,"msg":"Unknown order sent."}')

    async def fapiPrivateDeleteAlgoOrder(self, params):
        self.calls.append(("cancel_algo", params))
        return {"algoId": 77, "code": "200"}


def _client(ex: FakeEx) -> ExchangeClient:
    c = ExchangeClient.__new__(ExchangeClient)
    c.kind, c.ex, c.rules, c.time_offset_ms = "futures", ex, {}, 0
    return c


def test_the_backstop_is_an_algo_stop_market_that_closes_the_position():
    ex = FakeEx()
    r = asyncio.run(_client(ex).place_backstop("ENAUSDT", "SELL", 0.2567, "m1s", 97.0))
    kind, p = ex.calls[0]
    assert kind == "algo" and p["algoType"] == "CONDITIONAL" and p["type"] == "STOP_MARKET" and p["side"] == "SELL"
    assert p["closePosition"] == "true" and p["clientAlgoId"] == "m1s" and float(p["triggerPrice"]) == pytest.approx(0.2567)
    assert "stopPrice" not in p and "quantity" not in p
    assert r.status == "NEW" and r.exchange_id == "77" and len(ex.calls) == 1


def test_a_venue_without_the_algo_api_uses_the_legacy_order():
    ex = FakeEx(algo_fails=True, legacy_fails=False)
    r = asyncio.run(_client(ex).place_backstop("ENAUSDT", "SELL", 0.2567, "m1s", None))
    assert [k for k, _ in ex.calls] == ["algo", "order"] and r.status == "NEW"


def test_when_both_fail_the_algo_error_is_raised():
    ex = FakeEx(algo_fails=True, legacy_fails=True)
    with pytest.raises(RuntimeError, match="-1102"):
        asyncio.run(_client(ex).place_backstop("ENAUSDT", "SELL", 0.2567, "m1s", None))


def test_cancel_all_also_cancels_the_algo_orders():
    ex = FakeEx()
    asyncio.run(_client(ex).cancel_all("ENAUSDT"))
    assert [k for k, _ in ex.calls] == ["cancel_all", "cancel_all_algo"]


def test_cancel_one_falls_through_to_the_algo_order():
    ex = FakeEx()
    asyncio.run(_client(ex).cancel_order("ENAUSDT", "m1s"))
    assert ex.calls[-1] == ("cancel_algo", {"clientAlgoId": "m1s"})
