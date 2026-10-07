# Five-stock small-account trend / Alpaca

Authorized allocation: **$9.50**. Default: **paused**. Main PaperLab links to
`/public/stock-trend` as **Stock portfolio** and reuses its login session.
The same Railway service retains the independent crypto research history.

## Rule and capital

`TREND5-SMA200-MOM63-LOT1-v1`: retain the original monthly liquid 20-stock
S&P 500 universe and its aggregate SMA200 market filter. At each month's
first completed session, rank that universe by prior 63 completed-session
returns and select five. Alphabetical order resolves ties; selection stays
fixed for the month. Long these five when the index is above SMA200; cash
otherwise. Signals precede the next execution open. The 20-stock universe
uses dated membership, 504 prior observed sessions, trailing 60-session
average dollar volume and a $10M liquidity floor. Index returns use the
previous universe. The original frozen index seed is retained.

Five initial $1.88 buys total $9.40, leaving about $0.10 cash. Minimum initial
allocation is $5.06 with the 1% reserve and cents rounded down. Never round
small trades up to $1. Existing adjustments below $1 are skipped and shown;
weights drift. Dropped stocks and cash-regime shares exit using full actual
fractional quantities. Retained holdings count against new buy capacity.
Spare account cash cannot refill strategy losses. Gains can remain invested
while a trim is below $1; new buys cannot increase that exposure.

This is a new momentum-selection variant; the old 20-stock backtest does not
transfer. It is not a validated full S&P 500 implementation.

## Controls and paper verification

Account state and mutations require private Basic auth and X-PaperLab.
Paper/live keys are verified and stored in the existing encrypted vault.
Adding keys never starts trading. Keys do not enter browser storage or the
execution ledger; only the existing dashboard login-session convention is
reused. Use a dedicated flat broker account with no unrelated open orders.

**Verify paper execution** runs five $1.88 buys, five full fractional exits,
and confirms that the paper account has no positions/open orders. It waits
for the next regular open. The venue is hard-coded to Alpaca paper; no
operator network value can route these test orders to a real account.
Persisted ids reconcile after restart or response loss. Uncertain submissions
are not reissued with a new id. Fills are separately shown from strategy
orders. This verifies mechanics, not performance. Credential edits and
strategy start are blocked while this check owns a pending paper book.
Failed or uncertain exits require broker review; saved history is retained.

After a recorded paper pass, live start requires configured live keys,
executable funds, a dedicated flat live account, secure authentication and
**LIVE STOCKS 5**. Pause cancels this strategy's unfilled orders and retains
shares. Flatten sells strategy shares during an open session and requires
**FLATTEN STOCKS 5** for live holdings. The paper check independently completes
its paper liquidation. No live orders or deposits are enabled by deployment.

## Data, execution and persistence

State, deterministic intents and journal live in `stock-trend.db`; historical
observations and membership snapshots in `stock-trend-data`, on the Railway
volume. A rule migration pauses the old configuration while preserving its
intents and holdings. Account identity is pinned. Foreign positions or orders
block execution. No shorts, leverage or margin buying power.

The worker runs every 20 seconds, refreshing completed daily data roughly
every five minutes. Calendar/clock handles holidays and DST. Strategy orders
run only within two minutes after the next regular open, using the immediately
preceding completed daily signal. Sales precede buys, which require actual
fills and available cash. IEX bid/ask must be fresh; selected assets must be
tradable and fractionable. Failed or partial baskets are not replaced by a
new automatic batch. Alpaca adjusted SIP daily bars continue the seed;
dated membership uses the fja05680/sp500 community dataset. Missing required
closes block signals. No flat missing-bar substitution in the completed daily SIP signal. Membership is
not an official licensed feed. Actual cash credits apply, with no invented
T-bill yield in the live account.

## Separate research evidence

`research/stock_small_account` contains full code, reproduction instructions,
source/data pins, the 3-lookback/five-seed grid, physical-date daily ledgers,
monthly returns, equity/drawdown figure and report. Retrospective 2021–2025:
+1.54% monthly average net, 18.95% annualized, 0.90 Sharpe over T-bills,
17.56% max drawdown, worst month -6.31% (March 2025). All five default cost
stress seeds were positive; they are cost scenarios, not independent markets.
The default was not selected using this grid.

Costs are a proxy: 6bps per executed side, square-root impact and 1c per sell,
with 0c/3c sell-fee sensitivity and no cash interest. Actual fee/dividend
rounding differs. Adjusted units approximate fractional shares and corporate
actions. Already examined history and missing delisted prices mean this is
retrospective validation, not fresh OOS or prospective evidence. Concentrated
winners can reverse together before the slow aggregate filter reacts. These
numbers do not establish an expected future return.

Run `python -m pytest -q tests/test_stock_trend_live.py tests/test_key_vault.py
tests/test_public_api.py`. Fake-broker tests are separate from actual paper
broker evidence displayed on the page.

Official references: [fractional trading](https://docs.alpaca.markets/us/v1.1/docs/fractional-trading),
[orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca),
[historical bars](https://docs.alpaca.markets/us/reference/stockbars).

The requested allocation is a ceiling: start clips it down to actual cash/equity and cents. For example, $9.49 cash can fund five $1.87 initial targets without increasing the authorized $9.50 cap. Existing holdings are not automatically sold to make that cash available.

Execution sizing uses explicitly labeled free IEX quotes, whose venue coverage differs from consolidated SIP. SIP latest quotes returned a subscription error on both connected accounts. There is no paid-data requirement for sizing; no silent stale or delayed quote fallback. `STOCK_TREND_QUOTE_FEED=sip` is optional for accounts with that entitlement. The paper check validates quote freshness before entries. Historical signal bars remain completed, adjusted SIP daily data.

Quote timestamps are validated against actual response receipt time, not the earlier broker clock snapshot. A fresh quote updated during intervening network calls is valid. Quotes truly later than receipt time, older than 60 seconds, missing or non-executable still block entries. Opening quote delays wait without submitting intents only within the original two-minute entry window; they never produce a late catch-up trade.
