"""Worker 3 LIVE switch: copies the Worker 3 paper book (app/worker3/service.py) onto ONE linked Binance USD-M account.

The same machinery as Worker 1's switch (app/ensemble/live.py): a dedicated flat account, 1x isolated, market orders,
units scaled by the account's equity / the book's equity x SCALE, the day's trades just after 00:00 UTC (the book
re-sizes every position daily; re-sizes below Binance's minimum order are skipped), reconciliation, kill switch, no
loss limit unless one is set. A 1x isolated short whose coin doubles is liquidated by the exchange, as the research
book assumed; the reconciliation books it as closed.
Ships OFF; the operator switches it on from the private dashboard.
"""
from __future__ import annotations

from app.ensemble.live import EnsembleLive


class Worker3Live(EnsembleLive):
    NAME = "Worker 3"
    KIND, REF = "bot", "Worker3"
    CONFIRM = "GO LIVE WORKER 3"
    LABEL = "Worker 3"
    MIN_AMOUNT = 200.0      # 10 positions of ~19 USDT; BTC (100) and ETH (20 USDT minimum) are skipped at 200 USDT
                            # (research/worker3/round6/verify: 2021-26 geo +6.73%/month at 200 vs +6.78% at 10k)
    THREAD = "worker3-live"
