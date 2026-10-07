"""V10 SCOUT: symbol matching, tone, the Reddit and news sources, Jev headline tone, the store, one full cycle, the
public summary and the routes. No network, no keys."""
from __future__ import annotations

import asyncio
import io
import json
import urllib.error
import urllib.request

import pytest

from app.scout import jev_tone, text
from app.scout.service import ScoutService
from app.scout.sources import RedditClient, SourceError, fetch_news
from app.scout.store import ScoutStore

NOW = 1_790_500_000_000 // 300_000 * 300_000
H = 3_600_000
ID, SECRET = "reddit-client-id", "reddit-secret-xyz"


def run(c):
    return asyncio.run(c)


class TestText:
    @pytest.mark.parametrize("t,want", [("$SOL to the moon", ["SOL"]), ("El sol brilla hoy", []), ("Solana ETF approved", ["SOL"]),
                                        ("META earnings beat", ["META"]), ("that meta joke", []), ("arb bots everywhere", []),
                                        ("Bitcoin and ETH pump", ["BTC", "ETH"]), ("NVDA calls", ["NVDA"]), ("I am a spy", [])])
    def test_symbols(self, t, want):
        assert text.symbols_in(t) == want

    def test_tone_with_negation(self):
        assert text.tone("massive breakout, bullish") == 1.0
        assert text.tone("not bullish, looks like a dump") == -1.0
        assert text.tone("the weather is nice") is None


class FakeResp(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(json.dumps(body).encode())
        self.headers = headers or {}


class TestReddit:
    def test_token_then_listing(self):
        seen = []

        def opener(req, timeout):
            seen.append((req.full_url, req.get_header("Authorization")))
            if "access_token" in req.full_url:
                return FakeResp({"access_token": "tok", "expires_in": 3600})
            return FakeResp({"data": {"children": [{"data": {"name": "t3_a", "created_utc": NOW / 1000, "title": "SOL breakout",
                                                              "selftext": "", "permalink": "/r/solana/x", "score": 5}}]}},
                            {"x-ratelimit-remaining": "95"})
        c = RedditClient(ID, SECRET, opener=opener, clock=lambda: NOW / 1000)
        items = run(c.listing("solana"))
        assert items[0]["id"] == "reddit:t3_a" and items[0]["kind"] == "post" and items[0]["text"] == "SOL breakout"
        assert seen[0][1].startswith("Basic ") and seen[1][1] == "bearer tok" and c.ratelimit_remaining == 95.0
        run(c.listing("solana", "comments"))
        assert sum(1 for u, _ in seen if "access_token" in u) == 1                # the token is reused

    def test_refused_keys_and_foreign_hosts(self):
        def denied(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        c = RedditClient(ID, SECRET, opener=denied)
        with pytest.raises(SourceError) as e:
            run(c.fetch_balance())
        assert "401" in str(e.value) and SECRET not in str(e.value)
        with pytest.raises(SourceError):
            c._call(urllib.request.Request("https://evil.example/x"))


class FakeAlpaca:
    def __init__(self, pages):
        self.pages = list(pages)

    async def request(self, method, path, body=None, data=False):
        return self.pages.pop(0) if self.pages else {"news": []}


def _news(i, sym, t, headline):
    from app.live.alpaca_market import iso
    return {"id": i, "headline": headline, "summary": "", "created_at": iso(t), "symbols": [sym], "source": "benzinga", "url": "u"}


class TestNews:
    def test_pages_and_symbol_mapping(self):
        a = FakeAlpaca([{"news": [_news(1, "BTCUSD", NOW - H, "Bitcoin ETF inflows")], "next_page_token": "p2"},
                        {"news": [_news(2, "NVDA", NOW - 60_000, "Nvidia beats")]}])
        items = run(fetch_news(a, list(text.UNIVERSE), NOW - 2 * H, NOW))
        assert [(i["id"], i["symbols"]) for i in items] == [("news:1", ["BTC"]), ("news:2", ["NVDA"])]


class FakeJev:
    def __init__(self, ok=True):
        self.ok = ok

    def decide(self, state, questions, parse=False):
        from types import SimpleNamespace
        if not self.ok:
            return SimpleNamespace(ok=False, extra={})
        answers = {q: {"type": "choice", "choice": "BULLISH", "probabilities": {"BULLISH": 0.7, "NEUTRAL": 0.2, "BEARISH": 0.1}}
                   for q in questions}
        return SimpleNamespace(ok=True, extra={"raw": {"answers": answers, "usage": {"cost": 0.0001}}})


class TestJevTone:
    def test_batch_scores_and_failures(self):
        pairs = [{"symbol": "BTC", "headline": "ETF approved"}, {"symbol": "NVDA", "headline": "beats"}]
        tones, cost = jev_tone.score(FakeJev(), pairs)
        assert [t["tone"] for t in tones] == [0.6, 0.6] and cost == 0.0001
        assert set(jev_tone.questions_for(pairs)) == {"h0", "h1"}
        assert jev_tone.score(FakeJev(ok=False), pairs) == ([None, None], 0.0)


class FakeProviders:
    def __init__(self, keys):
        self._k = keys

    def keys(self, ex, net):
        return self._k.get((ex, net))

    def redact(self, s):
        return str(s).replace(SECRET, "***")


class TestService:
    def make(self, tmp_path, monkeypatch, news=(), posts=(), keys=None, trend=None, reddit=True):
        import app.scout.service as svc_mod
        snaps = list(trend or [])

        async def fake_news(client, symbols, start, end):
            return [dict(n) for n in news]

        async def fake_trending(opener=None):
            return [dict(t) for t in (snaps.pop(0) if snaps else [])]
        monkeypatch.setattr(svc_mod, "fetch_trending", fake_trending)

        async def fake_listing(self, sub, kind="new", limit=100):
            return [dict(p) for p in posts] if (sub, kind) == ("solana", "new") else []
        monkeypatch.setattr(svc_mod, "fetch_news", fake_news)
        monkeypatch.setattr(RedditClient, "listing", fake_listing)
        keys = keys if keys is not None else {("alpaca", "testnet"): ("k", "s"), ("reddit", "data"): (ID, SECRET)}
        env = {"SCOUT_ENABLED": "true", "SCOUT_REDDIT": "true" if reddit else "false"}
        return ScoutService(str(tmp_path / "scout.db"), providers=FakeProviders(keys), env=env,
                            clock=lambda: NOW / 1000, jev_client=FakeJev())

    def item(self, i, t, txt, src="reddit", kind="post", syms=None):
        return {"id": f"{src}:{i}", "source": src, "channel": "r/solana" if src == "reddit" else "benzinga", "kind": kind,
                "created_ms": t, "text": txt, "url": "", "score": 1, "comments": 0, "symbols": syms or [], "headline": txt}

    def test_a_cycle_records_attention_by_when_it_was_seen(self, tmp_path, monkeypatch):
        posts = [self.item(i, NOW - 60_000, "SOL breakout, bullish") for i in range(3)] + \
                [self.item(99, NOW - 10 * H, "old SOL post")]                          # a backfill: stored, not counted
        news = [self.item(1, NOW - 120_000, "Solana ETF approved", src="news", kind="headline", syms=["SOL"])]
        s = self.make(tmp_path, monkeypatch, news=news, posts=posts)
        out = run(s.cycle(NOW))
        f = s.store.features(0)
        assert out["symbols"] == ["SOL"] and len(f) == 1
        assert (f[0]["posts"], f[0]["news"], f[0]["tone_reddit"], f[0]["tone_news"]) == (3, 1, 1.0, 0.6)
        assert s.sources["news"]["state"] == "OK" and s.sources["reddit"]["state"] == "OK"
        assert len(s.store.items("SOL")) == 5                                          # 4 posts + 1 headline stored
        run(s.cycle(NOW + 300_000))                                                    # the same items again: nothing new
        assert sum(r["posts"] for r in s.store.features(0)) == 3

    def test_spike_against_the_symbols_normal_hour(self, tmp_path, monkeypatch):
        s = self.make(tmp_path, monkeypatch)
        for k in range(48):                                                            # 4 quiet hours: 1 mention / 5 min
            s.store.add_features(NOW - 5 * H + k * 300_000, {"SOL": {"posts": 1, "comments": 0, "news": 0}})
        for k in range(12):                                                            # the last hour: 4 per 5 min
            s.store.add_features(NOW - H + (k + 1) * 300_000 - 1, {"SOL": {"posts": 4, "comments": 0, "news": 0}})
        sol = next(r for r in s.summary(NOW)["symbols"] if r["symbol"] == "SOL")
        assert sol["mentions_1h"] == 48 and sol["normal_per_h"] == 12.0 and sol["spike"] == 4.0
        assert s.summary(NOW)["trending"] == ["SOL"]
        assert "text" not in json.dumps(s.summary(NOW)["symbols"])                     # public: numbers only

    def test_without_keys_it_says_what_is_missing(self, tmp_path, monkeypatch):
        s = self.make(tmp_path, monkeypatch, keys={})
        run(s.cycle(NOW))
        assert s.sources["news"]["state"] == "NEEDS_KEYS" and s.sources["reddit"]["state"] == "NEEDS_KEYS"

    def test_off_by_default_and_reddit_off_unless_asked(self, tmp_path, monkeypatch):
        assert ScoutService(str(tmp_path / "x.db"), env={}).enabled is False
        s = self.make(tmp_path, monkeypatch, reddit=False)
        run(s.cycle(NOW))
        assert s.sources["reddit"]["state"] == "OFF" and s.sources["news"]["state"] == "OK"

    def test_coingecko_trending_streaks_and_hours(self, tmp_path, monkeypatch):
        def snap(*syms):
            return [{"rank": i + 1, "symbol": x, "name": x.title(), "cg_id": x.lower(), "mcap_rank": 50} for i, x in enumerate(syms)]
        s = self.make(tmp_path, monkeypatch, trend=[snap("ARB", "NEAR"), snap("NEAR", "ARB"), snap("SUI", "ARB")])
        for k in range(3):
            run(s.cycle(NOW + k * 300_000))
        cg = s.coingecko(NOW + 900_000)
        assert [(c["rank"], c["symbol"], c["ours"]) for c in cg["now"]] == [(1, "SUI", False), (2, "ARB", True)]
        assert next(c for c in cg["now"] if c["symbol"] == "ARB")["since_ms"] == NOW          # on the list all 3 reads
        assert next(c for c in cg["now"] if c["symbol"] == "SUI")["since_ms"] == NOW + 600_000
        assert cg["hours_24h"]["ARB"] == 0.25 and s.sources["trending"] == {"state": "OK", "last_ok_ms": NOW + 600_000, "coins": 2}
        arb = next(r for r in s.summary(NOW + 900_000)["symbols"] if r["symbol"] == "ARB")
        assert arb["cg_rank"] == 2 and arb["cg_hours_24h"] == 0.25

    def test_trending_parser(self):
        from app.scout.sources import fetch_trending
        body = {"coins": [{"item": {"score": 0, "symbol": "qnt", "name": "Quant", "id": "quant-network", "market_cap_rank": 43}},
                          {"item": {"score": 1, "symbol": "arb", "name": "Arbitrum", "id": "arbitrum", "market_cap_rank": 62}}]}
        rows = run(fetch_trending(lambda req, timeout: FakeResp(body)))
        assert [(r["rank"], r["symbol"], r["cg_id"]) for r in rows] == [(1, "QNT", "quant-network"), (2, "ARB", "arbitrum")]


class TestProviderAndRoutes:
    def test_reddit_is_a_data_provider_that_never_trades(self, tmp_path):
        from app.live.key_vault import KeyVault
        from app.live.providers import PROVIDERS, Providers

        class Ok:
            async def fetch_balance(self):
                return {"wallet": 0.0, "available": 0.0, "currency": "", "access": "read-only token issued"}

            async def close(self):
                pass
        v = KeyVault(tmp_path / "k.vault", "p")
        pr = Providers({}, client_factory=lambda ex, net: Ok(), vault=v)
        out = run(pr.save_keys("reddit", "data", ID, SECRET))
        assert out["ok"] and out["balance"]["access"] == "read-only token issued" and pr.keys("reddit", "data") == (ID, SECRET)
        assert PROVIDERS[("reddit", "data")]["market"] == "data"

    def test_routes(self, tmp_path):
        from fastapi.testclient import TestClient

        from app.core import api_scout
        from app.main import create_app
        from tests.conftest import settings_factory
        assert {r.path for r in api_scout.public_router.routes} == {"/api/public/scout"}
        assert all(m == {"GET"} for r in api_scout.public_router.routes for m in [r.methods])
        app = create_app(settings_factory(data_dir=str(tmp_path / "d"), DASHBOARD_PASSWORD="pw-123456"))
        with TestClient(app) as c:
            r = c.get("/api/public/scout")
            assert r.status_code == 200 and r.json()["health"]["enabled"] is False
            assert c.get("/api/scout/items").status_code == 401
