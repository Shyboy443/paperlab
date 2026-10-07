"""LIVE MIRROR guards and behaviour against a fake exchange (no network, no money): who may go live, how orders are
sized, the exchange-side stop, closes, loss limits, STOP, reconciliation, the testnet round trip, mainnet locks and
that keys never leave the server."""
from __future__ import annotations

import asyncio

import pytest

from app.core.storage import Storage
from app.core.types import ExchangePosition, FundingInfo, MarketRules, OrderResult
from app.live.mirror import MirrorError, MirrorService
from app.live.providers import Providers

KEY, SECRET = "TESTKEY-abc123", "TESTSECRET-xyz789"


class FakeExchange:
    def __init__(self, price=2000.0, balance=500.0):
        self.price, self.balance = price, balance
        self.rules: dict[str, MarketRules] = {}
        self.orders: list[tuple] = []
        self.stops: list[tuple] = []
        self.pos: dict[str, float] = {}

    async def load_rules(self, symbols):
        for s in symbols:
            self.rules[s] = MarketRules(s, 0.01, 0.001, 0.001, 5.0, 0.025, 2, 3)
        return {s: self.rules[s] for s in symbols}

    async def ensure_account_mode(self, symbols, leverage):
        return {"leverage": leverage}

    async def market_order(self, intent):
        signed = intent.qty if intent.side == "BUY" else -intent.qty
        self.pos[intent.symbol] = round(self.pos.get(intent.symbol, 0.0) + signed, 9)
        self.orders.append((intent.side, intent.qty, intent.reduce_only, intent.client_id))
        return OrderResult(intent.client_id, "x" + intent.client_id, "FILLED", self.price, intent.qty, {})

    async def place_backstop(self, symbol, side, stop, cid, qty=None):
        self.stops.append((symbol, side, stop, qty))
        return OrderResult(cid, "s" + cid, "NEW", None, 0.0, {})

    async def cancel_all(self, symbol):
        self.stops = [s for s in self.stops if s[0] != symbol]

    async def fetch_positions(self, symbols, all_symbols=False):
        return {s: ExchangePosition(s, self.pos.get(s, 0.0), 0.0, None, 0.0, 0.0, 1, self.price) for s in symbols}

    async def fetch_balance(self):
        return {"wallet": self.balance, "available": self.balance}

    async def fetch_premium_index(self, symbol):
        return FundingInfo(symbol, 0.0, 0, self.price, self.price, 0)

    async def close(self):
        pass


def make(tmp_path, status="QUALIFIED", env=None, paper_open=True, exchange=None):
    fx = exchange or FakeExchange()
    env = {"BYBIT_TESTNET_API_KEY": KEY, "BYBIT_TESTNET_API_SECRET": SECRET, **(env or {})}
    st = Storage(str(tmp_path / "m.db"))
    row = {"key": "V8.1-ETH-5M", "symbol": "ETHUSDT", "coin": "ETH", "program_status": status,
           "open_positions": [{"side": "long"}] if paper_open else []}
    svc = MirrorService(st, Providers(env, client_factory=lambda ex, net: fx), lambda prog, key: row if key == row["key"] else None)
    return svc, fx, st, row


REQ = {"program": "v8", "bot_key": "V8.1-ETH-5M", "exchange": "bybit", "network": "testnet", "amount_usdt": 100,
       "risk_pct": 0.01, "max_daily_loss": 10, "max_total_loss": 25, "confirm": "GO LIVE V8.1-ETH-5M"}
OPEN = {"type": "open", "bot_key": "V8.1-ETH-5M", "side": "long", "price": 2000.0, "stop": 1990.0, "ts": 1_790_000_000_000}
CLOSED = {"type": "closed", "bot_key": "V8.1-ETH-5M", "side": "long", "exit_kind": "tp", "counterfactual": False}


def run(coro):
    return asyncio.run(coro)


class TestArming:
    @pytest.mark.parametrize("change,msg", [({"confirm": "go live"}, "type exactly"), ({"risk_pct": 0.03}, "at most 2%"),
                                            ({"amount_usdt": 1000}, "at most 200"), ({"network": "mainnet"}, "keys are not configured"),
                                            ({"exchange": "kraken"}, "Bybit or Binance"), ({"max_daily_loss": 500}, "loss limits")])
    def test_refusals(self, tmp_path, change, msg):
        svc, *_ = make(tmp_path)
        with pytest.raises(MirrorError, match=msg):
            run(svc.arm({**REQ, **change}))

    def test_only_qualified_bots(self, tmp_path):
        svc, *_ = make(tmp_path, status="ACTIVE")
        with pytest.raises(MirrorError, match="only QUALIFIED"):
            run(svc.arm(REQ))

    def test_balance_must_cover_the_amount(self, tmp_path):
        svc, *_ = make(tmp_path, exchange=FakeExchange(balance=50))
        with pytest.raises(MirrorError, match="below the amount"):
            run(svc.arm(REQ))

    def test_mainnet_needs_the_switch_and_a_testnet_round_trip(self, tmp_path):
        env = {"BYBIT_API_KEY": "LIVEKEY-1", "BYBIT_API_SECRET": "LIVESECRET-1"}
        svc, *_ = make(tmp_path, env=env)
        with pytest.raises(MirrorError, match="LIVE_MIRROR_MAINNET_ENABLED"):
            run(svc.arm({**REQ, "network": "mainnet"}))
        svc, fx, st, _ = make(tmp_path, env={**env, "LIVE_MIRROR_MAINNET_ENABLED": "true"})
        with pytest.raises(MirrorError, match="testnet round trip"):
            run(svc.arm({**REQ, "network": "mainnet"}))
        assert run(svc.verify_testnet("bybit"))["ok"] is True and fx.pos["ETHUSDT"] == 0.0
        assert run(svc.arm({**REQ, "network": "mainnet"}))["status"] == "ARMED"

    def test_one_live_mirror_per_bot(self, tmp_path):
        svc, *_ = make(tmp_path)
        run(svc.arm(REQ))
        with pytest.raises(MirrorError, match="already live"):
            run(svc.arm(REQ))


class TestFollowing:
    def test_open_is_risk_sized_with_an_exchange_stop_and_close_books_pnl(self, tmp_path):
        svc, fx, st, _ = make(tmp_path)
        m = run(svc.arm(REQ))
        run(svc.handle("v8", OPEN))
        assert fx.orders[0][:3] == ("BUY", 0.1, False)            # 100 x 1% / (2000 - 1990) = 0.1 ETH
        assert fx.stops == [("ETHUSDT", "SELL", 1990.0, 0.1)]
        assert svc.mirrors[m["id"]]["position"]["qty"] == 0.1
        fx.price = 2015.0
        run(svc.handle("v8", CLOSED))
        side, qty, reduce_only, _ = fx.orders[1]
        assert (side, qty, reduce_only) == ("SELL", 0.1, True) and fx.pos["ETHUSDT"] == 0.0 and fx.stops == []
        saved = st.mirrors()[0]
        assert saved["position"] is None and saved["trades"] == 1 and saved["realized"] == pytest.approx(1.5 - 0.0006 * 0.1 * 4015)

    def test_replayed_and_foreign_events_are_ignored(self, tmp_path):
        svc, fx, *_ = make(tmp_path)
        run(svc.arm(REQ))
        run(svc.handle("v8", {**OPEN, "bot_key": "V8.2-ETH-5M"}))
        run(svc.handle("v7", OPEN))
        svc.loop = asyncio.new_event_loop()
        svc.on_event("v8", {**OPEN, "rederived": True})            # never scheduled
        svc.loop.close()
        assert fx.orders == []

    def test_below_the_exchange_minimum_is_skipped_not_enlarged(self, tmp_path):
        svc, fx, st, _ = make(tmp_path)
        m = run(svc.arm({**REQ, "amount_usdt": 5, "max_daily_loss": 1, "max_total_loss": 2}))
        run(svc.handle("v8", {**OPEN, "stop": 1000.0}))
        assert fx.orders == [] and any(x["kind"] == "skip" for x in st.mirror_logs(m["id"]))

    def test_the_loss_limit_stops_the_mirror(self, tmp_path):
        svc, fx, st, _ = make(tmp_path)
        m = run(svc.arm({**REQ, "max_daily_loss": 1, "max_total_loss": 1}))
        run(svc.handle("v8", OPEN))
        fx.price = 1985.0
        run(svc.handle("v8", {**CLOSED, "exit_kind": "stop"}))
        assert st.mirrors()[0]["status"] == "STOPPED" and svc.mirrors[m["id"]]["reason"] == "loss limit reached"
        run(svc.handle("v8", OPEN))
        assert len(fx.orders) == 2                                  # nothing new after the stop


class TestStopsAndReconcile:
    def test_operator_stop_and_kill_flatten(self, tmp_path):
        svc, fx, st, _ = make(tmp_path)
        m = run(svc.arm(REQ))
        run(svc.handle("v8", OPEN))
        run(svc.disarm(m["id"]))
        assert fx.pos["ETHUSDT"] == 0.0 and st.mirrors()[0]["status"] == "STOPPED"
        svc2, fx2, *_ = make(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path)
        run(svc2.arm(REQ))
        run(svc2.handle("v8", OPEN))
        assert run(svc2.disarm_all("kill switch")) == 1 and fx2.pos["ETHUSDT"] == 0.0

    def test_a_stop_that_fired_on_the_exchange_is_booked(self, tmp_path):
        svc, fx, st, _ = make(tmp_path)
        run(svc.arm(REQ))
        run(svc.handle("v8", OPEN))
        fx.pos["ETHUSDT"] = 0.0                                     # the exchange stop closed it
        run(svc.reconcile())
        assert st.mirrors()[0]["position"] is None and len(fx.orders) == 1

    def test_a_live_position_the_paper_bot_no_longer_holds_is_closed(self, tmp_path):
        svc, fx, st, row = make(tmp_path)
        run(svc.arm(REQ))
        run(svc.handle("v8", OPEN))
        row["open_positions"] = []
        run(svc.reconcile())
        assert fx.pos["ETHUSDT"] == 0.0 and st.mirrors()[0]["position"] is None


class TestSecrets:
    def test_status_and_logs_never_carry_keys(self, tmp_path):
        svc, fx, st, _ = make(tmp_path)
        status = run(svc.providers.status(st))
        blob = repr(status) + repr(st.mirror_logs())
        assert KEY not in blob and SECRET not in blob
        assert any(p["exchange"] == "bybit" and p["network"] == "testnet" and p["keys_configured"] for p in status)
        assert svc.providers.redact(f"error with {KEY} inside") == "error with *** inside"

    def test_routes_are_private(self):
        from app.core import api_live_mirror
        paths = {r.path for r in api_live_mirror.router.routes}
        assert paths and all(p.startswith("/api/mirror") for p in paths)


class Flaky(FakeExchange):
    """An exchange that misbehaves on demand: unconfirmed fills, rejected stops, failing closes, outages."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.open_mode = "ok"          # ok | unconfirmed_filled | unconfirmed_empty | raise_filled
        self.stop_status = "NEW"
        self.close_fails = 0
        self.down = False
        self.cancels = 0

    async def market_order(self, intent):
        if not intent.reduce_only and self.open_mode != "ok":
            if self.open_mode in ("unconfirmed_filled", "raise_filled"):
                await super().market_order(intent)            # the exchange DID fill it
            if self.open_mode == "raise_filled":
                raise TimeoutError("read timed out")
            return OrderResult(intent.client_id, "", "UNCONFIRMED", None, 0.0, {})
        if intent.reduce_only and self.close_fails > 0:
            self.close_fails -= 1
            return OrderResult(intent.client_id, "", "REJECTED", None, 0.0, {}, "rejected")
        return await super().market_order(intent)

    async def place_backstop(self, symbol, side, stop, cid, qty=None):
        res = await super().place_backstop(symbol, side, stop, cid, qty)
        return OrderResult(cid, "s" + cid, self.stop_status, None, 0.0, {}) if self.stop_status != "NEW" else res

    async def cancel_all(self, symbol):
        self.cancels += 1
        await super().cancel_all(symbol)

    async def fetch_positions(self, symbols, all_symbols=False):
        if self.down:
            raise ConnectionError("exchange unreachable")
        return await super().fetch_positions(symbols, all_symbols)


def make_flaky(tmp_path, **kw):
    fx = Flaky()
    svc, fx, st, row = make(tmp_path, exchange=fx, **kw)
    alerts: list[str] = []
    svc.alert_fn = alerts.append
    svc.recheck_s = 0
    return svc, fx, st, row, alerts


class TestFailurePaths:
    @pytest.mark.parametrize("mode", ["unconfirmed_filled", "raise_filled"])
    def test_an_unconfirmed_entry_that_filled_is_adopted_and_protected(self, tmp_path, mode):
        svc, fx, st, _, alerts = make_flaky(tmp_path)
        m = run(svc.arm(REQ))
        fx.open_mode = mode
        run(svc.handle("v8", OPEN))
        pos = svc.mirrors[m["id"]]["position"]
        assert pos and pos["qty"] == 0.1 and pos["adopted"] and fx.stops == [("ETHUSDT", "SELL", 1990.0, 0.1)]
        assert any("DID fill" in a for a in alerts) and any(x["kind"] == "adopted" for x in st.mirror_logs(m["id"]))

    def test_an_unconfirmed_entry_that_did_not_fill_leaves_nothing_open(self, tmp_path):
        svc, fx, st, _, alerts = make_flaky(tmp_path)
        m = run(svc.arm(REQ))
        fx.open_mode = "unconfirmed_empty"
        run(svc.handle("v8", OPEN))
        assert svc.mirrors[m["id"]]["position"] is None and fx.cancels == 1 and fx.stops == []
        assert any("did not fill" in a for a in alerts)

    def test_a_rejected_exchange_stop_closes_the_position(self, tmp_path):
        svc, fx, st, _, alerts = make_flaky(tmp_path)
        m = run(svc.arm(REQ))
        fx.stop_status = "REJECTED"
        run(svc.handle("v8", OPEN))
        assert fx.pos["ETHUSDT"] == 0.0 and svc.mirrors[m["id"]]["position"] is None
        assert any("could not be placed" in a for a in alerts)

    def test_a_failed_close_puts_the_stop_back_and_reconcile_retries(self, tmp_path):
        svc, fx, st, row, alerts = make_flaky(tmp_path)
        m = run(svc.arm(REQ))
        run(svc.handle("v8", OPEN))
        fx.close_fails = 1
        run(svc.handle("v8", CLOSED))
        assert fx.pos["ETHUSDT"] == 0.1 and svc.mirrors[m["id"]]["position"]                  # still open ...
        assert fx.stops and fx.stops[-1][2] == 1990.0                                          # ... and protected
        assert any("stop is back" in a for a in alerts)
        row["open_positions"] = []
        run(svc.reconcile())                                                                   # the retry closes it
        assert fx.pos["ETHUSDT"] == 0.0 and svc.mirrors[m["id"]]["position"] is None

    def test_the_loss_limit_is_checked_mark_to_market(self, tmp_path):
        svc, fx, st, _, alerts = make_flaky(tmp_path)
        m = run(svc.arm({**REQ, "max_daily_loss": 1, "max_total_loss": 2}))
        run(svc.handle("v8", OPEN))
        fx.price = 1985.0                                       # -1.5 USDT open loss, the paper bot still holds
        run(svc.reconcile())
        mm = svc.mirrors[m["id"]]
        assert fx.pos["ETHUSDT"] == 0.0 and mm["position"] is None and mm["status"] == "STOPPED"
        assert "loss limit" in mm["reason"] and any("LOSS LIMIT" in a for a in alerts)

    def test_safe_mode_blocks_new_entries_but_not_management(self, tmp_path):
        svc, fx, st, row, alerts = make_flaky(tmp_path)
        m = run(svc.arm(REQ))
        svc.set_program_safe("v8", "V8 market data stale for 3 min")
        run(svc.handle("v8", OPEN))
        assert fx.orders == [] and any("SAFE MODE ON" in a for a in alerts)
        svc.set_program_safe("v8", None)
        run(svc.handle("v8", OPEN))
        assert len(fx.orders) == 1 and any("SAFE MODE OFF" in a for a in alerts)
        svc.set_program_safe("v8", "worker restarting")
        run(svc.handle("v8", CLOSED))                           # an open position is still closed normally
        assert fx.pos["ETHUSDT"] == 0.0 and svc.mirrors[m["id"]]["position"] is None

    def test_an_exchange_outage_turns_on_safe_mode_and_recovery_clears_it(self, tmp_path):
        svc, fx, st, _, alerts = make_flaky(tmp_path)
        m = run(svc.arm(REQ))
        run(svc.handle("v8", OPEN))
        fx.down = True
        for _ in range(3):
            run(svc.reconcile())
        assert svc.safe_reason(svc.mirrors[m["id"]]) and any("SAFE MODE ON" in a for a in alerts)
        fx.down = False
        run(svc.reconcile())
        assert svc.safe_reason(svc.mirrors[m["id"]]) is None and any("SAFE MODE OFF" in a for a in alerts)

    def test_the_kill_switch_and_arming_alert_the_operator(self, tmp_path):
        svc, fx, st, _, alerts = make_flaky(tmp_path)
        run(svc.arm(REQ))
        run(svc.handle("v8", OPEN))
        run(svc.disarm_all("kill switch"))
        assert any("ARMED" in a for a in alerts) and any("KILL SWITCH" in a for a in alerts)
        assert all(KEY not in a and SECRET not in a for a in alerts)


class TestPortfolioLimits:
    def test_armed_amounts_together_cannot_exceed_the_account_equity(self, tmp_path):
        fx = FakeExchange(balance=150.0)
        svc, fx, st, row = make(tmp_path, exchange=fx)
        run(svc.arm(REQ))                                                         # 100 of 150 committed
        row2 = {**row, "key": "V8.2-ETH-5M"}
        svc.bot_view = lambda prog, key: row if key == row["key"] else (row2 if key == row2["key"] else None)
        with pytest.raises(MirrorError, match="already committed"):
            run(svc.arm({**REQ, "bot_key": "V8.2-ETH-5M", "confirm": "GO LIVE V8.2-ETH-5M", "amount_usdt": 100}))
        assert run(svc.arm({**REQ, "bot_key": "V8.2-ETH-5M", "confirm": "GO LIVE V8.2-ETH-5M", "amount_usdt": 50,
                            "max_daily_loss": 5, "max_total_loss": 10}))["status"] == "ARMED"

    def test_at_most_two_same_direction_live_positions_per_account(self, tmp_path):
        fx = FakeExchange(balance=1000.0)
        svc, fx, st, row = make(tmp_path, exchange=fx)
        alerts = []
        svc.alert_fn = alerts.append
        rows = {}
        for coin in ("ETH", "SOL", "XRP"):
            key = f"V8.1-{coin}-5M"
            rows[key] = {**row, "key": key, "symbol": coin + "USDT", "coin": coin}
        svc.bot_view = lambda prog, key: rows.get(key)
        for key in rows:
            run(svc.arm({**REQ, "bot_key": key, "confirm": f"GO LIVE {key}", "amount_usdt": 100}))
        for key in rows:
            run(svc.handle("v8", {**OPEN, "bot_key": key}))
        assert len(fx.orders) == 2 and any("correlated exposure cap" in a for a in alerts)
        run(svc.handle("v8", {**OPEN, "bot_key": "V8.1-XRP-5M", "side": "short", "stop": 2010.0}))
        assert len(fx.orders) == 3                                               # the other direction is allowed


def test_a_bot_that_loses_its_qualification_takes_no_new_live_entries(tmp_path):
    svc, fx, st, row = make(tmp_path)
    alerts = []
    svc.alert_fn = alerts.append
    m = run(svc.arm(REQ))
    run(svc.handle("v8", OPEN))
    assert len(fx.orders) == 1
    row["program_status"] = "ELIMINATED"
    run(svc.handle("v8", CLOSED))                               # the open position is still closed
    assert fx.pos["ETHUSDT"] == 0.0 and len(fx.orders) == 2
    run(svc.handle("v8", OPEN))
    run(svc.handle("v8", OPEN))
    assert len(fx.orders) == 2 and sum("no longer QUALIFIED" in a for a in alerts) == 1
    row["program_status"] = "QUALIFIED"
    run(svc.handle("v8", OPEN))
    assert len(fx.orders) == 3 and svc.mirrors[m["id"]]["status"] == "ARMED"
