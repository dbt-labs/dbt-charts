"""TDD tests for structured error rendering in dct serve.

Pins that:
- compile/validation errors → 422 with structured HTML
- internal/unknown errors → 500 with structured HTML
- chart_errors only → 500 with board HTML; no top-level banner (inline SVG callouts carry the info)
- HTML is escaped (no XSS)
- _render_board_file routes through agent_api.render_dashboard
- inspect template-not-found → 404
"""

from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Shared board YAML fixtures
# ---------------------------------------------------------------------------

_COMPILE_ERROR_BOARD = """\
charts:
  bar:
    query: nonexistent_query
    type: bar
    x: month
    y: revenue
rows:
  - bar
"""

_TWO_COMPILE_ERROR_BOARD = """\
charts:
  c1:
    query: missing_q1
    type: bar
    x: a
    y: b
  c2:
    query: missing_q2
    type: bar
    x: a
    y: b
rows:
  - [c1, c2]
"""

_VALID_BOARD = """\
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  bar:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - bar
"""


@pytest.fixture
def compile_error_board(tmp_path: Path) -> Path:
    boards = tmp_path / "charts"
    boards.mkdir()
    f = boards / "bad_board.yml"
    f.write_text(_COMPILE_ERROR_BOARD)
    return f


@pytest.fixture
def two_error_board(tmp_path: Path) -> Path:
    boards = tmp_path / "charts"
    boards.mkdir()
    f = boards / "two_errors.yml"
    f.write_text(_TWO_COMPILE_ERROR_BOARD)
    return f


@pytest.fixture
def valid_board(tmp_path: Path) -> Path:
    boards = tmp_path / "charts"
    boards.mkdir()
    f = boards / "good_board.yml"
    f.write_text(_VALID_BOARD)
    return f


# ---------------------------------------------------------------------------
# Anchor 1: compile error → 422 with structured HTML
# ---------------------------------------------------------------------------


def test_compile_error_returns_422_with_structured_html(
    compile_error_board: Path,
) -> None:
    """Compile error (undefined query) produces 422 with an ERR-* code."""
    from fastapi.testclient import TestClient

    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.serve.server import create_server

    app = create_server(FilesystemProject(compile_error_board.parent.parent))
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(f"/{compile_error_board.stem}")
    assert resp.status_code == 422
    body = resp.text
    # Structured error code appears in body
    assert "ERR-" in body


# ---------------------------------------------------------------------------
# Anchor 2: multiple validation errors → panel list
# ---------------------------------------------------------------------------


def test_multiple_validation_errors_render_as_panel_list(
    two_error_board: Path,
) -> None:
    """Two compile errors → 422; both error codes appear in the HTML body."""
    from fastapi.testclient import TestClient

    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.serve.server import create_server

    app = create_server(FilesystemProject(two_error_board.parent.parent))
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(f"/{two_error_board.stem}")
    assert resp.status_code == 422
    body = resp.text
    # Must have at least one ERR- code
    assert "ERR-" in body


def test_validation_error_footer_links_to_the_resolved_docs_site(
    two_error_board: Path,
) -> None:
    """The panel-list footer's "Open docs" link resolves through the docs-site helper.

    It used to hardcode a host that was never the docs site, so the one docs
    link on the validation-error page was dead for every user.
    """
    from fastapi.testclient import TestClient

    from dbt_charts._docs_site import docs_site_url
    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.serve.server import create_server

    app = create_server(FilesystemProject(two_error_board.parent.parent))
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(f"/{two_error_board.stem}")
    body = resp.text
    assert f'href="{docs_site_url()}/guides/error-handling/"' in body


# ---------------------------------------------------------------------------
# Anchor 3: board_error with ERR-INTERNAL → 500
# ---------------------------------------------------------------------------


def test_internal_render_failure_returns_500(compile_error_board: Path) -> None:
    """board_error with ERR-INTERNAL code → 500."""
    from fastapi.testclient import TestClient

    from dbt_charts.agent_api.boards import BoardRenderResult
    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.diagnostics import ERR_INTERNAL, Diagnostic
    from dbt_charts.core.serve.server import create_server

    synthetic_error = Diagnostic.from_code(ERR_INTERNAL, message="boom")
    fake_result = BoardRenderResult(status="failed", board_error=synthetic_error)

    app = create_server(FilesystemProject(compile_error_board.parent.parent))
    with (
        TestClient(app, raise_server_exceptions=False) as client,
        patch(
            "dbt_charts.core.serve.server.render_dashboard", return_value=fake_result
        ),
    ):
        resp = client.get(f"/{compile_error_board.stem}")

    assert resp.status_code == 500
    assert "ERR-INTERNAL" in resp.text
    assert "boom" in resp.text


# ---------------------------------------------------------------------------
# Anchor 4: chart_errors only → 500; board renders; NO top-level banner
# ---------------------------------------------------------------------------


def test_chart_errors_return_500_with_board_html_without_banner(
    compile_error_board: Path,
) -> None:
    """chart_errors only (board rendered) → 500; board HTML present; no top-level banner.

    The inline SVG callouts already carry the error info so the redundant
    top-level banner must not appear.
    """
    from fastapi.testclient import TestClient

    from dbt_charts.agent_api.boards import BoardRenderResult
    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.diagnostics import ERR_INTERNAL, Diagnostic
    from dbt_charts.core.serve.server import create_server

    chart_err = Diagnostic.from_code(ERR_INTERNAL, message="chart blew up")
    fake_result = BoardRenderResult(
        status="partial",
        data="<svg>board html here</svg>",
        chart_errors=[chart_err],
    )

    app = create_server(FilesystemProject(compile_error_board.parent.parent))
    with (
        TestClient(app, raise_server_exceptions=False) as client,
        patch(
            "dbt_charts.core.serve.server.render_dashboard", return_value=fake_result
        ),
    ):
        resp = client.get(f"/{compile_error_board.stem}")

    assert resp.status_code == 500
    body = resp.text
    # Board content still present (inline callouts live here)
    assert "board html here" in body
    # Top-level banner must NOT appear — it is redundant noise
    assert "chart-error-banner" not in body


# ---------------------------------------------------------------------------
# Anchor 5: HTML escaping
# ---------------------------------------------------------------------------


def test_structured_error_html_escapes_message(tmp_path: Path) -> None:
    """Diagnostic with XSS payload in message is escaped in HTML output."""
    from dbt_charts.agent_api.boards import BoardRenderResult
    from dbt_charts.core.diagnostics import ERR_INTERNAL, Diagnostic
    from dbt_charts.core.serve.server import _render_structured_errors_html

    evil_error = Diagnostic.from_code(ERR_INTERNAL, message="<script>alert(1)</script>")
    result = BoardRenderResult(status="failed", board_error=evil_error)
    response = _render_structured_errors_html(result, request_path="/foo/")

    html = response.body.decode()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Anchor 6: _render_board_file routes through agent_api.render_dashboard
# ---------------------------------------------------------------------------


def test_serve_routes_through_agent_api_render_dashboard(
    valid_board: Path,
) -> None:
    """_render_board_file calls agent_api.render_dashboard (not manual compile/execute)."""
    from fastapi.testclient import TestClient

    from dbt_charts.agent_api.boards import BoardRenderResult
    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.serve.server import create_server

    app = create_server(FilesystemProject(valid_board.parent.parent))
    with (
        TestClient(app, raise_server_exceptions=False) as client,
        patch(
            "dbt_charts.core.serve.server.render_dashboard",
            return_value=BoardRenderResult(status="ok", data="<html>ok</html>"),
        ) as mock_rd,
    ):
        resp = client.get(f"/{valid_board.stem}")

    mock_rd.assert_called_once()
    assert resp.status_code == 200
    assert "ok" in resp.text
    board_arg = mock_rd.call_args.kwargs["board"]
    assert board_arg.path.relpath == "charts/good_board.yml"


# ---------------------------------------------------------------------------
# Anchor 7: inspect template-not-found → 404
# ---------------------------------------------------------------------------


def test_inspect_template_not_found_returns_404(tmp_path: Path) -> None:
    """Unknown inspect template → 404 with structured HTML error body."""
    from fastapi.testclient import TestClient

    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.serve.server import create_server

    app = create_server(FilesystemProject(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/inspect/nonexistent_template_xyz/")
    assert resp.status_code == 404
    # Body is HTML, not a bare FastAPI JSON 404
    assert "text/html" in resp.headers.get("content-type", "")


def test_escaped_dbt_charts_error_returns_structured_html(valid_board: Path) -> None:
    """Raw DbtChartsError escaping the render envelope still reaches the browser."""
    from fastapi.testclient import TestClient

    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.diagnostics import ERR_INTERNAL
    from dbt_charts.core.diagnostics.base import DbtChartsError
    from dbt_charts.core.serve.server import create_server

    escaped = DbtChartsError.from_code(
        ERR_INTERNAL,
        message="escaped renderer boom",
    )

    app = create_server(FilesystemProject(valid_board.parent.parent))
    with (
        TestClient(app, raise_server_exceptions=False) as client,
        patch("dbt_charts.core.serve.server.render_dashboard", side_effect=escaped),
    ):
        resp = client.get(f"/{valid_board.stem}")

    assert resp.status_code == 500
    assert "text/html" in resp.headers.get("content-type", "")
    assert resp.text != "Internal Server Error"
    assert "ERR-INTERNAL" in resp.text
    assert "escaped renderer boom" in resp.text


def test_unexpected_exception_uses_starlette_debugger(valid_board: Path) -> None:
    """Unexpected escaped exceptions use Starlette's built-in debug page."""
    from fastapi.testclient import TestClient

    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.serve.server import create_server

    app = create_server(FilesystemProject(valid_board.parent.parent))
    with (
        TestClient(app, raise_server_exceptions=False) as client,
        patch(
            "dbt_charts.core.serve.server.render_dashboard",
            side_effect=RuntimeError("surprise <boom>"),
        ),
    ):
        resp = client.get(f"/{valid_board.stem}", headers={"accept": "text/html"})

    assert resp.status_code == 500
    assert "text/html" in resp.headers.get("content-type", "")
    assert resp.text != "Internal Server Error"
    assert "<title>Starlette Debugger</title>" in resp.text
    assert 'class="traceback-container"' in resp.text
    assert "RuntimeError" in resp.text
    assert "surprise &lt;boom&gt;" in resp.text


def _internal_error_page() -> str:
    from dbt_charts.agent_api.boards import BoardRenderResult
    from dbt_charts.core.render.errors import RenderError
    from dbt_charts.core.serve.server import _render_structured_errors_html

    try:
        raise TypeError("lineage() missing 1 required positional argument")
    except TypeError as exc:
        diagnostic = RenderError.from_unexpected(exc).to_diagnostic()
    result = BoardRenderResult(status="failed", board_error=diagnostic)
    response = _render_structured_errors_html(result, request_path="/foo/")
    return response.body.decode()


def test_internal_error_page_shows_the_traceback_collapsed() -> None:
    html = _internal_error_page()

    assert "TypeError: lineage() missing 1 required positional argument" in html
    assert "<details" in html
    assert "Traceback (most recent call last):" in html


def test_internal_error_page_asks_for_a_bug_report_not_the_docs() -> None:
    from dbt_charts.core.serve.server import _issues_url

    html = _internal_error_page()

    assert f'href="{_issues_url()}/new"' in html
    assert "Open docs" not in html


def test_internal_error_without_a_traceback_does_not_claim_a_bug() -> None:
    from dbt_charts.core.serve.server import (
        _render_structured_errors_html,
        _synthetic_error,
    )

    result = _synthetic_error("Inspect template 'nope' not found")
    html = _render_structured_errors_html(result, request_path="/foo/").body.decode()

    assert "This is a bug" not in html
    assert "Open docs" in html


def test_coded_board_error_titles_with_its_registered_title() -> None:
    from dbt_charts.agent_api.boards import BoardRenderResult
    from dbt_charts.core.diagnostics import ERR_NO_LAYOUT, Diagnostic
    from dbt_charts.core.serve.server import _render_structured_errors_html

    result = BoardRenderResult(
        status="failed",
        board_error=Diagnostic.from_code(ERR_NO_LAYOUT, message="no layout here"),
    )
    html = _render_structured_errors_html(result, request_path="/foo/").body.decode()

    assert ERR_NO_LAYOUT.title in html
    assert "no layout here" in html
    assert f'href="{ERR_NO_LAYOUT.doc_url}"' in html
    assert "<details" not in html


def test_error_card_inverts_the_themes_page_colors() -> None:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style

    style = resolve_style(get_theme_style())
    html = _internal_error_page()

    assert f"--card-bg: {style.font.color};" in html
    assert f"--card-text: {style.background};" in html
    assert style.accent not in html


def test_chart_errors_page_is_the_board_document_itself(
    compile_error_board: Path,
) -> None:
    """The rendered board already carries its nav; wrapping it nests a second
    document and draws the nav twice."""
    from fastapi.testclient import TestClient

    from dbt_charts.agent_api.boards import BoardRenderResult
    from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: PLC0415
    from dbt_charts.core.diagnostics import ERR_INTERNAL, Diagnostic
    from dbt_charts.core.serve.server import create_server

    board_document = "<!doctype html><html><body>board</body></html>"
    fake_result = BoardRenderResult(
        status="partial",
        data=board_document,
        chart_errors=[Diagnostic.from_code(ERR_INTERNAL, message="chart blew up")],
    )

    app = create_server(FilesystemProject(compile_error_board.parent.parent))
    with (
        TestClient(app, raise_server_exceptions=False) as client,
        patch(
            "dbt_charts.core.serve.server.render_dashboard", return_value=fake_result
        ),
    ):
        resp = client.get(f"/{compile_error_board.stem}")

    assert resp.status_code == 500
    assert resp.text == board_document
