# Reproduce the $9.50 five-stock test

From the paperlab folder, with Python 3.12 and numpy/pandas/matplotlib:

```powershell
python research/stock_small_account/backtest.py --data ../stock_trend/data/n20_arrays.npz
python -m pytest -q tests/test_stock_trend_live.py
```

Frozen inputs and their source pipeline are in `../../../stock_trend` from this folder (resolve from the repository root as `stock_trend`). The exact source SHA is in `output/pin.json`. The original `stock_trend/data_pipeline.py` and README document the stored Yahoo adjusted-price and dated membership rebuild; fresh downloads can differ from frozen hashes. The forward reproducible Alpaca pipeline is `app/stock_trend/data.py`, with the frozen seed and SIP bars, cached on the Railway volume. Never share encrypted vault contents or broker keys with a research bundle.

The backtest imports the actual production planner from `app/stock_trend/model.py`. It ranks before the execution open, keeps monthly selection fixed, commits no OOS-selected parameter, never borrows, and checks the full cash/PnL/cost ledger for every run. Cash is not credited with hypothetical T-bill yield. All reported returns include stated execution cost proxies.

See REPORT.md and output/monthly_returns.csv. This is retrospective evaluation of previously viewed history. It is not a fresh holdout or proof of future profitability.
