"""BoardRenderResult.warnings is the single unified channel.

Pre-merge: BoardRenderResult had `warnings: list[str]` (compile-time) and a
separate render-time warning list (render-time) side by side. After the
unification this task ships, there is one `warnings: list[Diagnostic]`
field that carries every producer's output.
"""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from unittest.mock import Mock

import pytest

from dbt_charts.core.board import BoardRenderResult
from dbt_charts.core.diagnostics import (
    WARN_TABLE_COLUMNS_OVERFLOW,
    WARN_UNREFERENCED_CHART,
    Diagnostic,
)
from dbt_charts.core.project import Project
from dbt_charts.core.render.warnings.base import WarningContext

_ORPHAN_YAML = """
source: examples_db
queries:
  q: SELECT 1 AS x, 2 AS y
charts:
  used:
    query: q
    type: bar
    x: x
    y: y
  unused:
    query: q
    type: bar
    x: x
    y: y
rows:
  - used
"""


class TestBoardRenderResultUnifiedWarnings:
    def test_warnings_field_holds_diagnostic_objects(self) -> None:
        rd = BoardRenderResult(
            status="ok",
            warnings=[Diagnostic.from_code(WARN_TABLE_COLUMNS_OVERFLOW, message="m")],
        )
        assert len(rd.warnings) == 1
        assert isinstance(rd.warnings[0], Diagnostic)

    def test_compile_warnings_flow_into_unified_field(
        self, tmp_path, local_project: Callable[..., Project]
    ) -> None:
        """A compile-time orphan-chart warning lands on BoardRenderResult.warnings."""
        from dbt_charts.core.board import render_dashboard
        from dbt_charts.core.project import InMemoryBoard

        # Set up a project-less adapter registry mock.
        ok = Mock()
        ok.is_success = True
        ok.data = [{"x": 1, "y": 2}]
        ok.column_descriptions = None
        ok.resolved_relations = None
        ok.truncated_reason = None
        registry = Mock()
        registry.execute.return_value = ok
        registry.project_file_sources.return_value = {}

        project = local_project(tmp_path)
        result = render_dashboard(
            board=InMemoryBoard(_ORPHAN_YAML, path=project.path("charts/_t.yml")),
            adapter_registry=registry,
            format="json",
            project=project,
            result_cache=None,
        )
        # An unused chart triggers compile-side UNREFERENCED_CHART.
        assert result.warnings, "expected at least one compile-side warning"
        codes = {w.code for w in result.warnings}
        assert WARN_UNREFERENCED_CHART.code in codes

    def test_render_warnings_flow_into_unified_field(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
        local_project: Callable[..., Project],
    ) -> None:
        """A render-side detector warning lands on BoardRenderResult.warnings."""
        from dbt_charts.core.board import render_dashboard
        from dbt_charts.core.render.warnings import registry as _registry

        fake = ModuleType("fake_unified_detector")

        def _detect(ctx: object) -> list[Diagnostic]:
            return [
                Diagnostic.from_code(WARN_TABLE_COLUMNS_OVERFLOW, message="unified")
            ]

        fake.detect = _detect  # type: ignore[attr-defined]
        monkeypatch.setattr(_registry, "DETECTORS", [fake])

        import importlib

        renderer_mod = importlib.import_module("dbt_charts.core.render.renderer")

        def _run_all(ctx: WarningContext) -> list[Diagnostic]:
            return fake.detect(ctx)

        monkeypatch.setattr(renderer_mod, "run_all", _run_all)

        ok = Mock()
        ok.is_success = True
        ok.data = [{"x": 1, "y": 2}]
        ok.column_descriptions = None
        ok.resolved_relations = None
        ok.truncated_reason = None
        registry = Mock()
        registry.execute.return_value = ok
        registry.project_file_sources.return_value = {}

        yaml_content = """
source: examples_db
queries:
  q: SELECT 1 AS x, 2 AS y
charts:
  c:
    query: q
    type: bar
    x: x
    y: y
rows:
  - c
"""
        from dbt_charts.core.project import InMemoryBoard

        project = local_project(tmp_path)
        result = render_dashboard(
            board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
            adapter_registry=registry,
            format="json",
            project=project,
            result_cache=None,
        )
        codes = {w.code for w in result.warnings}
        assert WARN_TABLE_COLUMNS_OVERFLOW.code in codes
