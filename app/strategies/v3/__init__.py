"""Strategy version 3: aggressive intraday families (docs/V3_PROTOCOL.md).

New ids S31-S36 -- V3 does not repair V1 or V2 in place (both stay frozen and reproducible). Each
family is a distinct hypothesis, bound per bot to one signal timeframe (3m / 5m / 15m, 30m benchmark,
1m experimental) with two context timeframes and the 1m execution tape.
"""
