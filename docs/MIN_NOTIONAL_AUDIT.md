# Min-notional audit (2026-09-23)

PaperLab used to require every entry to be at least **2 × minNotional**, on the reasoning that "a
hypothetical 50% partial fill should still clear minNotional". This audit checked that rule against
Binance USD-M and removed it as an exchange constraint.

## Where the 2× came from

The rule was PaperLab's own, written into three places:

| file | what it did |
|---|---|
| `app/core/risk.py` (`approve`) | `min_needed = 2.0 * rules.min_notional  # a 50% partial must still clear the exchange minimum` |
| `app/competition/universe.py` | `min_notional_multiple: float = 2.0` in the universe filter |
| `app/competition/bots.py` (`sizing_window`) | `floor = 2.0 * rules.min_notional` in arena preflight |

Two related rules followed the same assumption:

* `app/core/portfolio.py` (`close_position`): a partial exit whose *remainder* was below minNotional
  was widened into a full close.
* `app/exchange/paper_router.py` (`plan`): any net delta below minNotional was held back, including
  reduce-only deltas.

## What Binance actually enforces

Sources (fetched 2026-09-23):

* USD-M common definitions: *"The `MIN_NOTIONAL` filter defines the minimum notional value allowed
  for an order on a symbol. An order's notional value is the `price` \* `quantity`. Since `MARKET`
  orders have no price, the mark price is used."*
  <https://developers.binance.com/docs/derivatives/usds-margined-futures/common-definition>
* Error -4164 MIN_NOTIONAL: *"Order's notional must be no smaller than 5.0 (unless you choose reduce
  only)"* <https://developers.binance.com/docs/derivatives/usds-margined-futures/error-code>
* Binance announcement (introducing the rule): the minimum applies to "each order", an order below it
  "will be rejected", and "'Reduce-Only' order is not affected". The threshold is changed "from time
  to time", so it must be read from the API.
  <https://www.binance.com/en/support/announcement/detail/76719bbaeeb847bbac4daa2906fcdcc0>
* BTCUSDT / BTCUSDC minimum notional lowered 100 → 50 on 2026-04-14 06:30 UTC, "existing orders will
  remain unaffected" — the check happens at order entry, not on resting orders.
* Third-party confirmation with `POST /fapi/v1/order/test`: a below-notional request fails without
  `reduceOnly` and passes with `reduceOnly=true`.
  <https://github.com/QuantConnect/Lean.Brokerages.Binance/issues/82>

Production tape (public `GET /fapi/v1/trades`, 1,000 trades per symbol, 2026-09-23 07:17 UTC):
fills far below minNotional are routine. 11.1% of ETHUSDT trades were under 20 USDT (e.g. 0.001 ETH
= 2.74 USDT fills of a 1,162 USDT taker order), 2.9% of SOLUSDT trades under 5 USDT (0.01 SOL fills of
an 8.32 USDT order). BTCUSDT had none, because one BTC lot (0.001 × 86,361 = 86 USDT) is already
above its 50 USDT minimum. Grouping fills into one taker order (same ms, same aggressor, consecutive
ids, monotonic price) is a heuristic and is labelled as such.

A signed test-order probe was not run: no API keys exist on the workstation, and production keys
were deliberately not pulled from the deployment for this.

## Answers

1. **Minimum notional on submission:** `price × quantity` of the submitted order (mark price for
   MARKET) must be at least the symbol's `MIN_NOTIONAL.notional` — currently 50 BTCUSDT, 20 ETH/LINK/
   LTC, 5 SOL/BNB/XRP/DOGE/ADA/AVAX. Reduce-only orders are exempt. LOT_SIZE/MARKET_LOT_SIZE
   (minQty, stepSize) still apply to every order, reduce-only included.
2. **Partial fills below minNotional:** yes. The filter is an order-entry check; nothing constrains
   the size of an individual fill, and the production tape shows it constantly.
3. **Remainder below the minimum:** the remainder is governed by the order type and time in force,
   not by its size. Nothing in the documentation cancels a remainder for being small, and the BTC
   change shows existing orders are not re-checked.
4. **Cancel or keep:** GTC/GTD remainders stay on the book as PARTIALLY_FILLED until filled,
   cancelled or expired. IOC remainders expire. FOK is all-or-nothing. GTX (post-only) is rejected
   if it would take.
5. **MARKET vs LIMIT:** MARKET notional uses the mark price, and a MARKET order never rests — its
   documented RESULT is FILLED, and any unfilled part expires. LIMIT notional uses the limit price;
   the remainder follows its time in force as above.

## What changed

* `RiskManager`: entries need `qty >= minQty` and `notional >= minNotional × min_notional_safety_multiplier`.
  The multiplier (`MIN_NOTIONAL_SAFETY_MULTIPLIER`, default **1.0**) is an explicit PaperLab
  preference; 2.0 restores the old rule. `below_min_qty` is now reported separately from
  `below_min_notional`.
* `Portfolio.close_position`: a partial exit is reduce-only, so it only has to be at least one lot,
  and so does the remainder. The old remainder-notional rule applies only when the multiplier is > 1.
* `PaperRouter.plan`: reduce-only deltas go out at any lot-legal size; opening deltas — including
  the opening leg of a flip — must clear minNotional.
* Execution model: `Order.time_in_force` and `remainder_policy()` model the rules above, and the
  replay opens a position at the quantity that actually filled (it used to book the requested size).
* Arena preflight: the floor is the smallest step-aligned order that clears minQty and minNotional
  at the reference price; the ceiling is the fee gate at the ordinary risk.

## Found on the way: fees

The replay charged `settings.taker_fee` (0.04%) while the competition's FeeSchedule declared Binance's
regular-user rate of 0.05% taker / 0.02% maker. The HIGHER_FEES stress scenario therefore changed
nothing it charged. `ReplayEngine(fee_source="schedule")` (the default) now charges the schedule and
feeds the same rate to the fee gate. `fee_source="settings"` reproduces the old runs exactly;
validation run bdddc67f6315 was produced that way (it has no stress results, so the no-op stress
scenario never touched stored evidence).
