"""Two-year backtest of every running crypto bot, each through its own program's code.

Each bot uses its program's own strategy class, engine, execution, fees, sizing and leverage, built exactly as its live
worker builds them. The CONTROL and +LADDER roles are covered; there are no Jev twins (an AI cannot be replayed honestly).
The data is Bybit's own 1m history (scripts/v5_bybit.py fetch).

The book is one continuous 20 USDT account per bot from 2024-10-01 to 2026-10-01:
- no daily reset;
- the daily and strategy halts are off, so every bot runs the full two years;
- funding comes from the recorded settlements.

V6 / V7 / V8 read positioning (funding, open interest, premium, account ratio) through the live feed classes. A
decision at hour H sees exactly what the live watermarks show at H (measured on the live V7 database: funding, OI and
ratio up to H, premium and context closes up to H - 1h).

    python scripts/backtest_2y.py prep   --data-dir /data/bt2y      (1h closes for the V6 / V7 market context)
    python scripts/backtest_2y.py fetch9 --data-dir /data/bt2y      (V9: Alpaca IEX 1m bars + the session calendar)
    python scripts/backtest_2y.py run    --data-dir /data/bt2y --workers 10 [--only v8,v11]
    python scripts/backtest_2y.py report --data-dir /data/bt2y

Each bot writes <data-dir>/results/<program>__<key>.json. A finished bot is skipped on a re-run.

Caveats:
- The coin lists were chosen in September 2026; coins that did not trade yet simply have no data until listed.
- Instrument rules (tick, step, minimum order) are today's.
- Several strategies' settings were chosen on parts of this same period. This is not an unseen test.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import io
import json
import os
import sys
import time
import traceback
import zipfile
from array import array
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

MIN, HOUR, DAY = 60_000, 3_600_000, 86_400_000


def ms(d: str) -> int:
    return int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


START, END = ms(os.environ.get("BT_START", "2024-10-01")), ms(os.environ.get("BT_END", "2026-10-01"))
MID = ms(os.environ.get("BT_MID", "2025-10-01"))
WARM_SINGLE = int(os.environ.get("BT_WARM_SINGLE_DAYS", "92")) * DAY
WARM_MULTI = int(os.environ.get("BT_WARM_MULTI_DAYS", "46")) * DAY        # BT_*: a short smoke test only


def month_of(t: int) -> str:
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m")


def months(a: int, b: int) -> list[str]:
    out, t = [], a
    while t < b:
        m = month_of(t)
        if not out or out[-1] != m:
            out.append(m)
        t += DAY
    return out


# -- data -------------------------------------------------------------------------------------------------------------
def load_month(root: Path, sym: str, month: str) -> dict[str, Any] | None:
    p = root / "archive" / "klines" / sym / f"{sym}-1m-{month}.zip"
    if not p.exists():
        return None
    with zipfile.ZipFile(p) as z:
        text = z.read(z.namelist()[0]).decode()
    ts, cols = array("q"), [array("d") for _ in range(6)]
    for r in csv.reader(io.StringIO(text)):
        if not r or not r[0].isdigit():
            continue
        ts.append(int(r[0]))
        for i, k in enumerate((1, 2, 3, 4, 5, 7)):
            cols[i].append(float(r[k] or 0.0))
    return {"ts": ts, "cols": cols}


def tape(root: Path, symbols: list[str], anchor: str | None, a: int, b: int):
    """1m Candles minute by minute, every coin in a fixed order with the anchor last (the live scan feed's order)."""
    from app.core.types import Candle
    order = sorted(s for s in symbols if s != anchor) + ([anchor] if anchor in symbols else [])
    for month in months(a, b):
        data = {s: load_month(root, s, month) for s in order}
        idx = {s: 0 for s in order}
        lo = max(a, ms(month + "-01"))
        nxt = dt.datetime.fromisoformat(month + "-01").replace(tzinfo=dt.timezone.utc)
        nxt = (nxt.replace(year=nxt.year + 1, month=1) if nxt.month == 12 else nxt.replace(month=nxt.month + 1))
        hi = min(b, int(nxt.timestamp() * 1000))
        for s in order:
            d = data[s]
            if d is not None:
                i = 0
                while i < len(d["ts"]) and d["ts"][i] < lo:
                    i += 1
                idx[s] = i
        for m in range(lo, hi, MIN):
            for s in order:
                d = data[s]
                if d is None:
                    continue
                i = idx[s]
                if i < len(d["ts"]) and d["ts"][i] == m:
                    idx[s] = i + 1
                    o, h, l, c, v, q = (col[i] for col in d["cols"])
                    yield Candle(s, "1m", m, o, h, l, c, v, m + MIN - 1, True, q, 0, "historical", 0.0)


def funding_rows(root: Path, sym: str, a: int, b: int) -> list[tuple[int, float]]:
    out = []
    for month in months(a, b):
        p = root / "archive" / "fundingRate" / sym / f"{sym}-fundingRate-{month}.zip"
        if not p.exists():
            continue
        with zipfile.ZipFile(p) as z:
            text = z.read(z.namelist()[0]).decode()
        for r in csv.reader(io.StringIO(text)):
            if r and r[0].isdigit():
                out.append((int(r[0]), float(r[2])))
    return sorted(set(out))


class HourCaps(dict):
    """The live watermark for any exact hour H, as offsets from H."""

    def __init__(self, offsets: dict[str, int]):
        super().__init__()
        self.offsets = offsets

    def __contains__(self, h: object) -> bool:
        return isinstance(h, int) and h % HOUR == 0

    def get(self, h: Any, default: Any = None) -> Any:
        h = int(h)
        return {k: h + off for k, off in self.offsets.items()} if h % HOUR == 0 else default


def positioning(root: Path, traded: list[str], breadth: list[str], intervals: dict[str, int], a: int, b: int):
    from app.backtest.bybit_archive import read_series
    from app.competition.v6_features import MarketContextV6, PositioningFeedV6
    pos = root / "positioning"
    feeds = {s: PositioningFeedV6(s, funding=funding_rows(root, s, a - 100 * DAY, b), oi=read_series(pos, s, "oi"),
                                  premium=read_series(pos, s, "premium"), ratio=read_series(pos, s, "ratio"),
                                  caps=HourCaps({"funding": 0, "oi": 0, "premium": -HOUR, "ratio": 0}),
                                  funding_interval_h=intervals.get(s, 480) / 60.0) for s in traded}
    offs = {}
    for s in breadth:
        offs.update({f"close:{s}": -HOUR, f"oi:{s}": 0, f"funding:{s}": 0})
    ctx = MarketContextV6(breadth, caps=HourCaps(offs))
    for s in breadth:
        ctx.close[s].extend(read_series(pos, s, "close"))
        ctx.oi[s].extend(read_series(pos, s, "oi"))
        ctx.funding[s].extend(funding_rows(root, s, a - 100 * DAY, b))
    return feeds, ctx


def prep(args: argparse.Namespace) -> int:
    """1h closes (hour open time, last 1m close of that hour) for every coin: the V6 / V7 market context."""
    from app.backtest.bybit_archive import write_series
    root = Path(args.data_dir)
    syms = json.loads((root / "universe.json").read_text())["universe"]
    for s in syms:
        rows = {}
        for month in months(START - WARM_SINGLE, END):
            d = load_month(root, s, month)
            if d is None:
                continue
            for t, c in zip(d["ts"], d["cols"][3]):
                rows[t // HOUR * HOUR] = c
        n, _ = write_series(root / "positioning", s, "close", sorted(rows.items()))
        print(f"{s}: {n} hourly closes", flush=True)
    return 0


WARM_V9 = 30 * DAY


def fetch9(args: argparse.Namespace) -> int:
    """V9's stocks: Alpaca IEX 1m bars per symbol per month and the regular-session calendar (the paper keys from the
    server's vault, as the live V9 feed; nothing is printed or stored but bars)."""
    import asyncio
    from app.competition import v9_config as v9
    from app.exchange.alpaca_client import AlpacaClient
    from app.live.alpaca_market import AlpacaLiveMarket
    from app.live.v9_worker import alpaca_keys
    root = Path(args.data_dir) / "stocks"
    root.mkdir(parents=True, exist_ok=True)
    keys = alpaca_keys()
    if keys is None:
        raise SystemExit("no Alpaca paper keys")
    feed = AlpacaLiveMarket(v9.SYMBOLS, AlpacaClient("testnet", *keys), lambda *a: None, warmup_from_ms=START - WARM_V9)
    asyncio.run(feed._refresh_calendar())
    (root / "sessions.json").write_text(json.dumps(feed.sessions.iv))
    for sym in v9.SYMBOLS:
        for month in months(START - WARM_V9, END):
            p = root / sym / f"{month}.csv"
            if p.exists():
                continue
            a = max(ms(month + "-01"), START - WARM_V9)
            nxt = dt.datetime.fromisoformat(month + "-01").replace(tzinfo=dt.timezone.utc)
            nxt = (nxt.replace(year=nxt.year + 1, month=1) if nxt.month == 12 else nxt.replace(month=nxt.month + 1))
            b = min(END, int(nxt.timestamp() * 1000))
            bars = asyncio.run(feed.fetch_bars([sym], a, b)).get(sym, [])
            if not feed._fetch_ok:
                raise SystemExit(f"{sym} {month}: Alpaca did not return every page")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("".join(f"{c.open_time},{c.open},{c.high},{c.low},{c.close},{c.volume}\n" for c in bars))
            print(f"{sym} {month}: {len(bars)} bars", flush=True)
            time.sleep(0.4)
    return 0


def stock_tape(root: Path, sym: str):
    from app.core.types import Candle
    from app.live.alpaca_market import Sessions, session_tape
    sessions = Sessions([tuple(x) for x in json.loads((root / "stocks" / "sessions.json").read_text())])
    bars = []
    for month in months(START - WARM_V9, END):
        p = root / "stocks" / sym / f"{month}.csv"
        if p.exists():
            for line in p.read_text().splitlines():
                t, o, h, l, c, v = line.split(",")
                t = int(t)
                bars.append(Candle(sym, "1m", t, float(o), float(h), float(l), float(c), float(v), t + MIN - 1, True,
                                   0.0, 0, "historical", 0.0))
    return session_tape(sym, bars, sessions, until_ms=END), sessions


# -- the field ----------------------------------------------------------------------------------------------------------
def freeze(program: str) -> dict[str, Any]:
    return json.loads((PROJECT / "docs" / f"{program.upper()}_FREEZE.json").read_text(encoding="utf-8"))


def jobs(only: set[str] | None) -> list[dict[str, Any]]:
    from app.competition import v6_config, v7_config, v8_config, v9_config, v11_config, v12_config, v13_config, v14_config
    out: list[dict[str, Any]] = []

    def add(program: str, spec: Any, multi: bool) -> None:
        if only and program not in only:
            return
        out.append({"program": program, "key": spec.key, "strategy_id": spec.strategy_id, "role": spec.role,
                    "coin": spec.coin, "symbol": "" if multi else spec.symbol, "timeframe": spec.timeframe,
                    "horizon": getattr(spec, "horizon", ""), "multi": multi})
    for program, cfg in (("v6", v6_config), ("v7", v7_config)):
        coins = list(freeze(program)["universe"]["traded"])
        for spec in cfg.field_plan(coins, jev=False):
            add(program, spec, False)
    for spec in v8_config.field_plan(list(v8_config.COINS), jev=False, ladder=True):
        add("v8", spec, False)
    for spec in v9_config.field_plan(list(v9_config.SYMBOLS), jev=False):
        add("v9", spec, False)
    for spec in v11_config.field_plan(jev=False):
        add("v11", spec, True)
    for spec in v12_config.field_plan(jev=False):
        add("v12", spec, True)
    for spec in v13_config.field_plan():
        add("v13", spec, True)
    for spec in v14_config.field_plan():
        add("v14", spec, spec.multi)
    out.sort(key=lambda j: (not j["multi"], j["program"], j["key"]))      # the long multi-coin replays first
    return out


def thin(engine_cls: type) -> type:
    """The engine keeps one equity sample per bar (30 coins x 2 years: tens of millions). Keep the last per hour."""
    def _sample_leverage(self, res, sid, eq, _base=engine_cls._sample_leverage):
        _base(self, res, sid, eq)
        e = res.equity
        if len(e) >= 2 and e[-1][0] // HOUR == e[-2][0] // HOUR:
            e[-2] = e[-1]
            e.pop()
    return type("Thin" + engine_cls.__name__, (engine_cls,), {"_sample_leverage": _sample_leverage})


def build(job: dict[str, Any], root: Path):
    """(engine, strategy class, tape symbols, anchor, leverage): what the program's live worker builds for this bot."""
    from app.backtest.funding import FundingSchedule
    from app.competition.v6_config import AGGRESSIVE_V6, EXECUTION_V6, FEES_V6, SizingV6, settings_v6
    from app.core.types import MarketRules
    program, sid = job["program"], job["strategy_id"]
    man = freeze(program)
    rules_all = {s: MarketRules(**r) for s, r in (man.get("rules") or {}).items()}
    no_halts = dict(strategy_halt_pct=1.0, daily_halt_pct=1.0)
    risk = dict(fees=FEES_V6, fee_source="schedule", leverage_policy="needed", cost_gate=None, seed=7)

    def fund(symbols, warm):
        return FundingSchedule({s: funding_rows(root, s, START - warm, END) for s in symbols})

    if program in ("v6", "v7"):
        from app.backtest.replay import ReplayEngine
        if program == "v6":
            from app.strategies.registry import load_v6 as load
        else:
            from app.strategies.v7.active import load_v7 as load
        sym = job["symbol"]
        intervals = {s: int(v) for s, v in (man.get("funding_interval_min") or {}).items()}
        feeds, ctx = positioning(root, [sym], list(man["breadth_set"]), intervals, START - WARM_SINGLE, END)
        cls = load()[sid].for_class(job["horizon"], feeds[sym], ctx)
        eng = thin(ReplayEngine)(dataclasses.replace(settings_v6(), **no_halts), [sym], rules={sym: rules_all[sym]},
                                 funding=fund([sym], WARM_SINGLE), execution=EXECUTION_V6,
                                 sizing=sizing_v6(rules_all), max_risk_pct=AGGRESSIVE_V6.max_risk_pct, **risk)
        return eng, cls, [sym], None, 20, WARM_SINGLE
    if program == "v8":
        from app.competition import v8_config as v8
        from app.live.v8_engine import LevelMakerEngineV8
        from app.strategies.v8.ladder import ladder_class
        sym = job["symbol"]
        base = v8.load_v8()[sid]
        cls = (ladder_class(base) if job["role"] == "LADDER" else base).for_class(job["horizon"])
        eng = thin(LevelMakerEngineV8)(dataclasses.replace(settings_v6(), **no_halts), [sym],
                                       rules={sym: rules_all[sym]}, funding=fund([sym], WARM_SINGLE),
                                       execution=v8.EXECUTION_V8, sizing=sizing_v6(rules_all),
                                       max_risk_pct=AGGRESSIVE_V6.max_risk_pct, maker_tp=True, **risk)
        return eng, cls, [sym], None, 20, WARM_SINGLE
    if program == "v9":
        from app.live.stock_engine import StockReplayEngine as ReplayEngine
        from app.competition import v9_config as v9
        from app.strategies.v9.stocks import load_v9_stock_scalpers
        sym = job["symbol"]
        rules = {s: MarketRules(**r) for s, r in v9.RULES.items()}
        bars, sessions = stock_tape(root, sym)
        cls = load_v9_stock_scalpers()[sid].for_class(job["horizon"], session_at=sessions.at)
        eng = thin(ReplayEngine)(dataclasses.replace(v9.settings_v9(), **no_halts), [sym], rules={sym: rules[sym]},
                                 funding=None, execution=v9.EXECUTION_V9, sizing=sizing_v6(rules),
                                 max_risk_pct=AGGRESSIVE_V6.max_risk_pct,
                                 **{**risk, "fees": v9.FEES_V9})
        return eng, cls, bars, None, v9.LEVERAGE_CAP, WARM_V9
    if program == "v11":
        from app.competition import v11_config as v11
        from app.live.scan_engine import BE_COVER_BPS, ScanReplayEngine
        from app.strategies.v11.ladder import ladder_class
        universe = list(v11.UNIVERSE)
        cls = ladder_class(v11.load_v11()[sid]).for_universe(universe)
        eng = thin(ScanReplayEngine)(dataclasses.replace(v11.settings_v11(), **no_halts), universe,
                                     rules={s: rules_all[s] for s in universe}, funding=fund(universe, WARM_MULTI),
                                     execution=v11.EXECUTION_V11, sizing=sizing_v6(rules_all),
                                     max_risk_pct=AGGRESSIVE_V6.max_risk_pct, be_cover_bps=BE_COVER_BPS, maker_tp=True,
                                     **risk)
        return eng, cls, universe, v11.ANCHOR, v11.LEVERAGE_CEILING, WARM_MULTI
    if program == "v12":
        from app.competition import v12_config as v12
        from app.strategies.v12.bizzy import bizzy_engine, load_v12
        universe = list(v12.UNIVERSE)
        cls = load_v12()[sid].for_universe(universe)
        eng = thin(bizzy_engine())(dataclasses.replace(v12.settings_v12(), **no_halts), universe,
                                   rules={s: rules_all[s] for s in universe}, funding=fund(universe, WARM_MULTI),
                                   execution=v12.EXECUTION_V12, sizing=None, max_risk_pct=None, **risk)
        return eng, cls, universe, v12.ANCHOR, v12.LEVERAGE_CEILING, WARM_MULTI
    if program == "v13":
        from app.competition import v13_config as v13
        from app.live.v13_engine import LimitEntryEngineV13
        from app.strategies.v13.snapback import ANCHOR, load_v13
        universe = list(v13.UNIVERSE)
        cls = load_v13()[sid].for_universe(universe)
        eng = thin(LimitEntryEngineV13)(dataclasses.replace(v13.settings_v13(), **no_halts), universe,
                                        rules={s: rules_all[s] for s in universe}, funding=fund(universe, WARM_MULTI),
                                        execution=v13.EXECUTION_V13, sizing=sizing_v6(rules_all),
                                        max_risk_pct=AGGRESSIVE_V6.max_risk_pct, maker_tp=True, **risk)
        return eng, cls, universe, ANCHOR, v13.LEVERAGE_CEILING, WARM_MULTI
    if program == "v14":
        from app.competition import v14_config as v14
        from app.live.v14_engine import HTFLimitEngine, HTFScalpEngine, HTFScanEngine
        from app.live.scan_engine import BE_COVER_BPS
        from app.strategies.v14 import htf
        kind = htf.SOURCES[sid]["kind"]
        universe = [job["symbol"]] if kind == "single" else list(v14.UNIVERSE)
        cls = htf.build(sid, universe, htf=True)
        kw = dict(rules={s: rules_all[s] for s in universe},
                  funding=fund(universe, WARM_SINGLE if kind == "single" else WARM_MULTI),
                  execution=v14.EXECUTION_V14, sizing=sizing_v6(rules_all),
                  max_risk_pct=AGGRESSIVE_V6.max_risk_pct, maker_tp=True, **risk)
        settings = dataclasses.replace(v14.settings_v14(), **no_halts)
        if kind == "single":
            return thin(HTFScalpEngine)(settings, universe, **kw), cls, universe, None, v14.LEVERAGE_CEILING, WARM_SINGLE
        if kind == "scanner":
            return (thin(HTFScanEngine)(settings, universe, be_cover_bps=BE_COVER_BPS, **kw), cls, universe,
                    v14.ANCHOR, v14.LEVERAGE_CEILING, WARM_MULTI)
        return thin(HTFLimitEngine)(settings, universe, **kw), cls, universe, v14.ANCHOR, v14.LEVERAGE_CEILING, WARM_MULTI
    raise ValueError(program)


# BT_VARIANT=nohalt: the same bots without SizingV6's permanent stop 30% below the peak (the live rule), so a bot that
# the live rules would have halted keeps trading to the end: the strategy's whole two years.
VARIANT = os.environ.get("BT_VARIANT", "")
RESULTS = "results" + (f"_{VARIANT}" if VARIANT else "")


def sizing_v6(rules: Any) -> Any:
    from app.competition.v6_config import AGGRESSIVE_V6, SizingV6
    profile = dataclasses.replace(AGGRESSIVE_V6, halt_drawdown=99.0) if VARIANT == "nohalt" else AGGRESSIVE_V6
    return SizingV6(rules, jev=False, profile=profile)


def result_path(root: Path, job: dict[str, Any], results: str | None = None) -> Path:
    folder = results or RESULTS
    if job['program'] == 'v9':
        # A repaired strategy must never silently reuse its old losing (or
        # winning) replay just because the display key is unchanged.
        folder += f"__v9_{freeze('v9')['fingerprint']}__{START}_{END}"
    return root / folder / f"{job['program']}__{job['key'].replace('+', '_')}.json"


def was_halted(root: Path, job: dict[str, Any]) -> bool:
    p = result_path(root, job, "results")
    return p.exists() and bool(json.loads(p.read_text()).get("rejects", {}).get("bot_halted"))


def summarize(job: dict[str, Any], res: Any, start_eq: float, elapsed: float) -> dict[str, Any]:
    paid: dict[str, float] = {}
    for f in res.fills:
        if f.kind == "funding":
            paid[f.position_id] = paid.get(f.position_id, 0.0) + f.realized_pnl
    trades = sorted(res.trades, key=lambda t: t.exit_ts)
    rows = [{"entry": t.entry_ts, "exit": t.exit_ts, "symbol": t.symbol, "side": t.side,
             "net": round(t.net + paid.get(t.position_id, 0.0), 6), "fees": round(t.fees, 6),
             "funding": round(paid.get(t.position_id, 0.0), 6), "r": round(t.r_multiple, 4), "kind": t.exit_kind}
            for t in trades if t.exit_ts >= START]
    net = sum(r["net"] for r in rows)
    eq_curve = [(t, e) for t, e in res.equity if t >= START]
    final_eq = res.equity[-1][1] if res.equity else start_eq
    peak, dd, dd_pct = start_eq, 0.0, 0.0
    for _, e in eq_curve:
        peak = max(peak, e)
        dd = max(dd, peak - e)
        dd_pct = max(dd_pct, (peak - e) / peak if peak > 0 else 0.0)
    wins = [r["net"] for r in rows if r["net"] > 0]
    losses = [-r["net"] for r in rows if r["net"] < 0]
    monthly: dict[str, float] = {}
    for r in rows:
        k = month_of(r["exit"])
        monthly[k] = round(monthly.get(k, 0.0) + r["net"], 6)
    daily = [(t, e) for i, (t, e) in enumerate(eq_curve) if i == len(eq_curve) - 1 or eq_curve[i + 1][0] // DAY != t // DAY]
    return {**job, "start_equity": start_eq, "final_equity": round(final_eq, 4), "net": round(net, 4),
            "strategy_manifest": freeze('v9')['fingerprint'] if job['program'] == 'v9' else None,
            "return_pct": round(100 * (final_eq - start_eq) / start_eq, 2), "trades": len(rows),
            "win_rate": round(len(wins) / len(rows), 4) if rows else None,
            "profit_factor": round(sum(wins) / sum(losses), 3) if losses else None,
            "fees": round(sum(r["fees"] for r in rows), 4), "funding": round(sum(r["funding"] for r in rows), 4),
            "max_dd_usdt": round(dd, 4), "max_dd_pct": round(100 * dd_pct, 2),
            "year1": round(sum(r["net"] for r in rows if r["exit"] < MID), 4),
            "year2": round(sum(r["net"] for r in rows if r["exit"] >= MID), 4),
            "exits": dict(Counter(r["kind"] for r in rows)), "coins": dict(Counter(r["symbol"] for r in rows)),
            "monthly": monthly, "equity_daily": [(t, round(e, 4)) for t, e in daily], "rejects": dict(res.rejects),
            "signals": res.signals, "elapsed_s": round(elapsed, 1), "trade_list": rows}


def program_is_stock(job: dict[str, Any]) -> bool:
    return job["program"] == "v9"                     # build() returns its session tape itself, not coin names


def run_one(job: dict[str, Any], data_dir: str) -> dict[str, Any]:
    try:
        os.nice(19)
    except Exception:
        pass
    root = Path(data_dir)
    t0 = time.time()
    try:
        eng, cls, symbols, anchor, leverage, warm = build(job, root)
        eng.portfolio.closed_trades = deque(maxlen=None)
        start_eq = float(eng.settings.strategy_starting_balance)
        bars = symbols if program_is_stock(job) else tape(root, symbols, anchor, START - warm, END)
        res = eng.run(cls, bars, since_ms=START, leverage=leverage,
                      signal_tf=job["timeframe"], only_symbol=None if job["multi"] else job["symbol"])
        out = summarize(job, res, start_eq, time.time() - t0)
        p = result_path(root, job)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, separators=(",", ":")))
        tmp.replace(p)
        return {"ok": True, "key": job["key"], "program": job["program"], "net": out["net"], "trades": out["trades"],
                "elapsed": out["elapsed_s"]}
    except Exception as exc:
        return {"ok": False, "key": job["key"], "program": job["program"],
                "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-2000:]}


def run(args: argparse.Namespace) -> int:
    root = Path(args.data_dir)
    only = set(args.only.split(",")) if args.only else None
    todo = [j for j in jobs(only) if args.force or not result_path(root, j).exists()]
    if args.only_halted:
        todo = [j for j in todo if was_halted(root, j)]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} bots to replay, {args.workers} workers, {dt.datetime.utcnow():%Y-%m-%d %H:%M} UTC", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(args.workers) as ex:
        futs = [ex.submit(run_one, j, str(root)) for j in todo]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            if r["ok"]:
                print(f"[{i}/{len(todo)}] {r['program']} {r['key']}: {r['trades']} trades, net {r['net']:+.2f} "
                      f"({r['elapsed']:.0f}s) total {time.time() - t0:.0f}s", flush=True)
            else:
                print(f"[{i}/{len(todo)}] {r['program']} {r['key']} FAILED {r['error']}\n{r['trace']}", flush=True)
    print(f"done in {time.time() - t0:.0f}s", flush=True)
    return 0


def report(args: argparse.Namespace) -> int:
    root = Path(args.data_dir)
    rows = []
    stock_folder = result_path(root, {'program': 'v9', 'key': '_'}).parent
    paths = [p for p in (root / RESULTS).glob('*.json') if not p.name.startswith('v9__')]
    paths += list(stock_folder.glob('v9__*.json'))
    for p in sorted(paths):
        d = json.loads(p.read_text())
        tl = d.pop("trade_list", None) or []
        d["halted"] = bool((d.get("rejects") or {}).get("bot_halted"))     # the live 30%-below-peak stop fired
        d["last_trade"] = dt.datetime.fromtimestamp(tl[-1]["exit"] / 1000, dt.timezone.utc).strftime("%Y-%m-%d")             if tl else None
        rows.append(d)
    rows.sort(key=lambda d: -d["net"])
    keep = ("program", "key", "strategy_id", "role", "coin", "start_equity", "final_equity", "net", "return_pct",
            "trades", "win_rate", "profit_factor", "fees", "funding", "max_dd_usdt", "max_dd_pct", "year1", "year2",
            "exits", "monthly", "equity_daily", "halted", "last_trade", "strategy_manifest")
    out = {"built": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "window": ["2024-10-01", "2026-10-01"],
           "rules": __doc__, "bots": [{k: d.get(k) for k in keep} for d in rows]}
    (root / f"report{'_' + VARIANT if VARIANT else ''}.json").write_text(json.dumps(out, separators=(",", ":")))
    for d in rows:
        print(f"{d['program']:4s} {d['key']:22s} net {d['net']:+9.2f} ret {d['return_pct']:+8.1f}% trades {d['trades']:6d} "
              f"win {d['win_rate'] or 0:.0%} pf {d['profit_factor'] or 0:.2f} dd {d['max_dd_pct']:5.1f}% "
              f"y1 {d['year1']:+8.2f} y2 {d['year2']:+8.2f}" + (f"  HALTED after {d['last_trade']}" if d["halted"] else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prep", "fetch9", "run", "report"):
        p = sub.add_parser(name)
        p.add_argument("--data-dir", required=True)
        if name == "run":
            p.add_argument("--workers", type=int, default=8)
            p.add_argument("--only", default="")
            p.add_argument("--limit", type=int, default=0)
            p.add_argument("--force", action="store_true")
            p.add_argument("--only-halted", action="store_true", help="BT_VARIANT=nohalt: only bots the live rule halted")
    args = ap.parse_args()
    return {"prep": prep, "fetch9": fetch9, "run": run, "report": report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
