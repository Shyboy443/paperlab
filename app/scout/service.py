"""The V10 SCOUT service (docs/V10_PROTOCOL.md): every 5 minutes it reads the news and Reddit, finds which coins and
stocks each item is about, scores tone, and records per-symbol attention in scout.db. RESEARCH ONLY: it places no
order and drives no bot. Signals become paper bots only after the pre-registered study passes them.

    news     Benzinga headlines via Alpaca's news API (the Alpaca paper keys); headline tone from Jev, batched
    trending CoinGecko's public trending list (no key, no login): the 15 coins crypto users search for most, every
             5 minutes -- which coins the crowd is suddenly paying attention to
    reddit   OFF by default (Reddit now requires approved API access): with SCOUT_REDDIT=true and an approved app key,
             the newest posts and comments of 12 subreddits; tone from a transparent word list
    record   per symbol and 5-minute bucket (by when the scout SAW an item, as a live bot would): posts, comments,
             news, mean tone -- plus every run (fetched, new, errors, Jev cost)

Environment: SCOUT_ENABLED=true runs it (default off: tests and local runs never touch the network); SCOUT_JEV=false
skips the Jev headline tone; SCOUT_REDDIT=true turns the Reddit source on (needs an approved key).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any, Mapping

from app.scout import jev_tone, text
from app.scout.sources import SUBREDDITS, RedditClient, fetch_news, fetch_trending
from app.scout.store import BUCKET_MS, STALE_ON_ARRIVAL_MS, ScoutStore

log = logging.getLogger("paperlab.scout")

CYCLE_S = 300
OFFSET_S = 20                   # just after each 5-minute boundary
KEEP_TEXT_DAYS = 30
MAX_TONE_PAIRS = 100            # Jev pairs per cycle (10 calls); the rest keep the word-list tone
HOUR = 3_600_000
DAY = 24 * HOUR


def _flag(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class ScoutService:
    def __init__(self, db_path: str, providers: Any = None, bus: Any = None, env: Mapping[str, str] | None = None,
                 clock: Any = time.time, jev_client: Any = None):
        e = os.environ if env is None else env
        self.enabled = _flag(e.get("SCOUT_ENABLED"), False)
        self.jev_enabled = _flag(e.get("SCOUT_JEV"), True)
        self.reddit_enabled = _flag(e.get("SCOUT_REDDIT"), False)
        self.store = ScoutStore(db_path)
        self.providers = providers
        self.bus = bus
        self.env = e
        self.clock = clock
        self._jev = jev_client
        self._reddit: RedditClient | None = None
        self._reddit_keys: tuple[str, str] | None = None
        self.state = "DISABLED" if not self.enabled else "STARTING"
        self.sources: dict[str, dict[str, Any]] = {"news": {"state": "WAITING"}, "trending": {"state": "WAITING"},
                                                   "reddit": {"state": "WAITING" if self.reddit_enabled else "OFF"}}
        self._trending_opener: Any = None
        self.last_cycle_ms: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cache: tuple[float, dict[str, Any]] | None = None

    # -- lifecycle ------------------------------------------------------------------------------------------
    def start(self) -> None:
        if not self.enabled:
            return
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()), name="scout", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.store.close()

    async def _main(self) -> None:
        while not self._stop.is_set():
            try:
                await self.cycle()
                self.state = "RUNNING"
            except Exception as exc:
                self.state = "ERROR"
                log.exception("scout cycle failed: %s", str(exc)[:160])
            now = self.clock()
            nxt = (now // CYCLE_S + 1) * CYCLE_S + OFFSET_S
            while not self._stop.is_set() and self.clock() < nxt:
                await asyncio.sleep(min(2.0, max(0.1, nxt - self.clock())))

    # -- one cycle ------------------------------------------------------------------------------------------
    def _keys(self, exchange: str, network: str) -> tuple[str, str] | None:
        return self.providers.keys(exchange, network) if self.providers is not None else None

    def _jev_client(self) -> Any:
        if self._jev is not None:
            return self._jev
        api_key = self.env.get("OPENROUTER_API_KEY")
        if not (self.jev_enabled and api_key):
            return None
        import dataclasses
        from app.ai.jev.client import JevClient
        from app.ai.jev.models import JevConfig
        cfg = JevConfig.from_env()
        if not cfg.enabled:
            return None
        self._jev = JevClient(dataclasses.replace(cfg, timeout_ms=30_000, max_retries=1), api_key)
        return self._jev

    async def _news(self, now: int) -> list[dict[str, Any]]:
        keys = self._keys("alpaca", "testnet")
        if keys is None:
            self.sources["news"] = {"state": "NEEDS_KEYS", "detail": "enter the Alpaca paper keys (System -> Providers & Live)"}
            return []
        from app.exchange.alpaca_client import AlpacaClient
        since = max(self.store.last_created("news") or 0, now - DAY) + 1
        try:
            items = await fetch_news(AlpacaClient("testnet", *keys), list(text.UNIVERSE), since, now)
        except Exception as exc:
            self.sources["news"] = {"state": "ERROR", "detail": self._redact(exc)}
            self.store.add_run(now, "news", False, 0, 0, self.sources["news"]["detail"])
            return []
        for it in items:
            it["symbols"] = sorted(set(it["symbols"]) | set(text.symbols_in(it["headline"])))
            it["tone"] = text.tone(it["headline"])
        new = self.store.add_items([i for i in items if i["symbols"]], now)
        cost = await self._score_news(new, now)
        self.store.add_run(now, "news", True, len(items), len(new), "", cost)
        self.sources["news"] = {"state": "OK", "last_ok_ms": now, "fetched": len(items), "new": len(new)}
        return new

    async def _score_news(self, new: list[dict[str, Any]], now: int) -> float:
        client = self._jev_client()
        if client is None or not new:
            return 0.0
        pairs = [{"item_id": i["id"], "symbol": s, "headline": i["headline"], "summary": i["text"][len(i["headline"]):]}
                 for i in new for s in i["symbols"][:3]][:MAX_TONE_PAIRS]
        cost, rows = 0.0, []
        for k in range(0, len(pairs), jev_tone.BATCH):
            batch = pairs[k:k + jev_tone.BATCH]
            tones, c = await asyncio.to_thread(jev_tone.score, client, batch)
            cost += c
            rows += [(p["item_id"], p["symbol"], t) for p, t in zip(batch, tones) if t]
        if rows:
            self.store.save_tones(rows, now)
        return cost

    async def _trending(self, now: int) -> list[dict[str, Any]]:
        try:
            rows = await fetch_trending(self._trending_opener)
        except Exception as exc:
            self.sources["trending"] = {"state": "ERROR", "detail": self._redact(exc)}
            self.store.add_run(now, "trending", False, 0, 0, self.sources["trending"]["detail"])
            return []
        self.store.add_trending(now, rows)
        self.store.add_run(now, "trending", bool(rows), len(rows), 0)
        self.sources["trending"] = {"state": "OK" if rows else "ERROR", "last_ok_ms": now, "coins": len(rows)}
        return rows

    async def _reddit_items(self, now: int) -> list[dict[str, Any]]:
        if not self.reddit_enabled:
            self.sources["reddit"] = {"state": "OFF", "detail": "off: Reddit requires approved API access"}
            return []
        keys = self._keys("reddit", "data")
        if keys is None:
            self.sources["reddit"] = {"state": "NEEDS_KEYS", "detail": "enter a free Reddit app key (System -> Providers & Live)"}
            return []
        if self._reddit is None or self._reddit_keys != keys:
            self._reddit, self._reddit_keys = RedditClient(*keys), keys
        read, found, errors = 0, [], []
        for sub in SUBREDDITS:
            for kind in ("new", "comments"):
                try:
                    items = await self._reddit.listing(sub, kind)
                except Exception as exc:
                    errors.append(f"r/{sub}/{kind}: {self._redact(exc)}")
                    continue
                read += len(items)
                for it in items:
                    it["symbols"] = text.symbols_in(it["text"])
                    if it["symbols"]:
                        it["tone"] = text.tone(it["text"])
                        found.append(it)
        new = self.store.add_items(found, now)
        ok = read > 0
        self.store.add_run(now, "reddit", ok, read, len(new), "; ".join(errors)[:200])
        self.sources["reddit"] = {"state": "OK" if ok else "ERROR", "last_ok_ms": now if ok else self.sources["reddit"].get("last_ok_ms"),
                                  "read": read, "new": len(new), "errors": len(errors),
                                  "detail": errors[0] if errors and not ok else None,
                                  "ratelimit_remaining": self._reddit.ratelimit_remaining}
        return new

    async def cycle(self, now_ms: int | None = None) -> dict[str, Any]:
        now = int(now_ms if now_ms is not None else self.clock() * 1000)
        news = await self._news(now)
        trend = await self._trending(now)
        posts = await self._reddit_items(now)
        tones = self.store.tones([i["id"] for i in news])
        per: dict[str, dict[str, Any]] = {}
        for it in news + posts:
            if now - int(it["created_ms"]) > STALE_ON_ARRIVAL_MS:
                continue                          # a first-run backfill: stored, never counted as live attention
            for s in it["symbols"]:
                f = per.setdefault(s, {"posts": 0, "comments": 0, "news": 0, "tr": [], "tn": []})
                if it["source"] == "news":
                    f["news"] += 1
                    t = tones.get((it["id"], s), it.get("tone"))
                    if t is not None:
                        f["tn"].append(t)
                else:
                    f["posts" if it["kind"] == "post" else "comments"] += 1
                    if it.get("tone") is not None:
                        f["tr"].append(it["tone"])
        feats = {s: {"posts": f["posts"], "comments": f["comments"], "news": f["news"],
                     "tone_reddit": round(sum(f["tr"]) / len(f["tr"]), 4) if f["tr"] else None, "n_tone_reddit": len(f["tr"]),
                     "tone_news": round(sum(f["tn"]) / len(f["tn"]), 4) if f["tn"] else None, "n_tone_news": len(f["tn"])}
                 for s, f in per.items()}
        self.store.add_features(now // BUCKET_MS * BUCKET_MS, feats)
        self.store.prune(now - KEEP_TEXT_DAYS * DAY)
        self.last_cycle_ms = now
        self._cache = None
        if self.bus is not None:
            try:
                self.bus.publish("scout", self.summary(now), public=True, replay=False)
            except Exception:
                pass
        return {"news": len(news), "reddit": len(posts), "trending": [t["symbol"] for t in trend], "symbols": sorted(feats)}

    def _redact(self, exc: Any) -> str:
        msg = f"{type(exc).__name__}: {exc}"
        if self.providers is not None:
            msg = self.providers.redact(msg)
        return msg[:160]

    # -- reads ------------------------------------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return {"state": self.state, "enabled": self.enabled, "last_cycle_ms": self.last_cycle_ms, "reddit": self.reddit_enabled,
                "next_cycle_ms": ((int(self.clock()) // CYCLE_S + 1) * CYCLE_S + OFFSET_S) * 1000 if self.enabled else None,
                "sources": self.sources, "jev_tone": self.jev_enabled}

    def coingecko(self, now: int) -> dict[str, Any]:
        """The latest CoinGecko trending snapshot, each coin's current streak start, and hours on the list in 24 h."""
        rows = self.store.trending(now - DAY)
        snaps: dict[int, list[dict[str, Any]]] = {}
        for r in rows:
            snaps.setdefault(r["ts"], []).append(r)
        times = sorted(snaps)
        latest = snaps[times[-1]] if times else []
        on = {t: {r["symbol"] for r in snaps[t]} for t in times}
        hours: dict[str, float] = {}
        for i, t in enumerate(times):
            dt = ((times[i + 1] if i + 1 < len(times) else now) - t) / HOUR
            for s in on[t]:
                hours[s] = hours.get(s, 0.0) + min(dt, 0.25)
        cur = []
        for r in sorted(latest, key=lambda x: x["rank"]):
            since = times[-1]
            for t in reversed(times):
                if r["symbol"] not in on[t]:
                    break
                since = t
            cur.append({"rank": r["rank"], "symbol": r["symbol"], "name": r["name"], "mcap_rank": r["mcap_rank"],
                        "since_ms": since, "ours": r["symbol"] in text.UNIVERSE})
        return {"now": cur, "at_ms": times[-1] if times else None,
                "hours_24h": {k: round(v, 2) for k, v in hours.items()}, "source": "CoinGecko (public trending list)"}

    def summary(self, now_ms: int | None = None) -> dict[str, Any]:
        """Public-safe aggregates only (no item text): attention now vs its own normal, tone, 24 h series."""
        now = int(now_ms if now_ms is not None else self.clock() * 1000)
        if self._cache and self.clock() - self._cache[0] < 30 and now_ms is None:
            return self._cache[1]
        rows = self.store.features(now - 7 * DAY)
        by: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by.setdefault(r["symbol"], []).append(r)
        hour0 = now // HOUR * HOUR
        out = []
        for sym, (market, _n, _t) in text.UNIVERSE.items():
            rs = by.get(sym, [])
            att = lambda r: r["posts"] + r["comments"]  # noqa: E731
            m1 = sum(att(r) for r in rs if r["bucket_ms"] >= now - HOUR)
            n1 = sum(r["news"] for r in rs if r["bucket_ms"] >= now - HOUR)
            prior = [r for r in rs if r["bucket_ms"] < now - HOUR]
            span_h = max(1.0, (now - HOUR - min((r["bucket_ms"] for r in prior), default=now - HOUR)) / HOUR)
            base = sum(att(r) for r in prior) / span_h if prior else None
            tr = [(r["tone_reddit"], att(r)) for r in rs if r["bucket_ms"] >= now - HOUR and r["tone_reddit"] is not None]
            tn = [(r["tone_news"], r["news"]) for r in rs if r["bucket_ms"] >= now - DAY and r["tone_news"] is not None]
            wmean = lambda xs: round(sum(v * w for v, w in xs) / sum(w for _, w in xs), 3) if xs and sum(w for _, w in xs) else None  # noqa: E731
            series = []
            for k in range(24):
                a, b = hour0 - (23 - k) * HOUR, hour0 - (22 - k) * HOUR
                series.append([a, sum(att(r) for r in rs if a <= r["bucket_ms"] < b), sum(r["news"] for r in rs if a <= r["bucket_ms"] < b)])
            out.append({"symbol": sym, "market": market, "mentions_1h": m1, "news_1h": n1,
                        "mentions_24h": sum(att(r) for r in rs if r["bucket_ms"] >= now - DAY),
                        "news_24h": sum(r["news"] for r in rs if r["bucket_ms"] >= now - DAY),
                        "normal_per_h": round(base, 2) if base is not None else None,
                        "spike": round(m1 / base, 2) if base else None,
                        "tone_reddit_1h": wmean(tr), "tone_news_24h": wmean(tn), "series": series})
        trending = sorted([r for r in out if (r["spike"] or 0) >= 1.5 and r["mentions_1h"] >= 5],
                          key=lambda r: -(r["spike"] or 0))[:6]
        cg = self.coingecko(now)
        ranks = {c["symbol"]: c["rank"] for c in cg["now"]}
        for r in out:
            r["cg_rank"] = ranks.get(r["symbol"])
            r["cg_hours_24h"] = cg["hours_24h"].get(r["symbol"], 0.0)
        runs = self.store.runs(now - DAY)
        payload = {"ok": True, "health": self.health(), "symbols": out, "trending": [r["symbol"] for r in trending],
                   "coingecko": cg,
                   "last_24h": {"items_read": sum(r["fetched"] for r in runs), "new_items": sum(r["new"] for r in runs),
                                "cycles": len({r["ts"] for r in runs}), "errors": sum(1 for r in runs if not r["ok"]),
                                "jev_cost_usd": round(sum(r["cost"] or 0 for r in runs), 4)},
                   "note": "research only: attention and tone per symbol; no bot trades on these until a study passes them"}
        if now_ms is None:
            self._cache = (self.clock(), payload)
        return payload
