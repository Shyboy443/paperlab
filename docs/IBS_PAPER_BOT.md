# IBS paper bot: QQQ + TQQQ on Alpaca paper (frozen 2026-10-21)

> **Superseded the same day by docs/IBS_LIVE_BOT.md.** The operator chose a live bot armed from the private dashboard,
> with no paper phase. Its shadow books keep this paper test running. The rule below is unchanged.

**Purpose:** a forward test of the only rule that passed every check across Scribd rounds 1–7 (round 3's `ibs_qqq`,
docs/SCRIBD_ROUND3_STUDY.md). The bot trades **paper money only** on Alpaca's paper endpoint. It can never place a
live order: the live trading URL is refused in code.

## The rule (frozen; any change is a new experiment with a new freeze hash)

- **Signal:** QQQ's internal bar strength, IBS = (close − low) / (high − low), from QQQ's regular session.
- **Entry (flat):** IBS < 0.10 → buy at the close.
- **Exit (holding):** the close is above the previous session's high → sell at the close.
- Long only. No stop. One position per book.

## Two books, both driven by the QQQ signal

| Book | Instrument | Starting paper capital | Size |
|---|---|---|---|
| `IBS-QQQ` | QQQ | 10,000 USD | the book's whole equity, fractional shares |
| `IBS-TQQQ` | TQQQ (3× QQQ) | 10,000 USD | the book's whole equity, fractional shares |

The books are tracked separately inside the one Alpaca paper account (the bot's own ledger). Other positions in the
account are never touched.

## Execution (US market days, Alpaca market clock)

1. **Two minutes before the close** (15:58 New York, or two minutes before an early close):
   - read QQQ's session so far: high, low and last price;
   - estimate today's IBS with the last price as the close;
   - place market orders for the books that must enter or exit.
2. **Fifteen minutes after the close:**
   - read the official daily candle and record the official IBS and the exit test;
   - log any disagreement with the 15:58 estimate (a "signal slip");
   - mark both books to market and store the day's equity.

Holidays and early closes come from Alpaca's clock and calendar. The bot never acts on a closed day.

## What is recorded (SQLite, read-only public API)

- every decision, entry and exit (estimated vs official IBS, order id, fill price, quantity);
- daily equity per book;
- buy-and-hold QQQ / TQQQ over the same days.

Public, GET-only: `/api/public/ibs-bot`. Shown on the Strategy check page.

## What the backtest expects

- **Frequency:** about 18 trades a year, about 6 days per trade; about 25% of days in the market.
- **Per trade:** +0.85% mean on QQQ (2013–2026); 74% winners on QQQ in 2022–2026.
- **Risk:** the worst historical QQQ trade was −22% (about −50% in TQQQ). The daily max drawdown since 2010 was 16% on
  QQQ and 43% on TQQQ.

## Review

**Decide after 30 closed trades** (about 18 months), or earlier if the TQQQ book draws down more than 50%.
- **Pass:** the QQQ book's mean per trade is > 0 and its profit factor is ≥ 1.3.
- **Otherwise:** the forward test does not confirm the backtest.

Live money needs a separate, explicit decision by the operator.
