# Worker 3 round 1: token_unlocks (pre-registered 2026-10-26, BEFORE any TEST run)

## Idea and evidence
Large scheduled vesting cliffs (team/insider and investor tranches) add supply that recipients sell or hedge; the
price pressure is front-run in the weeks before the unlock.
- Keyrock (Dec 2024), "From Locked to Liquidity: What 16,000+ Token Unlocks Teach Us"
  (https://keyrock.com/from-locked-to-liquidity-what-16000-token-unlocks-teach-us/): 16k events across 40 tokens, prices
  normalised to ETH; ~90% of unlocks show negative pressure; price impact starts ~30 days BEFORE large unlocks,
  accelerates in the final week and stabilises ~14 days after; team unlocks worst, investor unlocks mild, ecosystem positive.
- Tokenomist research (2026, https://tokenomist.ai/research/do-token-unlocks-crash-prices), 236 events vs BTC/matched
  peers: median one-month move about -4.85% vs peers (secondary summary only; page body not readable).
- Universitat de Vic thesis (https://repositori.uvic-ucc.cat/handle/10854/180316): 492 DefiLlama unlock events, descriptive.
- No peer-reviewed SSRN/arXiv event study found. All evidence is industry research (in-sample, commercial interest).

## Data
- Unlock schedules: DefiLlama public dataset files https://defillama-datasets.llama.fi/emissions/<protocol> (373
  protocols; the api.llama.fi/emission* routes are paywalled now), symbols via coins.llama.fi. Cache data/w3r1_token_unlocks/.
- **Look-ahead limitation:** only TODAY's version of each schedule is available. I keep only categories whose dates are
  fixed in the published vesting terms at TGE (insiders, privateSale; cliff type). Revisions (delays, burns) after the
  fact cannot be excluded. Treasury/"noncirculating"/farming cliffs (often recorded on-chain ex post) are excluded.
- Size = cliff tokens (same-day cliffs of one token summed) / schedule-unlocked supply the day before.
- Prices: survivorship-free daily perp panel (research/monthly20/r2_rotation, incl. delisted coins), open prices,
  daily funding. 195 DefiLlama tokens map to a Binance USD-M perp symbol.

## Rule (selected on DEV only)
- Event: insider/investor cliff with size >= **5%** of unlocked supply.
- Eligible at entry: perp has a price at entry and exit, listed >= 30 days before entry, 30-day mean quote volume >= 2M USDT.
  One trade per coin at a time (an event whose window overlaps an accepted earlier window of the same coin is skipped).
- Trade: SHORT the perp at the daily open **X = 30** days before the unlock day, cover at the open of the unlock day
  (**Y = 0**). **Hedge:** equal notional LONG BTCUSDT perp over the same days (market-neutral pair, per Keyrock's
  benchmark-relative evidence).
- Sizing: each active event gets weight min(**cap = 0.33**, 1/n_active) of equity (gross short <= 1x, plus the BTC hedge
  leg of equal size; 1x per leg, no leverage on either leg). Rest in cash.
  NOTE: with 3+ active events the total notional is 2x equity (1x short + 1x BTC long). The strict "total gross <= 1x"
  version (every weight halved) is also reported as `strict1x`; the checks are judged on the primary version.
- Costs 0.05% fee + 0.02% slippage per side on all weight changes, funding included; doubled-cost run too.

## DEV grid (2021-01..2024-10), 216 configs, all in dev_grid.txt / dev_grid.json
min_size {2%,5%,10%} x X {14,21,30,45} x Y {0,3,7} x hedge {off,BTC} x cap {0.2,0.33,0.5}. 58% of configs geo > 0;
X >= 30 and BTC hedge clearly better. Selected config DEV: geo +4.35%/mo, median +1.72%, mean +4.90%, 28/46 months up,
max DD 20.5%, t 2.92, 51 trades. I chose cap 0.33 (middle) rather than the top cell (cap 0.5, geo 5.85).
DEV warning seen before TEST: X < 30 includes one ALPHA short in Jan 2021 that lost ~78% of its slot (alt-season squeeze);
the strategy has a fat left tail. Few events in DEV (~1.1 trades/month, many flat months).

## Neighbour grid for check 5 (16 neighbours, each one parameter moved)
min_size {2.5%, 3.75%, 6.25%, 7.5%}; X {15, 22, 38, 45}; Y {1, 3, 7}; cap {0.165, 0.25, 0.41, 0.5}; hedge off (1).
Pass if >= 70% are net positive on both DEV and TEST at baseline costs.

## Other checks (ROBUSTNESS.md)
Months 2021-01..2026-10 (flat months count as 0%). Check 6: 300 random versions with identical trade windows, weights,
hedge and costs, but the shorted coin drawn at random from coins eligible at that entry (age >= 30 d, 30d QV >= 2M,
not BTC). Check 7: drop the 5 coins with most profit; no single trade > 10% of total profit.
TEST (2024-11-01..2026-10-21) is run ONCE with exactly this config.
