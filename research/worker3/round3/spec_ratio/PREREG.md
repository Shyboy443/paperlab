# PREREG: spec_ratio (Worker 3, round 3). Written 2026-10-26, AFTER the DEV grid and BEFORE any TEST run.

## Family and sources
Speculation intensity = derivatives activity relative to spot activity, per coin:
ratio = Binance USD-M perp quote volume / Binance SPOT quote volume (USDT+BUSD+FDUSD+USDC+TUSD pairs summed).
Hypotheses (sign left to DEV, rule fixed below):
- "Overpricing" (sign -1): leveraged speculators with short-sale frictions on spot push prices above value; the
  closest published analogue is Johnson & So (2012), "The option to stock volume ratio and future returns", JFE 106(2)
  262-286 (https://ideas.repec.org/a/eee/jfinec/v106y2012i2p262-286.html): low-O/S stocks beat high-O/S by 0.34%/week;
  Ge, Lin & Pearson (2016) JFE 120(3) follow-up; practitioner view that perp-led (not spot-led) rallies fade
  (Kaiko 2023 on BTC perp/spot volume, via https://www.business-standard.com/amp/markets/cryptocurrency/bitcoin-perpetuals-becoming-an-even-bigger-driver-of-digital-token-s-price-123050501173_1.html;
  CryptoQuant 2026 via https://forklog.com/en/cryptoquant-labels-april-bitcoin-surge-as-speculative/;
  CoinGecko/CryptoScores derivatives/spot metric "speculative betting rather than actual purchases",
  https://www.cryptoscores.com/docs/liquidity/derivatives-spot).
- "Speculative demand / momentum" (sign +1): perpetuals are where strong directional beliefs go
  (He, Manela, Ross & von Wachter, "Fundamentals of Perpetual Futures", https://arxiv.org/pdf/2212.06888); futures
  returns follow lagged futures volume (https://businessperspectives.org/journals/investment-management-and-financial-innovations/issue-360/are-bitcoin-futures-contracts-for-hedging-or-speculation);
  Chi, Hao, Hu & Ran: futures basis (a demand measure) is the strongest cross-sectional crypto-futures signal
  (https://acfr.aut.ac.nz/__data/assets/pdf_file/0010/686755/6b-Yeguang-Chi.pdf).
I found NO published test of the perp/spot VOLUME ratio as a crypto cross-sectional predictor; evidence is by analogy.

## Data
- Perp volume: survivorship-free daily panel (`research/monthly20/r2_rotation/data.py`, incl. delisted perps).
- Spot volume: data.binance.vision spot monthly 1d klines (+ public REST klines for 2026-10), `fetch_spot.py`, cache
  `data/w3r3_spec_ratio/spot/`. All 531 perps ever in the point-in-time top-60 were mapped; 407 have a spot market
  (1000x / 1M prefixes stripped, LUNA2->LUNA by date), 1,148 spot pairs incl. delisted ones (e.g. old LUNA, SRM, BUSD pairs).
  Same-asset check (median |log(spot close / perp close)| <= 0.1) rejects 5 mismatched names: 1MBABYDOGE, FTT, RAY, SC, STRAX.
- Coins with no spot market, or < 80% of spot days in the window, get no score and are never traded.
  Optional OI/spot variant was NOT run (OI history only covers a subset of coins/dates).

## Engine / costs
Agent-02 engine `engine_r2.run` unchanged: score from days <= d-1, market fill at day d 00:00 open, fee 0.05% + slip
0.02% per side, funding, 1% volume cap, 1x gross, 50% long / 50% short, equal weight. Doubled-cost runs too.

## DEV grid (2021-01..2024-10 only), `dev_grid.py` -> `dev_grid.json` / `dev_grid.log` (48 rows, all kept)
kind {lvl = log ratio over L days, chg = lvl(L) minus lvl over the preceding 4L days} x L {7,14,30} x sign {+1,-1}
x k {5,10} x univ {top-40, top-60}, weekly rebalance, LS.
Findings: NO config has DEV t >= 2. Best |t|: chg14 sign-1 k10 top-40 t 1.50 (geo +1.45%); lvl rows with sign -1
(the literature's "overpricing" direction) are all negative (geo -0.7..-3.3%), i.e. high-ratio coins did NOT
underperform in DEV. Pooled median t: sign +1 0.11, sign -1 -0.37 -> sign +1 by the rule.

## Selection rule (fixed in dev_grid.py before it ran)
1. sign = the one with higher pooled median DEV t; 2. within it: DEV geo > 0, DEV net > 0 at 2x cost, DEV DD <= 40%,
DEV daily corr with Worker 1 < 0.5; highest DEV t.

## THE ONE selected config
**lvl, L = 7, sign +1, k = 10, LS, weekly rebalance, top-40, 1x.** Every 7th day at 00:00 UTC, among point-in-time
top-40 perps that have a spot market: score = log(sum of last 7 days' perp quote volume / sum of last 7 days' spot
quote volume). Long the 10 highest ratios, short the 10 lowest.
DEV: median -0.01%, geo +0.58%, mean +0.90%, 23/47 up, DD 33.1%, t 0.75, net +31.4% (2x cost +20.9%), corr W1 -0.05.
Honest prior: DEV is near zero; a PASS would require TEST ~10x DEV. Expected verdict FAIL; TEST run only because the
protocol requires the one pre-registered run.

## Checks (exactly ROBUSTNESS.md)
- Check 5 neighbours (each changed alone, 16): L {4, 5, 9, 11}; k {5, 8, 12, 15}; rebal {1, 3, 5, 14}; univ {20, 30, 50, 60}.
- Check 6: 500 random books 10/10, weekly, top-40 restricted to coins with a valid ratio score, same costs.
- Check 7: drop the top-5 profit coins and rerun; max single position <= 10% of total profit.
- Also W1/W2 monthly correlation, 2x-cost 2025-01..2026-10, 2026 YTD, Oct 2026, 500 USDT. TEST run ONCE by `run.py`.
Configs looked at in this family: 48 DEV rows (+ 16 neighbours inside run.py for check 5 only).
