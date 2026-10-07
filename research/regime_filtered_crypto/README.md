# Regime-filtered crypto research

The requested rules are implemented and validated, without parameter selection
on the held-out years. **The strategy FAILS acceptance.** This package is research
software; it does not place orders or change the Railway paper bots.

Revised frozen run: `7a36d262871367ef` (user-requested 50% / 70% breadth grid).
The prior 50% / 60% run `0c1f2a9fb634da68` and its original ZIP are preserved.

Default OOS 2022-2025: monthly arithmetic net mean **-0.4490%**, total net
return **-20.0067%**, hourly maximum drawdown **20.0390%**. Permanent kill:
2023-06-30 02:00 UTC. **0/5 execution seeds and 0/6 grid cases profitable.**
The gate improves pure momentum, but the combined strategy does not improve
OOS return versus its ungated counterpart. Requested acceptance therefore
fails despite drawdown staying below 25%.

`output/run_7a36d262871367ef/REPORT.md` contains the full results, caveats and
attribution. Its `pdf/Regime-Strategy-Summary.pdf` is the one-page summary.
Use `metrics.csv`, `monthly_returns.csv`, case ledgers and `charts/` for analysis.

## Rules and fidelity

Read [PROTOCOL.md](PROTOCOL.md) for the design frozen before evaluation.
Default 63-day momentum, strict breadth >50%, BTC spot >200-day MA,
point-in-time top 50 USDT perpetuals, 180-day conservative listing age,
daily decisions, fixed entry ATR stop, 15% per-coin volatility sizing capped
at 5%, 20% portfolio-vol scaling, 150% aggregate gross and permanent 20% kill.
Volatility reductions override the two-rank discretionary trade deadband.

Funding uses actual settlements, not a constant assumed rate. The trailing
three-settlement mean is normalized to eight-hour equivalent rates for the
eligibility signal only. Execution uses strictly older settlements; the
current settlement charges existing inventory before trades. Matching
Bybit spot prices and quantities account for basis and token multipliers.
Both hedge legs pay 0.04% fees + 0.02% adverse slippage on each side.
Separate venue wallets, margin reserves and delayed collateral transfers
are modeled. Spot inventory cannot collateralize Binance directly.

Retired Bybit TON spot history is recovered from Bybit trade archives. The
parser handles the sixth trade flag introduced during March 2025 without
shifting numeric columns. Binance provider holes use actual REST/daily
archive observations, never fabricated prices or funding. Delisted tickers
are in the historical universe. Reused/relisted lifecycles reset age.

Features and multi-asset accounting are vectorized. Stops, inventory-dependent
funding and permanent risk stops use a chronological hourly state loop;
a simple vectorized cumulative return calculation cannot enforce those rules.
Float64 price matrices are disk-backed to preserve precision without requiring
several gigabytes of resident RAM. Temporary matrices are removed on success.

The first full aged top-50 universe is 2021-03-14; preceding IS dates are cash.
Bybit spot coverage begins in July 2021. All 2022-2025 years were examined in
the original run. This user-requested grid revision is retrospective robustness
validation, not a previously unseen holdout. No OOS-based parameter selection
was performed in this study.

## Reproduce from public exchanges

Python 3.12, no API key or exchange account required. Run from this directory.
Commands below use the separate data root; never overwrite the earlier
cross-sectional momentum study's frozen inputs.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
.venv/Scripts/python.exe binance.py daily --data ../../data/regime_filtered_crypto --workers 16
.venv/Scripts/python.exe model.py --data ../../data/regime_filtered_crypto --output output
.venv/Scripts/python.exe market_data.py daily --data ../../data/regime_filtered_crypto --selection output/required_months.json
.venv/Scripts/python.exe binance.py intraday --data ../../data/regime_filtered_crypto --selection output/required_months.json --workers 16
.venv/Scripts/python.exe market_data.py hourly --data ../../data/regime_filtered_crypto --selection output/required_months.json --workers 8
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe run.py --data ../../data/regime_filtered_crypto
```

Inspect `output/latest_run.txt` for the generated fingerprint. Then run:

```powershell
.venv/Scripts/python.exe verify.py --data ../../data/regime_filtered_crypto --run output/run_7a36d262871367ef --raw
.venv/Scripts/python.exe report.py --data ../../data/regime_filtered_crypto --run output/run_7a36d262871367ef
```

The fingerprint depends on source code and exact downloaded inputs. Exchange
revisions or new retrieval metadata can produce a different identifier; use
your run's actual directory. A frozen run's source and every compiled input
are SHA-256 pinned before evaluation. `verify.py --raw` additionally checks
all referenced public archives/API snapshots and independently reconciles
closed-order PnL, per-leg fees, slippage, cash funding and final NAV. Cached
responses make the download resumable and preserve rejected/unsupported symbols.

The revised run reused invariant cases only after validating identical frozen
input hashes, engine/data code, calculation function ASTs, default rules,
periods and seeds, plus the previous independent ledger verification. New
70% breadth cells were evaluated from the same frozen inputs. The provenance
is recorded in `reuse_provenance.json`. Reuse is optional; a normal `run.py`
invocation recomputes every case. To reuse an earlier verified run, archive
its original `run.py` as `source_run.py` in that run folder and pass
`--reuse-from output/run_0c1f2a9fb634da68`. Mismatched inputs, engine code or
calculation functions are rejected. No cell is chosen by its return.

On Linux/macOS use `.venv/bin/python`. Public data collection may take time,
particularly retired-symbol trade archives. Binance archive checksum verification
is available with `--verify-checksums` on download commands; ZIP CRC and cached
SHA-256 checks run by default.

If Windows WMI makes `platform.system()` imports stall on this particular PC,
the bundled runtime used for the completed run is
`../../data/momentum-venv/Scripts/python.exe`. A local launch workaround is:

```powershell
../../data/momentum-venv/Scripts/python.exe -c "import platform; platform.system=lambda:'Windows'; import runpy,sys; sys.path.insert(0,'.'); sys.argv=['run.py','--data','../../data/regime_filtered_crypto']; runpy.run_path('run.py',run_name='__main__')"
```

This only avoids an OS identification query; it does not change strategy math.

## Interpret results carefully

Five seeds add uniform 0-2 bps slippage to the mandatory 2 bps per order on
the same market sample. They are execution-stress checks, not independent
market histories. No seed or sensitivity cell is chosen for deployment.
Gross and no-funding cases re-simulate NAV and path-dependent risk; inventory
and kill dates can differ. Exact booked cashflows and sleeve attribution are
provided separately.

Hourly opening mark prices approximate instantaneous funding settlement marks.
Hourly observations do not guarantee an intrahour drawdown ceiling. Terminal
liquidation at the last observed trade is a disclosed archival proxy. No
historical order-book depth, lot-size/minimum-notional constraints, exchange
default, transfer freeze or liquidation ladder is reconstructed. Quantities
are continuous; this is not a production execution adapter.

The cited AdaptiveTrend strategy differs materially and reports 40.5% CAGR,
about 2.9% compounded monthly, not a validation of 10% monthly for these rules.
The network-momentum and perpetual-pricing papers do not prove fixed net income
from this specification. Do not tune 2022-2025 until it passes, then call it
out-of-sample. Any revised carry gate needs a separately frozen design and
new prospective evidence.

Public source documentation:

- https://github.com/binance/binance-public-data
- https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History
- https://bybit-exchange.github.io/docs/v5/market/kline
- https://public.bybit.com/spot/
- https://arxiv.org/html/2602.11708v1
- https://arxiv.org/abs/2108.11921
- https://arxiv.org/abs/2212.06888
