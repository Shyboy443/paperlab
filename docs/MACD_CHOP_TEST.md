# MACD coin bots: choppy-market filter, forward paper test (pre-registered 2026-10-18)

**Why:** in October 2026 the MACD coin bots lost in a trendless, choppy market (simulated: STX −16.9%, DASH −7.7%,
XLM +5.1%; −7.5% together). A filter that stands aside when the market is choppy is a sound idea. It is tested
**forward on live data**, not on the 2024–2026 backtest. That backtest has already been used too often to be trusted
for one more variant.

## Fixed now, never changed during the test

- **Coins:** STX, DASH and XLM (the live coin bots), Binance USD-M perpetuals, 4h.
- **Two paper books per coin, both starting flat with 10,000 USDT:**
  - **CONTROL:** exactly the live rule. MACD 12/26/9 crosses, long and short, half size per position, 30% disaster
    stop.
  - **FILTER:** the same, except: at a cross, if the market is **choppy**, the book goes **flat** (it closes what it
    holds and doesn't open the new side). It stays flat until the next cross at which the market isn't choppy.
- **Choppy:** Choppiness Index CHOP(14) on the coin's completed 4h candles **above 61.8** at the cross. The formula is
  100 · log10(Σ true range over 14 candles / (highest high − lowest low over 14 candles)) / log10(14). These are the
  standard length and threshold, not tuned.
- **Costs for both books:** maker 0.02% plus 0.01% adverse per side (the live bots' limit-order execution), and
  funding at Binance's recorded rate and mark price.
- **Start:** the first 4h boundary after deployment.
- **Evaluation:** once, on **2026-12-31**, and not earlier.

## Decision rule (fixed now)

The filter is adopted for the live bots only if, over the test period, **all** of these hold for the three coins
together (equal money):

1. FILTER's return ≥ CONTROL's return;
2. FILTER's max drawdown ≤ CONTROL's max drawdown;
3. at least 20 crosses happened across the three coins, so the test isn't decided by a handful of signals.

Otherwise the live bots stay as they are. Either way the result is published here.

## Where

- **Code:** `app/macdtrio/chop_test.py` (service); `app/macdtrio/sim.py` (`chop_len`, `chop_max`).
- **Public results:** `GET /api/public/macd-chop` (weekly returns, both books, each coin and combined) and the home
  page's "Your MACD bots" section.
- **Paper only:** no orders, no keys.

## Note made before the start (2026-10-18)

A local smoke run on October's real candles (start backdated to 2026-10-01, only to check the code) gave identical
books: CHOP(14) at all 23 October crosses was 36–60, below 61.8. A MACD cross comes right after a price move, and
that move makes CHOP read "trending" at the moment of the cross. The registered filter may therefore rarely act. The
threshold was **not** changed after seeing this, because tuning it to October would defeat the test. If the filter
never acts, the test ends "no difference" and the live bots stay as they are.
