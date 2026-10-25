# Agent 08 - statistical arbitrage - pre-registration (written before any TEST run)

Written 2026-10-23 after DEV work (2021-01..2024-10) only. TEST (2024-11-01..2026-10-21) has not been run.
Code: `engine.py` (data, simulator), `strategies.py` (signals), `run.py` (this protocol). Not to be edited after TEST.

## Universe (fixed)
13 Binance USDT perps: BTC, ETH, SOL, ADA, AVAX, DOT, NEAR, ATOM, BNB, XRP, LTC, LINK, DOGE (1h bars, funding).
A coin is used only on bars where it has data. Today's listing only (survivorship bias: all 13 survived).

## Common rules
- Signals from closed 1h bars up to bar t; filled at open of bar t+1; P&L open-to-open.
- Factor model: OLS (with intercept) of each alt's 1h log return on BTC and ETH 1h log returns over the past
  720 h, refit every 24 h, past data only. Residual = alt return - betas x factor returns.
- Costs 0.05% fee + 0.02% slippage per side on every leg's traded notional; funding paid/received by each leg at
  every stamp; every result rerun with costs doubled.
- Leverage L: target notional = equity x L x weight. Isolated-margin liquidation per leg: a leg whose adverse
  bar excursion (high/low vs open) reaches 1/L - 0.5% is closed and loses its margin (notional / L).
  Account wiped at equity <= 0. Each leg's notional is capped at 1% of the previous bar's quote volume.
  Start equity 10,000 USDT, fresh at the start of TEST.

## Attempts that go to TEST (all reported)
1. **D_shock_both_h24_s5** (main candidate). Residual z = residual sum / 720h std of 1h residuals / sqrt(look).
   Event: 4h residual z > +4 (continuation) OR 1h residual z < -4 (bounce). On an event: LONG the alt with 1/5 of
   equity (alt notional), SHORT BTC and ETH by beta x that notional; hold 24 h (a new event restarts the clock).
   Total alt notional capped at 1x equity. Rebalanced hourly to these targets.
   Run at **L = 1** and at **L = 5** (5 = 20 / DEV mean month 3.93, rounded: the leverage the 20% target needs).
   PASS is judged on the L = 5 run.
2. **A_resid_cs_look72_rebal24**: cross-sectional z of 72h residual sums; weights = -(z - mean); BTC/ETH legs
   cancel the factor betas; gross normalised to 1; rebalanced every 24 h. L = 1.
3. **B_pairs_zw168_e2.5_all**: sector pairs (L1: SOL ADA AVAX DOT NEAR ATOM; payments: LTC XRP).
   Spread = log A - beta log B (beta rolling OLS on 720h log prices, refit daily); z vs past 168h;
   enter |z| > 2.5 against the spread, exit |z| < 0.5 or z crosses 0 or |z| > 4 (stop; then wait until |z| < 2.5)
   or 240 h. Legs 1/(1+beta) and beta/(1+beta); capital split equally across all tradeable pairs. L = 1.
4. **D2_basket_both_h12_s5** (same events as 1, but hedged with an equal-weight short basket of the other alts). L = 1.

## DEV evidence that matters (honest prior)
- All shock profits on DEV come from 2021 (alt season): D_shock_both_h24_s5 mean 14.6%/month in 2021 but
  0.18%/month in 2022-24. The BTC/ETH-hedged "always long every alt" control made 46.7%/month in 2021 and
  -0.5% in 2022-24, so most of the edge is alt-vs-BTC drift, not reversion. Expect FAIL.
- Hourly residual reversal has a raw (zero-cost) edge of ~+8%/month at 1x but turnover costs ~33%/month: dead.
- Lead-lag: BTC's last hour predicts a small residual REVERSAL in alts next hour (corr about -0.03 to -0.05);
  trading it loses after costs on DEV.

## Verdict rule (from PROTOCOL.md)
PASS only if attempt 1 at L = 5 has TEST mean month >= +20% at baseline costs, mean > 0 at doubled costs,
no wipe-out, and no single holding period makes more than half the profit.
Post-hoc leverage sweeps on TEST (L = 2, 10, 20) are diagnostics only, not attempts that can pass.
