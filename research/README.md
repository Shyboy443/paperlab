# Research environment (local only)

This is a DuckDB research store, a point-in-time feature engine, pre-registered studies and saved SQL queries. It runs
on your machine against `data/research/market.duckdb`. It never touches the live bots or the Railway server, apart from
two read-only pulls: paper trades, and news fetched with the vault key on the server.

```bash
pip install -r research/requirements.txt
python research/ingest.py          # build / top up the store (public Bybit + Binance market data, ~15 min first time)
python research/pull_trades.py     # copy the live paper trades (read-only) into paper_trades
python research/features.py        # raw tables -> features_5m (every value known at the decision; ~15 s)
python research/query.py           # list the saved queries
python research/query.py funding_oi_flush fz=-2 oz=2
python research/query.py --sql "SELECT event_type, count(*) FROM news_events GROUP BY 1"
```

## Studies

Each study's decision rule is written in its docstring before its first run. The results go to `docs/`.

| Script | Question | Result file |
|---|---|---|
| `study_altdata.py` | Which price and alternative-data features predict 5m–4h returns, add information beyond price, and pay after fees? | `docs/ALTDATA_STUDY.json` |
| `study_ml.py` | Does ML (ridge, gradient boosting) beat the best single feature out of sample (walk-forward)? | `docs/ML_VS_SIMPLE_STUDY.json` |
| `study_squeeze.py` | The operator's hypothesis: negative funding + OI jump + fast drop → bounce? | `docs/SQUEEZE_STUDY.json` |
| `news_events.py` | News → de-duplicated, typed events → do any event types move our coins? | `docs/NEWS_EVENT_STUDY.json` |

## Store tables

| Table | Contents |
|---|---|
| `candles_1m` | Bybit 1m bars (30 coins) |
| `oi_5m` | Bybit open interest, 5-minute |
| `ls_5m` | Bybit long/short account ratio, 5-minute |
| `premium_5m` | Bybit premium index, 5-minute |
| `funding` | Bybit funding, settled |
| `taker_5m` | Binance 5m taker-buy volume |
| `features_5m` | The feature panel, with forward returns |
| `paper_trades` | The live paper bots' closed trades, with entry spread |
| `news_items` | Raw news headlines and announcements |
| `news_events` | De-duplicated events: type, sentiment, importance, reliability, BTC relevance |

Liquidations and order-book depth have no public history on either venue. They can only be studied after a forward
collector has run for a while (see `docs/SYSTEM_REVIEW.md`).

## Rules

- Never add a feature, filter or model to a trading bot because one query looked good.
  - It must pass a pre-registered test: both halves, day-clustered statistics, net of costs.
  - Then it runs as a NEW frozen paper experiment.
- Research never edits production code.
