# V5 data availability audit (Bybit linear, public REST v5 — verified 2026-09-24)

Every probe below was a live public request (no key). "Verified back to" is the oldest point actually returned,
not the documented limit.

| feed | Bybit source | verified back to | V5 use | label |
|---|---|---|---|---|
| OHLCV 1m / 5m / 1h / 4h / 1d | `/v5/market/kline` (1000 bars/request) | 2022-01-01 (SOLUSDT 1m, 5m, 1h) | 1m execution tape; 1h / 4h / 1d / 1w aggregated from it | BYBIT-NATIVE |
| Funding rate | `/v5/market/funding/history` (every settlement, rate + timestamp) | 2022-01-01 (SOLUSDT); per-coin intervals (e.g. HYPE 480 min) | funding charged at the actual settlement instants; funding features | BYBIT-NATIVE |
| Open interest | `/v5/market/open-interest` (5min..1d; cursor) | 2022-01-01 (SOLUSDT, DOGEUSDT 1h) | OI level / change 1h-4h-24h, price/OI divergence, deleveraging proxy | BYBIT-NATIVE |
| Mark price | `/v5/market/mark-price-kline` | 2022-01-01 | recorded (liquidation / funding reference) | BYBIT-NATIVE |
| Index price | `/v5/market/index-price-kline` | 2022-01-01 | recorded (basis reference) | BYBIT-NATIVE |
| Premium index (mark vs index basis) | `/v5/market/premium-index-price-kline` | 2022-01-01 | basis level and percentile | BYBIT-NATIVE |
| Long / short account ratio | `/v5/market/account-ratio` (5min..1d) | 2022-01-01 | account positioning (extra feature) | BYBIT-NATIVE |
| Instrument rules (tick, qty step, min qty, min notional, max leverage, funding interval) | `/v5/market/instruments-info` | current snapshot only | legality, sizing, cost model | BYBIT-NATIVE (today's filters applied to history) |
| **Taker buy / sell volume** | not in the v5 REST API; only in the daily trade archive `public.bybit.com/trading/<SYMBOL>/` (every print with taker side) | files exist daily; 8-20 MB compressed per coin-day (~120 GB for the V5 universe and period) | **NOT USED** in V5 — too heavy to derive now; not fabricated. Binance taker volume would be SURROGATE DATA and is not used either | UNAVAILABLE (practically) |
| Liquidations | no historical endpoint (WebSocket `allLiquidation` is live-only) | — | **no liquidation map.** The deleveraging family uses a Bybit-native PROXY: sharp open-interest drop with a large same-direction price move | PROXY (declared) |
| Order-book depth / spread history | none | — | spread modelled from tick size + volatility (the existing execution model) | MODELLED |

**Conclusion.** V5 can be built entirely on Bybit-native data: the execution tape, funding, open interest, basis
and account ratio all exist historically for the venue it would trade on. No SURROGATE series is combined with Bybit
execution assumptions. The declared limits are: today's instrument filters applied to the past, no taker-volume
feature, no liquidation map (an OI-based proxy instead), and a modelled spread.
