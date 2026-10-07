"""Read-only trade statistics for the Telegram messages, from a program's forward database.

    bot_record(db, experiment, bot_key)                         -> (closed trades, wins) of one bot this experiment
    setup_confidence(db, experiment, strategy_id, role, now)    -> (win chance %, trades it rests on, scope) | None

CONFIDENCE (operator, 2026-10-03: "for each trade can we add a confidence"). It is the measured WIN CHANCE of the
setup: the share of this strategy's past paper trades (same strategy and role, every coin) that closed in profit, pulled
toward 50% when there are few of them (+5 wins / +10 trades), so 3 lucky trades never read as 100%. It deliberately
does NOT use the bots' setup-quality scores: checked on 2026-10-03 over 1,780 closed V7 / V8 / V11 trades, the top
third of those scores won no more often than the bottom third (V8 37% vs 38%, V11 53% vs 52%), so a score-based
"confidence" would have been a made-up number. The current experiment is used when it has >= MIN_TRADES of the setup,
else the last 30 days of the program (earlier experiments of the same strategy); below that, no number is shown.
"""
from __future__ import annotations

import sqlite3

MIN_TRADES = 20
PRIOR_WINS, PRIOR_TRADES = 5, 10
LOOKBACK_MS = 30 * 86_400_000
WIN = "COALESCE(SUM(CASE WHEN net > 0 THEN 1 ELSE 0 END), 0)"


def _ro(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)


def bot_record(db_path: str, experiment_id: str, bot_key: str) -> tuple[int, int]:
    con = _ro(db_path)
    try:
        n, w = con.execute(f"SELECT count(*), {WIN} FROM fwd6_trades WHERE experiment_id=? AND bot_key=? AND "
                           "counterfactual=0", (experiment_id, bot_key)).fetchone()
    finally:
        con.close()
    return int(n), int(w)


def setup_confidence(db_path: str, experiment_id: str | None, strategy_id: str, role: str,
                     now_ms: int) -> tuple[int, int, str] | None:
    con = _ro(db_path)
    try:
        n, w = (0, 0)
        if experiment_id:
            n, w = con.execute(f"SELECT count(*), {WIN} FROM fwd6_trades WHERE experiment_id=? AND strategy_id=? AND "
                               "role=? AND counterfactual=0", (experiment_id, strategy_id, role)).fetchone()
        scope = "this test run"
        if n < MIN_TRADES:
            n, w = con.execute(f"SELECT count(*), {WIN} FROM fwd6_trades WHERE strategy_id=? AND role=? AND "
                               "counterfactual=0 AND exit_ts >= ?", (strategy_id, role, now_ms - LOOKBACK_MS)).fetchone()
            scope = "last 30 days"
    finally:
        con.close()
    if n < MIN_TRADES:
        return None
    return round((w + PRIOR_WINS) / (n + PRIOR_TRADES) * 100), int(n), scope
