"""V11 Juno (V11.1) / Kilo (V11.2) follow-up to docs/V11_ZAP_STUDY.json (operator, 2026-10-02: "do the same analysis for
the other bots and improve them"). Their take-profits already rest as limits (MAKER_TP, adopted); on top of that:

    MAKER_TP+MINSTOP12 / MAKER_TP+MINSTOP15    a 1.2% / 1.5% minimum stop (live 0.8%)
    MAKER_TP+TOP2                              at most the 2 best candidates per decision bar

DECISION RULE (fixed before running): a change is adopted only if its net R per trade beats MAKER_TP's in BOTH halves;
of the two minimum stops, the better passing one. Aria (V11.4) is not tested: its fees are 0.03 R a trade.

    python scripts/v11_scanner_cost_study.py        (V11_STUDY_WORKERS, default 2)
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
from v11_zap_study import _line, passes, run  # noqa: E402

VARIANTS = ("MAKER_TP", "MAKER_TP+MINSTOP12", "MAKER_TP+MINSTOP15", "MAKER_TP+TOP2")


def main() -> None:
    jobs = [(f, v) for f in ("V11.1", "V11.2") for v in VARIANTS]
    with ProcessPoolExecutor(int(os.environ.get("V11_STUDY_WORKERS", "2"))) as ex:
        rows = list(ex.map(run, jobs))
    chosen = {}
    for f in ("V11.1", "V11.2"):
        mine = [r for r in rows if r["family"] == f]
        base = next(r for r in mine if r["variant"] == "MAKER_TP")
        ok = [r for r in mine if r is not base and passes(r, base)]
        mins = [r for r in ok if "MINSTOP" in r["variant"]]
        if len(mins) > 1:
            keep = max(mins, key=lambda r: r["all"]["net_r"])
            ok = [r for r in ok if "MINSTOP" not in r["variant"] or r is keep]
        for r in mine:
            print(_line(r, "PASSES" if r in ok else ""), flush=True)
        chosen[f] = [r["variant"] for r in ok]
    print("passing:", chosen)
    (PROJECT / "docs" / "V11_SCANNER_COST_STUDY.json").write_text(json.dumps({"runs": rows, "passing": chosen}, indent=1))


if __name__ == "__main__":
    main()
