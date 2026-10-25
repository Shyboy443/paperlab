"""pump.fun recorder: message parsing, storage, the price schedule, holders, gaps and the stall alert (no network)."""
import json

from app.pumpfun import sources
from app.pumpfun.service import HOUR, MIN, PumpfunRecorder

T0 = 1_792_000_000_000
MINT = "LfE2pfsSwtuPjJiJmqAJCTP66LeeDQ3KyySSPfxpump"
CREATOR = "ESzZTVpJEgd5f3nqDgQBqYCWeinWEraxGA4Xp9H6pD2M"


def create_msg(mint=MINT, creator=CREATOR, initial_buy=50_000_000):
    return json.dumps({"signature": "s1", "mint": mint, "traderPublicKey": creator, "txType": "create", "initialBuy": initial_buy,
                       "solAmount": 1.5, "vTokensInBondingCurve": 1.0e9, "vSolInBondingCurve": 30.0, "marketCapSol": 28.0,
                       "name": "Test", "symbol": "TST", "uri": "https://ipfs.io/x", "pool": "pump"})


def migrate_msg(mint=MINT):
    return json.dumps({"signature": "s2", "mint": mint, "txType": "migrate", "pool": "pump-amm"})


class FakeWeb:
    def __init__(self):
        self.calls = []
        self.dex = {}          # mint -> pair dict
        self.holders = {}

    def __call__(self, url):
        self.calls.append(url)
        if "dexscreener" in url:
            mints = url.rsplit("/", 1)[1].split(",")
            return [self.dex[m] for m in mints if m in self.dex]
        if "geckoterminal" in url:
            m = url.split("/tokens/")[1].split("/")[0]
            return {"data": {"attributes": {"holders": self.holders[m]}}}
        raise AssertionError(url)


def pair(mint, price=0.0004, liq_base=70_000_000.0, dex="pumpswap", liq_usd=50_000.0):
    return {"dexId": dex, "pairAddress": "P" + mint[:6], "baseToken": {"address": mint}, "quoteToken": {"symbol": "SOL"},
            "priceUsd": str(price), "priceNative": "0.0000035", "liquidity": {"usd": liq_usd, "base": liq_base, "quote": 233.5},
            "fdv": 400000, "volume": {"m5": 1000}, "txns": {"m5": {"buys": 10, "sells": 5}}}


def make(tmp_path, web=None):
    clock = {"now": T0 / 1000}
    web = web or FakeWeb()
    svc = PumpfunRecorder(tmp_path / "p.db", env={"PUMPFUN_RECORDER_ENABLED": "false"}, clock=lambda: clock["now"], getter=web)
    return svc, clock, web


def test_parse_keeps_tokens_and_graduations_and_drops_program_addresses():
    assert sources.parse(json.loads(create_msg()), T0)[0] == "token"
    assert sources.parse(json.loads(migrate_msg()), T0) == ("migration", {"mint": MINT, "t": T0, "pool": "pump-amm", "sig": "s2"})
    assert sources.parse(json.loads(migrate_msg("pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn")), T0) is None   # seen live
    assert sources.parse({"message": "Successfully subscribed to token creation events."}, T0) is None
    assert sources.parse({"txType": "buy", "mint": MINT}, T0) is None


def test_a_graduation_links_its_creation_and_the_creators_launch_count(tmp_path):
    svc, clock, _ = make(tmp_path)
    other = "So11111111111111111111111111111111111111112"
    svc.on_message(create_msg(other))                                       # an earlier launch by the same creator
    clock["now"] += 3600
    assert svc.on_message(create_msg()) == "token"
    clock["now"] += 600
    assert svc.on_message(migrate_msg()) == "migration"
    assert svc.on_message(migrate_msg()) == "migration"                    # duplicates are ignored
    with svc.store.lock:
        row = dict(svc.store.db.execute("SELECT * FROM migrations").fetchone())
    assert row["creator"] == CREATOR and row["creator_launches_24h"] == 2 and abs(row["initial_buy_pct"] - 5.0) < 1e-9
    assert svc.store.counts(svc.now())["graduations_total"] == 1


def test_price_schedule_holders_and_finish(tmp_path):
    svc, clock, web = make(tmp_path)
    svc.on_message(create_msg())
    svc.on_message(migrate_msg())
    web.dex[MINT] = pair(MINT)
    web.holders[MINT] = {"count": 2511, "distribution_percentage": {"top_10": "20.7"}}
    assert svc.poll_once()["priced"] == 1
    clock["now"] += 10
    assert svc.poll_once()["priced"] == 0                                  # 30 s cadence in the first 2 hours
    clock["now"] += 25
    assert svc.poll_once()["priced"] == 1
    clock["now"] += 2 * 60                                                  # holders 2 min after graduation
    out = svc.poll_once()
    assert out["holders"] == 1
    with svc.store.lock:
        m = dict(svc.store.db.execute("SELECT * FROM migrations").fetchone())
    assert m["holders"] == 2511 and abs(m["pool_pct"] - 7.0) < 1e-9 and abs(m["top10_ex_pool_pct"] - 13.7) < 1e-9
    assert m["pair"] == "P" + MINT[:6] and m["dex"] == "pumpswap"
    clock["now"] = (T0 + 3 * HOUR) / 1000                                   # 2-minute cadence between 2 h and 6 h
    assert svc.poll_once()["priced"] == 1
    clock["now"] += 60
    assert svc.poll_once()["priced"] == 0
    clock["now"] = (T0 + 49 * HOUR) / 1000                                  # past 48 h: tracking ends
    svc.poll_once()
    assert svc.store.counts(svc.now())["tracking_now"] == 0


def test_the_deepest_pumpswap_pair_wins_and_a_token_without_a_pair_stops_after_misses(tmp_path):
    rows = sources.dexscreener_prices([MINT], T0, lambda url: [pair(MINT, 1.0, dex="raydium", liq_usd=99e6), pair(MINT, 2.0),
                                                               pair(MINT, 3.0, liq_usd=60_000.0)])
    assert rows[MINT]["price_usd"] == 3.0                                   # PumpSwap first, then the deepest
    svc, clock, web = make(tmp_path)
    svc.on_message(migrate_msg())
    for _ in range(20):
        svc.poll_once()
        clock["now"] += 31
    assert svc.store.counts(svc.now())["tracking_now"] == 0


def test_stall_alert_once_and_recovery(tmp_path):
    sent = []

    class T:
        def ops_alert(self, text):
            sent.append(text)
    svc, clock, _ = make(tmp_path)
    svc.telegram = lambda: T()
    svc.on_message(create_msg())
    assert svc.check_stall() is None
    clock["now"] += 11 * 60
    assert "no stream message" in svc.check_stall() and svc.check_stall() is None
    svc.on_message(create_msg("So11111111111111111111111111111111111111112"))
    assert "recovered" in svc.check_stall() and len(sent) == 2
    assert svc.summary()["paper_only"] and svc.summary()["counts"]["tokens_total"] == 2


def test_gaps_are_recorded(tmp_path):
    svc, _, _ = make(tmp_path)
    svc.store.add_gap(T0, T0 + 2 * MIN, "stream reconnect")
    c = svc.store.counts(T0)
    assert c["gaps"] == 1 and c["gap_minutes"] == 2.0


def test_a_restart_is_recorded_as_a_gap_and_a_quick_one_is_not(tmp_path):
    clock = {"now": T0 / 1000}
    web = FakeWeb()
    mk = lambda: PumpfunRecorder(tmp_path / "p.db", env={"PUMPFUN_RECORDER_ENABLED": "false"}, clock=lambda: clock["now"], getter=web)
    a = mk()
    a.on_message(create_msg())
    clock["now"] += 30
    a.on_message(create_msg("So11111111111111111111111111111111111111112"))    # saved: the last message time
    a.stop()
    clock["now"] += 90                                                      # a deploy: 90 s without data
    b = mk()
    b.on_message(create_msg())
    gaps = b.store.db.execute("SELECT start_t, end_t, reason FROM gaps").fetchall()
    assert [tuple(g) for g in gaps] == [(T0 + 30_000, T0 + 120_000, "restart")]
    b.stop()
    clock["now"] += 2                                                       # a 2 s restart is not a gap
    c = mk()
    c.on_message(create_msg())
    assert c.store.counts(c.now())["gaps"] == 1
