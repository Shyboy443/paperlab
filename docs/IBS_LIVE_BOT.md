# IBS bot: QQQ + TQQQ on Alpaca, live, armed by the operator (built 2026-10-21)

**Why:** this is the only rule that passed every check across Scribd rounds 1–7: round 3's `ibs_qqq`
(docs/SCRIBD_ROUND3_STUDY.md). The operator chose Alpaca and no paper phase ("ok use alpaca, build it, no need to
paper").

**What changed from the paper draft (docs/IBS_PAPER_BOT.md):**
- The bot can send real orders, but it ships **OFF**. Only the operator switches it on, from the private dashboard.
- Until then, and the whole time after, it logs every decision on two shadow paper books, so the forward test runs anyway.

Code:
- `app/ibsbot/`:
  - `rule.py`: the frozen rule and its freeze hash;
  - `market.py`: data;
  - `service.py`: scheduler, executor and ledger.
- `app/core/api_ibsbot.py`: the routes.
- `app/dashboard/ibsbot.js`: the panel.
- Tests: `tests/test_ibsbot.py` and `tests/test_ibsbot_live.py`.

## The rule (frozen; any change is a new experiment with a new freeze hash)

- **Signal:** QQQ's internal bar strength, IBS = (close − low) / (high − low), from the regular session.
- **Entry (flat):** IBS < 0.10 → buy at the close.
- **Exit (holding):** the close is above the previous session's high → sell at the close.
- Long only. No stop. One position per book.

## Books

| Book | Instrument | Live capital | Shadow (paper) capital |
|---|---|---|---|
| `IBS-QQQ` | QQQ | set by the operator (0 = unused, else ≥ 10 USD) | 10,000 USD |
| `IBS-TQQQ` | TQQQ (3× QQQ) | set by the operator (0 = unused, else ≥ 10 USD) | 10,000 USD |

Both books follow the QQQ signal. Each one invests its whole book equity (capital plus realized P&L) in fractional
shares.

## Every US session (Alpaca calendar; early closes included; closed days are skipped)

1. **Two minutes before the close** (15:58 New York, or 12:58 on an early close):
   - **Estimate.** QQQ's session high and low come from SIP minute bars up to 16 minutes ago (Alpaca's free plan delays
     SIP by 15 minutes), plus IEX minute bars for the rest. The latest IEX trade is taken as the close.
     With a paid data plan, set `IBS_BOT_FEED=sip` to use SIP throughout.
   - **Shadow books.** The decision is recorded and the shadow books follow it at the last prices.
   - **Live orders (only when armed).** Each live book that must enter or exit sends a fractional **day market order**.
     - Alpaca refuses market-on-close orders for fractional shares, and refuses them after 15:50 anyway.
     - No new rule order is sent in the last 30 seconds before the close.
2. **Twenty minutes after the close:**
   - read the official SIP daily candle and record the official IBS and the exit test;
   - flag a **signal slip** where the estimate disagrees with the official candle;
   - mark every book to market next to buy and hold QQQ / TQQQ.
   - A session with no estimate (server down, data missing) is recorded as **missed**. No late order is ever sent.

## Live safety

**Switching on** needs all of the following. The server re-checks every one of them.

**The request**
- the private, password-protected dashboard (Basic auth plus the `X-PaperLab` header on POST);
- the typed phrase **`GO LIVE IBS`**.

**Keys and server switches**
- Alpaca keys for the chosen account, saved in **System → Providers & Live**:
  - "Alpaca (real money)" for live;
  - "Alpaca paper" for paper.
  - The keys stay in the encrypted vault. The bot never shows, logs or returns them.
- For real money: `LIVE_MIRROR_MAINNET_ENABLED=true` on the server.

**Amounts**
- Total capital ≤ `IBS_BOT_MAX_CAPITAL_USD`. When that is unset, the limit is `LIVE_MIRROR_MAX_AMOUNT_USDT`
  (default 200).
- 0 < loss limit ≤ total capital.

**The account**
- An ACTIVE USD account with at least the total capital in **cash**. No margin is ever used.
- No QQQ/TQQQ position and no open QQQ/TQQQ order. Other holdings are fine; the bot never touches them.
- No other strategy on that Alpaca account: the stock trend strategy and this bot lock each other out.

**While it is on**
- **Deterministic order ids.** Orders are journalled with a deterministic client order id before they are sent, so a
  restart never sends one twice.
- **Ledger.** Fills update the bot's own ledger. It sells exactly the quantity it bought.
- **Reconciliation, every minute.** **HALTED** (nothing traded automatically) if any of these happens:
  - the broker's QQQ/TQQQ quantity differs from the ledger;
  - a QQQ/TQQQ order exists that the bot did not send;
  - the account behind the keys changes.
- **Loss limit.** Both books together are marked to market every minute while the market is open, and again at each
  official close. When the loss limit is reached, the bot sells everything at market (at the next open if the market
  is closed) and switches OFF.
- **Switch off / kill switch.** Both do the same: they sell the bot's positions at market and switch it OFF.
  - **HALTED:** switching off sells nothing. The operator settles the positions in Alpaca.

**Public view:** `GET /api/public/ibs-bot` is read-only. It shows the status, decisions and shadow books, and live trades
in % only: no account, no amounts, no keys.
**Private view:** `GET /api/ibs-bot`. Controls: `POST /api/ibs-bot/start` and `POST /api/ibs-bot/stop` (`sure: true`).
**Dashboard:** Strategy check → **IBS live bot** tab. The controls appear on the private dashboard only.

## What the backtest expects

- **Activity:** about 18 trades a year, about 6 days per trade, in the market about 25% of days.
- **QQQ book:** +0.85% mean per trade (2013–2026); 74% winners in 2022–2026.
- **Risk:** the worst QQQ trade was −22%, about −50% in TQQQ. Daily max drawdown since 2010: 16% (QQQ), 43% (TQQQ).
  Set the loss limit with that in mind.
- **Known differences from the backtest:**
  - fills come about 2 minutes before the official close;
  - Alpaca's SIP daily candles are used instead of Yahoo's;
  - small regulatory fees on sells are not in the ledger.

## Review

Decide after **30 closed trades** (about 18 months), or earlier if the TQQQ book draws down more than 50%.
- **Pass:** the QQQ book's mean per trade is > 0 and its profit factor is ≥ 1.3.
- **Otherwise:** the forward results do not confirm the backtest.
