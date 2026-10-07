"""Run a saved research query against the DuckDB store (read-only: research never touches the live bots).

    python research/query.py                              # list the saved queries
    python research/query.py funding_oi_flush fz=-2 oz=2  # run one, overriding its {name:default} parameters
    python research/query.py --sql "SELECT count(*) FROM features_5m"

Parameters in a .sql file are written {name:default}; key=value arguments replace them (identifiers and numbers only:
the values are checked, so a parameter can't smuggle SQL in).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import duckdb

PROJECT = Path(__file__).resolve().parents[1]
DB = PROJECT / "data" / "research" / "market.duckdb"
QUERIES = Path(__file__).resolve().parent / "queries"
PARAM = re.compile(r"\{(\w+):([^{}]*)\}")
SAFE = re.compile(r"^-?[\w.]*$")


def render(sql: str, args: dict[str, str]) -> str:
    def sub(m: re.Match) -> str:
        v = args.get(m.group(1), m.group(2))
        if m.group(1) == "where":                 # an optional extra filter, e.g. where="AND t.program = 'v11'"
            return v
        if not SAFE.match(v):
            raise SystemExit(f"parameter {m.group(1)}={v!r}: numbers and identifiers only")
        return v
    return PARAM.sub(sub, sql)


def main(argv: list[str]) -> None:
    if not argv:
        for f in sorted(QUERIES.glob("*.sql")):
            first = f.read_text(encoding="utf-8").splitlines()[0].lstrip("- ")
            print(f"{f.stem:22s} {first}")
        return
    if argv[0] == "--sql":
        sql = argv[1]
    else:
        args = dict(a.split("=", 1) for a in argv[1:])
        sql = render((QUERIES / f"{argv[0]}.sql").read_text(encoding="utf-8"), args)
    con = duckdb.connect(str(DB), read_only=True)
    import pandas as pd
    with pd.option_context("display.max_rows", 300, "display.max_columns", 50, "display.width", 250):
        print(con.sql(sql).df().to_string(index=False))
    con.close()


if __name__ == "__main__":
    main(sys.argv[1:])
