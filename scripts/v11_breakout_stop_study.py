"""V11.1 breakout stop study (operator, 2026-10-01: "check thoroughly ... it's not working").

A live replay of the stored bars (48 h) showed V11.1 finds breakouts but drops most of them: 22 passed every filter and the
score cut, 18 were refused because the stop -- below the LOWEST LOW OF THE LAST 3 BARS minus 0.25 ATR -- would have been
3.4% to 25% away (max 3%). On a big breakout candle that low is far below the price.

    python scripts/v11_breakout_stop_study.py

Two stop references for V11.1, everything else live (25/50/25 ladder, its after-TP1 lock, level fills, gap check), 90 days:
    LOW3   the lowest low of the last 3 bars (live)
    LEVEL  the 4h level the breakout broke (the prior 16 bars' high for a long, low for a short)

DECISION RULE (fixed before running): LEVEL replaces LOW3 only if its net R per trade on the DISCOVERY half is at least as
good as LOW3's; the confirmation half and the trade count are reported, not used to choose.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "scripts"))
DAY = 86_400_000


def run(variant: str) -> dict:
    from app.competition import v11_config as v11
    from app.competition.v6_config import AGGRESSIVE_V6, FEES_V6, SizingV6
    from app.core.types import MarketRules
    from app.live.scan_engine import BE_COVER_BPS
    from app.strategies.v11.ladder import LOCK_AFTER_TP1, SHARES
    from app.strategies.v11.scan import ANCHOR, Cand
    from app.strategies.v6.base import close_location, scale
    from v11_activity_check import DATA, tape
    from v11_tp1_study import study_classes

    Study, Engine = study_classes("V11.1", SHARES["V11.1"], LOCK_AFTER_TP1["V11.1"], None, True)

    class Level(Study):
        def scan(self, ctx, t):
            ranks = self.rs_ranks(ctx, t, "15m", 16)
            out = []
            for sym, rk in ranks.items():
                cs = self.current(ctx, sym, "15m", t, 60)
                atr = self.atr(ctx, sym, "15m") if cs else None
                if not cs or not atr:
                    continue
                c = cs[-1]
                med = self.median_volume(cs, 20)
                if med <= 0 or c.volume < 1.5 * med:
                    continue
                prior = cs[-17:-1]
                t1h = self.ctx_trend(ctx, sym, "1h")
                loc = close_location(c)
                hi, lo = max(x.high for x in prior), min(x.low for x in prior)
                if rk >= 0.8 and c.close > hi and t1h == "up" and loc >= 0.6:
                    side, ext = "long", hi                       # the stop sits under the level it broke
                elif rk <= 0.2 and c.close < lo and t1h == "down" and loc <= 0.4:
                    side, ext = "short", lo
                else:
                    continue
                f = {"rs": scale(abs(rk - 0.5), 0.3, 0.5), "volume": scale(c.volume / med, 1.5, 4.0)}
                out.append(Cand(sym, side, ext, atr, sum(f.values()) / 2, f, f"RS {rk:.2f} breakout of the 4h range"))
            return out

    cls = (Level if variant == "LEVEL" else Study)
    uni = json.loads((DATA / "universe.json").read_text())
    syms = [u["symbol"] for u in uni["coins"]]
    end = int(uni["end_ms"])
    start = end - 95 * DAY
    since = start + 5 * DAY
    rules = {s: MarketRules(**r) for s, r in v11.load_freeze()["rules"].items()}
    settings = dataclasses.replace(v11.settings_v11(), strategy_halt_pct=1.0, daily_halt_pct=1.0)
    k = cls.for_universe(syms)
    eng = Engine(settings, syms, rules=rules, seed=7, execution=v11.EXECUTION_V11, fees=FEES_V6, fee_source="schedule",
                 sizing=SizingV6(rules, jev=False), leverage_policy="needed", max_risk_pct=AGGRESSIVE_V6.max_risk_pct,
                 be_cover_bps=BE_COVER_BPS)
    eng.portfolio.closed_trades = deque(maxlen=None)
    res = eng.run(k, tape(syms, ANCHOR, start, end), since_ms=since, leverage=v11.LEVERAGE_CEILING,
                  signal_tf=k.signal_tf, only_symbol=None, reset_at=list(range(since + DAY, end, DAY)))
    mid = since + (end - since) // 2
    out: dict = {"variant": variant}
    for name, lo, hi in (("discovery", since, mid), ("confirmation", mid, end), ("all", since, end)):
        tr = [t for t in res.trades if lo <= t.entry_ts < hi and t.exit_kind != "reset"]
        r = np.array([t.r_multiple for t in tr]) if tr else np.zeros(0)
        pos_, neg = r[r > 0].sum(), -r[r < 0].sum()
        out[name] = {"trades": len(tr), "per_day": round(len(tr) / ((hi - lo) / DAY), 1),
                     "win": round(float((r > 0).mean()), 3) if len(r) else None,
                     "net_r": round(float(r.mean()), 4) if len(r) else None,
                     "net_usdt": round(float(sum(t.net for t in tr)), 3),
                     "pf": round(float(pos_ / neg), 3) if neg > 0 else None}
    return out


def main() -> None:
    with ProcessPoolExecutor(2) as ex:
        rows = list(ex.map(run, ("LOW3", "LEVEL")))
    base, lvl = rows
    adopt = lvl["discovery"]["net_r"] is not None and base["discovery"]["net_r"] is not None and \
        lvl["discovery"]["net_r"] >= base["discovery"]["net_r"]
    for r in rows:
        d, c, a = r["discovery"], r["confirmation"], r["all"]
        print(f"{r['variant']:5s} {a['per_day']:5.1f}/d win {a['win']} net R disc {d['net_r']} | conf {c['net_r']} | all "
              f"{a['net_r']} pf {a['pf']} | net {a['net_usdt']} USDT over {a['trades']} trades", flush=True)
    print("adopt LEVEL:", adopt)
    (PROJECT / "docs" / "V11_BREAKOUT_STOP_STUDY.json").write_text(json.dumps({"runs": rows, "adopt_level": adopt}, indent=1))


if __name__ == "__main__":
    main()
