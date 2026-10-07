# V12 BIZZY: beebots' Bizzy Bee, ported (2026-10-02)

The operator asked to add Bizzy from https://github.com/imikerussell/beebots ("refer how the bizzy logic was built, and
add that bot").

## How Bizzy is built in beebots

Sources:
- `src/bees/bizzy.ts` and `strategies/BIZZY_BEE.md`: her live rules since 2026-09-24.
- `src/market/data.ts` `breakoutLevels`: how the levels are computed.
- `src/config.ts`: her settings.

Her live rules, a Larry Williams volatility breakout:
- **Levels:** from 1h candles, today's UTC open and yesterday's full high−low range (≥ 20 hourly candles).
  **Trigger = open + 0.5 × range.**
- **Coins:** BTC, ETH, SOL and HYPE, in that order when several trigger together. Spread must be ≤ 5 bp.
- **Entry:** while no position is open, Jev sees a menu only when a coin is through its trigger: `BREAKOUT_<coin>` or
  `WAIT` ("not convinced, keep waiting"). With nothing triggered there is no question, which saves spend.
- **Size:** full size (`sizeFrac 1`), so the bee's book × `MAX_LEVERAGE` 2. **Long only.**
- **Stop:** today's open ("failed breakout: back below today's open").
- **Exit:** the UTC day close (1 minute before midnight). While holding, Jev may `HOLD`, or `CUT_LOSS` when losing.
- **Limits:** 1 trade a day, fee budget $1.00, never forced in, 5-minute cooldown.
- **History:** her earlier design, a Bollinger + RSI fade, is kept in the repo for reference only.

## The port

**Code:** `app/strategies/v12/bizzy.py`, `app/ai/jev/v12.py`, `app/competition/v12_config.py`, `app/live/v12_worker.py`.

**What carries over unchanged:**
- **Levels:** computed as beebots does, from the coin's 1h candles (today's first 1m candle during the first hour).
- **Entry:** decided on a 1m close above the trigger, on BTC's (the anchor's) minute, when every coin of that minute is
  in. The order fills at the first 1m open ≥ 60 s later, the same decision window as V11.
- **Size:** the whole book as margin at 2×, so notional = 2× equity (`margin_pct 1.0`, leverage ceiling 2, no risk
  tiers, no risk cap).
- **Stop:** the day's open, filled at its level (V11's level fills); a gap fills at the open.
- **Exit:** a time limit that ends at 23:59 UTC.
- **One trade a day:** the day counts once a position is OPEN. `BizzyEngine` tells the strategy when an entry fills.
  A declined or refused candidate is re-proposed after 5 minutes (beebots' cooldown) while the coin is still through
  its trigger.

**Differences from beebots:**
- **BTC:** not traded. A 20 USDT book at 2× is 40 USDT of notional, and Bybit's smallest BTC order (0.001 BTC) is about
  110 USDT. BTC stays the feed's anchor. With a bigger book BTC would be legal.
- **Fee rule:** PaperLab's general pre-check applies: a trade whose round-trip fee exceeds 25% of its risk is refused.
  This hits a trigger that sits barely above the open. It is then re-proposed 5 minutes later.
- **Jev:** asked once per candidate. "CONTRADICT" means WAIT; "SUPPORT" or "STRONGLY SUPPORT" means take it at full size.
  Jev does not manage the open position: beebots' `CUT_LOSS` is not ported.
- **Venue:** Bybit linear perps, at our cost model (0.055% taker each side + spread + funding). beebots runs on OKX.

**Field:** 2 bots:
- `V12.1-DAY`, **"Bizzy"**: rules only.
- `V12.1-DAY+JEV`, **"Bizzy AI"**: Jev says GO or WAIT.

Both use a 20 USDT paper book. They are never eliminated, and they are pinned on the home screen in their own section.

## Expectation: no edge

`scripts/v12_bizzy_study.py` → `docs/V12_BIZZY_STUDY.json`. Beebots' rules exactly, with Bybit costs:

| view | variant | trades | win | total |
|---|---|---|---|---|
| 95 days, 1m precision | BTC/ETH/SOL/HYPE | 59 | 36% | −20% |
| 95 days, 1m precision | ETH/SOL/HYPE (as run here) | 56 | 32% | −24% |
| ~2.5 years, 1h bars | BTC/ETH/SOL/HYPE | 589 | 35% | −99% |
| ~2.5 years, 1h bars | ETH/SOL/HYPE | 554 | 33% | −99% |

- **Single coins:** each coin alone was positive over the recent 95 days (ETH +21%, SOL +31%, HYPE +21%), but every
  coin lost over 2.5 years.
- **Port check:** the engine replays the 95 days with the same result as the study: 56 trades, 32.1% wins, 24 stops.
- **Why it runs anyway:** the operator asked for Bizzy. Live forward paper data is the judge.

## Freeze

Freeze `docs/V12_FREEZE.json` pins the V12 files and the V11 files it imports (`scan.py`, `scan_market.py`,
`scan_engine.py`, `v11_worker.py`). A change to any of them means a new V12 experiment.

Enable it with the Railway variables `V12_FORWARD_ENABLED=true` and `V12_FORWARD_JEV=true`; the database is
`v12-forward.db`.
