# Pre-registration: tug_of_war (Worker 3 round 3), written 2026-10-26 BEFORE any TEST run

## Idea and sources
Lou, Polk & Skouras (2019, JFE 134(1), "A tug of war: Overnight versus intraday expected returns",
https://personal.lse.ac.uk/loud/ATugofWar_appendix.pdf, https://conference.nber.org/confer/2015/APf15/Lou_Polk_Skouras.pdf):
in US stocks, momentum profits come from the overnight return component while the intraday component reverses.
Clienteles that trade in different hours push prices in opposite directions. Crypto trades 24/7, so the analogue
splits each coin's return by trader geography instead of by the exchange's open and close.
Crypto evidence (indirect; no direct cross-sectional replication was found):
- Wen, Bouri, Xu & Zhao (2022, NAJEF 62) find both intraday momentum and reversal in BTC hourly returns
  (https://ideas.repec.org/a/eee/ecofin/v62y2022ics1062940822000833.html).
- An MDPI JRFM (2026) paper studies lagged momentum and reversal in BTC/ETH across daytime and overnight sessions
  (https://www.mdpi.com/1911-8074/19/9/692).
- A Springer RQFA paper, "The crypto world trades at tea time", finds session-dependent intraday return patterns across exchanges
  (https://link.springer.com/article/10.1007/s11156-024-01304-1).
- An arXiv preprint (2608.21888) finds short-horizon reversal in crypto at the 1h horizon, strongest in active hours.
- A Galaxy Research note (via ChainCatcher) reports that BTC's 2025 gains came in the Asia session while Europe was a drag
  (https://www.chaincatcher.com/en/article/2297951).
This is a cross-sectional sort on session-split past returns. It is not Worker 2's market-level 16:00->02:00 trade.

## Data and engine
- Data: the survivorship-free hourly panel (`research/monthly20/r4_09_session/hpanel.py`; coins ever in the top-40, incl. delisted).
- Sessions (UTC, fixed before any run): A = Asia 00-08, E = Europe 08-14, U = US 14-24; T = full day.
- Session return: log(open at session end / open at session start) from hourly opens.
- Score at day i uses days i-L..i-1 only.
- 'X-Y' signal: cross-sectional z of cum_X minus z of cum_Y, taken over hourly-panel top-40 members.
- Engine: the unchanged agent-02 rotation engine (`engine_r2.run`). Fill at the day-open price, fee 0.05% + slip 0.02% per
  side, funding charged, 1% of volume cap, 1x, k long / k short equal weight, top-40 point-in-time universe.

## DEV grid (2021-01..2024-10 only): `dev_grid.py`, `dev_grid.json`, `dev_grid.log`
- 9 signals {A, E, U, A-E, A-U, E-U, A-T, E-T, U-T} x sign {mom, rev} x L {7,14,30,60} x k {5,10} x rebal {1,7}
  = 288 session configs, plus 16 total-momentum reference rows. **304 configs were looked at.**
- Selection rule (fixed in `dev_grid.py` before it ran): DEV geo > 0, daily corr with W1 < 0.5, corr with same-parameter total
  momentum < 0.7, DEV DD <= 40%, DEV net > 0 at 2x costs; take the highest DEV t.
- DEV family summary: E-T reversal was positive in 16/16 configs (mean DEV geo +1.55%/month).
  - U momentum: 13/16. A momentum: 13/16.
  - Every plain 'rev' of A/E/U: 0/16 (no crypto analogue of the intraday-reversal leg).

## THE selected config (run on TEST once)
**sig = E-T, sign = -1 (reversal), L = 14 days, k = 5 long / 5 short, rebalance every 7 days, top-40, 1x.**
In plain words: each week, go long the 5 coins whose last-14-day Europe-session return is weakest relative to their total
14-day return, and short the 5 where Europe did most of the work.
That is total momentum earned outside European hours, minus momentum earned in European hours (the tug-of-war split).

DEV numbers: median +4.53%, geo +3.65%, mean +4.23%, 30/46 months up, max DD 29.2%, t 2.60, DEV net at 2x cost +348%,
corr W1 0.30, corr total-momentum 0.57.

## Neighbour grid for check 5 (each varied alone; net > 0 on both DEV and TEST at baseline costs)
- L: 7, 10, 18, 21.
- k: 3, 4, 6, 8.
- rebal: 1, 3, 5, 10.
- universe: top-20, 30, 50, 60. The z-score is taken over the hourly-panel top-40 members, so for top-50/60 any coin outside the
  top-40 has no score.

## Other checks
As `research/monthly20/ROBUSTNESS.md`, with the same implementation as round-2 high52 `run.py`:
- check 6: 500 random-score draws, daily re-drawn and 30-day blocks; must beat 95% in both;
- check 7: drop the 5 top-profit coins from the panel, and no single position may hold more than 10% of profit.
Correlations with W1/W2 are monthly, plus daily with W1. The W1+W2+X thirds portfolio is reported.
