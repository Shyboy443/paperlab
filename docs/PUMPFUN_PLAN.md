# pump.fun research plan (paper only, pre-registered)

**Status:** the recorder was built and deployed on 2026-10-17 (new Railway project, Singapore). Recording runs
2026-10-17 → 2026-11-14.

**Principles:**
- Record first, then test ideas that were fixed **before** looking at the data.
- Count every cost.
- Paper only. No Solana wallet, no private key and no orders anywhere in this plan.

## Why this order

- **The odds are bad.** About 0.7–2% of pump.fun tokens ever graduate, and a third-party report puts the share of rug
  pulls and pump-and-dumps near 98%.
- **The costs are high.** Trades on the bonding curve pay 1% per side, and PumpSwap pays about 0.25–0.3% per side.
  Slippage and Solana priority fees come on top.
- **No usable history.** There's no reliable free history of these short-lived tokens, so we have to record our own.
- **Launch sniping is ruled out.** Bots with privileged Solana access own the first seconds after a launch; we would
  always be last.

## Phase 1: the recorder (build: 1–2 days; run: 4 weeks)

A small PaperLab service, `pumpfun_recorder`:
- It has its own database, `pumpfun.db`, and the switch `PUMPFUN_RECORDER_ENABLED`.
- It's read-only. It never trades.

| Data | Source | Cost |
|---|---|---|
| Every new token: mint, name and symbol, creator wallet, the creator's first buy, metadata link, time | PumpPortal WebSocket `subscribeNewToken` | free |
| Every graduation (migration to PumpSwap): mint, pool, time | PumpPortal WebSocket `subscribeMigration` | free |
| Price and liquidity of each graduated token, every 30 s for 6 h after graduation, then every 5 min up to 48 h | DexScreener public API (batch size and rate limit checked at build time), with GeckoTerminal as the fallback | free |
| Holder concentration at graduation: top-10 share, the creator's share | Solana public RPC `getTokenLargestAccounts`, called once per graduation (a few hundred a day) | free |
| Creator history: earlier launches and graduations by the same wallet | our own records | free |

**Amendments, 2026-10-17, made before any data was collected** (the sources turned out differently from the plan):
- **Holders:** keyless Solana RPCs refuse `getTokenLargestAccounts`, so holder data comes from GeckoTerminal's free
  token info: the holder count and the top-10 share, taken once, 2 min after graduation.
  - *Top 10 excluding the pool* = GeckoTerminal's top-10 share minus the pool's share (pool tokens from DexScreener
    divided by the 1 bn supply).
  - The creator's share **at graduation** has no free source, so that H2 rule is **dropped**. The creator's-first-buy
    rule and the serial-launcher rule stay, unchanged.
- **Price sampling:** every 30 s up to 2 h, every 2 min up to 6 h, then every 10 min up to 48 h. About 1,000
  graduations a day at the original 30 s / 5 min would not fit the 5 GB disk. Every H1 exit happens within 6 h, at
  30 s or 2 min resolution.
- **Not a token:** a "migration" whose mint is a pump.fun program address is skipped. One was seen live.

**Not recorded in phase 1:** trade-by-trade data on the curve. PumpPortal charges 0.01 SOL per 10,000 trade events and
needs a funded wallet. That's needed only for hypothesis H3 (below), and only if you choose to fund it later.

**Health checks:**
- the recorder appears on the deep health check;
- a Telegram alert fires if the stream has been silent for more than 10 minutes;
- after any reconnect, the gap is recorded so the analysis can exclude it.

**Size:** about 20–40 MB per day, which is fine on the server.

## Phase 2: the pre-registered test (after 4 weeks of data)

**Split:**
- **DEV:** weeks 1–2, to calibrate the cost model only.
- **TEST:** weeks 3–4, untouched until the single evaluation.
- No rule, threshold or variant may change after TEST is opened.

**Simulated execution:**
- **Size:** 0.1 SOL per trade.
- **Entry:** at the first recorded price at least **60 s after the event**, which allows for our reaction time.
- **Costs per side:** 0.30% PumpSwap fee, plus slippage from the pool's liquidity at that moment (constant-product AMM
  price impact for 0.1 SOL), plus 0.0005 SOL for network and priority fees.
- **Exit:** the first price at or after the exit condition. Stops are checked on the recorded price path, not
  intrabar, so the test keeps a "gap through the stop" risk.

**Hypotheses:** three variants each, fixed now.

| ID | Entry | Exit variants |
|---|---|---|
| **H1 graduation momentum** | every graduated token, 60 s after graduation | A: hold 30 min · B: −20% stop / +50% target / 2 h limit · C: trailing −25% from peak / 6 h limit |
| **H2 = H1 + rug filter** | as H1, but skip a token if the top 10 wallets (excluding the pool) hold > 35%, the creator's first buy was > 5% of supply, or the creator wallet launched > 3 tokens in the past 24 h (the creator-holding rule was dropped on 2026-10-17, before any data; see the amendments) | same A / B / C |
| **H3 near-graduation** (optional; needs paid trade data) | the curve is 70–90% full and SOL in the curve rose over the last 5 min | sell at graduation, or a −15% stop, or a 1 h limit |

The filter thresholds in H2 are set now. They can't be tuned on TEST.

**A variant passes only if every one of these holds on TEST:**
1. At least 100 trades.
2. The average return per trade, after all costs, is above 0, and its 95% bootstrap confidence interval excludes 0.
3. Profit factor ≥ 1.2.
4. It's still profitable with **its single best trade removed**. One lucky memecoin must not carry the result.
5. It's still profitable at **double the slippage**.
6. With 9 variants tested, a pass is checked against luck: a variant must clear 2 to 4 with the confidence interval
   corrected for 9 tests (Bonferroni, 99.4%).

## Phase 3 (only if a variant passes)

- **Paper bot:** the passing variant becomes a paper-only PaperLab bot on live data for at least 4 more weeks. Same
  costs, same rules, frozen.
- **Real money:** this would be a separate decision for you. It needs a Solana wallet whose private key lives on the
  server, which is a much bigger risk than a Binance key with trading-only permissions. I wouldn't build that without
  your explicit go-ahead and a small, separate wallet.

## If nothing passes

The study is published as a failure on the Programs page, like the other failed studies. The recorder can keep running
as a data source, or be switched off.

## Timeline

| When | What |
|---|---|
| Server back up (Oracle or Railway) | build the recorder and its tests (1–2 days), deploy, check the first hour of data |
| +2 weeks | DEV analysis: cost model and data-quality report (gaps, coverage) |
| +4 weeks | TEST evaluation, once, against the criteria above; report |
| +8 weeks (only if passed) | paper bot forward result |

## Open choices (yours)

- **H3:** funding paid trade data means a small PumpPortal wallet with about 0.05 SOL. Otherwise H3 is skipped.
- **Server:** Oracle (free) or Railway ($5/month).
