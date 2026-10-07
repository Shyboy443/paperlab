# Crypto perpetual cross-sectional momentum

This research package runs the requested strategy on historical Binance USDT
linear perpetuals. It never sends orders and is separate from the running bots.
The 63-day/top-50 case is specified in advance. The complete sensitivity grid is
30/63/126 days by top-40/50/60, separately for long-only and long-short. A case
counts as positive only after fees, slippage and actual historical funding.

## Reproduce

Use Python 3.12. From this directory:

```powershell
python -m venv ../../data/momentum-venv
../../data/momentum-venv/Scripts/python.exe -m pip install -r requirements-lock.txt
../../data/momentum-venv/Scripts/python.exe download.py daily --data ../../data/cross_sectional_momentum
../../data/momentum-venv/Scripts/python.exe model.py --data ../../data/cross_sectional_momentum
../../data/momentum-venv/Scripts/python.exe download.py intraday --data ../../data/cross_sectional_momentum --selection output/required_months.json
../../data/momentum-venv/Scripts/python.exe -m pytest -q -c pytest.ini
../../data/momentum-venv/Scripts/python.exe run.py --data ../../data/cross_sectional_momentum
../../data/momentum-venv/Scripts/python.exe report.py
../../data/momentum-venv/Scripts/python.exe verify.py --data ../../data/cross_sectional_momentum
```

On Linux/macOS use the virtual environment's `bin/python`. The downloader uses
public exchange APIs and the public archive; no API key is needed. Downloads
resume from the local raw cache. `--verify-checksums` additionally verifies the
exchange's checksum file for every ZIP. ZIP CRCs are always checked and SHA-256
digests are recorded for reproducibility. Archive providers can revise their
files: compare your manifest with the delivered input snapshot before claiming
an exact replication. An entirely new snapshot gets a new backtest identity.

The ZIP includes the final results and `data_audit/` provenance snapshot. On a
new machine, copy `data_audit/catalog.json` and the `api_daily`, `api_funding`,
`api_mark`, `api_trade` and `rest_2019` directories into the empty data directory
before downloading. This preserves historical universe discovery and cached
exchange responses. Keep the delivered manifests for comparison with your new
download. Raw ZIP archives and processed market CSVs are not duplicated inside
the research ZIP; the complete local cache remains in the data directory above.

After a full verified rerun, `python build_bundle.py --data <data-directory>`
creates a fresh ZIP and SHA-256 checksum. Each bundled file also has its own
hash in `BUNDLE_MANIFEST.json`.

`run.py --main-only` is a quicker accounting check. The full run executes 80
period/mode/lookback/universe/funding cases. Results are cached under a source
and input-manifest fingerprint. Changed code or data cannot reuse an old result
as if it belonged to the changed strategy.

## Frozen interpretation

- **Historical universe:** discover all archived USDT contract directories,
  including delisted contracts. Exclude stablecoins, wrapped/staked derivatives
  and synthetic basket indices using the explicit list in `download.py`.
  Rank past 30-day mean *quote* volume in USDT, rather than incomparable native
  token volumes. Age is measured from the first real observed perpetual trade,
  with the first trading day's end used as a conservative listing-age bound.
  Contract relistings reset the 180-day
  age clock; old and new contract lifecycles cannot share a position. Verified
  daily archive holes are restored from REST, including Binance's historical
  BNXUSDTSETTLED alias. No current exchange roster selects past
  assets. All lookbacks use a common requirement of 127 complete daily prices.
- **Early dates:** portfolios hold cash when fewer than 40 contracts qualify.
  When 40-49 qualify, top-50 uses the available 40-49; top-60 similarly expands
  up to 60. The first 40-contract weekly universe is 22 February 2021. The
  2019-2021 evaluation includes the preceding cash period. The archive starts
  January 2020; public REST supplies the available 2019 daily warm-up.
- **Signals:** Monday 00:00 UTC decisions use the preceding Sunday's completed
  close and completed daily histories. Rank the full selected universe; select
  the top/bottom floor(20% * N), then skip vol > 2x the universe median without
  refilling those slots. Long-short does not impose dollar neutrality after
  filtering. Both gross and net exposure are reported.
- **Volatility:** standard deviation of the past 30 daily log returns, sample
  ddof=1, multiplied by sqrt(365). Desired absolute weight = 0.40 / asset vol;
  actual weight = min(desired, 0.05). The 5% cap usually dominates. This is not
  a promise of 40% portfolio volatility or a requirement to use 200% gross.
- **Rank buffer:** two rank positions since the last allocation permit a weekly
  signal trade. For an asset never allocated, use its prior weekly rank; the
  first eligible allocation is allowed. A one-rank move can retain an incumbent
  beyond the quintile boundary. Stops and loss of universe/chop eligibility
  override the deadband. Stopped assets cannot re-enter intraweek. Hard risk
  reductions also override the deadband; they are costed, not free rebalances.
- **Stops:** prior 20 completed daily highs minus 2x prior Wilder ATR(14) for
  longs, and lows plus 2x ATR for shorts. Wilder ATR is seeded with a 14-period
  simple mean. Stops ratchet and never loosen. Hourly bars trigger stops; a gap
  fills at the opening price, an ordinary crossing at the stop. Both pay the
  stipulated market-order costs. Today’s extremes cannot set today’s stop.
- **Risk caps:** observed-hourly position weight <= 5%, total gross <= 200%,
  with paid trims. A small buffer prevents fee-driven oscillation at the cap.
  Continuous intrahour compliance cannot be guaranteed from hourly data.
- **Costs:** fill price incorporates 0.02% adverse slippage and every filled
  side pays 0.04% taker fees. End-of-period liquidation costs are included.
- **Funding:** actual archived rates at every recorded settlement, including
  historical intervals other than eight hours when present. Signed quantity
  times historical opening mark price times rate is paid or received. Existing
  inventory pays before a boundary rebalance; a newly opened Monday position
  first pays at the next settlement. The hourly opening mark is the settlement
  notional-price proxy. Missing rates/marks while held raise errors; they are
  never replaced with zero. With/without-funding runs each compound their own
  wallet, so the equity difference also includes subsequent sizing effects.
  Missing monthly funding files and interval transitions are verified through
  the historical funding REST API. Missing mark bars are recovered through the
  mark-price REST API or the exchange's daily mark archives. Original responses
  and repair-file hashes are retained in the data manifest. Schedule changes
  can include a six-hour bridge from a two-hour to an eight-hour UTC grid;
  every actual settlement is charged once, regardless of interval length.
  Internal price gaps are checked against the exchange API. Real zero-trade
  hours mark inventory but cannot execute orders; pending exits await the next
  traded bar. Stops and caps cannot guarantee execution during a trading halt.
- **Terminations:** remove zero-trade padded candles. At the first unavailable
  hour after a contract's real trading history ends, close existing inventory
  using its last real hourly price and mandatory costs. This is a disclosed
  terminal-settlement proxy, not a verified exchange TWAP settlement or a
  forecast-based early exit. The output counts affected round trips.
  This applies at every lifecycle termination, including temporary delisting
  followed by a relaunch under the same ticker.
- **Periods:** independent 100,000 USDT books for 2019-01-01 through 2021-12-31
  and 2022-01-01 through 2024-12-31, with prior data used only for warm-up.
  All parameters stay fixed in the temporal out-of-sample period.

## Benchmarks and statistics

The equal-weight buy-and-hold benchmark buys the first eligible historical
universe on the first Monday of each period, at 1x gross, then keeps the initial
holdings without replacements or rebalancing. It pays the same costs/funding
and is closed at the period boundary or contract termination. It does not use
the strategy's stop/vol/cap rules: applying them would cease to be buy-and-hold.
Its exposure and volatility differ from the strategy and are reported.

The time-series momentum baseline trades the sign of each asset's own 63-day
return across the historical top-50 universe, with the same chop/vol/stops/caps
and costs/funding. It rebalances weekly. Cross-sectional rank deadbands do not
apply to this own-return signal.

Monthly average return is the arithmetic average of full-calendar net monthly
returns. Annualized return is CAGR on the entire requested period (including
cash). Sharpe uses daily net returns with a zero risk-free rate and sqrt(365).
Sortino uses the root mean square of min(daily return, 0), including zero days.
Max drawdown is measured from hourly equity including the initial capital.
Calmar = CAGR / max drawdown. Win rate and average holding period refer to
closed position episodes, aggregating partial trims and funding; period-end
closures are included. Funding drag reports both direct signed cash payments
and the difference between paired compounded results.

## Source documentation

- [Binance public archives, field layouts and checksums](https://github.com/binance/binance-public-data)
- [Binance USD-M market data and funding history API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)
- [TLM/ICP 2022 USDT contract settlements](https://www.binance.com/en/support/announcement/detail/af469aeeab074738bb4a276070a9d11b)
- [BNX 2023 old-contract settlement and redenomination](https://www.binance.com/en/support/announcement/detail/4d23ada51a2e4fa182835c77d51ba1a9)

Raw archives and manifests remain in `../../data/cross_sectional_momentum`.
The output includes a machine-readable frozen protocol, data-quality audit,
closed-trade CSVs, daily curves, main-case hourly curves/orders, all metrics and
the nine-case positivity checks. Returns are observations from a historical
simulation, not guaranteed future returns.

`verify.py` independently reconciles the 80 exported books, monthly compounding,
closed-trade cash flows and funding/fee totals. It checks frozen source hashes,
every daily input file, repair response and source archive. Its integrity PASS
does not mean the strategy is profitable; see the separate robustness result.
