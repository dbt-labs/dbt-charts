"""Regression tests: check_manifest_refs wired into render_dashboard.

The board render path (board.py) must run Tier-2 manifest-ref validation on
every compile so Cloud's write-path surfaces WARN-DBT-MANIFEST-MISSING in the
status pill. On the compile-only (as_link) arm, ref errors surface in
validation_errors. On the full-render arm, the execution layer handles them
per chart.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import render_dashboard
from dbt_charts.core.execute.env import ExecEnv
from dbt_charts.core.project import InMemoryBoard
from dbt_charts.core.render.render_result import RenderResult

_BOARD_WITH_REF = """\
queries:
  orders:
    sql: "SELECT * FROM {{ ref('fct_orders') }}"
    source: dbt_duckdb
rows: []
"""

_BOARD_NO_REFS = """\
queries:
  plain:
    sql: SELECT 1
    source: analytics
rows: []
"""

_MANIFEST = json.dumps(
    {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"
        },
        "nodes": {
            "model.analytics.fct_orders": {
                "resource_type": "model",
                "name": "fct_orders",
                "schema": "analytics",
                "relation_name": "analytics.fct_orders",
            }
        },
        "sources": {},
    }
)


def _project(tmp_path: Path) -> FilesystemProject:
    (tmp_path / "charts").mkdir()
    return FilesystemProject(tmp_path)


def _project_with_manifest(tmp_path: Path) -> FilesystemProject:
    (tmp_path / "charts").mkdir()
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "manifest.json").write_text(_MANIFEST)
    return FilesystemProject(tmp_path)


def test_manifest_missing_warning_for_ref_query(tmp_path: Path) -> None:
    """Board with ref() and no manifest → WARN-DBT-MANIFEST-MISSING in warnings."""
    project = _project(tmp_path)
    result = render_dashboard(
        board=InMemoryBoard(_BOARD_WITH_REF, path=project.path("charts/_t.yml")),
        project=project,
        as_link=True,
        result_cache=None,
    )
    assert "WARN-DBT-MANIFEST-MISSING" in [w.code for w in result.warnings]


def test_no_manifest_warning_for_plain_query(tmp_path: Path) -> None:
    """Plain SQL with no manifest → no WARN-DBT-MANIFEST-MISSING."""
    project = _project(tmp_path)
    result = render_dashboard(
        board=InMemoryBoard(_BOARD_NO_REFS, path=project.path("charts/_t.yml")),
        project=project,
        as_link=True,
        result_cache=None,
    )
    assert "WARN-DBT-MANIFEST-MISSING" not in [w.code for w in result.warnings]


def test_ref_error_surfaces_in_validation_errors_on_compile_only_arm(
    tmp_path: Path,
) -> None:
    """Unknown ref with a manifest → status="failed" carrying the ref error.

    The compile-only arm (as_link=True) has no execution layer to catch bad refs
    per chart, so ref errors are surfaced in validation_errors rather than silently
    discarded — and a board that cannot execute must not come back "ok" with a
    preview URL. Pins the RenderStatus contract at the top of board.py.
    """
    project = _project_with_manifest(tmp_path)
    result = render_dashboard(
        board=InMemoryBoard(
            """\
queries:
  orders:
    sql: "SELECT * FROM {{ ref('typo_model') }}"
    source: dbt_duckdb
rows: []
""",
            path=project.path("charts/_t.yml"),
        ),
        project=project,
        as_link=True,
        result_cache=None,
    )
    assert "ERR-DBT-REF-UNKNOWN-NODE" in [e.code for e in result.validation_errors]
    assert result.status == "failed"
    assert result.url is None


def test_valid_ref_with_manifest_no_error(tmp_path: Path) -> None:
    """Known ref with a manifest → no validation errors."""
    project = _project_with_manifest(tmp_path)
    result = render_dashboard(
        board=InMemoryBoard(_BOARD_WITH_REF, path=project.path("charts/_t.yml")),
        project=project,
        as_link=True,
        result_cache=None,
    )
    assert result.validation_errors == []


def test_manifest_missing_warning_in_full_render_arm(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """WARN-DBT-MANIFEST-MISSING reaches BoardRenderResult.warnings via as_link=False.

    Patches render and Executor so no warehouse round-trip occurs. Deleting
    check_manifest_refs from the full-render arm of board.py makes this test fail.
    """
    project = local_project(tmp_path)
    mock_registry = MagicMock(spec=ExecEnv)
    render_result = RenderResult(output='{"id": "test", "title": "Board", "items": []}')

    with (
        patch("dbt_charts.core.board.render", return_value=render_result),
        patch("dbt_charts.core.board.make_context"),
    ):
        result = render_dashboard(
            board=InMemoryBoard(_BOARD_WITH_REF, path=project.path("charts/_t.yml")),
            env=mock_registry,
            format="json",
            project=project,
            result_cache=None,
        )

    assert "WARN-DBT-MANIFEST-MISSING" in [w.code for w in result.warnings], (
        "board.py full-render arm must call check_manifest_refs; "
        f"got warnings: {[w.code for w in result.warnings]}"
    )
