"""Bybit venue, guard, client mapping and websocket frame handling.

These were written WITHOUT being executed (the authoring session was permission-blocked from running
pytest). Run them first; treat any failure here as a bug in the adapter, not in the test.

Nothing here touches the network: the client is driven with recorded v5 response shapes and the feed is
driven with recorded v5 frames.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app import config
from app.config import VENUES, ConfigError, load_settings
from app.core.feed import MarketFeed
from app.core.types import BookEvent, CandleClosed, CandleForming, MarkEvent, TradeEvent
from app.exchange.bybit_client import BybitClient, _decimals
from app.exchange.guard import GuardedBybit, LiveEndpointBlocked, build_exchange


def settings_for(tmp_path, **env):
    base = {"MODE": "BYBIT_TESTNET", "DASHBOARD_PASSWORD": "x", "DATA_DIR": str(tmp_path / "d")}
    base.update(env)
    return load_settings(base)


# ---- venue + guard -------------------------------------------------------------------------
class TestVenue:
    def test_bybit_testnet_is_registered_and_not_live(self):
        v = VENUES["BYBIT_TESTNET"]
        assert v.exchange == "bybit" and not v.is_live
        for url in (v.rest_base, v.ws_base):
            assert not config.is_live_host(url), url

    @pytest.mark.parametrize("alias", ["BYBIT", "BYBIT_DEMO", "BYBIT_TESTNET"])
    def test_aliases_resolve(self, tmp_path, alias):
        assert settings_for(tmp_path, MODE=alias).venue.mode == "BYBIT_TESTNET"

    @pytest.mark.parametrize("host", ["https://api.bybit.com", "wss://stream.bybit.com/v5/public/linear",
                                      "https://api.bytick.com"])
    def test_bybit_production_is_a_live_host(self, host):
        assert config.is_live_host(host)

    @pytest.mark.parametrize("host", ["https://api-testnet.bybit.com",
                                      "wss://stream-testnet.bybit.com/v5/public/linear"])
    def test_bybit_testnet_is_not_a_live_host(self, host):
        assert not config.is_live_host(host)

    def test_override_to_real_bybit_is_refused(self, tmp_path):
        """The whole point: you cannot reach real money by editing one env var."""
        with pytest.raises(ConfigError):
            settings_for(tmp_path, REST_BASE_OVERRIDE="https://api.bybit.com")

    def test_bybit_reads_bybit_keys_not_binance_ones(self, tmp_path):
        s = settings_for(tmp_path, BYBIT_API_KEY="bk", BYBIT_API_SECRET="bs", BINANCE_API_KEY="NO")
        assert s.api_key == "bk" and s.api_secret == "bs"

    def test_dry_run_false_demands_bybit_keys(self, tmp_path):
        with pytest.raises(ConfigError) as e:
            settings_for(tmp_path, DRY_RUN="false", BINANCE_API_KEY="k", BINANCE_API_SECRET="s")
        assert "BYBIT_API_KEY" in str(e.value)


class TestLiveDoor:
    """Real money is reachable only through MODE=LIVE_OVERRIDE_I_UNDERSTAND + EXCHANGE=bybit."""

    def live(self, tmp_path, **env):
        base = {"MODE": "LIVE_OVERRIDE_I_UNDERSTAND", "EXCHANGE": "bybit", "DASHBOARD_PASSWORD": "x",
                "DATA_DIR": str(tmp_path / "d"), "BYBIT_API_KEY": "k", "BYBIT_API_SECRET": "s"}
        base.update(env)
        return load_settings(base)

    def test_live_bybit_venue_is_flagged_live(self, tmp_path):
        s = self.live(tmp_path)
        assert s.is_live and s.venue.is_live and s.venue.exchange == "bybit"
        assert config.is_live_host(s.venue.rest_base)
        assert s.venue.label == "LIVE BYBIT"

    def test_live_binance_is_still_the_default_exchange(self, tmp_path):
        s = self.live(tmp_path, EXCHANGE="binance", BINANCE_API_KEY="k", BINANCE_API_SECRET="s")
        assert s.venue.exchange == "binance" and "binance" in s.venue.rest_base

    def test_unknown_exchange_is_refused(self, tmp_path):
        with pytest.raises(ConfigError):
            self.live(tmp_path, EXCHANGE="ftx")

    def test_live_bybit_uses_the_bybit_client(self, tmp_path):
        from app.exchange.client import make_client
        c = make_client(self.live(tmp_path))
        assert isinstance(c, BybitClient)

    def test_testnet_is_not_live(self, tmp_path):
        assert not settings_for(tmp_path).is_live


class TestLivePreflight:
    """The arm gate. Each of these would, if it failed open, put wrongly-sized real orders on an exchange."""

    def checks(self, engine, balance=None, candidates=None):
        return {c["id"]: c for c in engine.live_preflight(balance, candidates=candidates)}

    def test_more_than_one_live_book_blocks_arming(self, engine):
        """MAX_LIVE_STRATEGIES defaults to 1: two books on one real account is refused."""
        picks = set(list(engine.meta)[:3])
        assert self.checks(engine, candidates=picks)["live_books"]["ok"] is False

    def test_exactly_one_live_book_passes(self, engine):
        engine.meta["S15"].enabled = True
        c = self.checks(engine, candidates={"S15"})
        assert c["live_books"]["ok"] and c["enabled"]["ok"]

    def test_no_live_book_blocks_arming(self, engine):
        assert self.checks(engine, candidates=set())["live_books"]["ok"] is False

    def test_a_disabled_strategy_cannot_go_live(self, engine):
        engine.meta["S15"].enabled = False
        assert self.checks(engine, candidates={"S15"})["enabled"]["ok"] is False

    def test_paper_book_larger_than_the_real_wallet_blocks_arming(self, engine):
        """The oversizing trap: a big paper book against a small real account."""
        for m in engine.meta.values():
            m.enabled = False
        engine.meta["S15"].enabled = True
        paper = engine.portfolio.wallet_equity("S15", engine.prices())
        c = self.checks(engine, {"wallet": paper / 10.0}, candidates={"S15"})["paper_matches_wallet"]
        assert c["ok"] is False and "too large" in c["help"]

    def test_an_empty_exchange_account_blocks_arming(self, engine):
        """A 0 USDT trading account must block, and must not print a nonsense ratio like '1869158x'."""
        for m in engine.meta.values():
            m.enabled = False
        engine.meta["S15"].enabled = True
        c = self.checks(engine, {"wallet": 0.0}, candidates={"S15"})["paper_matches_wallet"]
        assert c["ok"] is False
        assert "UNIFIED TRADING" in c["help"] and "too large" not in c["help"]

    def test_unreadable_balance_blocks_rather_than_passes(self, engine):
        """No balance means the size check cannot be made, so it must fail closed, never open."""
        for m in engine.meta.values():
            m.enabled = False
        engine.meta["S15"].enabled = True
        assert self.checks(engine, {}, candidates={"S15"})["paper_matches_wallet"]["ok"] is False

    def test_matching_sizes_pass(self, engine):
        for m in engine.meta.values():
            m.enabled = False
        engine.meta["S15"].enabled = True
        paper = engine.portfolio.wallet_equity("S15", engine.prices())
        assert self.checks(engine, {"wallet": paper * 2}, candidates={"S15"})["paper_matches_wallet"]["ok"]

    @pytest.mark.asyncio
    async def test_solo_leaves_exactly_one_strategy_enabled(self, engine):
        for m in engine.meta.values():
            m.enabled = True
        r = await engine.solo_strategy("S15")
        enabled = [sid for sid, m in engine.meta.items() if m.enabled]
        assert enabled == ["S15"] and len(r["disabled"]) == len(engine.meta) - 1
        assert self.checks(engine, candidates={"S15"})["live_books"]["ok"]

    @pytest.mark.asyncio
    async def test_solo_is_idempotent(self, engine):
        await engine.solo_strategy("S15")
        again = await engine.solo_strategy("S15")
        assert again["disabled"] == [] and [s for s, m in engine.meta.items() if m.enabled] == ["S15"]

    @pytest.mark.asyncio
    async def test_solo_refuses_while_another_book_holds_a_position(self, engine):
        """Disabling a book does not close its position, so solo must not orphan one."""
        from app.core.engine import EngineError
        pos = next(iter(engine.portfolio.positions.values()), None)
        if pos is None:
            pytest.skip("no open position in the fixture")
        other = pos.strategy_id
        if other == "S15":
            pytest.skip("fixture position belongs to S15")
        with pytest.raises(EngineError):
            await engine.solo_strategy("S15")

    @pytest.mark.asyncio
    async def test_arm_refuses_without_the_phrase(self, engine):
        from app.core.engine import EngineError
        with pytest.raises(EngineError):
            await engine.arm_live("go live please")
        assert engine.router.dry_run is True

    @pytest.mark.asyncio
    async def test_arm_refuses_on_a_paper_venue_even_with_the_phrase(self, engine):
        from app.core.engine import EngineError
        with pytest.raises(EngineError):
            await engine.arm_live("GO LIVE")
        assert engine.router.dry_run is True, "a refused arm must leave the router on paper fills"


class TestOnlyLiveBooksReachTheExchange:
    """The core of running a live book beside a paper bake-off: the router nets ONLY the live books."""

    def test_net_qty_can_be_restricted_to_a_strategy_set(self, engine):
        pf = engine.portfolio
        sym = next((p.symbol for p in pf.positions.values()), None)
        if sym is None:
            pytest.skip("no open positions in the fixture")
        owners = {p.strategy_id for p in pf.positions.values() if p.symbol == sym}
        one = sorted(owners)[0]
        whole = pf.net_qty(sym)
        just_one = pf.net_qty(sym, only={one})
        assert just_one == sum(p.signed_qty for p in pf.positions.values()
                               if p.symbol == sym and p.strategy_id == one)
        if len(owners) > 1:
            assert just_one != whole, "restricting to one book must not equal the whole-lab net"

    def test_none_means_every_book(self, engine):
        pf = engine.portfolio
        sym = next((p.symbol for p in pf.positions.values()), None)
        if sym is None:
            pytest.skip("no open positions in the fixture")
        assert pf.net_qty(sym, only=None) == pf.net_qty(sym)

    def test_a_paper_book_contributes_nothing_when_it_is_not_live(self, engine):
        pf = engine.portfolio
        sym = next((p.symbol for p in pf.positions.values()), None)
        if sym is None:
            pytest.skip("no open positions in the fixture")
        assert pf.net_qty(sym, only=set()) == 0.0

    def test_router_defaults_to_all_books(self, engine):
        assert engine.router.live_strategies is None

    @pytest.mark.asyncio
    async def test_disarm_clears_the_live_set(self, engine):
        """Disarming must forget which books were live, or a later arm could silently reuse the old set."""
        async def noop(*a, **k):
            return None
        engine.router.flatten_exchange = noop      # the fixture client has no exchange behind it
        engine.router.cancel_orphans = noop
        engine.router.live_strategies = {"S15"}
        engine.router.dry_run = False
        await engine.disarm_live()
        assert engine.router.live_strategies is None and engine.router.dry_run is True


class TestLiveVenueNeverAutoArms:
    """DRY_RUN=false on a live venue must NOT start sending real orders at boot."""

    def test_router_boots_on_paper_even_with_dry_run_false(self, tmp_path):
        from app.exchange.paper_router import PaperRouter
        s = load_settings({"MODE": "LIVE_OVERRIDE_I_UNDERSTAND", "EXCHANGE": "bybit", "DRY_RUN": "false",
                           "BYBIT_API_KEY": "k", "BYBIT_API_SECRET": "s", "DASHBOARD_PASSWORD": "x",
                           "DATA_DIR": str(tmp_path / "d")})
        assert s.dry_run is False and s.venue.is_live
        r = PaperRouter(s, object(), None, None, {}, lambda: 0, lambda _s: 0.0)
        assert r.dry_run is True, "a live venue must require an explicit arm, never auto-arm at boot"


class TestGuard:
    @pytest.mark.asyncio
    async def test_urls_point_at_the_venue_and_never_at_live(self, tmp_path):
        ex = build_exchange(settings_for(tmp_path))
        try:
            assert isinstance(ex, GuardedBybit)
            for url in ex.urls["api"].values():
                assert not config.is_live_host(url), url
        finally:
            await ex.close()

    @pytest.mark.asyncio
    async def test_fetch_refuses_a_live_host_before_any_socket(self, tmp_path):
        ex = build_exchange(settings_for(tmp_path))
        try:
            with pytest.raises(LiveEndpointBlocked):
                await ex.fetch("https://api.bybit.com/v5/market/time")
        finally:
            await ex.close()


# ---- client response mapping ---------------------------------------------------------------
INSTRUMENT = {"result": {"list": [{
    "symbol": "SOLUSDT",
    "priceFilter": {"tickSize": "0.010"},
    "lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1", "minNotionalValue": "5"},
    "maintenanceMargin": "0.005",
}]}}
KLINES = {"result": {"list": [                       # newest first, as bybit sends it
    ["1700000120000", "3", "4", "2", "3.5", "10", "35"],
    ["1700000060000", "2", "3", "1", "2.5", "20", "50"],
]}}
POSITIONS = {"result": {"list": [{
    "symbol": "SOLUSDT", "side": "Sell", "size": "1.5", "avgPrice": "100", "liqPrice": "120",
    "positionIM": "15", "unrealisedPnl": "-2", "leverage": "10", "markPrice": "101",
}]}}
WALLET = {"result": {"list": [{"totalAvailableBalance": "9.5",
                               "coin": [{"coin": "USDT", "walletBalance": "10", "availableToWithdraw": "9.5",
                                         "unrealisedPnl": "-0.5"}]}]}}


def client_with(tmp_path, **responses):
    c = BybitClient(settings_for(tmp_path))
    for name, value in responses.items():
        async def call(params=None, _v=value):
            return _v
        setattr(c.ex, name, call)
    return c


class TestClientMapping:
    @pytest.mark.asyncio
    async def test_load_rules(self, tmp_path):
        c = client_with(tmp_path, publicGetV5MarketInstrumentsInfo=INSTRUMENT)
        r = (await c.load_rules(["SOLUSDT"]))["SOLUSDT"]
        assert (r.tick, r.step, r.min_qty, r.min_notional) == (0.01, 0.1, 0.1, 5.0)
        assert (r.price_precision, r.qty_precision) == (2, 1)
        await c.close()

    @pytest.mark.asyncio
    async def test_klines_come_back_oldest_first(self, tmp_path):
        c = client_with(tmp_path, publicGetV5MarketKline=KLINES)
        out = await c.fetch_klines("SOLUSDT", "1m")
        assert [x.open_time for x in out] == [1700000060000, 1700000120000]
        assert out[0].close_time == 1700000060000 + 60_000
        assert out[0].close == 2.5 and out[0].source == "backfill"
        await c.close()

    @pytest.mark.asyncio
    async def test_short_position_comes_back_signed_negative(self, tmp_path):
        c = client_with(tmp_path, privateGetV5PositionList=POSITIONS)
        p = (await c.fetch_positions(["SOLUSDT"]))["SOLUSDT"]
        assert p.qty == -1.5 and p.entry_price == 100 and p.leverage == 10
        await c.close()

    @pytest.mark.asyncio
    async def test_balance(self, tmp_path):
        c = client_with(tmp_path, privateGetV5AccountWalletBalance=WALLET)
        assert await c.fetch_balance() == {"wallet": 10.0, "available": 9.5, "upnl": -0.5}
        await c.close()

    @pytest.mark.parametrize("raw,want", [("Filled", "FILLED"), ("New", "NEW"), ("Cancelled", "CANCELED"),
                                          ("PartiallyFilled", "PARTIALLY_FILLED"), ("Rejected", "REJECTED")])
    def test_status_vocabulary_is_translated(self, raw, want):
        assert BybitClient._norm_status(raw) == want

    @pytest.mark.parametrize("side,want", [("BUY", "Buy"), ("SELL", "Sell"), ("long", "Buy"), ("short", "Sell")])
    def test_side_vocabulary_is_translated(self, side, want):
        assert BybitClient._side(side) == want

    @pytest.mark.parametrize("step,want", [("0.010", 2), ("0.1", 1), ("1", 0), ("0.001", 3), ("1e-05", 5)])
    def test_decimals(self, step, want):
        assert _decimals(step) == want

    @pytest.mark.asyncio
    async def test_market_order_never_reports_a_fill_from_an_ack(self, tmp_path):
        """v5 order/create returns ids only. An unconfirmed order must NOT be reported as filled."""
        c = client_with(tmp_path)
        async def create(params=None):
            return {"result": {"orderId": "1", "orderLinkId": "plb-x"}}
        async def realtime(params=None):
            return {"result": {"list": [{"orderStatus": "New", "cumExecQty": "0"}]}}
        c.ex.privatePostV5OrderCreate = create
        c.ex.privateGetV5OrderRealtime = realtime
        res = await c.market_order(SimpleNamespace(symbol="SOLUSDT", side="BUY", qty=0.1,
                                                   client_id="plb-x", reduce_only=False))
        assert res.status == "UNCONFIRMED" and res.executed_qty == 0
        await c.close()


# ---- websocket frames ----------------------------------------------------------------------
def feed_for(queue):
    return MarketFeed(VENUES["BYBIT_TESTNET"], ["SOLUSDT"], ("1m", "15m"), queue)


async def drain(q):
    out = []
    while not q.empty():
        out.append(await q.get())
    return out


class TestBybitFeed:
    def test_topics_use_bare_minutes(self):
        t = feed_for(asyncio.Queue()).bybit_topics()
        assert "kline.1.SOLUSDT" in t and "kline.15.SOLUSDT" in t
        assert "tickers.SOLUSDT" in t and "publicTrade.SOLUSDT" in t and "orderbook.50.SOLUSDT" in t

    def test_market_url_has_no_query_string(self):
        assert feed_for(asyncio.Queue()).market_url() == VENUES["BYBIT_TESTNET"].ws_base

    @pytest.mark.asyncio
    async def test_closed_and_forming_klines(self):
        q = asyncio.Queue()
        f = feed_for(q)
        await f._handle_market({"topic": "kline.1.SOLUSDT", "data": [
            {"start": 1700000000000, "end": 1700000059999, "open": "1", "high": "2", "low": "0.5",
             "close": "1.5", "volume": "9", "turnover": "12", "confirm": False}]})
        await f._handle_market({"topic": "kline.1.SOLUSDT", "data": [
            {"start": 1700000000000, "end": 1700000059999, "open": "1", "high": "2", "low": "0.5",
             "close": "1.8", "volume": "11", "turnover": "14", "confirm": True}]})
        evs = await drain(q)
        assert isinstance(evs[0], CandleForming) and isinstance(evs[1], CandleClosed)
        c = evs[1].candle
        assert c.tf == "1m" and c.closed and c.close == 1.8 and c.close_time == 1700000060000

    @pytest.mark.asyncio
    async def test_public_trade_maker_flag_matches_binance_semantics(self):
        q = asyncio.Queue()
        await feed_for(q)._handle_market({"topic": "publicTrade.SOLUSDT", "data": [
            {"T": 1700000000000, "s": "SOLUSDT", "S": "Sell", "v": "2", "p": "100"}]})
        ev = (await drain(q))[0]
        assert isinstance(ev, TradeEvent) and ev.tick.buyer_is_maker is True and ev.tick.price == 100

    @pytest.mark.asyncio
    async def test_tickers_becomes_a_mark_event(self):
        q = asyncio.Queue()
        await feed_for(q)._handle_market({"topic": "tickers.SOLUSDT", "ts": 1700000000000, "data": {
            "symbol": "SOLUSDT", "markPrice": "101.5", "indexPrice": "101.4", "fundingRate": "0.0001",
            "nextFundingTime": "1700003600000"}})
        ev = (await drain(q))[0]
        assert isinstance(ev, MarkEvent) and ev.mark.mark == 101.5

    @pytest.mark.asyncio
    async def test_orderbook_snapshot_then_delta_is_merged(self):
        q = asyncio.Queue()
        f = feed_for(q)
        await f._handle_market({"topic": "orderbook.50.SOLUSDT", "type": "snapshot", "ts": 1,
                                "data": {"s": "SOLUSDT", "b": [["100", "1"], ["99", "2"]],
                                         "a": [["101", "1"], ["102", "2"]]}})
        await f._handle_market({"topic": "orderbook.50.SOLUSDT", "type": "delta", "ts": 2,
                                "data": {"s": "SOLUSDT", "b": [["100", "0"], ["98", "5"]], "a": []}})
        evs = [e for e in await drain(q) if isinstance(e, BookEvent)]
        book = evs[-1].book
        assert [p for p, _ in book.bids] == [99.0, 98.0]     # 100 deleted by the zero-size level
        assert [p for p, _ in book.asks] == [101.0, 102.0]

    @pytest.mark.asyncio
    async def test_subscribe_ack_and_pong_are_ignored(self):
        q = asyncio.Queue()
        f = feed_for(q)
        await f._handle_market({"op": "subscribe", "success": True, "conn_id": "x"})
        await f._handle_market({"op": "pong"})
        assert q.empty()


class TestArmingNeverGuesses:
    """Arming once defaulted to the first enabled strategy and put S01 on real money instead of S15.
    Real funds must only ever follow an explicitly named book."""

    @pytest.mark.asyncio
    async def test_empty_strategy_list_is_refused(self, engine):
        from app.core.engine import EngineError
        with pytest.raises(EngineError) as e:
            await engine.arm_live("GO LIVE", [])
        assert "name the strategy" in str(e.value)
        assert engine.router.dry_run is True

    @pytest.mark.asyncio
    async def test_blank_entries_are_refused(self, engine):
        from app.core.engine import EngineError
        with pytest.raises(EngineError):
            await engine.arm_live("GO LIVE", ["", "  "])
        assert engine.router.dry_run is True

    @pytest.mark.asyncio
    async def test_unknown_strategy_is_refused(self, engine):
        from app.core.engine import EngineError
        with pytest.raises(EngineError):
            await engine.arm_live("GO LIVE", ["S99"])
        assert engine.router.dry_run is True
