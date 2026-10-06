"""Unit tests for serve link-context building and built-in route dispatch.

Covers:
- ``_build_link_context`` slug and root derivation.
- ``GET /data/`` routes to the data browser via ``_BUILTIN_ROUTER``.
- ``GET /inspect/<model>`` routes to the inspect handler.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.serve.server import _build_link_context

_SIMPLE_BOARD = """\
title: T
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
"""


# ---------------------------------------------------------------------------
# _build_link_context: slug + root derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("file_path_rel", "expected_slug", "expected_root"),
    [
        # charts/ is always mounted at /; slug strips the "charts/" prefix
        ("charts/x.yml", "x", ""),
        # top-level file (no "charts/" prefix) — board_slug is unchanged
        ("sales.yml", "sales", ""),
        # nested slug (regression: keep the full nested path)
        ("nested/sales.yml", "nested/sales", ""),
        # index board: the leaf is kept (a file-identity slug), so a
        # relative link authored on this board still resolves against its
        # OWN directory ("reports"), not its parent (regression: collapsing
        # "index" here made board_links._resolve_slug strip one directory
        # level too many for every relative link on an index board).
        ("charts/reports/index.yml", "reports/index", ""),
    ],
)
def test_build_link_context_slug_and_root(
    tmp_path: Path,
    file_path_rel: str,
    expected_slug: str,
    expected_root: str,
) -> None:
    """_build_link_context derives the board slug and root correctly."""
    file_path = tmp_path / file_path_rel
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(_SIMPLE_BOARD)

    ctx = _build_link_context(FilesystemProject(tmp_path).path(file_path_rel))

    assert ctx.current_board_slug == expected_slug
    assert ctx.root == expected_root


# ---------------------------------------------------------------------------
# Built-in route dispatch: /data/ → data browser, /inspect/<model> → inspect
# ---------------------------------------------------------------------------


_PROJECT_BOARD = """\
title: T
queries:
  q: {type: values, rows: [{n: 1}]}
charts:
  t: {query: q, type: table}
rows: [t]
"""


def test_data_routes_to_data_browser(tmp_path: Path) -> None:
    """``GET /data/`` reaches the built-in data browser via ``_BUILTIN_ROUTER``.

    The dispatch code at server.py strips any serve_storage_prefix then passes
    the path to ``_BUILTIN_ROUTER``. With no prefix (the default), ``data/``
    must match the router and return 200 text/html. A bare non-reserved slug
    with no board 404s — proving data/ is specifically router-handled.
    """
    from fastapi.testclient import TestClient

    from dbt_charts.core.serve.server import create_server

    boards = tmp_path / "charts"
    boards.mkdir()
    (boards / "overview.yml").write_text(_PROJECT_BOARD)

    with TestClient(
        create_server(FilesystemProject(tmp_path)),
        raise_server_exceptions=False,
    ) as client:
        data = client.get("/data/")
        missing = client.get("/notaview/")

    assert data.status_code == 200
    assert data.headers.get("content-type", "").startswith("text/html")
    assert missing.status_code == 404


def test_inspect_routes_to_inspect_handler(tmp_path: Path) -> None:
    """``GET /inspect/<model>`` reaches the inspect handler.

    The dispatch code at server.py checks whether ``_route_path`` starts with
    ``inspect/`` and delegates to ``_handle_inspect_route``. With a DuckDB
    project the handler returns 200 text/html.
    """
    from fastapi.testclient import TestClient

    from dbt_charts.core.serve.server import create_server

    boards = tmp_path / "charts"
    boards.mkdir()
    (boards / "overview.yml").write_text(_PROJECT_BOARD)

    with TestClient(
        create_server(FilesystemProject(tmp_path)),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/inspect/model")

    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("text/html")
