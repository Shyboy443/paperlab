# oi_flow: pre-registration (written 2026-10-25, before any TEST number was computed)

## Family
Open-interest change combined with price change, cross-sectional, top-40 point-in-time USDT perps (survivorship-free
daily panel of r2_rotation, delisted coins included). OI = `sum_open_interest` (contracts, last 5-minute snapshot of
the UTC day) from the Binance UM daily `metrics` archive, cache `data/monthly20_r4_positioning/daily` (read only, no
download). Contracts, not `sum_open_interest_value`, so the OI change is not mechanically the price change.

**Data coverage:** the metrics archive starts 2021-12-01 for every coin except BTC, so the book is flat (0%)
2021-01..2021-11. Effective DEV = 2021-12..2024-10 (35 months, not 46). The 70-month statistics count those 11 months as 0%.

## Rule
At the close of day i-1, for each universe coin: k-day log price return and k-day log OI change, ranked cross-sectionally.
Four regimes on the q-corners: UU (price top-q, OI top-q), DU (price bottom-q, OI top-q), UD (price top-q, OI bottom-q),
DD (price bottom-q, OI bottom-q). Each regime gets a sign (+1 long / -1 short / 0). Long book 0.5 and short book 0.5 of
equity, inside each book equal risk (weight proportional to 1/vol20). A new tranche (1/h of equity) is opened every day at
the open of day i and held h days (h overlapping tranches, weights frozen at entry). Days with no long or no short coin:
that tranche is flat. 1x gross. Costs 0.05% fee + 0.02% slippage per side on traded notional, funding included, size
cap 1% of the previous day's quote volume (never binds at 500 USDT). Engine: r4_03 `engine.py` (read-only import).

## DEV work (all on 2021-12..2024-10 only)
1. Event study (`dev_scan.py`, `dev_scan.log`): forward 1-3 day XS return of each regime in equal-risk units, grid
   k in {1,2,3,5,7}, q in {0.2,0.3}, h in {1,2,3}. Finding: effects are tiny (a few % of one sigma, i.e. about the size
   of the round-trip cost). The one consistent sign: DU (price down while OI rises = new shorts) underperforms in all 30
   cells (t -0.3 to -3.9). The first-principles hypothesis that DU is "squeeze fuel" is wrong on DEV.
2. Books (`dev_books.py`, `dev_books.json`), 5 sign maps x 30 cells = 150 DEV runs:
   - hypothesis (long DU+DD, short UU+UD): **0/30 positive** (dead).
   - anti-hypothesis: 4/30; short DU/long DD: 7/30; short DU/long UD: 4/30;
   - **short DU / long UU (OI-confirmed momentum): 9/30**, all at k = 5 or 7 with h = 2-3; k <= 3 loses everywhere.
3. Control (`dev_control.py`): with k=7, q=0.2, h=3, price-only momentum loses (-20% DEV) and OI-only loses (-28%);
   the combination makes +118%. So the OI leg is doing work on DEV.
Caveat: the selected cell is the best of 150 DEV runs and its DEV t-stat is only 1.38 (DD 41%). Expect TEST to be weaker.

## The ONE selected config
`k = 7, q = 0.2, h = 3, signs = {UU: +1, DU: -1, UD: 0, DD: 0}`
= every day, long the coins whose 7-day price return AND 7-day OI change are both in the top 20% of the top-40 universe,
short the coins whose 7-day return is in the bottom 20% while their 7-day OI change is in the top 20%; hold 3 days.
DEV (2021-12..2024-10): net +117.9%, geometric month +1.71% (over 46 DEV months, 11 of them flat), max DD 41.0%,
t 1.38, doubled-cost net +61.3%.

## Neighbour grid for check 5 (DEV and TEST, baseline costs, each must be net positive on both)
- k (7): 4, 5, 9, 10   (~ -50%, -25%, +25%, +50%)
- q (0.2): 0.1, 0.15, 0.25, 0.3
- h / rebalance frequency (3 days): 1, 2, 4, 5
12 neighbours; pass needs >= 70% (9/12).

## Check 6 random
300 random versions: each day the same number of longs and shorts as the strategy, random coins among the same eligible
universe coins, same tranche/hold/sizing/costs. Must beat >= 95%.

## TEST
Run once with `python run.py` (2024-11-01..2026-10-21). No change after this file is written.
