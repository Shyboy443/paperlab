"""MACD Desk LIVE: the ZEC perpetual rule (app/macd/perp_monthly.py) on ONE linked Binance account.

MACD 12/26/9 on Binance USD-M ZECUSDT 4h closes, from the same stored perp candles as the monthly study: a bullish
cross -> buy at 1x, a bearish cross -> close. Long or flat. All execution, guards and states are the shared desk
executor's (app/live/desk_live.py).
"""
from __future__ import annotations

from typing import Any

from app.live.desk_live import DISASTER, LEVERAGE, TAKER_EST, TICK_S, DeskLive, LiveError  # noqa: F401  (re-exported)

from . import model, perp_monthly

SYMBOL = "ZECUSDT"
CONFIRM = "GO LIVE MACD ZEC"
KIND, REF = "desk", "MACD Desk · ZEC perp"


class MacdLive(DeskLive):
    NAME, KIND, REF, CONFIRM, STATE_KEY = "MACD Desk", KIND, REF, CONFIRM, "live"
    SYMBOLS, SHORTS, THREAD = (SYMBOL,), False, "macd-live"

    def _bars(self) -> list[dict[str, Any]]:
        return self.completed_bars("ZECUSDT|perp4h", perp_monthly.WARMUP_FROM,
                                   lambda start, end: self.svc._perp_klines("4h", model.TF_MS, start, end))

    @staticmethod
    def _signal(bars: list[dict[str, Any]]) -> tuple[int | None, str | None]:
        ch = model.channels(bars)
        i = len(bars) - 1
        if i < 1 or ch[i]["hist"] is None or ch[i - 1]["hist"] is None:
            return (bars[i]["t"] if bars else None), None
        if ch[i - 1]["hist"] <= 0 < ch[i]["hist"]:
            return bars[i]["t"], "buy"
        if ch[i - 1]["hist"] >= 0 > ch[i]["hist"]:
            return bars[i]["t"], "sell"
        return bars[i]["t"], None

    def decide(self, symbol: str, held: int):
        t, sig = self._signal(self._bars())
        if sig == "buy" and held == 0:
            return t, ("enter", 1)
        if sig == "sell" and held > 0:
            return t, ("exit", 0)
        return t, None
