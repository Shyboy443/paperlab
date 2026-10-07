"""Validate completed revision runs, attach their verdicts, and write the HTF audit."""
from __future__ import annotations

import difflib
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from app.competition import v14_config as cfg
from scripts.v14_htf_revision_study import ARCHIVE, OUT, report, revision


def main():
    old_report = json.loads(OUT.read_text())
    rows = old_report["runs"]
    assert len(rows) == 27 and len({(r["sid"], r["mode"], r["coin"]) for r in rows}) == 27
    assert all(r.get("revision") == revision() for r in rows if r["mode"] == "NEW")
    for path, sha in old_report["source_sha256"].items():
        if path.startswith("app/"):
            assert hashlib.sha256((PROJECT / path).read_bytes()).hexdigest() == sha, path
    results = report(rows)
    assert results["results"] == old_report["results"]  # metadata changes cannot change the reported outcome
    OUT.write_text(json.dumps(results, indent=1) + "\n")

    manifest = cfg.load_freeze()
    assert cfg.verify_freeze(manifest) == []
    manifest["study_verdicts"] = results["verdicts"]
    manifest["htf_revision"].update(study="docs/V14_HTF_REVISION_STUDY.json",
                                    consistent_improvement=False, paper_only=True)
    cfg.FREEZE_FILE.write_text(json.dumps(manifest, indent=2) + "\n")
    assert cfg.verify_freeze(manifest) == []

    lines = ["# Higher-timeframe logic audit", "", "Completed 2026-10-05. V14 only; local paper implementation.", "",
             "Correctness defects are repaired. The historical replay does **not** show a consistent performance "
             "improvement across this bot set. The revised aggregate result is worse than the archived filter.", "",
             "## Findings and changes", "",
             "- Hourly EMA(84/240) proxies were labelled 4h/daily. The revised filter builds exact UTC OHLC candles "
             "from 4/24 completed hours and evaluates EMA21/10 on their actual closes.",
             "- Entry prices determined both HTF trends, causing pullbacks to change the bigger-picture classification. "
             "Only each HTF's completed close now determines that timeframe's state.",
             "- A candle count alone admitted stale or discontinuous history. Explicit decision cutoffs exclude future "
             "and forming candles. Invalid, duplicate, unordered, missing or stale history blocks new entries.",
             "- One generic bias rule treated all setups alike. Breakouts require 4h agreement; pullbacks allow a "
             "neutral 4h inside an agreeing daily trend. Reversion requires at least one agreeing HTF and no opposing "
             "HTF. ATR-scaled deadbands reduce tiny trend flips.",
             "- Queued orders relied on an earlier filter decision. Dedicated V14 engines recheck the regime before "
             "market/limit execution. A stale feed or regime veto cancels an entry. Signal and fill contexts are "
             "stored in fill metadata; original exits and risk checks still operate.",
             "- Warm-up is 25 days; the existing 600-hour buffer supports 20 complete daily bars. V14 status now "
             "includes entry/fill rejection counts and the latest HTF context. Views are cached per coin/hour.", "",
             "[Rule specification](V14_PROTOCOL.md) and "
             "[Bybit's closed-kline semantics](https://bybit-exchange.github.io/docs/v5/market/kline).", "",
             "## Controlled replay", "",
             "27 runs: the nine bots in BASE (copy alone), OLD (archived HTF proxy), and NEW (revised HTF plus fill "
             "checks). Shared execution engines, 20 USDT daily-rebased books, sizing, fees, recorded funding, "
             "60.001-second latency and 25-day warm-up. Reset/final closes and their costs are included. Net R "
             "includes position-attributed funding. These sums are replay P/L across daily resets, not returns on "
             "a compounded live account.", "",
             "| Family | BASE net USDT | OLD net USDT | NEW net USDT | OLD / NEW trades | OLD / NEW mean net R |",
             "|---|---:|---:|---:|---:|---:|"]
    labels = {"V14.1": "VWAP (six coins)", "V14.2": "RS breakout", "V14.3": "RS pullback", "V14.4": "Limit snapback"}
    for sid, arms in results["results"].items():
        b, o, n = (arms[k] for k in ("BASE", "OLD", "NEW"))
        lines.append(f"| {sid} {labels[sid]} | {b['net_usdt']:+.2f} | {o['net_usdt']:+.2f} | {n['net_usdt']:+.2f} | "
                     f"{o['trades']} / {n['trades']} | {o['net_r']:+.4f} / {n['net_r']:+.4f} |")
    lines += ["", "| Family | OLD halves (mean net R) | NEW halves (mean net R) | Consistent improvement? |",
              "|---|---:|---:|---|"]
    for sid, arms in results["results"].items():
        o, n = arms["OLD"], arms["NEW"]
        lines.append(f"| {sid} | {o['net_r_half1']:+.4f} / {o['net_r_half2']:+.4f} | "
                     f"{n['net_r_half1']:+.4f} / {n['net_r_half2']:+.4f} | No |")
    for mode in ("OLD", "NEW"):
        trades = [t for r in rows if r["mode"] == mode for t in r["trades"]]
        lines.append(f"\n{mode} aggregate net: {sum(t['net'] for t in trades):+.4f} USDT; {len(trades)} closed trades.")
    first = rows[0]
    date = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")
    lines += ["", f"Measurement: {date(first['since'])} through {date(first['end'])} UTC (end exclusive), 72 days.", "",
              "Pullback improves overall net and mean R, but its first half worsens. Breakout loses less mostly "
              "because it trades less; its mean R worsens in both halves. Snapback's total profit grows with a "
              "larger sample, but mean R declines and its first half loses money. VWAP worsens overall. No family "
              "meets the criterion of improved mean net R in both halves. The copied VWAP/breakout strategies are "
              "also negative without either filter; these HTF repairs do not establish an edge in the underlying setups.", "",
              "The first prototype permitted both-neutral reversion entries. Its VWAP discovery-half result "
              "worsened, so that behavior was removed. This was an exploratory revision, not an untouched "
              "pre-registration. The halves use previously studied data; neither is an unseen holdout. No EMA/ATR "
              "parameter grid was optimized. Forward evidence is still required.", "",
              "No NEW runs encountered missing/stale HTF history or a fill-time regime rejection in this complete "
              "cached dataset. Gap/rollover/order-cancellation behavior is covered by targeted regression tests; "
              "the measured P/L differences here come from the signal filter.", "",
              "Raw trades, funding coverage, source and dataset SHA256 hashes: `V14_HTF_REVISION_STUDY.json`. "
              "Original V1 sources, freeze and study: `../snapshots/2026-10-05-htf-audit/`. BASE/OLD arms were reused "
              "from the initial comparison with unchanged common execution inputs; all nine NEW arms were rerun "
              "after the final production rule and engines were complete.", "",
              "## Validation and deployment", "",
              "Full suite: 1,466 passed, 4 skipped (`data/htf-revision-tests.log`). Latest focused tests: 46 passed, "
              "including one additional snapback ranking regression. Coverage includes UTC OHLC construction, "
              "hour/day boundaries, future/forming bars, stale/malformed/gapped history, true HTF EMA inputs, "
              "symmetric policies, preserved exits, pre-ranking filters, fill metadata, queued-order cancellation "
              "and retained risk checks.", "",
              "All eight arena freezes verify. Only V14's protocol/identity changes for this HTF task. V2 verdicts "
              "do not inherit V1's positive snapback label. Revised bots remain paper-only and are not qualified "
              "by this replay. No running service or remote deployment was restarted.", "",
              f"V14 manifest `{manifest['fingerprint']}`; experiment `{cfg.experiment_identity(manifest)[0]}`.", ""]
    (PROJECT / "docs/HTF_LOGIC_AUDIT.md").write_text("\n".join(lines), encoding="utf-8")

    prototype = ARCHIVE / "prototype-comparison.json"
    p = json.loads(prototype.read_text())
    p["provenance_warning"] = ("Exploratory pre-final-rule results. Its original source hashes were generated "
                               "while code was being revised and do not validate executed prototype code. "
                               "Use the final V14_HTF_REVISION_STUDY.json for validated production results.")
    prototype.write_text(json.dumps(p, indent=1) + "\n")

    paths = ["app/strategies/v14/htf.py", "app/competition/v14_config.py", "app/live/v14_worker.py",
             "app/live/v14_engine.py", "app/live/telegram.py", "tests/test_v14.py", "tests/test_v14_closed_htf.py",
             "tests/test_v14_fill_context.py", "docs/V14_FREEZE.json", "docs/V14_PROTOCOL.md", "docs/HTF_LOGIC_AUDIT.md",
             "scripts/v14_htf_study.py", "scripts/v14_htf_revision_study.py", "scripts/write_v14_htf_audit.py"]
    patch = []
    for path in paths:
        before = ARCHIVE / ("v14_htf_study.py" if path == "scripts/v14_htf_study.py" else path)
        previous = before.read_text(encoding="utf-8").splitlines(keepends=True) if before.exists() else []
        current = (PROJECT / path).read_text(encoding="utf-8").splitlines(keepends=True)
        patch.extend(difflib.unified_diff(previous, current, fromfile="a/" + path, tofile="b/" + path))
    (ARCHIVE / "changes.patch").write_text("".join(patch), encoding="utf-8")
    print(json.dumps({"audit": "docs/HTF_LOGIC_AUDIT.md", "verdicts": manifest["study_verdicts"],
                      "freeze": manifest["fingerprint"], "source_hashes_match": True}, indent=2))


if __name__ == "__main__":
    main()
