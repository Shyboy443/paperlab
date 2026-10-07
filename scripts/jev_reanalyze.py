"""Recompute a stored CONTROL vs +JEV summary from its frozen ledger. Makes no Jev request.

    railway ssh -- python scripts/jev_reanalyze.py --run <run_id>

The ledger and the bot rows are the evidence; the summary is derived from them. Re-deriving it with
new analytics (e.g. the never-trade null) changes no decision and no trade.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.competition.jev_experiment import analytics  # noqa: E402
from app.core.storage import Storage  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--db", default="")
    args = ap.parse_args(argv)
    db = args.db or str(Path(os.environ.get("DATA_DIR") or PROJECT / "data") / "paperlab.db")
    st = Storage(db)
    if st.jev_run(args.run) is None:
        print("no such run", args.run)
        return 2
    s = analytics(st, args.run)
    st.update_jev_run(args.run, summary_json=s)
    print(json.dumps({k: v for k, v in s.items() if k not in ("pairs_detail", "calibration", "quintiles")},
                     indent=1, default=str))
    st.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
