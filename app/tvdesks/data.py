"""Candles for the TradingView research desks: hourly -> UTC 4h, only when all four constituent hours exist."""
from __future__ import annotations

from typing import Iterable, Sequence

H1, H4 = 3_600_000, 14_400_000


def aggregate_4h(hours: Iterable[dict]) -> list[dict]:
    """First open, highest high, lowest low, last close, summed volume per UTC-aligned 4h bucket (00/04/08/12/16/20).
    A bucket missing any of its four hourly candles is dropped (the package's `count == 4` rule)."""
    buckets: dict[int, list[dict]] = {}
    for h in hours:
        buckets.setdefault(int(h["t"]) // H4 * H4, []).append(h)
    out = []
    for t in sorted(buckets):
        g = sorted(buckets[t], key=lambda x: x["t"])
        if len(g) != 4 or [x["t"] for x in g] != [t, t + H1, t + 2 * H1, t + 3 * H1]:
            continue
        out.append({"t": t, "o": float(g[0]["o"]), "h": max(float(x["h"]) for x in g), "l": min(float(x["l"]) for x in g),
                    "c": float(g[-1]["c"]), "v": sum(float(x["v"]) for x in g)})
    return out


def contiguous(bars: Sequence[dict], step: int = H4) -> bool:
    return bool(bars) and all(b["t"] - a["t"] == step for a, b in zip(bars, bars[1:]))
