# Telegram trade updates (2026-10-02)

`app/live/telegram.py` posts every LIVE paper trade of every forward program (V6, V7, V8, V9, V11, V12) to the
operator's Telegram bot:

Messages are kept short (the operator asked for "very simple, on point" text on 2026-10-03):

```
🟢 LONG ENA — Zap                     open: a new message
Entry: 0.2344
Stop: 0.2315 (−1.2%)
Targets: 0.2350 · 0.2362 · 0.2374 (+0.3% to +1.3%)
Win rate 54% (61 of 113)

✅ ENA target 1 hit (+0.3%)            TP hit: a REPLY to the open
Stop moved to 0.2347 (above entry: profit locked)

💰 WIN +0.12 USDT — ENA                closed: a REPLY to the open (🔻 LOSS ... when it lost)
Hit the target · held 42m
Zap win rate 54% (62 of 114)
```

- **Win rate** is the bot's closed trades with net > 0 in its program's CURRENT experiment. It is read from the program's
  database by `main._bot_stats`. The worker saves a trade before publishing its close, so a close message counts its
  own trade.
- A loss worse than −1.5 R adds "⚠️ Bigger loss than planned".
- Size, leverage, risk and bot keys are left out on purpose. The dashboard still has them.

**Threads:**
- The reply target is keyed by program, bot, coin, side and the entry fill time, stored in `/data/telegram.db`.
- A restart still replies to the right message.
- History replays (`rederived`) and counterfactual trades are never sent.

**Setup** (the token is a secret: never in code, logs or chat):
1. Paste the bot token in System → Providers & Live → **Telegram bot** → Enter keys → Save & verify. It is checked with
   `getMe` and stored encrypted.
   Leave the chat-id box empty. If you fill it, it must be a numeric chat id; anything else is refused at save,
   and an old non-numeric value is ignored (2026-10-02 fix: a token-only save used to fail with a 500).
2. Send `/start` to the bot from your private chat. The first private chat is linked; any other chat is told the bot is
   private.

**Commands:** `/settings` lists the default TP / SL rules of every program; `/help` explains the messages.

**Filters** (Railway variables):
- **Per program:** `TELEGRAM_FILTER=v11,v12,v8:CONTROL`, the operator's choice on 2026-10-02: every V11 bot, both Bizzy
  bots, and only V8's CONTROL bots (not its JEV / LADDER twins).
- **Coarser:** `TELEGRAM_PROGRAMS` and `TELEGRAM_ROLES`.
- **Unfiltered:** roughly 1,000–2,000 messages a day across all programs. Sending is paced at about one message a second;
Telegram's 429 is honoured.

**Confidence line** (operator, 2026-10-03): `Confidence: 65% ▰▰▰▰▰▰▰▱▱▱ (59 past trades)`.
- **What it is:** the setup's MEASURED win chance (`app/live/trade_stats.setup_confidence`): the share of the same
  strategy's paper trades (same role, every coin) that closed in profit, shrunk toward 50% (+5 wins / +10 trades).
- **Data used:** the current experiment, or the last 30 days when it has fewer than 20; with fewer still it reads
  "building up".
- **What it is not:** the bots' setup-quality scores. Over 1,780 V7 / V8 / V11 trades the top third of those scores
  won no more often than the bottom third.
