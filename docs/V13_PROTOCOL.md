# V13 SNAPBACK: limit-order mean reversion (paper)

The operator asked for it on 2026-10-03: "yes build the limit order reversal bot".

## Why this bot

`docs/SYSTEM_REVIEW.md` and `docs/ALTDATA_STUDY.json` covered 30 Bybit perps over 99 days.

- **The one robust effect is short-term mean reversion.** It shows at every horizon, with |t| between 5 and 13.
- **Market orders cannot capture it.** It is worth only 0–5 bp per trade, while a market-order round trip costs about
  11 bp.
- **Every taker bot loses.** The fees alone are 0.13–0.20 R per trade (V8, V11).

V13 attacks the cost side. It never buys at market. It rests a post-only limit order a little beyond a stretched coin
and pays the maker fee (0.020% vs 0.055%).

## The rule (`app/strategies/v13/snapback.py`)

**Universe:** the V11 universe, 29 alts. BTC is the feed's anchor only, because a 20 USDT book cannot trade it.

**Every 5 minutes, for every coin:**

```
stretch = ln(close / VWAP_24h) / (24 h volatility of 5m returns x sqrt(288))      (daily-volatility units)
|stretch| >= threshold  ->  a set-up against the stretch (long below the VWAP, short above)
```

**Orders:**
- The most stretched coins go first: at most 4 orders per decision, and one order or position per coin.
- **Entry:** a POST-ONLY limit `offset_atr` × ATR(5m, 14) beyond the close. It lives 15 minutes, then is cancelled.
- **Stop:** max(1.0%, 2.5 ATR / entry). A set-up that needs more than 3% is skipped.
- **Target:** `target_r` × the stop distance, resting as a limit (maker).
- **Time limit:** `max_hold_min`, closed at market.

**Book:**
- 20 USDT.
- SizingV6 tiers: 1% base risk, legal Bybit minimums, a hard cap of 2%.
- Up to 4 positions.

## The engine (`app/live/v13_engine.py`)

A new subclass of the V11 scan engine; no frozen module changed.

| Step | Behaviour |
|---|---|
| Placement | The order reaches the book at the first 1m bar after the 60 s decision window. If the price is already through the limit there, a post-only order would cross, so it is **rejected** and the trade is missed, never bought at market. |
| Fill | Only when the price trades **through** the limit by 0.5 bp (a touch is not a fill). The fill is at the limit, maker fee, no spread or slippage. A gap fills at the limit. |
| Expiry | An order unfilled after 15 minutes is cancelled. |
| Slots | When an order fills, its coin must still be free and the book under 4 positions, or the order is cancelled. |
| Fill bar | Exits are walked from the fill: limit → adverse extreme → favourable extreme → close. A stop on the same bar's dip is hit; a target before the fill is not. |

**The price of maker entries is adverse selection.** The order fills exactly when the price keeps going the wrong way,
and it misses the cleanest snaps. The study prices this in, because it replays this exact class through this exact
engine.

## Pre-registered study (`scripts/v13_snapback_study.py`, `docs/V13_SNAPBACK_STUDY.json`)

The full rules are in the script's docstring, written before its first run. In short:

- **Grid:** 54 variants.

  | Parameter | Values |
  |---|---|
  | threshold | 0.80, 0.95, 1.10 |
  | offset_atr | 0.25, 0.50, 1.00 |
  | target_r | 0.75, 1.0, 1.5 |
  | hold | 2 h, 4 h |

- **Data:** 90 days on the V11 cache. DEV is the first half, TEST the second.
- **Selection** uses DEV only: the highest DEV net R per trade, smoothed over grid neighbours, among variants with
  ≥ 150 DEV trades.
- **Verdict on TEST, once:**

  | Verdict | Condition |
  |---|---|
  | PASS | net R > 0, day-clustered t ≥ 2, PF ≥ 1.10, ≥ 100 trades |
  | WEAK | net R > 0 but another condition fails |
  | FAIL | net R ≤ 0 |

- **The forward bot runs the DEV-selected variant whatever the verdict,** because the operator asked for the bot. The
  verdict is its label. A paper result alone never takes it live.

### Result: FAIL (2026-10-03)

**Selected on DEV** (smoothed DEV net R +0.028): threshold 1.10, offset 0.5 ATR, target 0.75 R, hold 4 h.

| Half | Trades | Win | Net R per trade | PF | Fees per trade | Net |
|---|---|---|---|---|---|---|
| DEV | 320 | 60% | +0.022 (day t 3.1) | 1.06 | 0.046 R | +0.95 USDT |
| TEST | 421 | 53% | **−0.089** | 0.81 | 0.039 R | −6.45 USDT |

- **All 54 variants lost on TEST**, between −0.02 and −0.13 R per trade. DEV had 22 positive variants, the best +0.046 R.
- **Diagnostics for the selected variant:**
  - TAKER (the same set-ups bought at market): DEV −0.037 R, TEST −0.083 R, PF 0.65. The maker entry was worth about
    +0.06 R per trade on DEV and nothing on TEST.
  - TOUCH (limits filled on a touch): identical to the trade-through rule. The result does not hinge on the 0.5 bp
    fill rule.
- **Reading:** the cost attack worked; fees fell from 0.13–0.20 R to about 0.04 R per trade. But in the second half
  the stretched coins kept going (adverse selection plus regime), and the set-ups lost before fees.
- **Engine bug found while running the study.** End-of-replay closes on a multi-coin tape used BTC's bar. It is fixed
  in `v13_engine.py`, and a re-check task for the older V11 studies was raised. The verdict above is from the
  corrected run.

**Decision (pre-registered):** the forward paper bot runs the selected variant, labelled FAIL. Freeze
`6167bc184aeda80a`.

## Program

| Item | Value |
|---|---|
| Files | `app/competition/v13_config.py`, `app/live/v13_worker.py`, `scripts/v13_freeze.py`, `docs/V13_FREEZE.json` |
| Database | `v13-forward.db` |
| Switch | `V13_FORWARD_ENABLED` |
| Field | one CONTROL bot, `V13.1-SNAP`, named **Bounce**. No Jev twin: no Jev gate has beaten chance |
| QUALIFIED | far stricter than V8 / V11: ≥ 14 days, ≥ 150 trades, net > 0, PF ≥ 1.2, max drawdown ≤ 20% |
| Elimination | never |
| Live mirror | refuses it (it follows single-coin V8 bots only) |
