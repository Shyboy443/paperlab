# Ensemble: paper book and live switch (2026-10-23)

## What it is

Round 4's `r4_07_ensemble` is the only strategy in the monthly-20% project that passed all 8 luck checks
(`research/monthly20/ROBUSTNESS.md`). It has three legs:

| Leg | Source | Rule |
|---|---|---|
| R: rotation | `r2_rotation` | 14-day return, top 40 by volume, long 10 / short 10, weekly |
| M: ML | `r2_ml` A_hgb_H6_k3 | long 3 / short 3, daily, model re-trained monthly |
| T: 60-day momentum | `r2_trend` T2 | top 50, inverse-vol weights, monthly resize + flips |

The legs are weighted by inverse volatility (60-day window, re-set weekly), at 1x. The rules are frozen in
`research/monthly20/r4_07_ensemble/PREREG.md`.

Research result, 2021-01..2026-10, at 1x:

| Mean month | Median month | Geometric month | Max drawdown | t-stat |
|---|---|---|---|---|
| +4.1% | +2.7% | +3.8% | 23% | 4.1 |

**Caveat:** the legs were picked after round 2 had already seen 2024-11..2026-10. Only forward results are clean.

The operator asked for it live on 2026-10-23.

## Code

| File | What it holds |
|---|---|
| `app/ensemble/engine.py` | `ens.py` ported. Reproduces the research full run exactly (+1,233.26%). Changes: the ML leg reads a prediction dict; the rotation's 7-day rebalance is anchored at 2021-01-01. |
| `app/ensemble/ml.py` | Features, model and training rule from `r2_ml/run.py`. Reproduces the research October predictions to about 5e-8. |
| `app/ensemble/seed/` | Written by `scripts/ensemble_export.py`: research training rows to 2026-10 (archive data incl. delisted coins), research predictions from 2026-03 to 2026-10-21, and the 2026 universes. |
| `app/ensemble/data.py` | Public Binance REST data (daily bars of every USDT perp, 4h bars of the ML universe, funding) in `ensemble.db`. |
| `app/ensemble/service.py` | The paper book (see below). |
| `app/ensemble/live.py` | The live switch (see below). |

**Paper book.** Every day after 00:01:30 UTC it refreshes the data, predicts with the month's model, and replays the
book:
- the standalone legs run from 2026-05-01;
- the book runs from 2026-07-01;
- days before the server's first day are replay, and every later day is forward paper.

**Live switch.** EnsembleLive copies the book onto one linked Binance account:
- **Sizing:** isolated 1x, units × 0.95 × account equity / book equity.
- **Switch-on:** it opens the whole book at once.
- **Daily trades:** it then trades only the coins the book trades that day, reductions first.
- **Small orders:** an order below the exchange minimum is skipped; closes are always allowed.
- **Every 60 s:** reconciliation, the loss limit, and the day's rebalance.
- **Reconciliation:** a foreign position halts the switch; a position that vanished is booked as closed (e.g. a 1x
  short liquidated).
- **Turning it off:** switching off, or the kill switch, closes everything.
- **Arming:** the phrase is `GO LIVE ENSEMBLE`, with a minimum of 500 USDT and a dedicated, flat account.
- **Mainnet gates:** the same as the other live switches.

**Routes:**
- private: `/api/ensemble/live`, `/start`, `/stop`;
- public: `GET /api/public/ensemble`.

**UI:** the home page has an "Ensemble" section.

## Known differences from the research run (checked 2026-10-23 on 2026-07..10)

Daily correlation with the research book is 0.95. August and October match within 0.5 points.

| Difference | Effect |
|---|---|
| Binance lists new high-volume perps whose names are Chinese characters (e.g. 龙虾USDT, 牛来USDT). The archive the research used has no data for them, so the research never held them. | Live keeps them, because the rule is "top 40 by volume". From 2026-09-17 they enter the rotation universe. September: live +10.5% vs research +13.7%. |
| The replay starts fresh on 2026-07-01. | The first days differ (July: −6.8% vs −1.6%). |
| The research archive has no funding after 2026-09-30. | The live book pays and receives October funding; the research did not. |
| Coins delisted before the server started are missing live. | They only matter for history. |
