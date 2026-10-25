"""ZEC bots LIVE: the six TradingView strategies of rules.py on the ZECUSDT USD-M perpetual, each on ONE linked Binance
account (one strategy per account). Asked for by the operator on 2026-10-18 from another study's table of their best
single-coin results; every one of them reproduces that table exactly (docs/ZEC_BOTS_STUDY.md).

LONG-ONLY: a buy signal opens a long, a sell signal closes it. The sources also go short, but on the ZEC perpetual the
short side lowered the return and raised the drawdown for all six (study), so SHORTS is off.

The shared desk executor (app/live/desk_live.py) does everything else: 1x isolated, maker-first orders, an exchange-side
30 % disaster stop on every entry, the operator's loss limit, reconciliation, mainnet gate, kill switch. It starts flat
and only acts on candles that close after it was switched on. All six trade the same coin: they are not diversified.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.live.desk_live import DeskLive

from .rules import RULES

SYMBOL = "ZECUSDT"
SIZE = 1.0                                       # full allocation, 1x (the operator's choice for the coin bots)
WARM_CANDLES = 1200                              # EMA200 / SMA200 on 1h and EMA48 on 4h: fully converged
HERE = Path(__file__).resolve().parent


class ZecMarket:
    """Completed ZECUSDT perpetual candles (1h and 4h) for the bots, kept in one database (public data, no keys)."""

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

    def klines(self, symbol: str, interval: str, tf: int, start: int, end: int) -> list[dict]:
        out, cursor = [], start // tf * tf
        while cursor < end:
            part = self.client.get("perp", "klines", {"symbol": symbol, "interval": interval, "startTime": cursor,
                                                      "endTime": end - 1, "limit": 1500}, weight=5)
            if not part:
                break
            out += [dict(t=int(r[0]), o=float(r[1]), h=float(r[2]), l=float(r[3]), c=float(r[4]), v=float(r[5]))
                    for r in part if int(r[0]) + tf <= end]                 # completed candles only
            nxt = int(part[-1][0]) + tf
            if nxt <= cursor or len(part) < 1500:
                break
            cursor = nxt
        return out


def backtests() -> dict[str, Any]:
    """The 2-year backtest of each bot (all costs, long-only, perpetual) with monthly and weekly returns."""
    try:
        return json.loads((HERE / "backtest.json").read_text())
    except (OSError, ValueError):
        return {}


class ZecBotLive(DeskLive):
    SHORTS = False
    SIZE = SIZE
    MAKER = True             # post-only limits first (maker fee 0.02% instead of 0.05%), market after 3 min
    KIND = "desk"

    def __init__(self, key: str, market: ZecMarket, *a, **k):
        super().__init__(market, *a, **k)
        self.key = key
        self.title, self.rule, self.tf, self.INTERVAL, _ = RULES[key]
        self.symbol = SYMBOL
        self.NAME = f"ZEC {self.title}"
        self.REF = f"ZEC bot {key}"
        self.CONFIRM = f"GO LIVE ZEC {key.upper()}"
        self.STATE_KEY = f"live:zec:{key}"
        self.SYMBOLS = (SYMBOL,)
        self.THREAD = f"zec-{key}-live"

    def _bars(self) -> list[dict[str, Any]]:
        return self.completed_bars(f"{SYMBOL}|perp{self.INTERVAL}", self.now() - WARM_CANDLES * self.tf,
                                   lambda start, end: self.svc.klines(SYMBOL, self.INTERVAL, self.tf, start, end), tf=self.tf)

    def decide(self, symbol: str, held: int):
        bars = self._bars()
        if len(bars) < 300:
            return None, None
        buy, sell = self.rule(bars)
        t = bars[-1]["t"]
        if buy[-1] and held <= 0:
            return t, ("enter", 1)
        if sell[-1] and not buy[-1] and held > 0:
            return t, ("exit", 0)
        return t, None

    def view(self) -> dict[str, Any]:
        v = super().view()
        v["interval"] = self.INTERVAL
        bt = backtests().get(self.key)
        if bt:
            v["backtest"] = {k: bt[k] for k in bt if k not in ("weekly",)}
        return v
