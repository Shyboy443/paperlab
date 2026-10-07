# Frozen design — regime-filtered crypto, before results

Default: Binance USDT linear perpetuals / Bybit USDT spot, BTC regime.
Independent 100,000 USDT books: IS 2019-01-01–2021-12-31, OOS
2022-01-01–2025-12-31. Earlier research already examined 2022–2024;
the original run added 2025. This requested grid revision follows that run,
so all 2022-2025 results are now retrospective validation, not a pristine
unseen holdout. No parameter is selected using those results.
No optimization, no OOS-based parameter selection. UTC throughout.

## Signals

Point-in-time top 50 by the previous 30 completed days' quote volume;
exclude stable, wrapped, synthetic-index and delivery contracts. Require
180 days since the observed listing day's end, resetting after relisting.
Require 91 consecutive completed daily closes for every sensitivity case.
When fewer than 50 qualify, both strategy sleeves remain cash.
BTC spot yesterday's close must exceed its previous 200-day simple mean,
and strictly more than 50% of the universe must exceed their 50-day means.
Default momentum: prior 63-day close-to-close return, top 10, long only.
Weights min(5%, 15% / annualized 30-day log-return standard deviation).
Daily 00:00 decisions. Two-rank deadband against last executed allocation;
mandatory exits and risk reductions override it. Fixed entry stop = executed
entry minus twice prior-day Wilder ATR14; additions do not loosen it.
No 20-day trailing-high stop or chop filter from the earlier strategy.

## Funding sleeve

Always eligible, including UP days. Equal allocation among eligible universe
members with actual Bybit spot history and a trailing three-settlement mean
greater than 0.00005 per eight hours. Normalize each observed settlement
rate by its actual preceding interval to eight hours for the signal only.
At a decision, use strictly earlier settlements; never the current/future
settlement to decide holdings charged at that settlement. Receipts use actual
rates and historical Binance opening marks, including non-eight-hour schedules.
Native 1000-token perpetuals hedge 1000 actual spot units per contract unit.
No spot price substitution between exchanges; no hedge for uncertain ticker
identity or missing history. No assumed spot availability before its launch.
Both hedge legs count toward gross exposure. Spot purchases require cash.

Capital: 50/50 initial Binance / Bybit wallets. Up to 2x notional on Binance,
with 5% wallet equity kept as margin reserve. Allocate funding using capacity
left after momentum, subject to both wallets and 150% aggregate gross.
Daily collateral transfers equalize venue equity at observed 00:00 marks;
assume USDT transfers available, one-hour delay, cost 1 USDT per transfer,
with a 100 USDT minimum imbalance. Existing holdings remain funded during
delay. This is an operational assumption, not proof of guaranteed transfers
or protection from exchange failure. Track both venue equities; flatten if
either becomes insolvent. No interest on idle USDT or spot lending income.

## Risk and execution

Portfolio scale min(1, 20% / previous 30 completed days' realized annualized
portfolio volatility), with no leverage increase in quiet markets. First 30
days use scale 1. Hard permanent kill after observed equity drawdown exceeds
20% from the running peak; closing costs/gaps can overshoot 20%. No restart
within a period. Enforce individual momentum 5%, venue margin and aggregate
150% caps at hourly observations; futures cannot use spot as local collateral.
Hourly actual trade bars execute stops: gap open when below stop, otherwise
the crossed level plus adverse slippage. In a stopped hour, conservatively
book any boundary settlement before closing. Terminal exits use last actual
observed trade, explicitly reported as archival settlement proxies. Prices
at unobserved execution times are not invented. No instantaneous response to
an unobserved within-hour portfolio peak; hourly risk limits are a discrete
simulation, not a contractual drawdown guarantee.

Taker fee 0.0004 and adverse slippage 0.0002 on EVERY filled leg, including
spot, risk exits and final liquidation. Paired gross and net runs retain real
funding; gross removes fees/slippage/transfer charges. A no-funding run holds
the same historical funding signals but removes cash receipts/payments.

## Validation locked before evaluation

Default 63 days / breadth 50%; grid 42,63,90 × breadth 50%,70%.
Revision requested by the user on 2026-10-07: replace the previous 60%
sensitivity column with 70%. Default, engine, data, costs and seeds remain
identical. The prior 50%/60% run is preserved; 70% is not chosen by results.
Five execution-stress seeds 11,29,47,71,101: independently sampled additional
0–2 basis points adverse slippage per order, above the mandatory 2 bps.
These are operational sensitivity runs on the same market history, not five
independent historical samples. Do not select the best seed or grid cell.
A/B isolate pure momentum (no funding sleeve) with / without regime; also
compare combined strategies with / without regime and report funding-only.
Benchmarks BTC spot buy-and-hold with mandatory entry/exit costs, and 0% USDT
cash assuming peg holds. No claim of 10% monthly return before evidence.

Metrics: monthly arithmetic mean; CAGR using elapsed calendar days /365;
daily Sharpe at zero risk-free, Sortino with RMS negative daily returns,
hourly peak-to-trough drawdown, Calmar, worst/best month, positive-month share.
Acceptance: default combined OOS monthly mean >0; observed max DD <25%;
at least four of five execution seeds profitable OOS; combined regime-filtered
OOS monthly net mean must exceed the otherwise identical unfiltered combined
book. Pure-momentum monthly mean/return/drawdown A/B is also reported to isolate
the gate's effect. Failure is reported, not repaired by tuning held-out years.

Sources and processed inputs receive SHA-256 pins before the first full run.
Public data queries and archive content are cached with provenance. All
missing-data limitations are disclosed; current listings are not the universe.
