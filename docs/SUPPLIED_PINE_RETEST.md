# Retest of the user's exact Pine file

The supplied file is **byte-identical** to the 517-line LuxAlgo Pine source used by the corrected QQQ and crypto studies. Both SHA-256 digests are `79b5fb709ee7d1dcac5b3cdbaf3373fcf2a1fbd9a61a7127ca5bdf9039f8a56f`. The exact bytes are preserved in `app/value_reversal/sources/user-provided.pine` with the original MPL-2.0 notice.

The file is an **indicator**, not a Pine `strategy`. It defines no execution orders, stops, take-profit orders, position sizing, fees or backtest wallet. Its actual defaults are previous-session signals, **10** outside bars, current-session signals disabled, 60 rows, 70% value-area input and an 18:00 America/New_York anchor. The previous video's five-bar examples are separate inputs, not this file's defaults.

The actual supplied file was re-executed with PineTS 0.11.0. All **463,584** independent profile-level and signal comparisons matched: 311,520 checks on QQQ across 2024–2025, and 152,064 checks on BTC/ETH/SOL crypto sessions spanning both 2024 DST changes. Signal/level diagnostic plots, instrument tick overrides and labelled 5/10-bar sensitivity inputs are the only source overrides. This does not claim an independent Pine check of every crypto candle across the full four years.

The complete 2022–2023 and 2024–2025 histories were re-simulated under the supplied **10-bar previous-profile** defaults: six crypto settings with gross/net/no-funding/stress wallets and two QQQ settings with net/stress wallets, yielding **56** historical wallet evaluations. Every metric and complete ledger reproduced the earlier results within numerical tolerance. Reusing signal features was allowed only after checking the exact supplied-source identity, unchanged feature function, current indicator/backtest hashes and all six feature-file hashes. No old book or report was overwritten.

## Retrospective 2024–2025 results with the supplied defaults

| Market | Timeframe | Arrows | Trades | Net total | Doubled-cost net |
|---|---:|---:|---:|---:|---:|
| BTC perpetual | 15m | 97 | 95 | +0.69% | −7.54% |
| BTC perpetual | 30m | 67 | 65 | −12.64% | −14.61% |
| ETH perpetual | 15m | 98 | 93 | +5.15% | −0.39% |
| ETH perpetual | 30m | 70 | 67 | +3.26% | −0.15% |
| SOL perpetual | 15m | 106 | 101 | −12.96% | −16.39% |
| SOL perpetual | 30m | 79 | 76 | −4.32% | −6.66% |
| QQQ ETF adaptation | 15m | 31 | 27 | −3.35% | −4.42% |
| QQQ ETF adaptation | 30m | 21 | 18 | −2.51% | −3.12% |

All eight supplied-default comparison settings **FAIL** the existing execution qualification gates; none is positive with doubled costs. These are total returns over two years, not monthly returns. They are retrospective studies on already known years, not new holdout or prospective results. The per-setting period tables, monthly averages, Sharpe, drawdowns, fees and actual funding amounts are included in `/api/public/value-reversal/source-verification`.

Crypto carries 4 bps taker fees plus 2 bps slippage per side and real historical funding; stress doubles fees/slippage. QQQ retains its approved regular-hours Alpaca ETF adaptation, whole shares and 1 bp fee allowance plus 2 bps slippage. Crypto sessions run 24/7 anchored at 18:00 New York, including DST. QQQ does not become NQ futures by sharing the indicator code.

Execution remains the explicitly disclosed next-open entry, excursion stop, opposite-value-area target, session-close exit, one position at a time, 0.5% stop risk, 100% notional cap and 20% drawdown halt. These assumptions are separate from the pasted indicator. Frozen rules, saved QQQ histories, forward starts, scanner qualification gates and existing stock trading are preserved.

## Reproduce the supplied-code verification

After the documented crypto/QQQ pipelines have generated their verified history and feature caches:

```powershell
$env:PINE_SOURCE='app/value_reversal/sources/user-provided.pine'
$env:PARITY_OUTPUT='data/user-crypto-pine-verified.json'
node scripts/check_crypto_source_parity.cjs
$env:PARITY_OUTPUT='data/user-qqq-pine-verified.json'
node scripts/check_qqq_source_parity.cjs
.venv/Scripts/python.exe scripts/validate_supplied_value_reversal.py
.venv/Scripts/python.exe scripts/package_supplied_source_verification.py
```

The validator supports `--source`, `--data` and `--output`. It refuses different supplied source bytes, changed research code and stale feature hashes; it is not an automatic translator for arbitrary Pine. Neither the verifier nor these read-only routes can submit an order. On the existing dashboard, the “Your pasted Pine · exact defaults” table links each setting to its corresponding historical candle replay.
