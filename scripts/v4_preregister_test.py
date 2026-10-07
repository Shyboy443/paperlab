"""Write docs/V4_TEST_PREREGISTRATION.json and .md -- BEFORE any holdout month is downloaded.

    python scripts/v4_preregister_test.py --dev-run v4-7bdf031bfc

Requires docs/V4_UNIVERSE_TEST.json (the frozen tradeability rule applied to the 30 days before the holdout,
2025-03; it reads Bybit klines for that warm-up month and only checks that the holdout's archive files EXIST).
Records the frozen fingerprints, the TEST configuration (without the dataset and rules snapshot, which only exist
after download), the universe, the gates, the edge model, the field rule and the DEVELOPMENT run whose passes
count toward ADVANCED. scripts/run_v4_arena.py --role TEST refuses to run if anything differs.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.competition.v4_arena import FIELD_MIN_ENTRIES  # noqa: E402
from app.competition.v4_config import (COINS_PER_TF, PARTICIPATION_PER_DAY, PROTOCOL_VERSION, TEST, EdgeConfigV4,  # noqa: E402
                                       V4Gates, load_universe)
from scripts.run_v4_arena import build_config, fingerprints  # noqa: E402

DOCS = PROJECT / "docs"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-run", required=True)
    args = ap.parse_args()
    out = DOCS / "V4_TEST_PREREGISTRATION.json"
    if out.exists():
        print(f"{out} already exists: a pre-registration is written once")
        return 2
    universe = load_universe(TEST)
    fps = fingerprints()
    cfg = build_config(TEST, universe, fps)
    reg = {
        "protocol": PROTOCOL_VERSION, "written_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "role": "TEST", "window": {"months": list(TEST.months), "observe_from": TEST.observe_from,
                                   "trade_from": TEST.trade_from, "trade_to": TEST.trade_to, "warm_up": TEST.months[0]},
        "data_rule": "no TEST month's price data was downloaded before this file existed",
        "fingerprints": fps, "config_fingerprint": cfg.fingerprint(),
        "universe": universe["snapshot"]["universe"], "universe_fingerprint": universe["fingerprint"],
        "scoring_period": universe["snapshot"].get("scoring_period"), "coins_per_tf": {k: list(v) for k, v in universe["per_tf"].items()},
        "allocation": dict(COINS_PER_TF), "participation_per_day": dict(PARTICIPATION_PER_DAY),
        "gates": V4Gates().to_dict(), "edge_model": EdgeConfigV4().to_dict(),
        "field_rule": f"activity only: top 2 coins per family x timeframe by executed entries (>= {FIELD_MIN_ENTRIES}), ties alphabetical",
        "dev_run_id": args.dev_run,
        "advanced": "a bot is ADVANCED only if it passes every gate in DEVELOPMENT (run dev_run_id) AND in this TEST",
        "if_it_fails": "V4 is not patched: a failed TEST is answered by a V5 hypothesis",
    }
    out.write_text(json.dumps(reg, indent=1), encoding="utf-8")
    md = [f"# V4 TEST pre-registration ({reg['written_at']})", "",
          f"Holdout **{TEST.trade_from} .. {TEST.trade_to}** (warm-up {TEST.months[0]}); DEVELOPMENT run `{args.dev_run}`.",
          f"Config fingerprint `{reg['config_fingerprint']}` (dataset and rules snapshot excluded), universe fingerprint "
          f"`{reg['universe_fingerprint']}`.", "",
          "Universe (frozen tradeability rule, scored on " + json.dumps(reg["scoring_period"]) + "): " + ", ".join(reg["universe"]), "",
          "| source | fingerprint |", "|---|---|"] + [f"| {k} | `{v}` |" for k, v in fps.items()] + [
          "", "Gates, edge model, field rule and allocation: see the JSON. " + reg["advanced"] + ". " + reg["if_it_fails"] + "."]
    (DOCS / "V4_TEST_PREREGISTRATION.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({k: reg[k] for k in ("config_fingerprint", "universe", "universe_fingerprint", "dev_run_id")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
