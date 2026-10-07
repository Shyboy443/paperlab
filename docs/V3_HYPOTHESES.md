# v3 hypotheses (RECORDED ONLY -- no v3 exists)

Recorded 2026-09-23 from the v2 multi-year validation (`9688867c33bc`). Per docs/DATASET_SPLIT_V2.md
rule 3, a v3 built on any of these must be developed on an explicitly declared development dataset,
frozen, and evaluated only on data that occurs AFTER its freeze date. None of the data that produced
these observations (v2 DEVELOPMENT, TEST, the 2021-01..2025-10 XRP/BNB holdout, forward shadow to date)
may be used to evaluate it.

1. **S26 trend rider needs a stricter regime gate.** On XRP 2021-2025 the frozen S26 v2 lost in RANGE
   days (267 trades, -5.30 USDT, -0.18R) and was mildly positive in TREND_UP (+0.19R, 53 trades) and
   TREND_DOWN (+0.05R, 206 trades). Its 4h EMA context filter lets range periods through.
2. **At 20 USDT the exchange minimum shapes the sample.** 65% of S26-XRP's signals were refused as
   below the 5 USDT minimum notional (wide stops -> small positions), so the traded subset is biased
   toward tight-stop entries. A v3 should size or filter with that constraint in mind, or be judged
   on a book large enough that it does not bind.
3. **Costs dominate a 2.5 bps gross edge.** S26-XRP's gross edge was +2.5 bps per round trip against
   14.3 bps of modelled cost (fees 10.0 bps -- every fill taker --, slippage 4.3 bps): any v3 needs a larger expected move per
   trade or a maker-based execution, not a better filter on the same move size.
