# V10 SCOUT: news and Reddit attention research (pre-registered)

Requested on 2026-09-27: "each 5 mins reading the news, Reddit 500 posts … build a separate bot to research".

**The approach:** research first. The scout collects and measures news and Reddit activity. **No bot trades on it**
until a study below passes a signal. After that, only paper bots, with controls and the usual elimination and
qualification.

## Update 2026-09-28: CoinGecko trending in, Reddit off

**Reddit is off.** Reddit now requires explicitly approved API access for any data use, under its Responsible
Builder Policy. The operator chose not to apply, so the Reddit source is off by default (`SCOUT_REDDIT=false`). The
code stays, for an approved key later.

**CoinGecko trending replaces it** as the free, no-login crowd-attention source. Every 5 minutes the scout records
CoinGecko's public trending list: the 15 coins users search for most, credited to CoinGecko on the dashboard.

**Trending study** (`scripts/v10_trending_study.py`, pre-registered now, runs once 14 days are collected):
- **Event:** a coin ENTERS the list, meaning it is on it in a snapshot and absent from every snapshot of the previous
  6 hours.
- **Coins counted:** every coin with a Bybit USDT perpetual, not only the bots' coins.
- **Trade:** LONG at the first 5m open at least 60 s later, measured over 60 minutes, against that coin's own baseline.
- **Pass rules:**
  - volatility ratio ≥ 1.3 in both halves, with ≥ 30 events per half;
  - "a rise": the mean return net of 13 bps is > 0 in both halves, with t ≥ 2.5 overall.
- **Output:** `docs/V10_TRENDING_STUDY.json`.

**News study result (2026-09-27): FAIL** (`docs/V10_NEWS_STUDY.json`).
- **Crypto:** the volatility ratio was 0.89 and 0.75. Tone direction was −2 bps before costs.
- **Stocks:** the volatility ratio was 1.09 and 1.23. Tone direction was −0.9 bps before costs.

## Sources

- **Benzinga news via Alpaca's news API.** This uses the Alpaca paper keys already stored and covers stocks and
  crypto. History goes back to 2015, so the news study can run immediately.
- **Reddit's official API.** Application-only OAuth with the operator's free "script" app key, entered in System →
  Providers & Live as `REDDIT_CLIENT_ID`/`_SECRET`. Each cycle reads the 100 newest **posts and comments** of 12
  subreddits: CryptoCurrency, CryptoMarkets, Bitcoin, ethtrader, solana, XRP, dogecoin, wallstreetbets, stocks,
  StockMarket, options, investing. That is 24 requests per 5 minutes, well inside Reddit's free 100 per minute.
- **Apify is not used.** At about 144,000 items a day it would cost far more than the free official API.

## What is measured (`app/scout/`)

**Symbols** (17): BTC, ETH, SOL, XRP, DOGE, ARB, ENA and the 10 V9 stocks. An item counts as a mention when it has a
cashtag, a ticker in capitals, or a full name. Ambiguous words need capitals or a cashtag: "sol", "meta" and "arb" in
lower case are not counted.

**Tone:**
- **News headlines:** Jev (JEV_SCOUT_TONE_V1), 10 headlines per call, scored as P(bullish) − P(bearish).
- **Reddit:** a transparent finance word list with simple negation.

**Record:** per symbol and 5-minute bucket, the scout stores posts, comments, news and mean tone. Buckets use **when
the scout saw the item**, which is what a live bot would have known. Items first seen more than 6 hours after they
were written are stored but never counted. Post and headline text is kept for 30 days; the numbers are kept
permanently.

**Dashboards:**
- **Public:** the Scout tab shows numbers only: mentions in the last hour against that symbol's normal hour (the
  "spike"), news counts, tone and a 24-hour sparkline.
- **Private:** the same numbers, plus the posts and headlines behind each symbol.

## Studies and pass rules (fixed before any result was seen)

Both studies use the same trade. It enters at the open of the first 5-minute bar at least 60 s after the signal (the
bots' own delay) and is measured over **60 minutes**. For stocks, both ends must fall in the same regular session.

The baseline is every third 5-minute bar start in the same window, measured over the same 60 minutes. The window is
split into two halves by time, and **a verdict passes only if it holds in both halves.**

| verdict | passes when |
|---|---|
| attention predicts VOLATILITY | mean \|60m return\| after events ÷ baseline ≥ **1.3**, with ≥ N events in each half |
| tone predicts DIRECTION | for events with \|tone\| ≥ 0.3: mean of sign(tone) × 60m return **minus the round trip** (crypto 13 bps, stocks 2 bps) is positive in both halves, **t ≥ 2.5** overall, with ≥ M toned events per half |

**News study** (`scripts/v10_news_study.py --jev`):
- **Window:** the last 180 days.
- **Events:** every (headline, symbol) pair.
- **Thresholds:** N = 200 events and M = 100 toned events per half.
- **Output:** `docs/V10_NEWS_STUDY.json`.

**Reddit study** (`scripts/v10_social_study.py`):
- **When:** once `scout.db` holds at least 14 days.
- **Events:** the last hour's mentions are at least 3× the symbol's normal hour (trailing 7 days, with at least 3 days
  of history) and at least 10, with one event per symbol per 2 hours.
- **Thresholds:** N = 50 events and M = 40 toned events per half.
- **Output:** `docs/V10_SOCIAL_STUDY.json`.

**What happens after a pass:**
- **Volatility pass:** attention is useful as a filter for the existing scalpers ("trade when it moves"). It would be
  tested as new twins against the unchanged bots.
- **Direction pass:** a V10 paper bot trades the tone, with a control.
- **Nothing passes:** the scout stays a dashboard, and that is reported plainly.

## Costs and switches

- **Costs:** news and Reddit are free. Jev headline tone runs up to 10 calls per cycle; a few hundred headlines a day
  cost cents.
- **`SCOUT_ENABLED=true`** runs the scout (it is off by default: tests and local runs never touch the network).
- **`SCOUT_JEV=false`** turns Jev tone off.
