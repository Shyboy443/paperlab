"""IBS bot executor (app/ibsbot/service.py) against a fake Alpaca: OFF by default, refusals, scheduling, orders."""
from __future__ import annotations

import asyncio
import json
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.exchange.alpaca_client import AlpacaError
from app.ibsbot.service import CONFIRM, IbsBot

ET = ZoneInfo("America/New_York")
U = lambda d, hm: datetime.combine(date.fromisoformat(d), datetime.strptime(hm, "%H:%M:%S").time(), ET).astimezone(timezone.utc)  # noqa: E731


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeAlpaca:
    """Just enough of Alpaca: calendar, SIP (15-min delayed) and IEX data, account, positions, orders, clock."""

    def __init__(self):
        self.now = U("2026-10-21", "10:00:00")
        self.days = {}                                  # date -> {"QQQ": (h, l, c), "TQQQ": (h, l, c)}
        self.closes = {}                                # date -> "HH:MM" (early closes)
        self.cash, self.positions, self.orders, self.posted = 10_000.0, {}, {}, []
        self.account_id = "acct-1"
        self.sip_requests = []
        d = date(2026, 9, 1)
        while d <= date(2026, 11, 30):
            if d.weekday() < 5:
                self.days[str(d)] = {"QQQ": (101.0, 99.0, 100.0), "TQQQ": (51.0, 49.0, 50.0)}
            d += timedelta(days=1)

    def session(self, d):
        return U(d, "09:30:00"), U(d, (self.closes.get(d, "16:00")) + ":00")

    def price(self, sym):
        return self.days[self.now.astimezone(ET).date().isoformat()][sym][2]

    async def fetch_account(self):
        return await self.request("GET", "/v2/account")

    async def close(self):
        return None

    async def request(self, method, path, body=None, data=False):
        u = urllib.parse.urlparse(path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        p = u.path
        if p == "/v2/calendar":
            return [{"date": d, "open": "09:30", "close": self.closes.get(d, "16:00")} for d in sorted(self.days)
                    if q["start"] <= d <= q["end"]]
        if p == "/v2/clock":
            d = self.now.astimezone(ET).date().isoformat()
            o, c = self.session(d) if d in self.days else (None, None)
            return {"timestamp": iso(self.now), "is_open": bool(o and o <= self.now < c)}
        if p == "/v2/stocks/bars":
            start, end = datetime.fromisoformat(q["start"].replace("Z", "+00:00")), datetime.fromisoformat(q["end"].replace("Z", "+00:00"))
            if q["feed"] == "sip":
                self.sip_requests.append(end)
                if end > self.now - timedelta(minutes=15):
                    raise AlpacaError(403, "subscription does not permit querying recent SIP data")
            out = {}
            for s in q["symbols"].split(","):
                rows = []
                if q["timeframe"] == "1Day":
                    for d, v in sorted(self.days.items()):
                        t = datetime.fromisoformat(d).replace(tzinfo=ET)
                        if start <= t < end and U(d, "16:00:00") <= self.now:
                            h, l, c = v[s]
                            rows.append({"t": iso(t), "o": c, "h": h, "l": l, "c": c, "v": 1})
                else:
                    d = start.astimezone(ET).date().isoformat()
                    h, l, c = self.days[d][s]
                    o, _ = self.session(d)
                    t = o
                    while t < min(end, self.now):
                        k = int((t - o).total_seconds() // 60)
                        rows.append({"t": iso(t), "o": c, "h": h if k == 5 else c, "l": l if k == 7 else c, "c": c, "v": 1})
                        t += timedelta(minutes=1)
                    rows = [r for r in rows if start <= datetime.fromisoformat(r["t"].replace("Z", "+00:00"))]
                out[s] = rows
            return {"bars": out, "next_page_token": None}
        if p == "/v2/stocks/trades/latest":
            return {"trades": {s: {"t": iso(self.now - timedelta(seconds=3)), "p": self.price(s)} for s in q["symbols"].split(",")}}
        if p == "/v2/account":
            return {"id": self.account_id, "status": "ACTIVE", "currency": "USD", "cash": str(self.cash), "equity": str(self.cash)}
        if p == "/v2/positions":
            return [{"symbol": s, "qty": str(x)} for s, x in self.positions.items() if x]
        if p.startswith("/v2/assets/"):
            return {"symbol": p.rsplit("/", 1)[1], "tradable": True, "fractionable": True}
        if p == "/v2/orders" and method == "GET":
            return [o for o in self.orders.values() if o["status"] not in ("filled", "canceled")]
        if p == "/v2/orders:by_client_order_id":
            if q["client_order_id"] not in self.orders:
                raise AlpacaError(404, "order not found")
            return self.orders[q["client_order_id"]]
        if p == "/v2/orders" and method == "POST":
            assert body["symbol"] in ("QQQ", "TQQQ") and body["type"] == "market" and body["time_in_force"] == "day"
            assert body["client_order_id"] not in self.orders, "the same order was sent twice"
            self.posted.append(body)
            px = self.price(body["symbol"])
            qty = float(body["notional"]) / px if "notional" in body else float(body["qty"])
            sign = 1 if body["side"] == "buy" else -1
            self.cash -= sign * qty * px
            self.positions[body["symbol"]] = self.positions.get(body["symbol"], 0.0) + sign * qty
            o = {**body, "id": "o" + str(len(self.orders)), "status": "filled", "filled_qty": str(qty), "filled_avg_price": str(px)}
            self.orders[body["client_order_id"]] = o
            return o
        raise AssertionError(f"unexpected {method} {path}")


class FakeProviders:
    def __init__(self, fake, keys=("mainnet", "testnet"), switch=True, max_amount=5000.0):
        self.fake, self.k, self.switch, self.max = fake, set(keys), switch, max_amount

    def keys(self, ex, net):
        return ("k", "s") if ex == "alpaca" and net in self.k else None

    def client(self, ex, net):
        assert ex == "alpaca"
        return self.fake

    def mainnet_switch(self):
        return self.switch

    def max_amount(self):
        return self.max

    def redact(self, t):
        return str(t)


def make(tmp_path, fake=None, **kw):
    fake = fake or FakeAlpaca()
    prov = FakeProviders(fake, **{k: kw.pop(k) for k in ("keys", "switch", "max_amount") if k in kw})
    bot = IbsBot(tmp_path / "ibs.db", prov, env={}, utcnow=lambda: fake.now, **kw)
    return bot, fake


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def at(bot, fake, d, hm):
    fake.now = U(d, hm)
    bot.next_check = 0.0
    await bot.tick()


def test_off_by_default_logs_shadow_decisions_but_never_orders(tmp_path):
    bot, fake = make(tmp_path)
    fake.days["2026-10-21"]["QQQ"] = (101.0, 99.0, 99.1)            # IBS 0.05 at the close: entry signal
    assert bot.status == "OFF"
    run(at(bot, fake, "2026-10-21", "15:58:05"))
    d = bot.public_view()["decisions"][0]
    assert d["day"] == "2026-10-21" and abs(d["estimate"]["ibs"] - 0.05) < 1e-9 and d["estimate"]["shadow"]["IBS-QQQ"] == "enter"
    assert bot.shadow["books"]["IBS-QQQ"]["qty"] > 0 and fake.posted == []
    # the free plan's SIP rule: every SIP request ends at least 15 minutes before now
    assert all(e <= fake.now - timedelta(minutes=15) for e in fake.sip_requests)
    run(at(bot, fake, "2026-10-21", "16:21:00"))                     # the official record
    off = bot.public_view()["decisions"][0]["official"]
    assert off["entry_signal"] is True and off["slip"] is False and fake.posted == []


def test_no_action_outside_the_window_or_on_a_closed_day(tmp_path):
    bot, fake = make(tmp_path)
    run(at(bot, fake, "2026-10-21", "15:57:50"))                     # 2 min 10 s before the close: too early
    run(at(bot, fake, "2026-10-21", "15:59:40"))                     # 20 s before the close: too late to trade
    assert bot.public_view()["decisions"] == []
    del fake.days["2026-10-22"]                                      # a holiday
    bot.sessions = {}
    run(at(bot, fake, "2026-10-22", "15:58:30"))
    assert bot.public_view()["decisions"] == []


def test_early_close_moves_the_decision(tmp_path):
    bot, fake = make(tmp_path)
    fake.closes["2026-11-27"] = "13:00"                              # the day after Thanksgiving
    fake.now = U("2026-11-27", "10:00:00")
    run(at(bot, fake, "2026-11-27", "12:58:10"))
    assert [d["day"] for d in bot.public_view()["decisions"]] == ["2026-11-27"]
    sch = bot._schedule()
    assert sch["next_session"] == "2026-11-27" and sch["early_close"] is True
    assert sch["decision_at"] == U("2026-11-27", "12:58:00").isoformat()


@pytest.mark.parametrize("change, expect", [
    (dict(confirm="go live"), "type"),
    (dict(capital_qqq=6000, capital_tqqq=0), "capped"),
    (dict(max_loss=5000), "loss limit"),
    (dict(capital_qqq=5), "at least"),
    (dict(network="paper"), "choose"),
])
def test_arm_refusals(tmp_path, change, expect):
    bot, fake = make(tmp_path)
    args = dict(network="mainnet", capital_qqq=1000, capital_tqqq=1000, max_loss=500, confirm=CONFIRM) | change
    with pytest.raises(ValueError, match=expect):
        run(bot.arm(**args))
    assert bot.status == "OFF"


def test_arm_refuses_unsafe_accounts_and_servers(tmp_path):
    args = dict(network="mainnet", capital_qqq=1000, capital_tqqq=1000, max_loss=500, confirm=CONFIRM)
    for kw, prep, expect in (
        (dict(switch=False), None, "locked"),
        (dict(keys=("testnet",)), None, "no Alpaca live keys"),
        (dict(secure=False), None, "password"),
        (dict(other_user=lambda net: "the stock trend strategy"), None, "one strategy per account"),
        ({}, lambda f: f.positions.update(QQQ=1.0), "already holds"),
        ({}, lambda f: setattr(f, "cash", 500.0), "cash"),
    ):
        bot, fake = make(tmp_path / str(len(expect)) / expect.replace(" ", "_"), **kw)
        if prep:
            prep(fake)
        with pytest.raises(ValueError, match=expect):
            run(bot.arm(**args))
        assert bot.status == "OFF" and fake.posted == []


def test_armed_round_trip_only_its_own_symbols_and_quantities(tmp_path):
    bot, fake = make(tmp_path)
    fake.positions["AAPL"] = 3.0                                     # the operator's other holdings are left alone
    run(bot.arm("mainnet", 1000, 500, 600, CONFIRM))
    assert bot.status == "ARMED"
    fake.days["2026-10-21"]["QQQ"] = (101.0, 99.0, 99.1)
    run(at(bot, fake, "2026-10-21", "15:58:05"))
    assert [(o["symbol"], o["side"], o["notional"]) for o in fake.posted] == [("QQQ", "buy", "1000.00"), ("TQQQ", "buy", "500.00")]
    books = bot.cfg["books"]
    assert abs(books["IBS-QQQ"]["qty"] - 1000 / 99.1) < 1e-9 and books["IBS-TQQQ"]["qty"] == pytest.approx(10.0)
    # restart: a new instance on the same database never sends the same orders again
    bot2 = IbsBot(tmp_path / "ibs.db", bot.providers, env={}, utcnow=lambda: fake.now)
    run(at(bot2, fake, "2026-10-21", "15:58:40"))
    assert len(fake.posted) == 2
    # next day: close above the previous high -> sell exactly what was bought
    fake.days["2026-10-22"]["QQQ"] = (103.0, 100.0, 102.5)
    fake.days["2026-10-22"]["TQQQ"] = (56.0, 50.0, 55.0)
    run(at(bot2, fake, "2026-10-22", "15:58:05"))
    sells = fake.posted[2:]
    assert [(o["symbol"], o["side"]) for o in sells] == [("QQQ", "sell"), ("TQQQ", "sell")]
    assert float(sells[0]["qty"]) == pytest.approx(1000 / 99.1, rel=1e-9)
    assert fake.positions["AAPL"] == 3.0 and abs(fake.positions["QQQ"]) < 1e-6
    tr = {t["book"]: t for t in bot2.private_view()["live"]["trades"]}
    assert tr["IBS-QQQ"]["ret_pct"] == pytest.approx((102.5 / 99.1 - 1) * 100)
    assert tr["IBS-TQQQ"]["pnl"] == pytest.approx(10 * 5.0)


def test_mismatch_halts_and_nothing_is_traded(tmp_path):
    bot, fake = make(tmp_path)
    run(bot.arm("testnet", 1000, 0, 300, CONFIRM))
    fake.days["2026-10-21"]["QQQ"] = (101.0, 99.0, 99.1)
    run(at(bot, fake, "2026-10-21", "15:58:05"))
    fake.positions["QQQ"] = 2.0                                      # someone sold the bot's shares by hand
    run(at(bot, fake, "2026-10-22", "10:00:00"))
    assert bot.status == "HALTED"
    fake.days["2026-10-22"]["QQQ"] = (103.0, 100.0, 102.5)
    run(at(bot, fake, "2026-10-22", "15:58:05"))
    assert len(fake.posted) == 1                                     # no automatic sell while HALTED
    run(bot.disarm("operator"))
    assert bot.status == "OFF" and len(fake.posted) == 1


def test_loss_limit_and_switch_off_close_at_market(tmp_path):
    bot, fake = make(tmp_path)
    run(bot.arm("mainnet", 1000, 1000, 150, CONFIRM))
    fake.days["2026-10-21"]["QQQ"] = (101.0, 99.0, 99.1)
    run(at(bot, fake, "2026-10-21", "15:58:05"))
    fake.days["2026-10-22"]["QQQ"] = (99.0, 95.0, 96.0)
    fake.days["2026-10-22"]["TQQQ"] = (50.0, 42.0, 43.0)             # 2000 USD of books now worth about 1829
    run(at(bot, fake, "2026-10-22", "11:00:00"))                     # hit, sold at market and OFF in one tick
    assert bot.status == "OFF" and "loss limit" in bot.cfg["reason"]
    assert [o["side"] for o in fake.posted] == ["buy", "buy", "sell", "sell"]
    assert abs(fake.positions["QQQ"]) < 1e-6 and abs(fake.positions["TQQQ"]) < 1e-6


def test_switch_off_while_closed_sells_at_the_next_open(tmp_path):
    bot, fake = make(tmp_path)
    run(bot.arm("mainnet", 1000, 0, 500, CONFIRM))
    fake.days["2026-10-21"]["QQQ"] = (101.0, 99.0, 99.1)
    run(at(bot, fake, "2026-10-21", "15:58:05"))
    fake.now = U("2026-10-21", "18:00:00")
    run(bot.call(lambda: bot.disarm("kill switch")))
    assert bot.status == "CLOSING" and len(fake.posted) == 1
    run(at(bot, fake, "2026-10-22", "09:31:00"))
    assert bot.status == "OFF" and fake.posted[-1]["side"] == "sell"


def test_public_view_hides_money_and_routes_are_read_only(tmp_path):
    bot, fake = make(tmp_path)
    run(bot.arm("mainnet", 1234, 0, 321, CONFIRM))
    text = json.dumps(bot.public_view())
    assert "acct-1" not in text and "1234" not in text and "321" not in text
    from app.core import api_ibsbot
    assert {m for r in api_ibsbot.public_router.routes for m in r.methods} == {"GET"}
