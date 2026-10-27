# lit_scout: literature shortlist (2026-10-26)

Scope: crypto anomalies with some published out-of-sample or robustness evidence that are NOT in the brief's
"already tried" list and NOT being tested by other round-1 agents (meta-allocator, information discreteness,
residual reversal, options IV timing, token unlocks). Ranked by (evidence quality) x (chance of surviving
0.07%/side + funding at 1x on Binance USD-M perps from 500 USDT) x (free, point-in-time data available).

| # | Candidate | Evidence | Why it might survive costs at 1x | Main risks | Data |
|---|---|---|---|---|---|
| 1 | **Stablecoin supply growth as a market timer** (long a liquid basket only while total stablecoin float is expanding) | Griffin & Shams (2020, J. Finance, "Is Bitcoin Really Untethered?"): Tether flows precede BTC price moves; Ante, Fiedler & Strehle (2021, Finance Research Letters, "The influence of stablecoin issuances on cryptocurrency markets"): positive abnormal returns around issuances; CryptoQuant research (2024-25): float growth led the 2021 and 2024-25 rallies (practitioner, no methodology) | Time-series timer, very few switches -> negligible turnover cost; sidesteps 2022-style bears when float shrinks (UST/BUSD) | TEST 2025-26 float grew almost every day -> timer = always long = plain beta; effect may be coincident, not leading | DefiLlama `stablecoins.llama.fi/stablecoincharts/all` (free, keyless; on-chain supply, so point-in-time with a 1-day lag) |
| 2 | **Turnover-volatility / liquidity factor** (long coins with stable dollar volume, short coins with erratic volume) | Mercik, Zaremba & Demir (2026, IRFA, "Crypto Factor Zoo", 2018-07..2024-07): turnover volatility and bid-ask spread are among the 2-3 factors that absorb all alphas of 36 factors; liquidity variables dominate across periods | Cross-sectional, weekly rebalance, liquid top-40 universe; different input (volume, not price) from the tried low-vol/beta factors | Authors note limited temporal persistence of selected factors; no market cap on Binance, so turnover must be proxied by dollar volume; may load on low-vol | Cached daily panel (QV) |
| 3 | CTREND (ML-aggregated price+volume trend signals) | Fieberg, Liedtke, Poddig, Walker & Zaremba (2025, JFQA): survives costs, persists in large liquid coins, across subperiods | Published claim of cost survival in big coins | Close cousin of the tried ML walk-forward + momentum families (W1 already holds 60d momentum + ML) | Cached panel |
| 4 | Cross-asset risk-on gate (S&P 500 / Nasdaq trend from FRED as crypto timer) | Weak: Park & Lee (2023, East Asian Econ. Review) find DXY/gold/oil insignificant OOS; Sanhueza (2026 thesis) finds macro predictors only episodic | Few switches, cheap; crypto-equity correlation rose after 2020 | Evidence mostly negative OOS; regime gates on rotation already failed in round 3 | FRED CSV `fredgraph.csv?id=SP500` (keyless) |
| 5 | Negative abnormal-volume (disagreement) effect | Garfinkel, Hsiao & Hu (2025, Financial Management): high abnormal volume -> lower returns, Miller (1977) | Shortable via perps | The paper itself finds it disappears once margin/short selling is enabled, i.e. exactly our perp universe; overlaps the tried attention-shock family | Cached panel |
| 6 | Quarter-hour opening order-imbalance drift | arXiv 2607.09426 (2026): opening returns predictable out of sample on 6 Binance perps, imbalance predicts 4-12 h | Binance perps, liquid coins | Needs trade-level data; close to the tried taker-flow and intraday families; magnitude/costs not reported | Binance aggTrades archive (heavy) |
| 7 | Skewness / co-skewness risk | IRFA 2024 "Skewness risk and the cross-section of cryptocurrency returns" (abstract not verified) | Weekly rebalance | Overlaps MAX/lottery (tried, 0/8) | Cached panel |
| 8 | Illiquidity (Amihud) premium | Zhang & Li (2023, IJFE); USQ four-factor model (2014-22) | - | Fieberg, Liedtke & Zaremba (2024): size/volume anomalies live in micro caps of negligible economic importance -> not tradable in a top-40 perp universe | Cached panel |
| - | Not ranked: BTC-dominance rotation (no academic OOS evidence, only practitioner blogs; also near the failed round-3 regime gates); network value ratios / new-address-to-price (needs per-coin on-chain data, not free) | | | | |

Sources: Griffin & Shams 2020 JF; Ante, Fiedler, Strehle 2021 FRL (https://www.blockchainresearchlab.org/portfolio/the-influence-of-stablecoin-issuances-on-cryptocurrency-markets/);
Mercik, Zaremba, Demir 2026 IRFA (https://open.icm.edu.pl/items/3411a3ad-2f53-4a74-9ff4-f5676afa18c9);
Fieberg et al. 2025 JFQA (https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/trend-factor-for-the-cross-section-of-cryptocurrency-returns/4C1509ACBA33D5DCAF0AC24379148178);
Park & Lee 2023 (https://ideas.repec.org/a/ris/eaerev/0423.html); Garfinkel, Hsiao, Hu 2025 (https://ideas.repec.org/a/bla/finmgt/v54y2025i3p633-672.html);
arXiv 2607.09426 (https://arxiv.org/abs/2607.09426); Zhang & Li 2023 (https://ideas.repec.org/a/wly/ijfiec/v28y2023i1p472-492.html);
Fieberg, Liedtke, Zaremba 2024 "Cryptocurrency anomalies and economic constraints" (https://open.icm.edu.pl/handle/123456789/25544).

**Tested this round:** #1 (stablecoin timer) and #2 (turnover-volatility factor), each pre-registered from DEV in PREREG.md.
