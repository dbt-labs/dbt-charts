"""Regression tests: render_dashboard reads a board through Project, not a disk Path.

``render_dashboard`` now takes a ``BoardFile`` (content bound to a ``ProjectPath``)
rather than resolving a path string itself. A stored ``BoardFile`` is built via
``ProjectPath.read_board``, which reads through the ``Project`` abstraction — so a
board living only in a non-filesystem ``Project`` (e.g. Cloud's git-blob store)
renders correctly even though it was never written to the local filesystem.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from dbt_charts.core.board import BoardRenderResult, render_dashboard
from dbt_charts.core.project import Project

# No underscores in the marker: markdown parses `_..._` as emphasis, which
# would split the literal string across a rendered <tspan>.
_MARKER_BOARD = "title: Marker\ntext: MARKERFULLRENDER\n"


class TestRenderDashboardExistenceChecksReadThroughProject:
    def test_as_link_succeeds_when_board_only_in_project_store(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """as_link=True must find the board via the Project, not a disk Path.exists().

        The board lives only in the InMemoryProject store (never written to
        disk); ``ProjectPath.read_board`` reads it through the Project
        abstraction, so as_link=True succeeds regardless of backing store.
        """
        project = in_memory_project(tmp_path, {"sales.yml": _MARKER_BOARD})

        result = render_dashboard(
            board=project.path("sales.yml").read_board(),
            project=project,
            result_cache=None,
            server_port=8765,
            as_link=True,
        )

        assert result.status == "ok", result.validation_errors

    def test_full_render_succeeds_when_board_only_in_project_store(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """The default (non-link) render arm must also read via Project."""
        from dbt_charts.core.execute.env import build_exec_env

        project = in_memory_project(tmp_path, {"sales.yml": _MARKER_BOARD})
        registry = build_exec_env(project, read_only=False)

        result = render_dashboard(
            board=project.path("sales.yml").read_board(),
            project=project,
            env=registry,
            result_cache=None,
            format="html",
        )

        assert result.status == "ok", result.validation_errors
        assert isinstance(result.data, str)
        assert "MARKERFULLRENDER" in result.data


class TestBoardRenderResultRejectsUnknownFields:
    """A field name that no longer exists must fail at construction.

    Regression: a Cloud test built its render result with ``board_cache_policies``
    after that field had been replaced by ``board_query_data_ages``. Pydantic's
    default ``extra="ignore"`` accepted and dropped the stale name, so the double
    looked fine and the drift only surfaced as a wrong snapshot status on main.
    """

    def test_unknown_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            BoardRenderResult.model_validate(
                {
                    "status": "ok",
                    "board_cache_policies": {},
                }
            )

    def test_known_fields_still_default(self) -> None:
        """Forbidding extras must not disturb the defaults callers rely on."""
        result = BoardRenderResult(status="ok")

        assert result.board_query_data_ages == []
        assert result.chart_errors == []
        assert result.warnings == []
        assert result.suppressed_warnings == []
