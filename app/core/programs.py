"""Which bots are in the arena.

Operator decision, 2026-10-08, after the two-year backtest of every bot (docs/BACKTEST_2Y_SUMMARY.json): keep only the
families that made money over two years -- V6.6 (4 of 4 bots profitable, +15% on average) and V6.2 (3 of 4, +4.9%),
with their Jev twins -- and switch off everything that lost or barely traded.

- RETIRED_PROGRAMS never start (app/live/v6_service.py) and are left out of the roster, the dashboard, the deep health
  and Telegram. Their databases stay on /data as history, and their public feeds still answer, marked retired.
- RETIRED_V6_FAMILIES share V6's single frozen forward experiment with the two winners. Taking them out of V6's field
  would change V6's freeze -- which every other program's freeze is built on -- and restart the winners' experiment.
  So they keep computing inside the frozen engine, but are retired from view: no list, no feed, no Telegram, and the
  live mirror never accepted them anyway (app/live/v6_golive.py).
"""
from __future__ import annotations

RETIRED_ON = "2026-10-08"
# 2026-10-09 the operator turned the scalpers back on as paper bots ("scalper bots are not doing anything at all, fix
# that"): V8 / V11 / V13 / V14 run again, still labelled no edge after costs. V7, V9 and V12 stay off.
# 2026-10-12 the operator switched V8 off entirely ("remove the retired bots ... don't waste server performance").
# 2026-10-14 the operator removed EVERY bot program, the V6.2/V6.6 backtest winners included ("just remove the bots,
# keep it empty for now, don't touch the trading desk"). Nothing here runs; the Trading Desk desks are separate.
REACTIVATED: frozenset[str] = frozenset()
RETIRED_PROGRAMS = frozenset({"v6", "v7", "v8", "v9", "v11", "v12", "v13", "v14", "v16", "v17"})
# Since 2026-10-12 these families are no longer in V6's field at all (they stopped computing); the filter stays for
# their old rows in the history databases.
RETIRED_V6_FAMILIES = frozenset({"V6.1", "V6.3", "V6.4", "V6.5"})
WHY = ("Switched off on 2026-10-08: the two-year backtest lost money (V6.2 and V6.6 were the only families that made "
       "money).")


def program_retired(program: str) -> bool:
    return str(program or "").lower() in RETIRED_PROGRAMS


def bot_retired(program: str, key: str) -> bool:
    """Is this bot out of the arena? Every bot of a retired program, and V6's unproven families."""
    p = str(program or "").lower()
    if p in RETIRED_PROGRAMS:
        return True
    return p == "v6" and str(key or "").split("-")[0] in RETIRED_V6_FAMILIES
