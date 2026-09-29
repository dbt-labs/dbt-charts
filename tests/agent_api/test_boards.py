"""Tests for the dashboard verbs in dbt_charts.agent_api.boards.

Covers list_boards, get_board, and render_board.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.agent_api.boards import get_board, list_boards
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import _view_url, render_dashboard
from dbt_charts.core.project import InMemoryBoard, Project


class TestListBoards:
    """Tests for list_boards tool."""

    def test_list_boards_empty_directory(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Test listing dashboards in an empty directory."""
        result = list_boards(local_project(tmp_path))

        assert result.success is True
        assert result.count == 0
        assert result.boards == []

    @pytest.mark.parametrize(
        "filename", ["sales.yml", "sales.yaml"], ids=["yml", "yaml"]
    )
    def test_list_boards_finds_dashboard_file(
        self,
        tmp_path: Path,
        filename: str,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """Test that list_boards finds both .yml and .yaml dashboard files."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / filename).write_text(
            """
title: Sales Dashboard
queries:
  revenue:
    sql: SELECT SUM(amount) as total FROM sales
    source: test
charts:
  revenue_chart:
    query: revenue
    type: kpi
    value: total
rows:
  - revenue_chart
"""
        )

        result = list_boards(local_project(tmp_path))

        assert result.success is True
        assert result.count == 1
        assert len(result.boards) == 1

        dash = result.boards[0]
        assert dash.title == "Sales Dashboard"
        assert "revenue" in dash.queries
        assert "revenue_chart" in dash.charts

    def test_list_boards_reports_board_notes(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
    ) -> None:
        """BoardSummary.notes carries the board's authored notes: (published on
        the MCP dct://boards resource) — not the retired description: key."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "revenue.yml").write_text(
            """
title: Sales Dashboard
notes: Monthly revenue rollup
rows: []
"""
        )

        result = list_boards(local_project(tmp_path))

        assert result.boards[0].notes == "Monthly revenue rollup"

    def test_list_boards_skips_underscore_files(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Test that files starting with underscore are skipped."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "_partials.yml").write_text(
            """
queries:
  shared_query:
    sql: SELECT 1
    source: test
"""
        )

        (boards / "_settings.json").write_text('{"key": "value"}')

        (boards / "dashboard.yml").write_text(
            """
title: Main Dashboard
queries:
  test:
    sql: SELECT 1
    source: test
rows:
  - test
"""
        )

        result = list_boards(local_project(tmp_path))

        assert result.count == 1
        assert result.boards[0].title == "Main Dashboard"

    def test_list_boards_finds_boards_that_declare_no_layout(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A prose-only board and an ``extends``-only board are still boards.

        Both render — ``text:`` is content, and composition folds the base's
        layout into a bare ``extends:`` child — so a listing that misses them
        hides real dashboards from ``list_boards`` and from ``dct search``.
        """
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "readme.yml").write_text(
            "title: Team Readme\ntext: |\n  How we work.\n"
        )
        (boards / "inherited.yml").write_text("extends: base\n")

        result = list_boards(local_project(tmp_path))

        assert {b.title for b in result.boards} == {"Team Readme", "inherited"}

    def test_list_boards_skips_meta_cascade_files(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """``meta.yml`` carries a directory's defaults; it is never its own board."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "meta.yml").write_text(
            "extends: paper\nqueries:\n  shared:\n    sql: SELECT 1\n    source: test\n"
        )
        (boards / "dashboard.yml").write_text("title: Main\ntext: Hello\n")

        result = list_boards(local_project(tmp_path))

        assert [b.title for b in result.boards] == ["Main"]

    def test_list_boards_recursive(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Test recursive dashboard discovery."""
        subdir = tmp_path / "charts" / "sales"
        subdir.mkdir(parents=True)

        (subdir / "overview.yml").write_text(
            """
title: Sales Overview
queries:
  data:
    sql: SELECT 1
    source: test
rows:
  - data
"""
        )

        result = list_boards(local_project(tmp_path), recursive=True)

        assert result.count == 1
        assert "charts/sales/overview.yml" in result.boards[0].file.relpath

    def test_list_boards_non_recursive(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Test non-recursive dashboard discovery: only charts/ root, not subdirs."""
        boards = tmp_path / "charts"
        boards.mkdir()
        nested = boards / "nested"
        nested.mkdir()
        (nested / "deep.yml").write_text(
            """
title: Deep
queries:
  test:
    sql: SELECT 1
    source: test
rows:
  - test
"""
        )

        (boards / "top.yml").write_text(
            """
title: Top
queries:
  test:
    sql: SELECT 1
    source: test
rows:
  - test
"""
        )

        result = list_boards(local_project(tmp_path), recursive=False)

        assert result.count == 1
        assert result.boards[0].title == "Top"

    def test_list_boards_reports_skipped_files(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Test that skipped files are reported with reasons."""
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "invalid.yml").write_text("title: Bad\n  indentation: error")

        (boards / "valid.yml").write_text(
            """
title: Valid
queries:
  test:
    sql: SELECT 1
    source: test
rows:
  - test
"""
        )

        result = list_boards(local_project(tmp_path))

        assert result.count == 1
        assert len(result.skipped_files) >= 1
        skipped_relpaths = [f.file.relpath for f in result.skipped_files]
        assert "charts/invalid.yml" in skipped_relpaths


class TestGetBoard:
    """Tests for get_board tool."""

    def test_get_board_nonexistent_file(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Test getting a nonexistent dashboard."""
        result = get_board(
            tmp_path / "nonexistent.yml", project=local_project(tmp_path)
        )

        assert result.success is False
        assert len(result.errors) >= 1
        assert "not found" in result.errors[0].message.lower()
        assert result.board is None

    def test_get_board_valid_file(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Test getting a valid dashboard."""
        dashboard = tmp_path / "test.yml"
        dashboard.write_text(
            """
title: Test Dashboard
queries:
  test_query:
    sql: SELECT 1 as value
    source: test_profile
charts:
  test_chart:
    query: test_query
    type: kpi
    value: value
rows:
  - test_chart
"""
        )

        result = get_board(dashboard, project=local_project(tmp_path))

        assert result.success is True
        assert result.board is not None
        assert result.board.title == "Test Dashboard"
        assert "test_query" in result.board.queries
        assert "test_chart" in result.board.charts

    def test_get_board_with_raw_yaml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Test getting dashboard with raw YAML included."""
        yaml_content = """
title: Raw Test
queries:
  q:
    sql: SELECT 1
    source: test
charts:
  chart:
    query: q
    type: kpi
    value: value
rows:
  - chart
"""
        dashboard = tmp_path / "raw.yml"
        dashboard.write_text(yaml_content)

        result = get_board(dashboard, project=local_project(tmp_path), include_raw=True)

        assert result.success is True
        assert result.raw_yaml is not None
        assert "title: Raw Test" in result.raw_yaml


class TestGetDashboardReadsThroughProject:
    """get_board must read the board body through Project, not the raw Path."""

    def test_get_board_include_raw_reads_board_through_project(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        """get_board(include_raw=True) must serve both raw_yaml and the
        compiled dashboard from Project, never a raw on-disk Path.

        The project identity ``d.yml`` exists only in the injected in-memory
        Project, not on disk. A raw ``Path("d.yml").read_text()`` (the
        regression) would miss it entirely; sourcing through Project yields the
        RIGHT content for both raw_yaml and the compiled dashboard.
        """
        right_yaml = "title: RIGHT\ntext: from memory\n"
        project = in_memory_project(tmp_path, {"d.yml": right_yaml})

        result = get_board(Path("d.yml"), project=project, include_raw=True)

        assert result.success is True
        assert result.board is not None
        assert result.board.title == "RIGHT"
        assert result.raw_yaml == right_yaml


class TestRenderDashboard:
    """Tests for render_dashboard tool (which replaces validate_board)."""

    def test_render_returns_errors_for_invalid_yaml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Test that render_dashboard returns rich errors for invalid YAML."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(
                "not: valid: yaml: {", path=project.path("charts/_t.yml")
            ),
            project=project,
            adapter_registry=registry,
            result_cache=None,
        )

        assert result.status == "failed"
        assert len(result.validation_errors) > 0

    def test_render_returns_errors_for_bad_structure(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Test render returns validation errors with tips for bad structure."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        yaml_content = """
title: Bad
queries:
  - this_is_a_list_not_a_dict
rows:
  - nonexistent_chart
"""
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
            project=project,
            adapter_registry=registry,
            result_cache=None,
        )

        assert result.status == "failed"
        assert len(result.validation_errors) > 0

    def test_render_valid_dashboard_compiles(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Test that valid static-data YAML compiles and executes successfully."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        yaml_content = """
title: Valid Dashboard
queries:
  data:
    columns: [value]
    values:
      - [1]
charts:
  chart:
    query: data
    type: kpi
    value: value
rows:
  - chart
"""
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
            project=project,
            adapter_registry=registry,
            result_cache=None,
        )
        assert result.status == "ok"

    def test_render_yaml_content_inherits_source_from_boards_meta(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """In-memory render (yaml_content, no path) inherits the project-level
        default source from charts/meta.yml.

        Hosts that compile a stored/edited board in isolation (Cloud, playground)
        pass yaml_content, which bypasses the on-disk meta.yml cascade. A board
        whose SQL query omits `source:` and relies on `charts/meta.yml: source:`
        would otherwise fail with ERR-SOURCE-REQUIRED.
        """
        from dbt_charts.core.execute.adapters import build_adapter_registry

        (tmp_path / "dbt_charts.yml").write_text(
            'sources:\n  db:\n    type: duckdb\n    path: ":memory:"\n'
        )
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "meta.yml").write_text("source: db\n")

        # Covers both a named query and an inline chart query — the inherited
        # default must reach the synthesized inline query too, not only named ones.
        yaml_content = """
title: Inherits Source
queries:
  nums:
    sql: SELECT 1 AS n
charts:
  named_chart:
    query: nums
    type: kpi
    value: n
  inline_chart:
    query:
      sql: SELECT 2 AS n
    type: kpi
    value: n
rows:
  - named_chart
  - inline_chart
"""
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
            project=project,
            adapter_registry=registry,
            result_cache=None,
        )
        assert result.status == "ok", (
            f"expected inherited meta source to satisfy the query, got: {result.validation_errors}"
        )

    def test_render_board_outside_boards_ignores_boards_meta(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A BoardFile's own location is the sole meta cascade root — not a
        separate 'base_dir' concept.

        BoardFile unifies the ref-resolution anchor and the meta.yml cascade
        root into a single `path`, so there is no longer a second,
        independently-supplied base_dir that could silently read a *different*
        directory's meta.yml (the old bug this test used to catch). Content
        anchored outside charts/ (e.g. a sibling `reports/` directory with no
        meta.yml of its own) must not inherit charts/meta.yml's default
        source — the cascade only walks the anchor's own ancestors.
        """
        from dbt_charts.core.execute.adapters import build_adapter_registry

        (tmp_path / "dbt_charts.yml").write_text(
            'sources:\n  db:\n    type: duckdb\n    path: ":memory:"\n'
        )
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "meta.yml").write_text("source: db\n")

        # A sibling directory with no meta.yml of its own — proves the cascade
        # doesn't cross into charts/ from an unrelated anchor.
        reports = tmp_path / "reports"
        reports.mkdir()

        yaml_content = (
            "title: T\nqueries:\n  q:\n    sql: SELECT 1 AS n\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: n\nrows:\n  - c\n"
        )
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(yaml_content, path=project.path("reports/_t.yml")),
            project=project,
            adapter_registry=registry,
            result_cache=None,
        )
        assert result.status == "failed", (
            "content anchored outside charts/ must not inherit charts/meta.yml's "
            "default source"
        )

    def test_render_args_accepts_terminal_format(self) -> None:
        """The agent tool must allow format='terminal' so the chat can show charts."""
        from dbt_charts.agent_api.boards import RenderBoardArgs

        args = RenderBoardArgs.model_validate(
            {"yaml_content": "title: x", "format": "terminal"}
        )
        assert args.format == "terminal"

    def test_render_args_accepts_text_data_format(self) -> None:
        """'text-data' is a real format value, not a separate include_raw_data flag —
        agent tool surfaces map their include_raw_data boolean to it before calling
        render_board (see dbt_charts.ai.tools.resolve_render_format)."""
        from dbt_charts.agent_api.boards import RenderBoardArgs

        args = RenderBoardArgs.model_validate(
            {"yaml_content": "title: x", "format": "text-data"}
        )
        assert args.format == "text-data"

    def test_render_terminal_returns_text(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """format='terminal' renders charts to an ANSI/text string."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  mem:\n    type: duckdb\n    path: ':memory:'\n"
        )
        yaml_content = """
title: Terminal Test
source: mem
queries:
  data:
    sql: "SELECT 'A' AS cat, 10 AS val UNION ALL SELECT 'B' AS cat, 20 AS val"
charts:
  bars:
    query: data
    type: bar
    x: cat
    y: val
rows:
  - bars
"""
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
            project=project,
            adapter_registry=registry,
            result_cache=None,
            format="terminal",
        )
        assert result.status == "ok", [e.message for e in result.validation_errors]
        assert isinstance(result.data, str)
        assert result.data.strip()

    def test_render_requires_path_or_content(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Test that render_dashboard requires one of board or compile_result."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        registry = build_adapter_registry(local_project(tmp_path), read_only=False)
        result = render_dashboard(
            adapter_registry=registry,
            project=local_project(tmp_path),
            result_cache=None,
        )
        assert result.status == "failed"
        assert "must provide" in result.validation_errors[0].message.lower()

    # test_render_rejects_both_path_and_content relocated to
    # dbt-charts/tests/ai/tools/test_dispatch.py::TestToolErrorEnvelopes::
    # test_render_dashboard_both_path_and_content_returns_failure — the
    # mutual-exclusion check now lives in ai/tools/__init__.py::_handle_render,
    # not a separate agent_api.boards dispatch function.

    _TWO_CHART_YAML = """
title: Sales
variables:
  region:
    default: EU
queries:
  totals:
    columns: [value]
    values:
      - [1]
  by_month:
    columns: [month, value]
    values:
      - [jan, 1]
      - [feb, 2]
charts:
  total_sales:
    query: totals
    type: kpi
    value: value
  monthly_sales:
    query: by_month
    type: bar
    x: month
    y: value
rows:
  - total_sales
  - monthly_sales
"""

    def test_render_chart_argument_renders_only_that_chart(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """chart= focuses one existing chart: the render contains it and not
        its siblings — reuse-by-reference instead of re-deriving the query."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(
                self._TWO_CHART_YAML, path=project.path("charts/_t.yml")
            ),
            chart="monthly_sales",
            format="text",
            project=project,
            adapter_registry=registry,
            result_cache=None,
        )

        assert result.status == "ok"
        assert isinstance(result.data, str)
        assert "monthly_sales" in result.data
        assert "total_sales" not in result.data

    def test_render_chart_argument_works_on_path_arm(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.execute.adapters import build_adapter_registry

        boards = tmp_path / "charts"
        boards.mkdir(exist_ok=True)
        (boards / "sales.yml").write_text(self._TWO_CHART_YAML)
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=project.path("charts/sales.yml").read_board(),
            chart="monthly_sales",
            format="text",
            project=project,
            adapter_registry=registry,
            result_cache=None,
        )

        assert result.status == "ok"
        assert isinstance(result.data, str)
        assert "monthly_sales" in result.data
        assert "total_sales" not in result.data

    def test_render_chart_composes_with_variables(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A variable the focused chart depends on survives the focus and its
        runtime value lands in the rendered output."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  mem:\n    type: duckdb\n    path: ':memory:'\n"
        )
        yaml_content = """
title: Sales
source: mem
variables:
  region:
    default: EU
queries:
  totals:
    sql: SELECT 1 AS value
  regional:
    sql: "SELECT '{{ region }}' AS region_label, 10 AS val"
charts:
  total_sales:
    query: totals
    type: kpi
    value: value
  regional_sales:
    query: regional
    type: bar
    x: region_label
    y: val
rows:
  - total_sales
  - regional_sales
"""
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
            chart="regional_sales",
            variables={"region": "US"},
            format="text",
            project=project,
            adapter_registry=registry,
            result_cache=None,
        )

        assert result.status == "ok", [e.message for e in result.validation_errors]
        assert isinstance(result.data, str)
        assert "US" in result.data
        assert "total_sales" not in result.data

    def test_render_unknown_chart_fails_listing_available_charts(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A bad chart id must fail loudly with the real ids — never silently
        render the full dashboard."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(
                self._TWO_CHART_YAML, path=project.path("charts/_t.yml")
            ),
            chart="does_not_exist",
            project=project,
            adapter_registry=registry,
            result_cache=None,
        )

        assert result.status == "failed"
        message = result.validation_errors[0].message
        assert "does_not_exist" in message
        assert "monthly_sales" in message and "total_sales" in message

    def test_render_chart_rejects_as_link(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """as_link URLs address whole saved boards; a focused link is not a
        thing — reject rather than return a link that ignores the focus."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        boards = tmp_path / "charts"
        boards.mkdir(exist_ok=True)
        (boards / "sales.yml").write_text(self._TWO_CHART_YAML)
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=project.path("charts/sales.yml").read_board(),
            chart="monthly_sales",
            as_link=True,
            project=project,
            adapter_registry=registry,
            result_cache=None,
        )

        assert result.status == "failed"
        assert "as_link" in result.validation_errors[0].message

    def test_render_resolves_relative_path_against_project_dir(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Relative dashboard paths should resolve inside the scoped project root.

        The path-string resolution (resolve_scoped_path) lives in
        resolve_board_or_error — the shared agent_api seam every surface routes
        a user path through before building a BoardFile; core.board.render_dashboard
        itself takes an already-located BoardFile and does no path-string
        resolution.
        """
        from dbt_charts.agent_api import ProjectSession
        from dbt_charts.agent_api._paths import resolve_board_or_error
        from dbt_charts.core.diagnostics import Diagnostic

        dashboards_dir = tmp_path / "dashboards"
        dashboards_dir.mkdir()
        dashboard = dashboards_dir / "test.yml"
        dashboard.write_text(
            """
title: Test
queries:
  q:
    columns: [value]
    values:
      - [1]
charts:
  c:
    query: q
    type: kpi
    value: value
rows:
  - c
"""
        )

        project = local_project(tmp_path)
        board = resolve_board_or_error(Path("dashboards/test.yml"), project)
        assert not isinstance(board, Diagnostic), board
        result = ProjectSession(project=project).render_board(
            board=board, server_port=8765
        )

        assert result.status == "ok"

    def test_render_rejects_path_that_escapes_project_dir(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Path-escape rejection lives in resolve_board_or_error
        (see test_render_resolves_relative_path_against_project_dir)."""
        from dbt_charts.agent_api._paths import resolve_board_or_error
        from dbt_charts.core.diagnostics import Diagnostic

        project = local_project(tmp_path)
        result = resolve_board_or_error(Path("../outside.yml"), project)

        assert isinstance(result, Diagnostic)
        assert "outside project root" in result.message

    def test_render_rejects_unknown_format(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Unknown formats error fast instead of silently routing through `else`."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        project = local_project(tmp_path)
        result = render_dashboard(
            board=InMemoryBoard("title: x", path=project.path("charts/_t.yml")),
            format="xyz",
            project=project,
            adapter_registry=build_adapter_registry(project),
            result_cache=None,
        )

        assert result.status == "failed"
        assert result.validation_errors[0].code == "ERR-FORMAT-UNSUPPORTED"

    def test_render_default_format_is_json(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Default format should be JSON, not HTML."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        dashboard = tmp_path / "tiny.yml"
        dashboard.write_text(
            """
title: Tiny
queries:
  q:
    columns: [value]
    values:
      - [1]
charts:
  c:
    query: q
    type: kpi
    value: value
rows:
  - c
"""
        )

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=project.path("tiny.yml").read_board(),
            project=project,
            adapter_registry=registry,
            result_cache=None,
            server_port=8765,
        )

        assert result.status == "ok"
        assert result.data is not None, f"expected JSON data, got {result}"
        # data should be a dict (JSON format), not an SVG string
        assert isinstance(result.data, dict)
        assert result.chart_errors == []

    def test_render_returns_url_when_path_given(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """When a path is provided, the response must include a localhost URL."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        dashboard = tmp_path / "url_test.yml"
        dashboard.write_text(
            """
title: URL Test
queries:
  q:
    columns: [value]
    values:
      - [1]
charts:
  c:
    query: q
    type: kpi
    value: value
rows:
  - c
"""
        )

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=project.path("url_test.yml").read_board(),
            project=project,
            variables={"region": "US"},
            adapter_registry=registry,
            result_cache=None,
            server_port=8765,
        )

        assert result.status == "ok"
        assert result.url is not None, "expected url in response"
        assert "localhost:" in result.url
        assert "url_test" in result.url
        assert "region=US" in result.url

    def test_render_url_strips_boards_prefix_for_serve_root(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """charts/*.yml paths should produce the /<slug> route served by dct serve."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        boards = tmp_path / "charts"
        boards.mkdir()
        dashboard = boards / "sales.yml"
        dashboard.write_text(
            """
title: Sales
queries:
  q:
    columns: [value]
    values:
      - [1]
charts:
  c:
    query: q
    type: kpi
    value: value
rows:
  - c
"""
        )

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=project.path("charts/sales.yml").read_board(),
            project=project,
            variables={"region": "US"},
            adapter_registry=registry,
            result_cache=None,
            server_port=8765,
        )

        assert result.status == "ok"
        assert result.url == "http://localhost:8765/sales?region=US"

    def test_view_url_strips_boards_prefix(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """`_view_url` takes a project-relative ProjectPath — never a raw
        absolute Path — and strips the charts/ prefix for the serve route.
        ProjectPath is always project-relative by construction (built via
        Project.path()), so the old absolute-path / outside-project-dir cases
        this once guarded against can no longer occur here."""
        project = local_project(tmp_path)
        board_path = project.path("charts/sales.yml")

        url = _view_url(board_path, {"region": "US"}, port=8765)

        assert url == "http://localhost:8765/sales?region=US"

    def test_view_url_omits_url_when_port_is_none(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """No server_port means no preview URL, regardless of the board's path."""
        project = local_project(tmp_path)
        board_path = project.path("charts/sales.yml")

        url = _view_url(board_path, {"region": "US"}, port=None)

        assert url is None

    def test_render_omits_url_for_yaml_content(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """No server_port means no URL, regardless of the board's location."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        yaml_content = """
title: Inline
queries:
  q:
    columns: [value]
    values:
      - [1]
charts:
  c:
    query: q
    type: kpi
    value: value
rows:
  - c
"""
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
            adapter_registry=registry,
            project=project,
            result_cache=None,
        )

        assert result.status == "ok"
        assert result.url is None

    def test_render_omits_url_for_pathless_board_with_server_port(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A pathless (path=None) board gets no preview URL even when a
        server_port is set — there is no addressable leaf to link to."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        yaml_content = (
            "title: Inline\n"
            "queries:\n  q:\n    columns: [value]\n    values:\n      - [1]\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: value\n"
            "rows:\n  - c\n"
        )
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(yaml_content, path=None),
            adapter_registry=registry,
            project=project,
            result_cache=None,
            server_port=8765,
        )

        assert result.status == "ok"
        assert result.url is None

    def test_render_leaves_pathless_compile_errors_unstamped(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Compile errors from a pathless (path=None) board carry no source
        range — there is no real board file to point the agent at."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        yaml_content = (
            "charts:\n  c:\n    query: nope\n    type: kpi\n    value: v\n"
            "rows:\n  - c\n"
        )
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(yaml_content, path=None),
            adapter_registry=registry,
            project=project,
            result_cache=None,
        )

        assert result.status == "failed"
        assert result.validation_errors
        for err in result.validation_errors:
            assert err.range is None

    def test_render_omits_url_when_server_port_unset(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Callers without an embedded server get no URL."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        dashboard = tmp_path / "no_port.yml"
        dashboard.write_text(
            """
title: No Port
queries:
  q:
    columns: [value]
    values:
      - [1]
charts:
  c:
    query: q
    type: kpi
    value: value
rows:
  - c
"""
        )

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=project.path("no_port.yml").read_board(),
            project=project,
            adapter_registry=registry,
            result_cache=None,
        )  # server_port defaults to None

        assert result.status == "ok"
        assert result.url is None


class TestRenderDashboardAsLink:
    """Tests for render_dashboard(as_link=True) — returns a localhost URL only."""

    def test_as_link_returns_url_with_variables(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """as_link=True returns a URL with variables encoded in the query string."""
        dashboard = tmp_path / "sales.yml"
        dashboard.write_text(
            "title: Sales\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n"
        )

        project = local_project(tmp_path)
        result = render_dashboard(
            board=project.path("sales.yml").read_board(),
            project=project,
            variables={"region": "EU", "year": 2024},
            result_cache=None,
            server_port=8765,
            as_link=True,
        )

        assert result.status == "ok"
        assert result.url is not None
        assert "sales" in result.url
        assert "region=EU" in result.url
        assert "year=2024" in result.url

    def test_as_link_errors_for_missing_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A missing path returns a not-found structured error.

        Not-found handling lives in resolve_board_or_error — the shared
        agent_api seam every surface routes a user path through before
        building a BoardFile; core.board.render_dashboard takes an
        already-located BoardFile and no longer checks store existence itself.
        """
        from dbt_charts.agent_api._paths import resolve_board_or_error
        from dbt_charts.core.diagnostics import Diagnostic

        project = local_project(tmp_path)
        result = resolve_board_or_error(Path("nope.yml"), project)

        assert isinstance(result, Diagnostic)
        assert "not found" in result.message.lower()

    def test_render_json_serializes_date_values(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """JSON render output should stringify Python date values from query results."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        csv_path = tmp_path / "revenue.csv"
        csv_path.write_text("created_date,amount\n2026-01-01,100\n2026-01-02,200\n")
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  db:\n"
            "    type: duckdb\n"
            "    path: ':memory:'\n"
            "    duckdb_config:\n"
            "      enable_external_access: true\n"
        )
        dashboard = tmp_path / "revenue.yml"
        dashboard.write_text(
            """
title: Revenue
source: db
queries:
  revenue:
    sql: |
      SELECT created_date, amount
      FROM read_csv('revenue.csv')
      ORDER BY created_date
charts:
  revenue_table:
    query: revenue
    type: table
rows:
  - revenue_table
"""
        )

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=project.path("revenue.yml").read_board(),
            project=project,
            adapter_registry=registry,
            result_cache=None,
            format="json",
            server_port=8765,
        )

        assert result.status == "ok"
        assert isinstance(result.data, dict)
        assert result.data["items"][0]["data"][0]["created_date"] == "2026-01-01"


class TestRenderDashboardDiagnostics:
    """BoardRenderResult.validation_errors is list[Diagnostic], not list[str]."""

    def test_compile_error_returns_structured_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Compilation errors produce Diagnostic objects, not strings."""
        from dbt_charts.core.diagnostics import Diagnostic
        from dbt_charts.core.diagnostics.registry import REGISTRY
        from dbt_charts.core.execute.adapters import build_adapter_registry

        yaml_content = """
title: Bad
queries:
  - this_is_a_list_not_a_dict
rows:
  - nonexistent_chart
"""
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
            project=project,
            adapter_registry=registry,
            result_cache=None,
        )

        assert result.status == "failed"
        assert len(result.validation_errors) > 0
        err = result.validation_errors[0]
        assert isinstance(err, Diagnostic)
        assert err.code
        assert err.message
        assert REGISTRY.get(err.code).doc_url

    def test_input_validation_returns_structured_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Input-validation errors (no path/content) produce Diagnostics."""
        from dbt_charts.core.diagnostics import Diagnostic
        from dbt_charts.core.execute.adapters import build_adapter_registry

        registry = build_adapter_registry(local_project(tmp_path), read_only=False)
        result = render_dashboard(
            project=local_project(tmp_path),
            adapter_registry=registry,
            result_cache=None,
        )

        assert result.status == "failed"
        assert len(result.validation_errors) > 0
        assert isinstance(result.validation_errors[0], Diagnostic)

    def test_unknown_format_rejects_xyz(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Truly unknown format (xyz) is rejected with a Diagnostic."""
        from dbt_charts.core.diagnostics import Diagnostic
        from dbt_charts.core.execute.adapters import build_adapter_registry

        project = local_project(tmp_path)
        result = render_dashboard(
            board=InMemoryBoard("title: x", path=project.path("charts/_t.yml")),
            format="xyz",
            project=project,
            adapter_registry=build_adapter_registry(project),
            result_cache=None,
        )

        assert result.status == "failed"
        assert isinstance(result.validation_errors[0], Diagnostic)

    def test_render_command_defaults_to_read_only(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """CLI render_command must open DuckDB read-only so it doesn't fight serve for the lock."""
        from dbt_charts.cli.commands.render import render_command
        from dbt_charts.core.execute.adapters import build_adapter_registry

        captured: dict[str, Any] = {}
        real_build = build_adapter_registry

        def spy_build(project_root, **kwargs):
            captured["read_only"] = kwargs.get("read_only")
            return real_build(project_root, **kwargs)

        monkeypatch.setattr(
            "dbt_charts.agent_api.project_session.build_adapter_registry", spy_build
        )
        (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
        board = tmp_path / "f.yml"
        board.write_text(
            "title: t\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n"
        )
        render_command(board, format="json", project_dir=tmp_path)
        assert captured.get("read_only") is True

    def test_render_dashboard_threads_result_cache_to_executor(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """result_cache passed by the caller must reach the Executor unchanged."""
        from unittest.mock import patch

        from dbt_charts.core.execute.adapters import build_adapter_registry

        class FakeCache:
            def close(self) -> None:
                pass

        sentinel_cache = FakeCache()
        captured: dict[str, Any] = {}

        # Capture the result_cache kwarg by raising before the executor runs.
        class _Done(Exception):
            pass

        def capture_and_stop(*_args, **kwargs):
            captured["result_cache"] = kwargs.get("result_cache")
            raise _Done

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        with (
            patch("dbt_charts.core.board.Executor", side_effect=capture_and_stop),
            pytest.raises(_Done),
        ):
            render_dashboard(
                board=InMemoryBoard(
                    "title: T\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
                    "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n",
                    path=project.path("charts/_t.yml"),
                ),
                adapter_registry=registry,
                project=project,
                result_cache=sentinel_cache,  # type: ignore[arg-type]
            )

        assert captured.get("result_cache") is sentinel_cache

    def test_render_dashboard_does_not_close_result_cache(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """render_dashboard never closes result_cache — the caller owns its lifecycle.

        Ported from the deleted compile_and_render's own cache-wiring test: cache
        lifecycle ownership stays with whoever opened it, not whoever rendered.
        """
        from unittest.mock import MagicMock

        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

        mock_cache = MagicMock(spec=TrivialDuckDBCache)
        mock_cache.get.return_value = None

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=True)
        try:
            result = render_dashboard(
                board=InMemoryBoard(
                    "title: T\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
                    "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n",
                    path=project.path("charts/_t.yml"),
                ),
                adapter_registry=registry,
                project=project,
                result_cache=mock_cache,
            )
        finally:
            registry.close()

        assert result.status == "ok"
        mock_cache.close.assert_not_called()

    def test_use_cache_false_propagates_to_executor(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """render_dashboard(use_cache=False) passes use_cache=False to Executor."""
        from unittest.mock import patch

        from dbt_charts.core.execute.adapters import build_adapter_registry

        captured: dict[str, Any] = {}

        original_executor = __import__(
            "dbt_charts.core.execute.executor", fromlist=["Executor"]
        ).Executor

        class CapturingExecutor(original_executor):
            def __init__(self, *args, **kwargs):
                captured["use_cache"] = kwargs.get("use_cache", True)
                super().__init__(*args, **kwargs)

        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        with patch("dbt_charts.core.board.Executor", CapturingExecutor):
            render_dashboard(
                board=InMemoryBoard(
                    "title: T\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
                    "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n",
                    path=project.path("charts/_t.yml"),
                ),
                adapter_registry=registry,
                use_cache=False,
                project=project,
                result_cache=None,
            )

        assert captured.get("use_cache") is False

    def test_missing_required_variables_preserves_label_in_structured_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """MissingRequiredVariablesError carries label info in fields and message."""
        from dbt_charts.core.execute.adapters import build_adapter_registry

        board_yaml = (
            "variables:\n"
            "  region:\n"
            "    input: select\n"
            "    label: Region Selector\n"
            "    required: true\n"
            "queries:\n"
            "  q:\n"
            "    columns: [v]\n"
            "    values:\n"
            "      - [1]\n"
            "charts:\n"
            "  c:\n"
            "    query: q\n"
            "    type: kpi\n"
            "    value: v\n"
            "rows:\n"
            "  - c\n"
        )
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=InMemoryBoard(board_yaml, path=project.path("charts/_t.yml")),
            adapter_registry=registry,
            project=project,
            result_cache=None,
        )

        assert result.status == "failed"
        assert result.board_error is not None
        err = result.board_error
        # message must include key and label
        assert "region" in err.message
        assert "Region Selector" in err.message
        # fields["missing"] carries structured metadata
        assert "missing" in err.fields
        missing_list = err.fields["missing"]
        assert len(missing_list) == 1
        assert missing_list[0]["key"] == "region"
        assert missing_list[0]["label"] == "Region Selector"


class TestDashboardErrorsAreDiagnostics:
    """CompiledBoard.errors is list[Diagnostic], not list[str]."""

    def test_missing_file_returns_structured_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """get_board() on a nonexistent path returns Diagnostic, not str."""
        from dbt_charts.core.diagnostics import Diagnostic

        result = get_board(
            tmp_path / "nonexistent.yml", project=local_project(tmp_path)
        )

        assert result.success is False
        assert len(result.errors) >= 1
        err = result.errors[0]
        assert isinstance(err, Diagnostic)
        assert err.code.startswith("ERR-")
        assert "not found" in err.message.lower()

    def test_missing_file_stamps_typed_compile_code(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """File-not-found must stamp ERR-FILE-NOT-FOUND, not the scary

        ERR-INTERNAL fallback — this is a well-understood, expected
        condition. The message must still carry the path.
        """
        result = get_board(
            tmp_path / "nonexistent.yml", project=local_project(tmp_path)
        )

        assert result.success is False
        err = result.errors[0]
        assert err.code == "ERR-FILE-NOT-FOUND"
        assert "nonexistent.yml" in err.message

    def test_broken_yaml_returns_structured_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """get_board() on a broken YAML board returns Diagnostic objects."""
        from dbt_charts.core.diagnostics import Diagnostic

        broken = tmp_path / "broken.yml"
        broken.write_text(
            "queries:\n  rev:\n    sql: SELECT 1\n    source: x\n"
            "charts:\n  c:\n    query: nonexistent\n    type: bar\n    x: a\n    y: b\n"
            "rows:\n  - c\n"
        )

        result = get_board(broken, project=local_project(tmp_path))

        assert result.success is False
        assert len(result.errors) >= 1
        for err in result.errors:
            assert isinstance(err, Diagnostic)
            assert err.code.startswith("ERR-")
            assert err.message


class TestDashboardPathTypes:
    """Path fields are seam handles (ProjectPath/ProjectDirectory), not raw str."""

    def test_list_boards_reports_scanned_subtree(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        result = list_boards(local_project(tmp_path))
        assert result.directory.relpath == "charts"

        result_root = list_boards(local_project(tmp_path), under=".")
        assert result_root.directory.relpath == "."

    def test_list_boards_dashboard_summary_paths_are_path(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        boards = tmp_path / "charts"
        boards.mkdir()
        (boards / "sales.yml").write_text(
            "title: Sales\nqueries:\n  q:\n    sql: SELECT 1\n    source: s\n"
            "rows:\n  - q\n"
        )
        result = list_boards(local_project(tmp_path))
        assert result.success is True
        assert result.count == 1
        assert result.boards[0].file.relpath == "charts/sales.yml"

    def test_get_board_accepts_path_arg(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        dashboard = tmp_path / "test.yml"
        dashboard.write_text(
            "title: T\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n"
        )
        result = get_board(dashboard, project=local_project(tmp_path))
        assert result.board is not None
        assert result.board.title == "T"

    def test_render_dashboard_accepts_path_arg(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.execute.adapters import build_adapter_registry

        dashboard = tmp_path / "r.yml"
        dashboard.write_text(
            "title: T\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n"
        )
        project = local_project(tmp_path)
        registry = build_adapter_registry(project, read_only=False)
        result = render_dashboard(
            board=project.path("r.yml").read_board(),
            adapter_registry=registry,
            project=project,
            result_cache=None,
        )
        assert result.data is not None

    def test_view_url_accepts_path_arg(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = local_project(tmp_path)
        url = _view_url(project.path("charts/sales.yml"), port=8765)
        assert url == "http://localhost:8765/sales"


class TestRequiredProjectScopedArgs:
    """Required project/result_cache on render_dashboard;
    required project on render_inspect_dashboard."""

    def test_render_dashboard_requires_project_and_result_cache(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        # project and result_cache are keyword-required: omitting either must
        # raise TypeError at the Python signature level — no silent fallback.
        with pytest.raises(TypeError, match="project"):
            render_dashboard(  # type: ignore[call-arg]
                board=InMemoryBoard("charts: {}", path=None),
                adapter_registry=None,  # type: ignore[arg-type]
                result_cache=None,
            )
        with pytest.raises(TypeError, match="result_cache"):
            render_dashboard(  # type: ignore[call-arg]
                board=InMemoryBoard("charts: {}", path=None),
                project=local_project(Path("/tmp")),
                adapter_registry=None,  # type: ignore[arg-type]
            )

    def test_render_inspect_dashboard_requires_project(self) -> None:
        from dbt_charts.core.inspect.renderer import render_inspect_dashboard

        with pytest.raises(TypeError, match="project"):
            render_inspect_dashboard(  # type: ignore[call-arg]
                template_yaml="charts: {}", variables={}
            )


class TestRaiseOnDashboardFailure:
    """raise_on_dashboard_failure preserves the render domain as a RenderError.

    Callers that classify a failure by exception type (looker_migrate's
    _stage_for_exception) or catch RenderError specifically (the markdown
    integration) rely on a render-stage fatal arriving as RenderError, not a
    flattened ValueError. Execute-domain and everything else stay ValueError so
    the markdown except tuple — which lists RenderError but not ExecutionError —
    keeps catching them.
    """

    @staticmethod
    def _board_error(code: Any, **fields: Any) -> Any:
        from dbt_charts.core.diagnostics.base import DbtChartsError

        return DbtChartsError.from_code(code, **fields).to_diagnostic()

    def test_render_domain_board_error_raises_render_error(self) -> None:
        from dbt_charts.core.board import (
            BoardRenderResult,
            raise_on_dashboard_failure,
        )
        from dbt_charts.core.diagnostics import ERR_INPUT_INVALID
        from dbt_charts.core.diagnostics.registry import REGISTRY
        from dbt_charts.core.render import RenderError

        fe = self._board_error(ERR_INPUT_INVALID, message="boom")
        assert REGISTRY.get(fe.code).domain == "render"
        with pytest.raises(RenderError, match="boom"):
            raise_on_dashboard_failure(
                BoardRenderResult(status="failed", board_error=fe)
            )

    def test_execute_domain_board_error_stays_value_error(self) -> None:
        from dbt_charts.core.board import (
            BoardRenderResult,
            raise_on_dashboard_failure,
        )
        from dbt_charts.core.diagnostics.codes_execute import ERR_SOURCE_NOT_FOUND_EMPTY
        from dbt_charts.core.diagnostics.registry import REGISTRY
        from dbt_charts.core.render import RenderError

        fe = self._board_error(ERR_SOURCE_NOT_FOUND_EMPTY, source="s")
        assert REGISTRY.get(fe.code).domain == "execute"
        with pytest.raises(ValueError, match="not found") as excinfo:
            raise_on_dashboard_failure(
                BoardRenderResult(status="failed", board_error=fe)
            )
        # Must NOT be promoted to RenderError — markdown's except omits
        # ExecutionError, and RenderError is not a ValueError subclass.
        assert not isinstance(excinfo.value, RenderError)

    def test_validation_errors_raise_value_error(self) -> None:
        from dbt_charts.core.board import (
            BoardRenderResult,
            raise_on_dashboard_failure,
        )
        from dbt_charts.core.diagnostics import ERR_INPUT_INVALID

        fe = self._board_error(ERR_INPUT_INVALID, message="bad")
        with pytest.raises(ValueError, match="Compilation errors"):
            raise_on_dashboard_failure(
                BoardRenderResult(status="failed", validation_errors=[fe])
            )


class TestRenderBoardArgs:
    """RenderBoardArgs model validation."""

    def test_duplicate_variable_names_rejected(self) -> None:
        from pydantic import ValidationError

        from dbt_charts.agent_api.boards import RenderBoardArgs

        with pytest.raises(ValidationError, match="region"):
            RenderBoardArgs.model_validate(
                {
                    "yaml_content": "title: T\nrows: []",
                    "variables": [
                        {"name": "region", "value": "US"},
                        {"name": "region", "value": "EU"},
                    ],
                }
            )
