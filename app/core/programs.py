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
RETIRED_PROGRAMS = frozenset({"v7", "v8", "v9", "v11", "v12", "v13", "v14"})
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
