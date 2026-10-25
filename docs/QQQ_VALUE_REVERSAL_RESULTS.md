> Archived v1 approximation, superseded by [the corrected published-source experiment](QQQ_VALUE_REVERSAL_V2.md). Its results do not evaluate the source-matched v2 rules.

# QQQ video-strategy evaluation — FAIL

The approved instrument is **QQQ on Alpaca**, adapting the video's Nasdaq futures idea to
regular stock-market hours. The default was fixed as 15-minute candles with the previous
session's 70% value area. No configuration was selected after seeing its returns.

| Default experiment | 2022–2023 | 2024–2025 |
|---|---:|---:|
| Mean monthly net return | −0.0087% | **−0.0345%** |
| Total net return | −0.2318% | **−0.8279%** |
| Maximum drawdown | 2.4802% | **1.4878%** |
| Completed trades | 20 | **14** |
| Total return with doubled costs | −1.1233% | **−1.5829%** |

For 2024–2025: annualized return −0.4155%, Sharpe −0.6529, worst month −0.2851%,
and 20.83% of months positive. These are observed historical returns, **not an expected
future income estimate**. The sample is too small to estimate sustainable profitability.

All four fixed variants were evaluated with base and doubled costs. The 30-minute developing
variant returned +0.2464% in 2024–2025, but had only nine trades and lost −0.2780% with doubled
costs. That is not sufficient evidence to promote it. The default fails because its net return
is negative, doubled costs worsen the loss, and it has fewer than 30 comparison trades.

The biggest limitation is translating actual volume-at-price and order-flow absorption into
15-minute OHLCV candles. The implementation spreads volume across the candle range and uses
direction/engulfing as proxies. Stops, gap handling and conservative sizing constrain modeled
losses; they do not fix an unproven signal or guarantee broker execution.

Alpaca's consolidated SIP history supplies volume across exchanges, with a 16-minute availability
allowance. Forward books are explicitly **delayed-feed candle simulations**, not live orders.
Startup history creates no forward fills. Each independent virtual book starts at $10,000 and
persists on Railway. Prior research has already observed these years, so 2024–2025 is a
retrospective comparison rather than newly unseen evidence.

Full methods: [QQQ_VALUE_REVERSAL.md](QQQ_VALUE_REVERSAL.md). Reproduce from the repository with
`python scripts/qqq_value_reversal.py`; it uses cached data or downloads with Alpaca paper
credentials. The hosted page provides equity/drawdown charts, all configurations, monthly
returns, the complete trade ledger and a downloadable JSON report.

Sources: [video](https://www.youtube.com/watch?v=dUczefIYKIU),
[Alpaca data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq).
