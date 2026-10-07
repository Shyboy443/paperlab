"""Verify the completed follow-up, record its verdicts, and write the audit artifact."""
import difflib
import hashlib
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from app.competition import v14_config as cfg
from scripts.v14_htf_revision_study import study_signature

ARCHIVE = PROJECT / "snapshots/2026-10-05-htf-followup"


def main():
    report = json.loads((PROJECT / "docs/V14_HTF_FOLLOWUP_STUDY.json").read_text())
    assert report["input_signature"] == study_signature()
    for path, sha in report["after_source_sha256"].items():
        assert hashlib.sha256((PROJECT / path).read_bytes()).hexdigest() == sha, path
    assert len(report["runs"]) == 18
    log = (PROJECT / "data/htf-followup-final-tests.log").read_text()
    test_summary = next(line for line in log.splitlines() if " passed" in line and " skipped" in line)
    assert "failed" not in test_summary and "error" not in test_summary.lower()
    manifest = cfg.load_freeze()
    assert cfg.verify_freeze(manifest) == []
    manifest["study_verdicts"] = report["verdicts"]
    manifest["htf_followup"].update(study="docs/V14_HTF_FOLLOWUP_STUDY.json", paper_only=True,
                                    validation=test_summary)
    cfg.FREEZE_FILE.write_text(json.dumps(manifest, indent=2) + "\n")
    assert cfg.verify_freeze(manifest) == []

    lines = ["# V14 follow-up logic audit", "", "Completed 2026-10-05. Local paper bots; no service restart or deployment.", "",
             "## Reproduced defects and repairs", "",
             "1. **HTF state drift without an HTF close.** The rolling 600-hour buffer could discard part of the "
             "oldest UTC day/hour group. EMA calculations then used a different history length. Fixed 42-bar 4h "
             "and 20-bar daily calculation windows now make the same completed history produce the same state "
             "across buffer sizes. A new incomplete 4h/day candle cannot change that state.",
             "2. **Cache missed corrections.** Count and last-object identity did not detect in-place corruption "
             "or repairs to a middle hour. Cache keys now include known source-bar contents. Repairs invalidate "
             "a rejected view; corruption invalidates an accepted view. Future bars never enter the key. Returned "
             "dicts are defensive copies. Errors outside the required history no longer block a valid current window.",
             "3. **Invalid candidates consumed signal slots.** The scanners truncated to `max_signals` before "
             "checking whether signal construction succeeded. The ranking loop now counts viable signals and "
             "continues to the next candidate when stop/entry construction fails.",
             "4. **Held coins displaced eligible setups.** V14 scanners/snapback now exclude held and pending "
             "coins before ranking. Entry budgets respect remaining position capacity. When one slot remains, "
             "only the best viable setup is queued, so alphabetic coin tape order cannot award that slot to "
             "a lower-ranked candidate. Pending limits also reserve capacity.",
             "5. **Cancelled orders left stale reservations.** The engine publishes its actual pending symbols "
             "after each execution pass. Snapback reconciles reservations against that book, releasing cancelled, "
             "expired or rejected orders before their old signal expiry. Filled positions remain protected by "
             "the held-coin filter.",
             "6. **Invalid regime values could pass.** A missing/NaN slope or a direction outside -1/0/+1 now "
             "fails closed. The worker snapshots rejection dictionaries before iterating them, avoiding a "
             "concurrent dictionary-size change while bot threads add rejection categories.",
             "7. **Replay checkpoints could mix experiments.** Resume now verifies the full source, dataset, "
             "instrument rules and execution inputs for every arm, including BASE and OLD. Legacy/mismatched "
             "rows are discarded, duplicate checkpoint runs fail, each single-family study has its own checkpoint, "
             "and writes are atomic. Inputs changing during a replay prevent publication.", "",
             "No EMA, ATR deadband, target, stop or holding-time grid was searched. Trading thresholds remain "
             "unchanged. The fixed EMA windows use the first close as their seed; these are reproducible bounded "
             "calculations, not infinite-history EMAs. Entry risk checks and exits remain active.", "",
             "## Verification", "", f"Full suite: **{test_summary}**. Focused HTF/order checks: **71 passed**.", "",
             "The regressions cover rolling-buffer stability, equal histories with different extra bars, in-place "
             "corruption, middle-bar repair, defensive copies, expired old history, invalid candidate geometry, "
             "held/pending exclusions, cancelled reservations, actual engine pending publication, zero signal "
             "budgets, free-slot priority, invalid regimes and study input consistency. Previous cutoff, gap, "
             "UTC rollover, fill-risk and exit-preservation tests also pass.", "",
             "## Paired replay results", "",
             "BEFORE is the preceding completed-candle V14 implementation. Its archived rows were reused only "
             "after verifying dataset SHA256, common frozen dependencies, execution/sizing/parameter profiles "
             "and instrument rules. All nine AFTER bots were replayed with recorded funding and the same "
             "72-day measurement window, 25-day warm-up, daily 20 USDT book resets and 60.001-second latency. "
             "Reset/final close costs are included. Figures are aggregate replay P/L across daily resets, "
             "not returns on a compounded live account.", "",
             "| Family | BEFORE trades | AFTER trades | BEFORE net USDT | AFTER net USDT | BEFORE / AFTER mean net R |",
             "|---|---:|---:|---:|---:|---:|"]
    labels = {"V14.1": "VWAP (six coins)", "V14.2": "RS breakout", "V14.3": "RS pullback", "V14.4": "Limit snapback"}
    for sid, arms in report["results"].items():
        a, b = arms["BEFORE"], arms["AFTER"]
        lines.append(f"| {sid} {labels[sid]} | {a['trades']} | {b['trades']} | {a['net_usdt']:+.2f} | "
                     f"{b['net_usdt']:+.2f} | {a['net_r']:+.4f} / {b['net_r']:+.4f} |")
    lines += ["", "| Family | BEFORE halves (mean net R) | AFTER halves (mean net R) | Verdict |",
              "|---|---:|---:|---|"]
    for sid, arms in report["results"].items():
        a, b = arms["BEFORE"], arms["AFTER"]
        lines.append(f"| {sid} | {a['net_r_half1']:+.4f} / {a['net_r_half2']:+.4f} | "
                     f"{b['net_r_half1']:+.4f} / {b['net_r_half2']:+.4f} | {report['verdicts'][sid]} |")
    for mode in ("BEFORE", "AFTER"):
        trades = [t for r in report["runs"] if r["mode"] == mode for t in r["trades"]]
        lines.append(f"\n{mode} total net: {sum(t['net'] for t in trades):+.4f} USDT; {len(trades)} trades.")
    lines += ["", "These are correctness and selection repairs. Fewer losses alone do not prove a better trade "
              "edge. The table reports trade counts, net R and both chronological halves to expose that distinction. "
              "This dataset was used in earlier studies and is not an unseen holdout; results do not establish "
              "future profitability. Bots remain paper-only and qualification still requires forward evidence.", "",
              "Raw rows, rejection counts, funding coverage and source hashes: `V14_HTF_FOLLOWUP_STUDY.json`. "
              "The incomplete first follow-up run was stopped after the free-slot priority defect was found. "
              "Its output was not published or reused as AFTER evidence; the final nine AFTER runs all share "
              "the completed implementation's input signature.", "",
              "The preceding implementation, freeze and comparison are preserved under "
              "`../snapshots/2026-10-05-htf-followup/`. The first HTF audit now identifies its results as historical. "
              "V14 gets a new experiment identity; other programs' source files and freeze identities are unchanged.", "",
              f"Manifest `{manifest['fingerprint']}`; experiment `{cfg.experiment_identity(manifest)[0]}`.", ""]
    (PROJECT / "docs/HTF_FOLLOWUP_AUDIT.md").write_text("\n".join(lines), encoding="utf-8")

    paths = ("app/strategies/v14/htf.py", "app/live/v14_engine.py", "app/live/v14_worker.py", "docs/V14_FREEZE.json",
             "docs/V14_PROTOCOL.md", "docs/HTF_LOGIC_AUDIT.md", "docs/HTF_FOLLOWUP_AUDIT.md",
             "scripts/v14_htf_revision_study.py", "scripts/v14_htf_followup_study.py", "scripts/write_v14_followup_audit.py",
             "tests/test_v14_closed_htf.py", "tests/test_v14_followup.py")
    patch = []
    for path in paths:
        previous = ARCHIVE / path
        before = previous.read_text(encoding="utf-8").splitlines(keepends=True) if previous.exists() else []
        after = (PROJECT / path).read_text(encoding="utf-8").splitlines(keepends=True)
        patch.extend(difflib.unified_diff(before, after, fromfile="a/" + path, tofile="b/" + path))
    (ARCHIVE / "changes.patch").write_text("".join(patch), encoding="utf-8")
    print(json.dumps({"audit": "docs/HTF_FOLLOWUP_AUDIT.md", "tests": test_summary,
                      "verdicts": report["verdicts"], "source_hashes_verified": True}, indent=2))


if __name__ == "__main__":
    main()
