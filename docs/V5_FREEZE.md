# V5 FREEZE (2026-09-24)

V5 — the HOURLY / DAILY FUTURES ARENA (docs/V5_PROTOCOL.md) — is frozen from this point. No source, parameter,
threshold, exit, universe rule, fee schedule, execution model, risk profile or Jev prompt / policy of V5 may change.
A change is V6, never a patched V5.

## What was frozen

| item | value |
|---|---|
| protocol | `V5_HOURLY_DAILY_PROTOCOL_V1` (docs/V5_PROTOCOL.md, Amendments 0, 1, 2 — all written before any DEVELOPMENT result) |
| strategies | `app/strategies/v5/` V5.1-V5.8 + base (fingerprints below) |
| exits | the pre-registered baseline for every family x class: structural stop (0.8-8%, the setup's extreme + 0.25 ATR) + time stop HOURLY 24 h / SWING 72 h, no target, no trail. No exit was selected: the DEVELOPMENT exit grid only runs for raw-edge survivors and there were none |
| universe rule | `UniverseRuleV5` fingerprint `c01f7c75cc3b` (DEVELOPMENT universe POPCAT, LDO, ENA, WLD, WIF, APT, GALA, TAO, RUNE, VIRTUAL) |
| costs | `BYBIT_LINEAR` fee schedule (maker 0.02%, taker 0.055%), the execution model (half spread + volatility widening, 400 ms latency to the next 1m bar), Bybit instrument filters, paper liquidation, actual Bybit funding settlements |
| risk | `AGGRESSIVE_V5`: TAKE 1.0%, HIGH CONVICTION 1.5% (reserved), ATTACK 2.0%; 20x ceiling; no ATTACK >= 18% below peak; halt at 30%; an order below the exchange minimum is skipped (MIN_NOTIONAL_LIMITED), never enlarged |
| Jev V5 | prompt `85b5d6019280fff2`, policy `d57140c8d63d45e6`, state builder `cb256942dc82` (JEV_PROMPT_V5 / JEV_STATE_V5 / JEV_POLICY_V5) — never called: no family survived before Jev |
| gates | `RawEdgeGate`, `EconomicGate`, `V5Gates` in app/competition/v5_config.py (as pre-registered in §7) |

| source | fingerprint |
|---|---|
| strategy:v3_base | `33d10d257921` |
| strategy:base | `7be736c53f9f` |
| strategy:V5.1 | `f4f72abc0a7d` |
| strategy:V5.2 | `3b421c29aec3` |
| strategy:V5.3 | `01e8ea7e986d` |
| strategy:V5.4 | `2fd17a549cf2` |
| strategy:V5.5 | `dfc5fbb80b56` |
| strategy:V5.6 | `0d541d70e47a` |
| strategy:V5.7 | `f54c7f2faec3` |
| strategy:V5.8 | `9decbe7ce090` |

## The DEVELOPMENT run it was frozen on

`v5-1d32b2c190` — DEVELOPMENT 2025-03-01 .. 2026-08-31 (warm-up from 2024-12), config `3ae0ac8c90549002`, Bybit-native
archive `71af0ed7551e66bf` (9,996,475 rows, 0 missing minutes). 320 books: 160 official 20 USDT CONTROLs + 160
100 USDT CAPACITY twins (Amendment 1).

* **Stage 1 (raw edge): no family x class passed.** The four with a positive pooled gross R all failed significance
  and the drop-the-best-5% robustness check (V5.8 HOURLY +0.129 R, P(mean <= 0) 0.13; V5.4 HOURLY +0.066 R, 0.066;
  V5.4 SWING +0.073 R, 0.20; V5.2 SWING +0.057 R, 0.25). Every family's mean turns negative without its best 5% of trades.
* Stages 2-3 did not run (protocol §7: no Jev call is spent on a family that loses before Jev).
* 160 official controls: gross +1.58 USDT, taker fees 27.22, maker fees 0, slippage 17.04, funding paid 2.38 /
  received 3.39, **net -41.64 USDT** over 2,982 trades; 37 of 160 net-positive, none active enough or significant.
* ADVANCED SET (DEVELOPMENT) = **NONE**.

The pseudo-holdout was pre-registered after this freeze (docs/V5_TEST_PREREGISTRATION.json): it can only replicate or
refute the Stage 1 picture; no V5 bot can be ADVANCED.
