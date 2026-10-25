# Desk live trading on a linked Binance sub-account (MACD and Donchian)

Two desks have a live switch, built on one shared executor (`app/live/desk_live.py`); everything below applies to both.

| Desk | Trades | Direction | Confirmation phrase |
|---|---|---|---|
| MACD | ZEC perpetual, 1x, the monthly-table rule | long or flat | `GO LIVE MACD ZEC` |
| Donchian | **one coin you choose** from the desk's 10, 1x, the exact DTR-1 rule (`app/donchian/model.rule`) | **long and short**; an opposite breakout reverses (the old side is closed reduce-only before the new one opens) | `GO LIVE DONCHIAN` |

On the Donchian desk, the disaster stop sits 30% against the position: below a long, above a short. Its two-year paper
backtest is negative overall (−27% across the 10 coins; XRP +94% with a 63% drawdown, ADA +28%). The strict
mark-price check found no 1x liquidation over those two years.

The MACD desk can trade the ZEC perpetual rule from the monthly table with real orders on **one** linked Binance
account. Everything is on the **private dashboard** (Trading Desk → MACD). The public page has no live controls and no
live data.

## The rule it trades

- **Signal:** MACD 12/26/9 on Binance USD-M **ZECUSDT** 4h closes. It uses the same stored perpetual candles as the
  monthly table, so it sees the same signals.
- **Entry:** a bullish cross triggers a market **buy at 1x** (isolated margin, one-way mode).
- **Exit:** a bearish cross triggers a reduce-only market **sell** of the whole position.
- **Flat start:** it starts flat and only acts on crosses that close **after** you switch it on, so it never jumps
  into a trade that is already running. It acts within about 15 s of the 4h close.
- **Sizing:** your amount plus realized P&L. Fees are estimated; funding is paid by the exchange and is not tracked
  in the allocation.

## Protection

- **Disaster stop:** an exchange-side STOP_MARKET 30% below every entry. It only fires in a crash or while the app is
  down. If it can't be placed, the position is closed at once.
- **Loss limit:** if the allocation, marked to market, loses your limit, the position is closed and the strategy
  switched off.
- **Reconciliation every 15 s:**
  - If the position disappears (the stop fired, or it was closed by hand), the strategy switches **OFF**.
  - If the position differs from what the strategy opened (someone else traded the account), it goes **HALTED** and
    trades nothing more automatically.
- **Failed close:** the disaster stop is put back, the state becomes **CLOSING**, and the close is retried.
- **Kill switch:** the private dashboard's KILL ALL also closes and switches off the MACD live account.
- **Telegram:** every switch-on and switch-off, fill, stop and failure is sent, whatever the chat's scope.

## One strategy per account

- **One owner per account:** each linked account is owned by at most one strategy (a bot, a desk or a trading-desk
  agent) while it is on or holding a position. Any other strategy is refused. The account is released when the
  strategy is switched off and flat.
- **Dedicated and flat:** switching on requires a dedicated, flat account: no position on any symbol, and no open
  ZECUSDT orders.
- **Duplicate keys refused:** the same API key can't be linked twice, and neither can the live mirror's shared key.
  Two *different* keys of the *same* Binance account can't be detected, so link each sub-account once.

## Setting it up

1. **Testnet first.** On the Binance futures testnet, create an API key. Link it under *Link a Binance account* with
   network **testnet**, then click **Run testnet round trip**. That runs the smallest buy, the disaster stop, a
   cancel, a reduce-only close and a flat check. A pass unlocks mainnet. You can also switch the desk on with the
   testnet account to watch it trade for real on testnet.
2. **Sub-account key.** In Binance, create the API key **on the sub-account**:
   - enable **Futures**;
   - **disable withdrawals**;
   - restrict it to the server's IP if possible;
   - fund the sub-account's USD-M futures wallet with USDT.
3. **Link it** with network **mainnet**. The key is checked with a read-only balance call, stored encrypted in the
   server's key vault, and never shown again; you only see a fingerprint.
4. **Server switch.** Mainnet also needs `LIVE_MIRROR_MAINNET_ENABLED=true` on Railway. The per-strategy ceiling is
   `LIVE_MIRROR_MAX_AMOUNT_USDT` (default 200).
5. **Flip the switch.** Pick the account, enter the amount and loss limit, and type `GO LIVE MACD ZEC`.
6. **To stop,** click the switch. It asks *"Stop live trading? … Are you sure?"* first. Yes closes the position at
   market and releases the account.

## Code

- `app/live/accounts.py` (linked accounts, ownership)
- `app/live/desk_live.py` (shared executor); `app/macd/live.py`, `app/donchian/live.py` (each desk's rule)
- `app/core/api_live_accounts.py`: private routes `/api/accounts…` and `/api/{macd|donchian}/live…`
- `app/dashboard/macdlive.js`: loaded by `index.html` only
- Tests: `tests/test_macd_live.py` and `tests/test_donchian_live.py`, against a fake exchange

## Limits

- **Selection bias:** the rule's history is one coin chosen with hindsight; the same rule across 84 coins returned
  +11.5%.
- **Live versus backtest:** fills, slippage and funding will differ from the backtest.
- **Minimum order:** the allocation must cover Binance's minimum ZECUSDT order.
