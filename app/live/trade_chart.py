"""Trade chart for Telegram: a TradingView-style dark candlestick picture of the moment a paper trade opened.

    render(bars, side=..., entry=..., stop=..., tps=[...], entry_ts=..., title=..., subtitle=...)  -> PNG bytes
    chart_for_open(db_path, program, event, name)                                                 -> PNG bytes | None

The candles are the program's own stored 1m bars (fwd6_bars in its forward database, read-only), aggregated to the
bot's timeframe. On top: the entry (blue), the stop (red, dashed) and every target (green, dashed) with price tags on
the right axis, the risk / reward zones shaded from the entry candle like TradingView's position tool, and an arrow
on the entry candle. Pillow only (its bundled font): no system fonts, no browser.
"""
from __future__ import annotations

import io
import sqlite3
import time
from typing import Any, Mapping, Sequence

MINUTE = 60_000
W, H, S = 1100, 620, 2                     # final size, drawn at 2x then downsampled (smooth lines and text)
BG, GRID, TEXT, MUTED = (19, 23, 34), (36, 41, 54), (209, 212, 220), (120, 126, 140)
UP, DOWN, BLUE, AMBER = (38, 166, 154), (239, 83, 80), (76, 141, 255), (240, 185, 11)
FIFTEEN = {"V11.1", "V11.2", "V12.1"}      # bots that decide on 15m (or a day) -> 15m candles; the rest 5m
BARS_SHOWN = 84
RIGHT_PAD_BARS = 10


def _font(size: int) -> Any:
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size * S)
    except TypeError:                       # Pillow < 10.1
        return ImageFont.load_default()


def fmt_price(v: float, ref: float | None = None) -> str:
    a = abs(ref if ref else v)
    dec = 2 if a >= 100 else 4 if a >= 1 else 5 if a >= 0.1 else 6 if a >= 0.01 else 8
    return f"{v:,.{dec}f}"


def aggregate(rows: Sequence[Sequence[float]], tf_ms: int) -> list[list[float]]:
    """1m rows (open_time, o, h, l, c, v) -> tf candles [t, o, h, l, c, v], complete or not."""
    out: list[list[float]] = []
    for t, o, h, lo, c, v in rows:
        b = int(t) // tf_ms * tf_ms
        if out and out[-1][0] == b:
            k = out[-1]
            k[2], k[3], k[4], k[5] = max(k[2], h), min(k[3], lo), c, k[5] + (v or 0.0)
        else:
            out.append([b, o, h, lo, c, v or 0.0])
    return out


def render(bars: Sequence[Sequence[float]], *, side: str, entry: float, stop: float | None, tps: Sequence[float],
           entry_ts: int, title: str, subtitle: str = "", tf_label: str = "5m", footer: str = "") -> bytes:
    from PIL import Image, ImageDraw
    bars = list(bars)[-BARS_SHOWN:]
    if len(bars) < 5:
        raise ValueError("not enough candles")
    w, h = W * S, H * S
    img = Image.new("RGB", (w, h), BG)
    over = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d, o = ImageDraw.Draw(img), ImageDraw.Draw(over)
    f_title, f_sub, f_axis, f_tag = _font(19), _font(13), _font(12), _font(12)
    left, right, top, bottom = 14 * S, w - 104 * S, 64 * S, h - 30 * S
    vol_h = 62 * S
    price_bottom = bottom - vol_h - 8 * S
    n = len(bars) + RIGHT_PAD_BARS
    step = (right - left) / n
    body = max(2.0, step * 0.68)
    levels = [entry] + ([stop] if stop else []) + list(tps)
    lo = min(min(b[3] for b in bars), *levels)
    hi = max(max(b[2] for b in bars), *levels)
    pad = (hi - lo) * 0.07 or hi * 0.01
    lo, hi = lo - pad, hi + pad

    def y(p: float) -> float:
        return top + (hi - p) / (hi - lo) * (price_bottom - top)

    def x(i: int) -> float:
        return left + (i + 0.5) * step

    # grid + price axis
    span = hi - lo
    raw = span / 6
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    tick = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    p = (int(lo / tick) + 1) * tick
    while p < hi:
        yy = y(p)
        d.line([(left, yy), (right, yy)], fill=GRID, width=S)
        d.text((right + 8 * S, yy - 8 * S), fmt_price(p, entry), font=f_axis, fill=MUTED)
        p += tick
    # time axis
    every = max(1, len(bars) // 6)
    for i in range(0, len(bars), every):
        xx = x(i)
        d.line([(xx, top), (xx, bottom)], fill=GRID, width=S)
        d.text((max(2 * S, xx - 18 * S), bottom + 8 * S), time.strftime("%H:%M", time.gmtime(bars[i][0] / 1000)),
               font=f_axis, fill=MUTED)
    # the entry candle
    k_entry = max((i for i, b in enumerate(bars) if b[0] <= entry_ts), default=len(bars) - 1)
    # risk / reward zones from the entry candle to the right edge (TradingView's position tool)
    x0 = x(k_entry) - step / 2
    if stop:
        o.rectangle([x0, min(y(entry), y(stop)), right, max(y(entry), y(stop))], fill=DOWN + (34,))
    if tps:
        o.rectangle([x0, min(y(entry), y(tps[-1])), right, max(y(entry), y(tps[-1]))], fill=UP + (34,))
    # volume
    vmax = max(b[5] for b in bars) or 1.0
    for i, b in enumerate(bars):
        col = UP if b[4] >= b[1] else DOWN
        vh = b[5] / vmax * vol_h
        o.rectangle([x(i) - body / 2, bottom - vh, x(i) + body / 2, bottom], fill=col + (90,))
    img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
    d = ImageDraw.Draw(img)
    # candles
    for i, b in enumerate(bars):
        col = UP if b[4] >= b[1] else DOWN
        xx = x(i)
        d.line([(xx, y(b[2])), (xx, y(b[3]))], fill=col, width=max(1, S))
        y1, y2 = sorted((y(b[1]), y(b[4])))
        d.rectangle([xx - body / 2, y1, xx + body / 2, max(y2, y1 + S)], fill=col)

    # level lines + right-axis tags
    def level(price: float, col: tuple, label: str, dashed: bool) -> None:
        yy = y(price)
        if dashed:
            xx = x0
            while xx < right:
                d.line([(xx, yy), (min(xx + 9 * S, right), yy)], fill=col, width=2 * S)
                xx += 15 * S
        else:
            d.line([(x0, yy), (right, yy)], fill=col, width=2 * S)
        txt = f"{label} {fmt_price(price, entry)}"
        tw = d.textlength(txt, font=f_tag)
        bx = right + 2 * S
        d.rectangle([bx, yy - 10 * S, bx + max(tw + 10 * S, 98 * S), yy + 10 * S], fill=col)
        d.text((bx + 5 * S, yy - 8 * S), txt, font=f_tag, fill=(255, 255, 255))
    for i, tp in enumerate(tps, 1):
        level(tp, UP, f"TP{i}" if len(tps) > 1 else "TP", True)
    if stop:
        level(stop, DOWN, "SL", True)
    level(entry, BLUE, "IN", False)
    # entry arrow
    eb = bars[k_entry]
    xe = x(k_entry)
    if side == "long":
        ya = y(eb[3]) + 8 * S
        d.polygon([(xe, ya), (xe - 9 * S, ya + 15 * S), (xe + 9 * S, ya + 15 * S)], fill=UP)
        label, col, ly = "BUY", UP, ya + 3 * S
    else:
        ya = y(eb[2]) - 8 * S
        d.polygon([(xe, ya), (xe - 9 * S, ya - 15 * S), (xe + 9 * S, ya - 15 * S)], fill=DOWN)
        label, col, ly = "SELL", DOWN, ya - 18 * S
    lw = d.textlength(label, font=f_tag)
    d.rectangle([xe + 12 * S, ly - 2 * S, xe + 20 * S + lw, ly + 15 * S], fill=BG)
    d.text((xe + 16 * S, ly), label, font=f_tag, fill=col)
    # header + footer
    d.text((left, 14 * S), title, font=f_title, fill=TEXT)
    tx = left + d.textlength(title, font=f_title) + 14 * S
    d.text((tx, 20 * S), f"{tf_label} · {subtitle}" if subtitle else tf_label, font=f_sub, fill=MUTED)
    tag = "LONG" if side == "long" else "SHORT"
    tagc = UP if side == "long" else DOWN
    tw = d.textlength(tag, font=f_sub)
    d.rectangle([w - tw - 34 * S, 14 * S, w - 14 * S, 40 * S], fill=tagc)
    d.text((w - tw - 24 * S, 18 * S), tag, font=f_sub, fill=(255, 255, 255))
    if footer:
        d.text((left, top - 22 * S), footer, font=f_axis, fill=MUTED)
    img = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def chart_for_open(db_path: str, program: str, ev: Mapping[str, Any], name: str | None = None) -> bytes | None:
    """The opening chart of one paper trade from the program's stored 1m bars (None when there is too little data)."""
    sym = str(ev.get("symbol") or "")
    entry = float(ev.get("price") or 0.0)
    ts = int(ev.get("ts") or 0)
    if not sym or entry <= 0 or not ts:
        return None
    tf_min = 15 if str(ev.get("strategy_id") or "") in FIFTEEN else 5
    tf_ms = tf_min * MINUTE
    since = ts - (BARS_SHOWN + 2) * tf_ms
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute("SELECT open_time, open, high, low, close, volume FROM fwd6_bars WHERE symbol=? AND "
                           "open_time >= ? AND open_time <= ? ORDER BY open_time", (sym, since, ts)).fetchall()
    finally:
        con.close()
    bars = aggregate(rows, tf_ms)
    if len(bars) < 10:
        return None
    tps = [float(x) for x in (ev.get("tps") or ([ev["target"]] if ev.get("target") else []))]
    coin = sym[:-4] if sym.endswith("USDT") else sym
    pair = f"{coin} / USDT" if sym.endswith("USDT") else sym
    when = time.strftime("%d %b %H:%M UTC", time.gmtime(ts / 1000))
    return render(bars, side=str(ev.get("side") or "long"), entry=entry, stop=float(ev["stop"]) if ev.get("stop") else None,
                  tps=tps, entry_ts=ts, title=pair, subtitle="Bybit perp" + (f" · {name}" if name else ""),
                  tf_label=f"{tf_min}m", footer=f"paper trade · opened {when}")
