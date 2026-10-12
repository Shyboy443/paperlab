# V8.3 entry diagnosis: where the loss comes from (2026-10-12)

`scripts/v8_entry_diagnosis.py` was run on the two **already inspected** windows only:
- **W1:** 2026-06-26 → 10-02, with recorded funding.
- **W2:** 2025-01-01 → 2026-06-25, without funding data.

It uses CONTROL exits (one 1.5R target), the live engine and 6 coins. Each trade's result is split from the decision
(signal close) price:

`net = gross − latency − spread − impact − fees + funding` (all in R of the trade's initial risk).

Results: `docs/V8_ENTRY_DIAGNOSIS.json`, and `docs/V8_ENTRY_DIAGNOSIS_latency1600ms.json` for the 2 s what-if.

## Live execution (fill 60 s after the 5m close)

| R per trade | W1 (3,652 trades) | W2 (21,277) |
|---|---|---|
| Gross move from the signal close | +0.083 | +0.067 |
| Latency (signal → fill, 60 s) | −0.074 | −0.090 |
| Spread (taker fills) | −0.018 | −0.017 |
| Fees | −0.088 | −0.087 |
| Funding | 0.000 | (no data) |
| **Net** | **−0.097** | **−0.127** |

- The gross edge is positive on every coin and both sides. Measured from the actual fill price, however, it is about
  zero: +0.009R (W1) and −0.023R (W2).
- Fees plus spread cost about 0.105R per trade at the fixed 1.2% stop.

## The delay is not an avoidable cost

A what-if entering about **2 s** after the close, at roughly the signal price, does **worse**:

| | 60 s entry (live) | 2 s entry |
|---|---|---|
| W1 net R per trade | −0.097 | −0.122 (3,205 trades) |
| W2 net R per trade | −0.127 | −0.144 (17,837 trades) |
| W2 gross from the signal | +0.067 | −0.043 |

The minute's delay works as a confirmation filter: setups that keep reverting during that minute are the ones that
work. The "latency" line above is an accounting attribution, not money that a faster entry could keep.

## "Expected move must cover costs"

Trades were grouped by the distance from the signal close to the 4h VWAP (the reversion target) relative to the
1.5R target distance. **No group is net-positive in either window.** Larger distances earn more gross, but pay more
in latency, and the best group (0.75–1.0) is −0.018R (W1) and −0.095R (W2).

| VWAP distance ÷ target | W1 n | W1 net R | W2 n | W2 net R |
|---|---|---|---|---|
| 0 – 0.5 | 2,355 | −0.092 | 11,314 | −0.126 |
| 0.5 – 0.75 | 684 | −0.140 | 5,255 | −0.140 |
| 0.75 – 1.0 | 332 | −0.018 | 2,672 | −0.095 |
| 1.0 – 1.25 | 155 | −0.038 | 1,175 | −0.100 |
| > 1.25 | 126 | −0.231 | 861 | −0.196 |

## Conclusion

V8.3's entries have no edge after realistic execution. Measured from the fill, the gross result is about zero, and
round-trip costs are about 0.1R. Neither a faster entry nor a cost-cover gate turns this positive on the inspected
data. Untouched data (2024-01 → 2024-09, never read by a V8 study) has not been opened.
