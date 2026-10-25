# PREREG: Worker 2 improvement round (written 2026-10-25, before any variant was run)

## Why
The user asked to improve or replace Worker 2 after the paper book showed -12.7% in October 2026.
The server book matches the research for Jul-Sep 2026 (+2.8/+6.3/+2.4 vs research +2.5/+8.8/+3.2), and the research
also has October 2026 negative (-7.7% to 21 Oct). The worst research month is -17.4% and 20 of 70 months lose.
So October is inside the normal range and is not by itself evidence that the rule broke. No variant is chosen
because it would have avoided October.

## What was looked at before writing this file
Only `diag.py` on DEV (2021-01..2024-10): every bucket (side, weekday, signals per day, BTC direction, BTC 30-day
trend x side) is net positive. Two weak spots:
- days with 8+ signals: 1,074 trades, mean trade +0.33% (other days +0.75..+2.96%). Those are market-wide moves,
  not coin-specific ones.
- shorts while BTC's 30-day trend is up: mean trade -0.38% (sum still +22% of equity because of sizing).

## The two variants (all else = rule P: H=16, k=2, exit 02:00, K=5, W=480, sig_ref 1.154%, costs, funding)
- **R, residual momentum.** The z-score uses the coin's move after removing BTC's part:
  res = log(C15/O0)_coin - beta * log(C15/O0)_BTC, z = res / (sigma_res * 4), where beta and sigma_res come from the
  previous 480 hourly open-to-open returns (same window and timing as sigma). Sizes still use rule P on the raw sigma.
  First principle: a coin moving on its own news keeps going; a coin just riding a BTC move does not.
- **L, no shorts against a BTC uptrend.** Skip a short when log(BTC open today / BTC open 30 days ago) > 0.
  First principle: shorting alts against a rising market fights the drift. Weaker: it comes straight from the DEV diag.

## Adoption rule (decided now)
A variant replaces P only if ALL of these hold:
1. It passes all 8 ROBUSTNESS checks (same code as session/run.py, its neighbours get the same change).
2. DEV median month >= P's (6.88) and TEST median month >= P's (2.22).
3. TEST geo month >= P's (3.02) and TEST max DD <= P's (17.3%) + 3 points.
If both qualify, take the one with the higher TEST median. If neither does, P stays and nothing is redeployed.
Caveat: TEST has been seen before (r4 round + session round), so any pass is weaker than a first look.
