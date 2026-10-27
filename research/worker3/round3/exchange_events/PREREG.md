# exchange_events: pre-registration (written 2026-10-26 before any TEST run)

## Idea
Exchange events other than new Binance listings move small alts, and the move keeps going after the news is public:
- negative Binance events: spot delisting announcement, Monitoring Tag addition;
- Upbit (Korea) listing announcements: "sell the news" after the pre-announcement and first-minute pump;
- Upbit "warning designation" (거래 유의 종목 지정).

Direction from DEV for all five types: **short** the Binance USDⓈ-M perp.
Why it could persist:
- forced or slow selling: spot liquidity and access disappear, and Monitoring-tag coins need a quiz;
- Korean retail pump-and-fade around Upbit listings.

## Sources
- Monitoring Tag drops reported in the press, not studied formally:
  - The Block, 2024-01-04: https://www.theblock.co/news/markets/2024-01-04-binance-monitoring-tag-monero-zcash-270282
  - iTiger news on the May 2025 batch: https://www.itiger.com/news/2541807819
- Delisting drops:
  - Forklog on Monero (−15% within 3 h): https://forklog.com/en/monero-price-drops-15-following-binance-delisting-announcement/amp
  - Tangem 2026: https://tangem.com/en/news/markets/18619-binance-delists-tokens-triggers-sharp-price-drops/
  - Santiment on HEGIC: https://insights.santiment.net/read/see-how-hegic-s-delisting-from-binance-has-major-impact-on-its-key-metrics-6941
- Listing effects and their reversal:
  - Coin Metrics, "the Coinbase effect is muted" (−1%..+14% from −10 to +10 days): https://cointelegraph.com/news/coinmetrics-finds-the-coinbase-effect-is-actually-pretty-lame
  - Decrypt, "Coinbase effect is a temporary high": https://decrypt.co/33311/coinbase-effect-crypto-temporary-high
  - Erasmus thesis on the Coinbase effect: https://thesis.eur.nl/pub/59675/Final-Draft.pdf
  - Coinbase listings insider run-ups, 15% CAR before the announcement: https://coingeek.com/coinbase-study-reveals-insider-trading-on-25-of-new-token-listings/
  - Blockchain Research Lab listing study (pre-event informed trading): https://www.blockchainresearchlab.org/wp-content/uploads/2020/02/BRL-Working-Paper-5.pdf
- No peer-reviewed study of Monitoring-tag or Upbit post-announcement drift was found. The evidence for this rule is the
  DEV event study here (study_dev.txt).

## Data (public, no keys)
Announcement lists and timestamps are cached in `data/w3r3_exchange_events/`:
- Binance CMS API catalogs 161, 48 and 49 (`releaseDate` in ms);
- Upbit notice API, category "trade" (`first_listed_at`).

The parsed events are in `events.json` (events.py). Coverage is good for Binance from 2021. Upbit KRW-listing coverage
is thinner in 2021–2023, partly because Upbit listed little in that period.

Prices and funding:
- agent 07's survivorship-free 1h cache of all 901 USDT perps, including delisted ones;
- compacted into `panel.npz` (panel.py).

Coinbase listings were NOT used: there is no keyless, timestamped public list.

## Rule (the ONE selected config)
1. **Event types:**
   - BN_SPOT_DELIST ("Binance Will Delist X, Y on date", including vote-to-delist results);
   - BN_MON_ADD (Monitoring Tag additions);
   - UP_KRW_LIST (Upbit KRW-market new listing or KRW-market addition);
   - UP_OTHER_LIST (Upbit BTC/USDT-market listing);
   - UP_WARN (Upbit warning designation; extensions and releases are excluded).
2. **Deduplication:** an event is (announcement time, ticker), counted once per (UTC day, ticker).
3. **Perp eligibility:** the coin's USDT perp (XUSDT, 1000XUSDT or 1000000XUSDT) must:
   - trade at the entry hour;
   - have at least 20 bars and at least 1,000,000 USDT of quote volume in the previous 24 h;
   - be at least 24 h old.
4. **Latency:** enter at the open of the first full hour after the announcement time (0–60 min after the news).
5. **Trade:** short at 1x and exit at the open 48 h later. If the perp stops trading first, exit at its last close.
6. **Sizing:**
   - each event takes 1/S = 25% of equity (S = 4);
   - gross exposure never exceeds 1x: if free capacity is short, simultaneous events split it equally, and an event with
     no capacity is skipped.
7. **Costs:**
   - 0.05% fee + 0.02% slippage per side, charged on the notional at entry and at exit;
   - funding per stamp (shorts receive positive funding);
   - doubled-cost run as well.

## DEV-only grid looked at before choosing (dev_run.txt; all DEV, net positive)
- **Horizons, in the event study (study_dev.txt):** 1, 4, 12, 24, 48, 72, 168, 336 h, for 10 event types.
- **Strategy grid:**
  - H: 24, 36, 48, 60, 72;
  - S: 2, 3, 4, 5, 6;
  - delay: 0, +1 h, +3 h;
  - drop-one-type variants.
- **Configs looked at:** about 20 strategy configs plus 10 × 8 event-study cells.
- **Types inspected and excluded:** BN_FUT_DELIST, BN_SPOT_LIST, UP_CAUTION, UP_DELIST and BN_MON_REMOVE showed no DEV drift.
- **Why H = 48:** it had the best pooled DEV t-stat.
- **Why S = 4:** it was fixed a priori as 25% per slot. S = 2 earned more on DEV but with a 35% drawdown.

### Selected config on DEV (2021-01..2024-10)
- median 0.00% (most DEV months have no event);
- geometric month +1.92%, mean +2.03%;
- 23/47 months up;
- max DD 16.2%;
- t = 2.87;
- 1.8 trades/month.

## Neighbour grid for check 5 (15 neighbours; ≥70% must be net positive on DEV AND TEST)
- H ∈ {24, 36, 60, 72} (−50%, −25%, +25%, +50%) at S = 4;
- S ∈ {2, 3, 5, 6} at H = 48;
- entry delay +1 h and +3 h (rebalance-time variants);
- drop each of the 5 event types in turn (5 variants).

## Random check 6
- 200 draws with the same entry hours, the same number of events per hour, the same H, S, side and costs.
- The coins are drawn at random from all perps that pass the same eligibility rule at that hour.
- The strategy's full-period net must beat at least 95% of the draws.

## Check 7
- Remove the 5 tickers with the largest total P&L contribution and rerun.
- No single trade may contribute more than 10% of total profit.

TEST (2024-11-01..2026-10-21) is run ONCE with this config by `run.py`.
