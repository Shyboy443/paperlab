# options_iv: pre-registration (written 2026-10-26 BEFORE any TEST run)

## Idea and sources
Time BTCUSDT / ETHUSDT perps with Deribit's DVOL (30-day implied-vol index, public API
`/api/v2/public/get_volatility_index_data`, daily candles from 2021-03-24, cached in `data/w3r1_options_iv/`).
- Crypto shows an *inverse leverage effect*: volatility rises together with price, and the Bitcoin return premium rises
  after volatility rises (Risk Premia in the Bitcoin Market, arXiv 2410.15195; Pricing Cryptocurrency Options,
  arXiv 2009.11007; Catania and Grassi 2017 as cited in the literature). So a strong rise in implied vol is read as
  "demand for upside / momentum regime" (long) and a strong fall as "deflating, risk-off" (short).
- Variance risk premium literature (Alexander and Imeraj, "The Bitcoin VIX and its Variance Risk Premium", J. Alt. Inv.;
  arXiv 2410.15195) motivated the VRP family; DVOL-as-predictor blog evidence (unexpectedcorrelations.substack.com) is
  anecdotal only.
- 25-delta skew and term structure were wanted, but Deribit's public API has no free history of them (only live
  snapshots), so they cannot be backtested point-in-time and are NOT used.

## Mechanics (core.py)
- Decision at the end of UTC day d using DVOL close of day d (stamped 00:00 d+1) and the perp close of day d.
- Executed at the 01:00 UTC open of day d+1 (one hour after the DVOL close), held to 01:00 the next day.
- 50% of equity per coin (BTC, ETH), position in {-1, 0, +1} x 50%, total 1x. Costs 0.05% fee + 0.02% slip per side on
  every traded notional (including daily drift rebalancing), Binance funding paid/received; doubled-cost run too.
- Days before DVOL exists (2021-01-01..03-24 plus the look-back) are flat.

## DEV grid (DEV = 2021-01..2024-10 only; 177 configs, all in dev_grid.json; 75 had positive DEV geo)
- `vrp`: z-score of (DVOL - 30d realized vol), zn in {60,90,180}, th in {0,0.5,1}, short on/off (18; 18 positive).
- `ivchg`: k-day DVOL change, k in {1,3,7,14}, th in {0,5%,10%}, short on/off, direct/contra (48; 23 positive).
- `ivlvl`: DVOL z-score level, zn in {90,180,365}, th in {0,0.5,1}, short on/off, direct/contra (36; 14 positive).
- `trend_iv`: price vs SMA{20,50,100} gated by VRP or IV z (75; 20 positive) + plain-trend references.
Selection rule (fixed): highest DEV t-stat of monthly mean.

## THE ONE selected config
`ivchg, k=7, th=0.10, short=True, contra=1`:
each day, for BTC and ETH separately, ch = DVOL_coin(d) / DVOL_coin(d-7) - 1;
ch > +10% -> long 50%; ch < -10% -> short 50%; otherwise flat.
DEV 1x: median +1.24%, geo +3.37%, mean +3.70%, 25/46 months up, max DD 37.6%, t 3.00, 5.4 trades/month, geo at 2x cost +2.97%.
(Already known from DEV: DD 37.6% > 23%, so bar (c) is likely to fail; reported anyway.)

## Neighbour grid for check 5 (one parameter at a time, baseline costs, DEV and TEST)
k in {4, 5, 9, 11} (about -50/-25/+25/+50%); th in {0.05, 0.075, 0.125, 0.15}; decision every 2 or 3 days (hold=2,3).
10 neighbours; pass if >= 7 are net positive on both DEV and TEST.

## Check adaptations (only 2 coins)
- Check 6: 500 random versions = each coin's signal circularly shifted by a random offset (same number of long/short/flat
  days, same turnover, same costs, random timing). Must beat 95%.
- Check 7: "remove top 5 coins" is impossible with 2 coins; replaced by: each coin alone net positive over the full
  period (i.e. still positive after removing the best coin) AND no single position (coin, holding spell) > 10% of profit.
- Check 2: 2021 is partial (DVOL starts 2021-03-24).
TEST (2024-11-01..2026-10-21) is run ONCE by run.py after this file is written.
