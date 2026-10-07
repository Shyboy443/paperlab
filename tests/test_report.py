"""Server-rendered public inspection report.

The property under test is that a reader which does NOT execute JavaScript still receives the real
numbers. So every assertion here works on the raw HTML bytes, and the embedded JSON blob is
stripped first wherever the question is "is this value visible" -- otherwise the blob would satisfy
assertions the visible page does not.
"""
from __future__ import annotations

import json
import re

import pytest

from tests.test_public_api import PW, SECRET_PATTERNS, _seed, anon  # noqa: F401  (fixture reuse)

REPORT = "/public/competition/report"


def visible(html: str) -> str:
    """The page minus the machine-readable blob."""
    return re.sub(r'<script type="application/json".*?</script>', "", html, flags=re.S)


# ---- 1, 9: anonymous, and useful without JavaScript ---------------------------------------------

class TestReportIsPublicAndStatic:
    def test_report_works_anonymously(self, anon):
        r = anon.get(REPORT)
        assert r.status_code == 200
        assert "www-authenticate" not in {k.lower() for k in r.headers}

    def test_text_report_works_anonymously(self, anon):
        r = anon.get(REPORT + ".txt")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")

    def test_report_needs_no_javascript(self, anon):
        """No fetch/XHR drives the content, and the body is not a loading placeholder."""
        body = visible(anon.get(REPORT).text)
        assert "loading" not in body.lower()
        assert "fetch(" not in body and "XMLHttpRequest" not in body
        assert "<table" in body and "<tbody" in body

    def test_report_uses_semantic_html(self, anon):
        body = visible(anon.get(REPORT).text)
        for tag in ("<h1", "<h2", "<table", "<thead", "<tbody", "<th", "<td"):
            assert tag in body, tag
        assert "<canvas" not in body

    def test_report_links_to_bot_pages_with_ordinary_hrefs(self, anon):
        body = visible(anon.get(REPORT).text)
        links = re.findall(r'href=\'/public/competition/report/bot/([^\']+)\'', body)
        assert links, "no ordinary anchors to bot reports"


# ---- 2, 3: the HTML carries real values ----------------------------------------------------------

class TestReportCarriesRealData:
    def test_html_contains_real_bot_identifiers(self, anon):
        body = visible(anon.get(REPORT).text)
        assert "T01" in body, "the seeded competitor does not appear in the HTML"

    def test_html_contains_leaderboard_numbers(self, anon):
        body = visible(anon.get(REPORT).text)
        # seeded season competitor: 12 trades, equity 18.00, net -2.00
        assert "18.00" in body and "12" in body

    def test_html_contains_validation_state(self, anon):
        body = visible(anon.get(REPORT).text)
        assert "2021-01" in body and "2026-08" in body
        assert "61" in body, "walk-forward window count missing"

    def test_text_report_contains_the_same_facts(self, anon):
        body = anon.get(REPORT + ".txt").text
        assert "PAPERLAB BOT ARENA" in body
        assert "QUALIFIED SET:" in body
        assert "LEADERBOARD" in body and "VALIDATION" in body

    def test_embedded_json_parses_and_matches(self, anon):
        html = anon.get(REPORT).text
        blob = re.search(r'id="inspection-data">(.*?)</script>', html, re.S).group(1)
        data = json.loads(blob)                      # would raise on NaN/Infinity
        assert set(data) >= {"diagnostics", "quality", "qualification",
                             "season_rows", "validation_rows"}


# ---- 4: qualification -----------------------------------------------------------------------------

class TestQualificationRendered:
    def test_qualified_set_is_explicit(self, anon):
        body = visible(anon.get(REPORT).text)
        assert "QUALIFIED SET" in body
        assert "NONE" in body, "an empty qualified set must say NONE, not be blank"

    def test_failure_reasons_are_counted(self, anon):
        body = visible(anon.get(REPORT).text)
        assert "failure reason" in body.lower()

    def test_text_report_states_the_qualified_set(self, anon):
        assert "QUALIFIED SET: NONE" in anon.get(REPORT + ".txt").text


# ---- 5: diagnostics ---------------------------------------------------------------------------------

class TestDiagnostics:
    def test_html_contains_deployment_diagnostics(self, anon):
        body = visible(anon.get(REPORT).text)
        for label in ("API version", "asset version", "schema version", "generated at",
                      "dataset fingerprint", "config fingerprint"):
            assert label in body, label

    def test_frontend_health_section_is_present(self, anon):
        body = visible(anon.get(REPORT).text)
        assert "Frontend health" in body
        for label in ("NaN metric values", "infinite metric values",
                      "competitors with null metrics"):
            assert label in body, label

    def test_data_quality_warnings_are_rendered(self, anon):
        body = visible(anon.get(REPORT).text)
        assert "Data quality warnings" in body
        assert "NOT ENTERED" in body or "Monte Carlo not yet run" in body


# ---- 6: bot detail -------------------------------------------------------------------------------

class TestBotReport:
    def test_bot_report_works_anonymously(self, anon):
        r = anon.get(REPORT + "/bot/T01")
        assert r.status_code == 200
        assert "www-authenticate" not in {k.lower() for k in r.headers}

    def test_bot_report_shows_cost_decomposition(self, anon):
        body = visible(anon.get(REPORT + "/bot/T01").text)
        for label in ("gross PnL", "commission", "spread crossed", "latency in flight",
                      "market impact", "funding", "net PnL"):
            assert label in body, label

    def test_bot_report_shows_stages_and_gates(self, anon):
        body = visible(anon.get(REPORT + "/bot/T01").text)
        assert "Validation stages" in body and "Qualification gates" in body
        assert "NOT RUN" in body

    def test_bot_report_needs_no_javascript(self, anon):
        body = visible(anon.get(REPORT + "/bot/T01").text)
        assert "fetch(" not in body and "loading" not in body.lower()

    def test_unknown_bot_is_a_clean_404_page(self, anon):
        r = anon.get(REPORT + "/bot/DOES-NOT-EXIST")
        assert r.status_code == 404
        assert "Unknown competitor" in r.text


# ---- 7: no secrets ----------------------------------------------------------------------------------

class TestReportLeaksNothing:
    def _pages(self, anon) -> str:
        return "\n".join(anon.get(p).text for p in
                         (REPORT, REPORT + ".txt", REPORT + "/bot/T01"))

    def test_no_secrets_in_any_report(self, anon):
        blob = self._pages(anon)
        for pattern in SECRET_PATTERNS:
            assert pattern not in blob, f"report leaked {pattern!r}"

    def test_no_filesystem_paths_or_env(self, anon):
        blob = self._pages(anon)
        assert "data_dir" not in blob and "db_path" not in blob
        assert not re.search(r"[A-Za-z]:\\\\Users", blob)

    def test_only_simulated_fills_are_shown(self, anon):
        """The seed carries one live fill with an exchange order id; it must not be published."""
        body = anon.get(REPORT + "/bot/T01").text
        assert "LIVE-ORDER-9911" not in body


# ---- 8: mutations still private ------------------------------------------------------------------------

class TestReportDoesNotWeakenAuth:
    def test_mutations_remain_authenticated(self, anon):
        for path in ("/api/competition/run", "/api/competition/cancel", "/api/kill",
                     "/api/live/arm"):
            r = anon.post(path, json={}, headers={"X-PaperLab": "1"})
            assert r.status_code == 401, path

    def test_private_reads_remain_authenticated(self, anon):
        assert anon.get("/api/state").status_code == 401
        assert anon.get("/api/competition").status_code == 401

    def test_report_routes_are_get_only(self):
        from app.main import create_app
        from tests.conftest import settings_factory
        app = create_app(settings_factory(data_dir="/tmp/reportroutes"))
        for route in app.routes:
            path = getattr(route, "path", "")
            if path.startswith("/public/"):
                assert set(getattr(route, "methods", set())) <= {"GET", "HEAD"}, path

    def test_report_is_not_shadowed_by_the_spa_catch_all(self, anon):
        """Route order matters: /public/competition/{rest} would otherwise serve the JS shell."""
        assert "PUBLIC INSPECTION REPORT" in anon.get(REPORT).text
        assert "PAPERLAB BOT ARENA" in anon.get(REPORT + ".txt").text


# ---- 10: missing values read as words ---------------------------------------------------------------------

class TestMissingValuesAreLabelled:
    def test_no_null_nan_or_undefined_in_visible_cells(self, anon):
        body = visible(anon.get(REPORT).text)
        for bad in ("null", "NaN", "Infinity", "undefined", "None"):
            assert not re.search(r">\s*" + bad + r"\s*<", body), f"rendered a bare {bad}"

    def test_unrun_stages_say_not_run(self, anon):
        body = visible(anon.get(REPORT).text)
        assert "NOT RUN" in body

    def test_not_entered_table_gives_a_reason(self, anon):
        from app.core import report as report_mod
        rows = [{"key": "S17@5x", "strategy_id": "S17", "name": "book scalp", "state": "COMPETING",
                 "score": None, "leverage": 5, "skipped": "order book", "ran": False,
                 "reasons": [], "metrics": None, "rank": 1}]
        data = {"diagnostics": {"api_version": "x"}, "season": None, "season_rows": [],
                "validation": None, "validation_rows": rows}
        data["quality"] = report_mod._quality(data)
        data["qualification"] = report_mod._qualification(rows)
        body = visible(report_mod.render_html(data))
        assert "order book" in body
        assert "Not entered" in body

    @pytest.mark.parametrize("value,expected", [
        (None, "—"), (float("nan"), "INVALID"), (float("inf"), "INVALID"), (1.5, "1.50"),
    ])
    def test_number_formatting_never_emits_raw_specials(self, value, expected):
        from app.core.report import fmt_num
        assert fmt_num(value) == expected

    def test_profit_factor_infinity_is_labelled(self):
        from app.core.report import fmt_pf
        assert fmt_pf(float("inf")) == "INF"
        assert fmt_pf(None) == "—"
