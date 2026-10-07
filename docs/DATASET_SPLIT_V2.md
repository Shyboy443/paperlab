# v2 dataset split (fixed 2026-09-23, before any v2 result existed)

This split is decided before a single v2 strategy has been run. It is recorded in every v2 arena
run's configuration (`dataset_role`) and must not be changed after results are seen.

| role | window | use |
|---|---|---|
| **DEVELOPMENT** | 2025-11-01 → 2026-04-30 (6 months) | designing v2 strategies: signal counts, cost ratios, sanity checks. Results here are never reported as performance. |
| **TEST** | 2026-05-01 → 2026-06-30 (2 months) | ONE evaluation of the frozen v2 strategies: the v2 discovery arena. |
| **CONTAMINATED** | 2026-07-01 → 2026-08-31 | inspected repeatedly (arena 9bbe42e53fd8, Jev 527331f5d0c7). Not used for v2 development or v2 evaluation. |
| **FORWARD** | 2026-09-23 onward, live | live shadow trading, including Jev V1 shadow pairs. Genuinely unseen by every model and strategy. |

Coins: ETH, SOL, BNB, XRP, DOGE, ADA, LINK, AVAX, LTC (BTC stays excluded: one 0.001 BTC lot exceeds
what a 1%-risk 20 USDT book can size under the fee gate).

## Rules

1. v2 strategies may be changed while only DEVELOPMENT data has been looked at.
2. Before the TEST arena runs, every v2 strategy is frozen (its source fingerprint is recorded).
3. After TEST, a v2 strategy is never tuned again. Any change is v3. FORWARD data is SACRED
   evaluation data (amended 2026-09-23): if forward results inspire a v3, record the hypothesis,
   develop v3 on an explicitly declared development dataset, freeze v3, and evaluate it ONLY on data
   that occurs AFTER its freeze date. v3 is never scored retroactively on forward data that
   influenced its creation.
4. v1 strategies are never edited. Their behaviour stays reproducible for 9bbe42e53fd8,
   527331f5d0c7 and bdddc67f6315.
5. JEV_POLICY_V2 is not created until base bots with an actual edge exist, and it will be frozen
   before it is evaluated.

## Historical contamination map (added 2026-09-23, before the multi-year validation ran)

| date range | coins | dataset role | used for | safe for v2 evaluation? |
|---|---|---|---|---|
| 2020-12 | XRP, BNB | warmup | indicator warmup of the multi-year replay only (no entries) | n/a |
| 2021-01 → 2025-10 | **XRP, BNB** | **HISTORICAL HOLDOUT** | never loaded by any PaperLab experiment before 2026-09-23 (the XRP/BNB archive files did not exist locally or on Railway); v2 was not designed on it | **YES, with the family-level limitation below** |
| 2021-01 → 2026-08 | BTC, ETH, SOL | v1 multi-year validation `bdddc67f6315` (+ `a7965533de2a`, 2024-01 → 10) | all 27 v1 strategies, including the v1 ancestors of S12 and S26; results seen before v2 was designed | not used for these candidates |
| 2025-11 → 2026-04 | 9 coins | DEVELOPMENT | designing v2, choosing the TEST field | NO |
| 2026-05 → 2026-06 | 9 coins | TEST | the one v2 evaluation — consumed | NO (already used) |
| 2026-07 → 2026-08 | 9 coins | CONTAMINATED | arena `9bbe42e53fd8`, Jev `527331f5d0c7`, August seasons | NO |
| 2026-09-23 → | 9 coins | FORWARD | live shadow, evaluation only | evaluation only, never development |

**Limitation, stated plainly:** the S12 and S26 *families* were not untouched in 2021–2025. Their v1
ancestors ran across that period on BTC, ETH and SOL in `bdddc67f6315`, and those (mostly failing)
results were visible before v2 was written. v2's rules were not fitted to 2021–2025 data and the
candidates' own coins were never loaded for that period, so it is a holdout at coin level — but a
family-level prior existed. It is called HISTORICAL HOLDOUT, not "untouched".

**Consumed 2026-09-23:** multi-year run `9688867c33bc` evaluated the three frozen v2 candidates on
XRP/BNB 2021-01 → 2025-10 (0/3 passed). That window is now CONSUMED for XRP and BNB: any strategy
designed after seeing those results may not be evaluated on it.
