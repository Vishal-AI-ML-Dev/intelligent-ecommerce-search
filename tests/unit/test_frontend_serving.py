"""Same-origin serving of the built Search UI (Milestone 25A, ADR-008).

These checks cover the FastAPI side only. A TestClient fetch of the page is not a browser test;
the Playwright smoke tests in `frontend/e2e/` exercise the UI-to-API flow.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ecommerce_search.api.app import FRONTEND_PAGE_HEADERS, create_app

INDEX_HTML = '<!doctype html><title>t</title><script type="module" src="/assets/app.js"></script>'


@pytest.fixture
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    return root


def api_paths(app) -> set[str]:
    return set(app.openapi()["paths"])


def test_unset_is_api_only(make_settings):
    app = create_app(make_settings())
    with TestClient(app) as client:
        assert client.get("/").status_code == 404
        assert client.get("/assets/app.js").status_code == 404
        assert client.get("/openapi.json").status_code == 200


def test_configured_build_serves_page_and_assets(make_settings, dist):
    app = create_app(make_settings(frontend_dist_dir=dist))
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert page.text == INDEX_HTML
        assert page.headers["content-type"].startswith("text/html")
        for name, value in FRONTEND_PAGE_HEADERS.items():
            assert page.headers[name] == value
        asset = client.get("/assets/app.js")
        assert asset.status_code == 200 and asset.text == "console.log(1)"
        assert client.get("/assets/missing.js").status_code == 404
        # No catch-all: unknown paths keep the API's JSON 404.
        unknown = client.get("/no-such-page")
        assert unknown.status_code == 404 and unknown.json() == {"detail": "Not Found"}
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_configured_build_does_not_change_the_api_surface(make_settings, dist):
    assert api_paths(create_app(make_settings(frontend_dist_dir=dist))) == api_paths(
        create_app(make_settings())
    )


@pytest.mark.parametrize("path", ["/assets/../secret.txt", "/assets/..%2Fsecret.txt"])
def test_asset_route_does_not_escape_the_build(make_settings, dist, path):
    with TestClient(create_app(make_settings(frontend_dist_dir=dist))) as client:
        response = client.get(path)
        assert response.status_code == 404
        assert "outside" not in response.text


@pytest.mark.parametrize("missing", ["index.html", "assets", "everything"])
def test_invalid_configured_build_is_a_clear_configuration_error(make_settings, dist, missing):
    if missing == "index.html":
        (dist / "index.html").unlink()
        target = dist
    elif missing == "assets":
        (dist / "assets" / "app.js").unlink()
        (dist / "assets").rmdir()
        target = dist
    else:
        target = dist / "does-not-exist"
    with pytest.raises(ValidationError, match="frontend_dist_dir must be a built frontend"):
        make_settings(frontend_dist_dir=target)
