"""Donchian Desk LIVE: the DTR-1 rule on ONE coin, chosen when switching on, on ONE linked Binance account.

The exact backtested rule (app/donchian/model.rule): a 4h close above the prior 20-bar high -> long, below the prior
20-bar low -> short, an opposite breakout reverses, a close beyond the prior 10-bar low / high exits. Long AND short at
1x. All execution, guards and states are the shared desk executor's (app/live/desk_live.py): every entry gets an
exchange-side disaster stop 30 % against it, a reversal closes reduce-only before it opens the other side.
"""
from __future__ import annotations

from typing import Any

from app.live.desk_live import DeskLive, LiveError  # noqa: F401  (re-exported)

from . import model

CONFIRM = "GO LIVE DONCHIAN"
WARM_DAYS = 30                                                        # the channels need 20 prior 4h candles


class DonchianLive(DeskLive):
    NAME, KIND, REF, CONFIRM, STATE_KEY = "Donchian Desk", "desk", "Donchian Desk", CONFIRM, "live"
    SHORTS, THREAD = True, "donchian-live"

    def symbols(self) -> list[str]:
        return list(self.svc.universe)

    def _bars(self, symbol: str) -> list[dict[str, Any]]:
        since = self.now() - WARM_DAYS * 86_400_000
        return self.completed_bars(symbol, since, lambda start, end: self.svc._klines(symbol, start, end))

    def decide(self, symbol: str, held: int):
        bars = self._bars(symbol)
        if not bars:
            return None, None
        ch = model.channels(bars)
        i = len(bars) - 1
        return bars[i]["t"], model.rule(ch[i], bars[i]["c"], held)
