"""Copy the live PAPER bots' closed trades from Railway into the research store (read-only on the server).

    python research/pull_trades.py

Every crypto program (V8 scalpers, V11 scanners, V12 Bizzy, V7, V6), every experiment, counterfactual and rederived rows
excluded. Each trade carries the live half-spread of its entry minute (fwd6_bars.half_spread_bps), so execution research
can compare the spread the bot actually faced with the cost model.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb

PROJECT = Path(__file__).resolve().parents[1]
DB = PROJECT / "data" / "research" / "market.duckdb"

REMOTE = r'''
import json, sqlite3, sys
PROGS = {"v8": "/data/v8-forward.db", "v11": "/data/v11-forward.db", "v12": "/data/v12-forward.db",
         "v7": "/data/v7-forward.db", "v6": "/data/paperlab.db"}
COLS = ("id", "experiment_id", "bot_key", "role", "symbol", "strategy_id", "side", "entry_ts", "exit_ts", "qty",
        "entry_price", "exit_price", "gross", "fees", "slippage", "funding", "net", "r", "exit_kind", "risk_pct",
        "risk_usd", "jev_level")
out = []
for prog, path in PROGS.items():
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    q = (f"SELECT {', '.join('t.' + k for k in COLS)}, b.half_spread_bps FROM fwd6_trades t "
         "LEFT JOIN fwd6_bars b ON b.symbol = t.symbol AND b.open_time = (t.entry_ts / 60000) * 60000 - 60000 "
         "WHERE t.counterfactual = 0 AND COALESCE(t.rederived, 0) = 0 AND t.exit_ts IS NOT NULL")
    for row in c.execute(q):
        out.append([prog, *row])
json.dump(out, sys.stdout)
'''


def main() -> None:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    res = subprocess.run([shutil.which("railway") or "railway", "ssh", "--service", "paperlab", "--", "sh", "-c",
                          "cat > /tmp/pull_trades.py && cd /srv/paperlab && python /tmp/pull_trades.py"],
                         input=REMOTE.encode(), capture_output=True, env=env, cwd=str(PROJECT), timeout=600)
    text = res.stdout.decode()
    rows = json.loads(text[text.index("["):])
    con = duckdb.connect(str(DB))
    con.execute("""CREATE OR REPLACE TABLE paper_trades(program VARCHAR, id VARCHAR, experiment_id VARCHAR,
        bot_key VARCHAR, role VARCHAR, symbol VARCHAR, strategy_id VARCHAR, side VARCHAR, entry_ts BIGINT, exit_ts BIGINT,
        qty DOUBLE, entry_price DOUBLE, exit_price DOUBLE, gross DOUBLE, fees DOUBLE, slippage DOUBLE, funding DOUBLE,
        net DOUBLE, r DOUBLE, exit_kind VARCHAR, risk_pct DOUBLE, risk_usd DOUBLE, jev_level VARCHAR,
        entry_half_spread_bps DOUBLE)""")
    import pandas as pd
    frame = pd.DataFrame(rows, columns=[r[0] for r in con.execute("DESCRIBE paper_trades").fetchall()])
    con.register("incoming", frame)
    con.execute("INSERT INTO paper_trades SELECT * FROM incoming")
    for r in con.execute("SELECT program, count(*), count(DISTINCT experiment_id), round(sum(net), 2), "
                         "round(sum(fees), 2) FROM paper_trades GROUP BY 1 ORDER BY 1").fetchall():
        print(f"{r[0]:4s} trades {r[1]:6d} experiments {r[2]:3d} net {r[3]:+9.2f} fees {r[4]:8.2f}")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
