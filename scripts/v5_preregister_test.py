"""Write docs/V5_TEST_PREREGISTRATION.json and .md -- BEFORE any PSEUDO-HOLDOUT month is downloaded.

    python scripts/v5_preregister_test.py --dev-run v5-... --dev-summary <the DEVELOPMENT run's summary JSON>

Requires docs/V5_UNIVERSE_TEST.json (the frozen V5 universe rule applied to the 30 days before the pseudo-holdout,
plus data-existence probes at its warm-up start). Records the frozen fingerprints, the TEST configuration (without the
dataset and rules snapshot, which only exist after download), the universe, the gates, the DEVELOPMENT run, its
economic survivors with their frozen exits and DEVELOPMENT edges (what Stage 2 / 3 may run on TEST), and the
diagnostic observations pre-registered before the holdout is seen. scripts/run_v5_arena.py --role TEST refuses to
run if anything differs.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.competition.v5_config import PROTOCOL_VERSION, TEST, EconomicGate, RawEdgeGate, V5Gates, load_universe  # noqa: E402
from scripts.run_v5_arena import build_config, fingerprints  # noqa: E402

DOCS = PROJECT / "docs"


def near_misses(summary: dict) -> list[dict]:
    """DEVELOPMENT families that failed the raw-edge gate with a positive pooled gross R and >= the trade minimum:
    recorded now so that what the holdout says about them cannot be cherry-picked afterwards."""
    out = []
    for f in summary.get("families") or []:
        r = f.get("raw_edge") or {}
        if not r.get("passed") and (r.get("mean_r") or 0) > 0 and r.get("checks", {}).get("sample"):
            out.append({"strategy_id": f["strategy_id"], "family": f.get("family"), "horizon": f["horizon"],
                        "dev_gross_r": r.get("mean_r"), "dev_p_mean_le_0": r.get("p_mean_le_0"), "dev_trades": r.get("trades"),
                        "dev_subperiod_gross_r": r.get("subperiod_mean_r"),
                        "dev_side_split": {k: v for k, v in (f.get("by_side") or {}).items()},
                        "failed_checks": [k for k, v in (r.get("checks") or {}).items() if not v]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-run", required=True)
    ap.add_argument("--dev-summary", required=True)
    ap.add_argument("--dev-config", default="", help="the DEVELOPMENT run's config fingerprint (from its log)")
    ap.add_argument("--dev-dataset", default="", help="the DEVELOPMENT archive fingerprint")
    args = ap.parse_args()
    out = DOCS / "V5_TEST_PREREGISTRATION.json"
    if out.exists():
        print(f"{out} already exists: a pre-registration is written once")
        return 2
    dev = json.loads(Path(args.dev_summary).read_text(encoding="utf-8"))
    if dev.get("run_id") != args.dev_run or dev.get("dataset_role") != "DEVELOPMENT":
        print("the summary is not the DEVELOPMENT run", args.dev_run)
        return 2
    universe = load_universe(TEST)
    fps = fingerprints()
    cfg = build_config(TEST, universe, fps)
    econ = {f"{e['strategy_id']}|{e['horizon']}": e for e in dev.get("economic") or []}
    survivors = [{"strategy_id": s["strategy_id"], "horizon": s["horizon"], "family": s.get("family"), "exit": s.get("exit"),
                  "family_edge": {"raw_edge_r": s.get("raw_edge_r"), "raw_edge_trades": s.get("raw_edge_trades"),
                                  "net_edge_r": ((econ.get(f"{s['strategy_id']}|{s['horizon']}") or {}).get("economic_20") or {}).get("mean_r")}}
                 for s in dev.get("survivors") or []
                 if (econ.get(f"{s['strategy_id']}|{s['horizon']}") or {}).get("verdict") == "PASSED"]
    misses = near_misses(dev)
    reg = {
        "protocol": PROTOCOL_VERSION, "written_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "role": "TEST (PSEUDO-HOLDOUT: V1-V4 used parts of this period; it is NOT untouched data)",
        "window": {"months": list(TEST.months), "trade_from": TEST.trade_from, "trade_to": TEST.trade_to,
                   "warm_up_from": TEST.months[0], "subperiods": [list(x) for x in TEST.subperiods]},
        "data_rule": "no PSEUDO-HOLDOUT month's price, funding or positioning data was downloaded before this file existed",
        "fingerprints": fps, "config_fingerprint": cfg.fingerprint(),
        "universe": universe["snapshot"]["universe"], "universe_fingerprint": universe["fingerprint"],
        "scoring_period": universe["snapshot"].get("scoring_period"),
        "gates": {"raw_edge": RawEdgeGate().to_dict(), "economic": EconomicGate().to_dict(), "bot": V5Gates().to_dict()},
        "dev_run_id": args.dev_run, "dev_config_fingerprint": args.dev_config or None,
        "dev_dataset_fingerprint": args.dev_dataset or None,
        "dev_advanced_set": dev.get("advanced_set"), "dev_passed": dev.get("passed_all") or [],
        "survivors": survivors,
        "stages_on_test": ("Stage 1 for every family x class (baseline exits; replication of the raw-edge test). Stages 2-3 "
                           "only for the DEVELOPMENT economic survivors listed here, with their frozen DEVELOPMENT exits; "
                           "nothing is selected on TEST." + (" There are none, so only Stage 1 runs." if not survivors else "")),
        "advanced": "a bot is ADVANCED only if it passes every gate in DEVELOPMENT (run dev_run_id) AND in this TEST",
        "expected": ("ADVANCED SET = NONE with certainty: no V5 family x class passed the DEVELOPMENT raw-edge gate"
                     if not survivors else "decided by the gates"),
        "diagnostic_near_misses": misses,
        "diagnostic_rule": ("Each DEVELOPMENT near-miss is DIRECTIONALLY REPLICATED if its TEST pooled gross R (100 USDT "
                            "twins) is > 0 on >= the class trade minimum, and REPLICATED only if it passes the whole TEST "
                            "raw-edge gate. Neither can promote a V5 bot: a replicated near-miss is at most a V6 hypothesis "
                            "that must then be tested on FORWARD data only. Pre-registered expectation for V5.4 HOURLY: its "
                            "DEVELOPMENT gross came from shorts (a falling altcoin market); if it is market beta rather than "
                            "selection, its shorts lose in the mostly rising 2023-09 .. 2025-02 market."),
        "if_it_fails": "V5 is not patched: a failed pseudo-holdout is answered by a V6 hypothesis",
    }
    out.write_text(json.dumps(reg, indent=1), encoding="utf-8")
    md = [f"# V5 PSEUDO-HOLDOUT pre-registration ({reg['written_at']})", "",
          f"Window **{TEST.trade_from} .. {TEST.trade_to}** (warm-up from {TEST.months[0]}); DEVELOPMENT run `{args.dev_run}` "
          f"(advanced set {dev.get('advanced_set')}).", "",
          "This is a PSEUDO-holdout: V1-V4 used parts of this period (docs/V5_DATASET_MAP.md), so it is not untouched data. "
          "No month of it was downloaded for V5 before this file existed.", "",
          f"Config fingerprint `{reg['config_fingerprint']}` (dataset and rules snapshot excluded), universe fingerprint "
          f"`{reg['universe_fingerprint']}`.", "",
          "Universe (frozen V5 rule, scored on " + json.dumps(reg["scoring_period"]) + "): " + ", ".join(reg["universe"]), "",
          "**Stages on the pseudo-holdout.** " + reg["stages_on_test"], "",
          f"**Expected.** {reg['expected']}.", "",
          "**Pre-registered diagnostic near-misses (DEVELOPMENT, failed the raw-edge gate):**", "",
          "| family x class | DEV gross R | P(mean <= 0) | trades | sub-periods | failed checks |", "|---|---|---|---|---|---|"] + [
          f"| {m['strategy_id']} {m['family']} {m['horizon']} | {m['dev_gross_r']} | {m['dev_p_mean_le_0']} | {m['dev_trades']} | "
          f"{m['dev_subperiod_gross_r']} | {', '.join(m['failed_checks'])} |" for m in misses] + [
          "", reg["diagnostic_rule"], "",
          "| source | fingerprint |", "|---|---|"] + [f"| {k} | `{v}` |" for k, v in fps.items()] + [
          "", "Gates: see the JSON. " + reg["advanced"] + ". " + reg["if_it_fails"] + "."]
    (DOCS / "V5_TEST_PREREGISTRATION.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({k: reg[k] for k in ("config_fingerprint", "universe", "universe_fingerprint", "dev_run_id", "survivors")},
                     indent=1))
    print("near misses:", [(m["strategy_id"], m["horizon"], m["dev_gross_r"]) for m in misses])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
