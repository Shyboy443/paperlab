# PREREG - agent 05, new listings (written 2026-10-23, before the single TEST run)

**Universe rule.** Every Binance USDT-M perpetual whose first 1h bar in the public archive (data.binance.vision,
which still holds delisted symbols) falls inside the window. Listings newer than the archive's last full month are
topped up from the API (`common.klines`). Listing time = first 1h bar. No volume or name filter. Delisted coins are
included; a coin that stops trading during a hold is settled at its last close.

**Rule (all three attempts).** Short the new perp at the open of the 2nd hourly bar after listing (1 h after the
first bar, so the signal is the listing itself, known in advance). Hold 7 days (168 h) and buy back at that bar's
open. Funding paid/received at every stamp on the current notional. Costs 0.05% fee + 0.02% slippage per side
(doubled in the stress run). Isolated margin per trade = current equity / `slots`; notional = margin x leverage,
capped at 1% of the entry bar's quote volume. If all slots are full, the listing is skipped. A stop buys back when
the 1h high reaches entry x (1 + stop), filled at the worse of the stop and the bar open; liquidation (loss =
1/leverage - 0.5%, checked on highs) wipes the trade margin if it comes first.

| attempt | leverage | slots | stop | role |
|---|---|---|---|---|
| A_SHORT_7d_L1_k5_stop50 | 1 | 5 | +50% | primary, the honest version (DEV: mean +3.7%/mo, max DD 46%) |
| B_SHORT_7d_L2_k5_stop25 | 2 | 5 | +25% | best DEV net (+625%), max DD 72% |
| C_SHORT_7d_L5_k5_chase20 | 5 | 5 | +25% (liq at +19.5% comes first) | "what 20%/month needs": DEV mean +16%/mo but max DD 99.6% and net -10% |

**Pass rule:** PROTOCOL.md (TEST mean month >= +20% at baseline costs, > 0 at doubled costs, not wiped out, no single
trade > half the profit). Attempt A is the primary result. A leverage sweep (1,2,3,5,10x) of A on TEST is reported
as a diagnostic only, not as a pre-registered attempt.

DEV attempts: 186 grid configs in `results_dev.json` (short delay 1/24/72 h x hold 7/14/30/60 d x stop x lev x slots;
tighter-stop pass; bounce longs after -30/-50% at day 14/30; plain longs at listing). None reached a geometric
+20%/month on DEV without being wiped out.
