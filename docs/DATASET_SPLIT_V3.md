# V3 dataset split (fixed 2026-09-23, before any V3 result)

Consumed by earlier research (never a fresh TEST again):

| data | used by |
|---|---|
| BTC / ETH / SOL 2021-01 → 2026-08 | v1 multi-year validation `bdddc67f6315` (+ `a7965533de2a`) |
| XRP / BNB 2020-12 → 2025-10 | V2 multi-year validation `9688867c33bc` (0/3 passed) |
| 9 coins (ETH SOL BNB XRP DOGE ADA LINK AVAX LTC) 2025-11 → 2026-04 | V2 DEVELOPMENT `78b995a21d07` |
| same 9 coins 2026-05 → 2026-06 | V2 TEST `8227dfb422cb` |
| same 9 coins 2026-07 → 2026-08 | arena `9bbe42e53fd8`, Jev V1 `527331f5d0c7` (CONTAMINATED) |
| same 9 coins 2026-09-23 → | live forward shadow `fx-f0a160e7bec7` |

## V3 coins: ZEC UNI HYPE SUI ARB TAO PENGU ENA AAVE ZRO

None of these ten had ever been loaded by PaperLab before 2026-09-23 (no archive file existed locally
or on Railway). For each of them:

| role | window | use |
|---|---|---|
| warmup | 2025-10 | indicator warmup only, no entries |
| **DEVELOPMENT** | 2025-11-01 → 2026-04-30 | V3 design sanity checks (signal counts, legality, cost ratios) and the V3 AGGRESSIVE DISCOVERY arena. Results here are in-sample and never called validation. |
| **TEST (holdout)** | 2026-05-01 → 2026-08-31 | ONE evaluation of frozen advancing V3 bots. Not downloaded until a bot advances and is frozen. |
| FORWARD | after a bot's freeze | live shadow, evaluation only |

Honest limitation: the V3 families were designed by someone who had already seen V1/V2 results on
other coins and periods (e.g. that 5m–30m bots on the V2 coins lost to costs). That is a general
prior, not knowledge of these coins' DEVELOPMENT or TEST data. The TEST window is untouched for all
ten V3 coins; it is also after DEVELOPMENT, so no V3 decision can have used it.

## What remains defensible for the ten earlier coins

| coin | DEVELOPMENT | untouched TEST |
|---|---|---|
| BTC ETH SOL | any (all seen) | none before the forward date |
| XRP BNB | any (all seen) | 2020-01 → 2020-11 only (pre-2021 market structure) |
| DOGE ADA LINK AVAX LTC | any | 2021-01 → 2025-10 (never loaded) |

## Rules

1. V3.0 may be changed only while DEVELOPMENT data alone has been looked at, and only for design
   reasons (signal counts, legality, costs) — never tuned on DEVELOPMENT PnL before the discovery run.
2. V3.0 is frozen (source fingerprints, docs/V3_FREEZE.md) before the discovery arena runs.
3. After discovery, V3.0 is never tuned again: an analyzer hypothesis becomes V3.1, developed on
   DEVELOPMENT data and judged only on data it has never seen.
4. TEST is loaded once per frozen version. FORWARD data is sacred: a problem it reveals becomes a
   V4 hypothesis, never a patch rescored on the same forward data.
