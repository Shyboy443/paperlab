"""MACD coin bots LIVE: the MACD desk's rule (12/26/9 on 4h) on STX, DASH and XLM perpetuals, long AND short, each on
ONE linked Binance account (one strategy per account). Chosen by the operator on 2026-10-17 from the 2-year consistency
table (docs/MACD_TRIO_STUDY.md) AFTER the pre-registered year-1 / year-2 selection passed no coin -- hindsight picks.

Rule (identical to app/macdtrio/sim.py and the backtest): at each completed 4h close of the coin's USD-M perpetual,
a bullish MACD cross -> long (a short is closed first), a bearish cross -> short (a long is closed first). The shared
desk executor (app/live/desk_live.py) does everything else: 1x isolated, market orders, an exchange-side 30 % disaster
stop on every entry, the operator's loss limit, reconciliation, mainnet gate, kill switch. It starts flat and only acts
on crosses at candles that close after it was switched on.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.live.desk_live import DeskLive, LiveError  # noqa: F401  (re-exported)

from .sim import H4, signals

COINS = ("STX", "DASH", "XLM")
SIZE = 1.0                # operator 2026-10-18: full size, 1x, no leverage ("drawdown is fine without liquidating")
WARM_MS = 120 * 86_400_000                         # 720 candles: the SMA-seeded EMAs have fully converged
HERE = Path(__file__).resolve().parent


class TrioMarket:
    """Completed USD-M perpetual 4h candles for the bots, kept in one desk database (public data, no keys)."""

    def __init__(self, path: Path | str, client: Any = None, clock=time.time):
        from app.divergence.store import Store
        self.store = Store(path)
        self._client = client
        self.clock = clock

    @property
    def client(self):
        if self._client is None:
            import threading

            from app.autoresearch.client import Client
            self._client = Client(threading.Event())
        return self._client

    def klines(self, symbol: str, start: int, end: int) -> list[dict]:
        out, cursor = [], start // H4 * H4
        while cursor < end:
            part = self.client.get("perp", "klines", {"symbol": symbol, "interval": "4h", "startTime": cursor,
                                                      "endTime": end - 1, "limit": 1500}, weight=5)
            if not part:
                break
            out += [dict(t=int(r[0]), o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]), v=float(r[5]))
                    for r in part if int(r[0]) + H4 <= end]                 # completed candles only
            nxt = int(part[-1][0]) + H4
            if nxt <= cursor or len(part) < 1500:
                break
            cursor = nxt
        return out


def backtests() -> dict[str, Any]:
    """The 2-year backtest of each coin (all costs, long and short), with monthly and weekly returns."""
    try:
        return json.loads((HERE / "backtest.json").read_text())
    except (OSError, ValueError):
        return {}


class MacdCoinLive(DeskLive):
    SHORTS = True
    SIZE = SIZE              # docs/MACD_TRIO_STUDY.md: a third at first, then half for the three together
    MAKER = True             # post-only limits first (maker fee 0.02% instead of 0.05%), market after 3 min
    KIND = "desk"

    def __init__(self, coin: str, market: TrioMarket, *a, **k):
        super().__init__(market, *a, **k)
        self.coin = coin
        self.symbol = coin + "USDT"
        self.NAME = f"MACD {coin}"
        self.REF = f"MACD {coin} perp"
        self.CONFIRM = f"GO LIVE MACD {coin}"
        self.STATE_KEY = f"live:{self.symbol}"
        self.SYMBOLS = (self.symbol,)
        self.THREAD = f"macd-{coin.lower()}-live"

    def _bars(self) -> list[dict[str, Any]]:
        return self.completed_bars(f"{self.symbol}|perp4h", self.now() - WARM_MS,
                                   lambda start, end: self.svc.klines(self.symbol, start, end))

    def decide(self, symbol: str, held: int):
        bars = self._bars()
        if len(bars) < 60:
            return None, None
        s = signals(bars)[-1]
        t = bars[-1]["t"]
        if s > 0 and held <= 0:
            return t, ("reverse", 1) if held < 0 else ("enter", 1)
        if s < 0 and held >= 0:
            return t, ("reverse", -1) if held > 0 else ("enter", -1)
        return t, None

    def view(self) -> dict[str, Any]:
        v = super().view()
        bt = backtests().get(self.symbol)
        if bt:
            v["backtest"] = {k: bt[k] for k in bt if k not in ("weekly",)}
        return v
