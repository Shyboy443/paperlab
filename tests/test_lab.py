"""Legacy links stay usable after removing the uploaded-file AI cost analyzer."""
import pytest
from fastapi.testclient import TestClient
from tests.conftest import settings_factory


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from app.main import create_app
    app = create_app(settings_factory(data_dir=str(tmp_path_factory.mktemp("lab"))))
    return TestClient(app)


def test_legacy_links_redirect(client):
    for path, target in (("/lab", "/public/competition"),
                         ("/lab/bots/v6--V6.6-XRP-1H", "/public/competition/bots"),
                         ("/lab/programs", "/public/competition"),          # Programs removed 2026-10-21
                         ("/lab/analyze", "/public/competition"),
                         ("/public/competition/analyzer", "/public/competition"),
                         ("/public/competition/analyzer/old", "/public/competition")):
        for method in ("GET", "HEAD"):
            r = client.request(method, path, follow_redirects=False)
            assert r.status_code == 307 and r.headers["location"] == target
            assert "www-authenticate" not in r.headers
    assert client.get("/public/competition/programs").status_code == 200        # an old link opens the Dashboard


def test_removed_research_scout_markets_programs(client):
    """2026-10-21: the operator removed Markets, Programs, 24/7 Market Research and News & sentiment."""
    assert not hasattr(client.app.state, "scout") and not hasattr(client.app.state, "research")
    for path in ("/api/public/research", "/api/public/scout", "/public/research", "/static/scout.js", "/static/research.html"):
        assert client.get(path, auth=("admin", "test-pw")).status_code == 404, path
    for path in ("/", "/public/competition", "/static/app.js", "/static/public.js", "/static/lab.js", "/static/arena.js"):
        text = client.get(path, auth=("admin", "test-pw")).text
        for removed in ("ProgramsView", "Scout.load", "scout.js", "24/7 Market Research", 'data-panel="programs"',
                        'data-panel="scout"', "News & sentiment", "view === 'markets'"):
            assert removed not in text, (path, removed)


def test_removed_analyzer_endpoints(client):
    assert not hasattr(client.app.state, "analyzer")
    for method, path in (("GET", "/api/analyzer/status"), ("POST", "/api/analyzer")):
        r = client.request(method, path, auth=("admin", "test-pw"), headers={"X-PaperLab": "1"})
        assert r.status_code == 404


def test_dashboard_assets_exclude_removed_feature(client):
    for path in ("/", "/public/competition", "/static/app.js", "/static/public.js", "/static/lab.js"):
        r = client.get(path, auth=("admin", "test-pw"))
        assert r.status_code == 200
        for removed in ("Cost analyzer", "AnalyzerView", "/api/analyzer", 'data-panel="analyzer"'):
            assert removed not in r.text
