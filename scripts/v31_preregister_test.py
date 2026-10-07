"""Write the V3.1 TEST pre-registration (docs/V31_PROTOCOL.md section 11) -- BEFORE any TEST month exists.

    python scripts/v31_preregister_test.py --dev-run v31-xxxx > docs/V31_TEST_PREREGISTRATION.json

Everything the TEST will use is fixed here: the source fingerprints, Jev V3 prompt and policy, the risk
profile, the edge gate (thresholds AND its DEVELOPMENT evidence, fingerprinted), maker / taker
assumptions, the field, the qualification thresholds, every bot identity, and the TEST configuration
fingerprint (without the dataset fingerprint, which cannot exist before the download). It refuses to
run if the TEST archive already has any TEST month on disk.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.ai.jev.v3 import POLICY_V3, QUESTIONS_V3, v3_fingerprints  # noqa: E402
from app.competition import v31_run as vr  # noqa: E402
from app.competition.v3_arena import day_ms  # noqa: E402
from app.competition.v31_config import (DEV_TO, TEST_FROM, TEST_MONTHS, TEST_TO, TIER_RISK, V31Config,  # noqa: E402
                                        V31Identity)
from app.competition.v3_config import SYMBOLS  # noqa: E402
from app.competition.v3_venue import load_or_fetch  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.strategies.registry import load_v31, v31_fingerprints  # noqa: E402
from scripts.run_v31_arena import fingerprints  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-run", required=True)
    ap.add_argument("--db", default="")
    ap.add_argument("--test-archive", default="")
    ap.add_argument("--rules", default="")
    args = ap.parse_args(argv)
    data_dir = Path(os.environ.get("DATA_DIR") or PROJECT / "data")
    test_dir = Path(args.test_archive or (data_dir / "v31test"))
    present = [p.name for p in (test_dir / "archive").rglob("*.zip")] if (test_dir / "archive").exists() else []
    leaked = [n for n in present if any(m in n for m in ("2026-05", "2026-06", "2026-07", "2026-08"))]
    if leaked:
        print(f"refusing: TEST months already on disk in {test_dir}: {leaked[:5]}", file=sys.stderr)
        return 2
    storage = Storage(args.db or str(data_dir / "paperlab.db"))
    run = storage.v31_run(args.dev_run)
    if run is None or run.get("dataset_role") != "DEVELOPMENT" or run.get("status") != "complete":
        print(f"refusing: {args.dev_run} is not a complete DEVELOPMENT run", file=sys.stderr)
        return 2
    evidence, ev = vr.evidence_for(storage, args.dev_run, day_ms(DEV_TO) + 86_400_000 - 1)
    snap = load_or_fetch(Path(args.rules or (data_dir / "v3" / "bybit_rules.json")), SYMBOLS)
    cfg = V31Config(dataset_role="TEST", months=TEST_MONTHS, trade_from=TEST_FROM, trade_to=TEST_TO,
                    edge_model_fingerprint=ev["fingerprint"],
                    strategy_fingerprints=tuple(sorted(v31_fingerprints().items())),
                    jev_fingerprints=tuple(sorted(v3_fingerprints().items())),
                    rules_source=f"{snap['source']} @ {snap['fetched_ts']}")
    field = run.get("field") or {"pairs": []}
    sids = sorted(load_v31())
    identities = {
        "CONTROL": [V31Identity(s, c, tf, "CONTROL").key for s in sids for c in cfg.coins for tf in cfg.timeframes],
        "JEV": [V31Identity(p["strategy_id"], p["coin"], p["timeframe"], "JEV", jev_policy="JEV_POLICY_V3").key for p in field["pairs"]],
        "TAKE": [V31Identity(p["strategy_id"], p["coin"], p["timeframe"], "TAKE", jev_policy="JEV_POLICY_V3").key for p in field["pairs"]],
        "RANDOM": f"{cfg.random_seeds} seeds per field pair, action rates from that pair's TEST +JEV3 bot",
        "CAPACITY": [V31Identity(p["strategy_id"], p["coin"], p["timeframe"], "CAPACITY", balance=b).key
                     for p in field["pairs"] for b in cfg.capacity_balances],
    }
    out = {
        "protocol": cfg.protocol, "written_ts": int(time.time() * 1000),
        "statement": ("Written before any TEST month (2026-05..2026-08) was downloaded. The TEST run must match "
                      "every fingerprint here; the ADVANCED SET is the bots that pass every gate on DEVELOPMENT "
                      "and on TEST, and stays NONE if none do."),
        "fingerprints": fingerprints(),
        "config_fingerprint": cfg.fingerprint(ignore_dataset=True),
        "config": {k: v for k, v in cfg.to_dict().items() if k != "dataset_fingerprint"},
        "jev": {"prompt_version": "JEV_PROMPT_V3", "state_version": "JEV_STATE_V3", "policy": POLICY_V3.to_dict(),
                "questions": QUESTIONS_V3, "model": "typesafe/jev-1.13 (pinned by JEV_MODEL)"},
        "risk": {"profile": cfg.risk.to_dict(), "tier_risk": dict(TIER_RISK)},
        "edge_gate": dataclasses.asdict(cfg.edge),
        "maker": dataclasses.asdict(cfg.maker),
        "gates": dataclasses.asdict(cfg.gates),
        "evidence": {"run_id": args.dev_run, "fingerprint": ev["fingerprint"], "observations": ev["observations"],
                     "until": f"{DEV_TO} 23:59:59 UTC (exit time)", "by_family_tf": ev["by_family_tf"]},
        "field": field,
        "dev_passed": ((run.get("summary") or {}).get("passed_all") or []),
        "identities": identities,
        "data": {"months": list(TEST_MONTHS), "warmup_month": TEST_MONTHS[0], "trade_from": TEST_FROM, "trade_to": TEST_TO,
                 "archive_dir": str(test_dir), "source": "https://data.binance.vision/data/futures/um (SHA-256 verified)",
                 "kinds": ["klines", "fundingRate"], "symbols": list(SYMBOLS), "rules": snap.get("source")},
    }
    print(json.dumps(out, indent=1, default=str))
    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
