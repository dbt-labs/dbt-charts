"""Tests for dbt_charts.agent_api.describe — describe_board."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.project import Project

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BOARD_YAML = """
title: Sales Dashboard
notes: Monthly revenue breakdown

queries:
  revenue:
    sql: SELECT month, SUM(amount) AS total FROM orders GROUP BY 1
    source: analytics
  product_list:
    sql: SELECT * FROM products
    source: files

charts:
  trend:
    query: revenue
    type: line
    x: month
    y: total
    title: Revenue Trend
  breakdown:
    query: revenue
    type: bar
    x: month
    y: total

variables:
  region:
    input: select
    options:
      static: [North, South, East, West]
    default: North

rows:
  - trend
  - breakdown
"""


def _write_board(tmp_path: Path, content: str = _BOARD_YAML) -> Path:
    f = tmp_path / "sales.yml"
    f.write_text(content)
    return f


# ---------------------------------------------------------------------------
# describe_board — file inspection mode
# ---------------------------------------------------------------------------


class TestDescribeBoardDescribesQueriesChartsVariablesLayout:
    """describe_board returns a populated DescribeBoardResult for a valid board."""

    def test_describes_queries_charts_variables_layout(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_board

        result = describe_board(_write_board(tmp_path), project=local_project(tmp_path))

        assert result.success is True
        assert result.title == "Sales Dashboard"
        assert result.notes == "Monthly revenue breakdown"

        # Queries
        assert {q.name for q in result.queries} == {"revenue", "product_list"}
        revenue = next(q for q in result.queries if q.name == "revenue")
        assert revenue.type == "sql"
        assert revenue.source == "analytics"
        assert revenue.sql is not None
        product_list = next(q for q in result.queries if q.name == "product_list")
        assert product_list.type == "sql"
        assert product_list.source == "files"

        # Charts
        assert {c.name for c in result.charts} == {"trend", "breakdown"}
        trend = next(c for c in result.charts if c.name == "trend")
        assert trend.query == "revenue"
        assert trend.type == "line"
        assert trend.title == "Revenue Trend"
        assert trend.encoding.get("x") == "month"
        assert trend.encoding.get("y") == "total"

        # Variables
        assert len(result.variables) == 1
        region = result.variables[0]
        assert region.name == "region"
        assert region.options == ["North", "South", "East", "West"]

        # Layout — chart names appear in declared order
        assert result.layout is not None
        assert result.layout.primitive == "rows"
        assert result.layout.items == ["trend", "breakdown"]

        # `label` is a KPI render hint, not an encoding channel; it must not
        # leak into a bar/line chart's encoding even if the field defaults to ""
        for c in result.charts:
            assert "label" not in c.encoding


class TestDescribeBoardOnNonFilesystemProject:
    """describe_board must work against any Project, not just FilesystemProject."""

    def test_describes_board_from_in_memory_project(
        self,
        tmp_path: Path,
        in_memory_project: Callable[[Path, dict[str, str]], Project],
    ) -> None:
        from dbt_charts.agent_api.describe import describe_board

        project = in_memory_project(tmp_path, {"sales.yml": _BOARD_YAML})

        result = describe_board(Path("sales.yml"), project=project)

        assert result.success is True
        assert result.title == "Sales Dashboard"
        assert {q.name for q in result.queries} == {"revenue", "product_list"}


class TestDescribeBoardMultiSeriesEncodingPassesThroughList:
    """`y: [a, b]` must round-trip as a list, not a Python repr string."""

    def test_multi_series_y_kept_as_list(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_board

        board_path = tmp_path / "multi.yml"
        board_path.write_text(
            """
queries:
  q:
    sql: SELECT month, revenue, profit FROM orders
    source: analytics
charts:
  combo:
    query: q
    type: line
    x: month
    y: [revenue, profit]
rows:
  - combo
"""
        )
        result = describe_board(board_path, project=local_project(tmp_path))
        assert result.success is True
        combo = next(c for c in result.charts if c.name == "combo")
        assert combo.encoding["y"] == ["revenue", "profit"]


class TestDescribeBoardReturnsDiagnostics:
    """describe_board returns success=False with Diagnostic list on compile failure."""

    def test_broken_yaml_returns_failure_with_structured_errors(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_board
        from dbt_charts.core.diagnostics import Diagnostic

        bad = tmp_path / "bad.yml"
        bad.write_text("title: broken\nqueries:\n  q: [invalid")
        result = describe_board(bad, project=local_project(tmp_path))

        assert result.success is False
        assert len(result.errors) > 0
        # Errors must be a Diagnostic, not plain strings
        assert all(isinstance(e, Diagnostic) for e in result.errors)

    def test_compile_errors_return_structured_errors(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Compile-time errors (e.g. missing chart query ref) must be a Diagnostic."""
        from dbt_charts.agent_api.describe import describe_board
        from dbt_charts.core.diagnostics import Diagnostic

        bad = tmp_path / "bad.yml"
        bad.write_text(
            "charts:\n  c:\n    query: nonexistent\n    type: bar\n    x: x\n    y: y\nrows:\n  - c\n"
        )
        result = describe_board(bad, project=local_project(tmp_path))

        assert result.success is False
        assert len(result.errors) > 0
        assert all(isinstance(e, Diagnostic) for e in result.errors)


class TestDescribeBoardPathResolutionRejectsEscape:
    """describe_board rejects paths that escape project_dir."""

    def test_path_escape_returns_failure(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_board
        from dbt_charts.core.diagnostics import Diagnostic

        result = describe_board(
            Path("../../etc/passwd"), project=local_project(tmp_path)
        )
        assert result.success is False
        assert len(result.errors) > 0
        assert all(isinstance(e, Diagnostic) for e in result.errors)

    def test_missing_file_returns_failure(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_board
        from dbt_charts.core.diagnostics import Diagnostic

        result = describe_board(
            tmp_path / "nonexistent.yml", project=local_project(tmp_path)
        )
        assert result.success is False
        assert len(result.errors) > 0
        assert all(isinstance(e, Diagnostic) for e in result.errors)

    def test_missing_file_stamps_typed_compile_code(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """File-not-found must stamp ERR-FILE-NOT-FOUND, not the scary

        ERR-INTERNAL fallback — this is a well-understood, expected
        condition. The message must still carry the path.
        """
        from dbt_charts.agent_api.describe import describe_board

        result = describe_board(
            tmp_path / "nonexistent.yml", project=local_project(tmp_path)
        )

        assert result.success is False
        err = result.errors[0]
        assert err.code == "ERR-FILE-NOT-FOUND"
        assert "nonexistent.yml" in err.message


class TestDescribePaths:
    """describe_paths() expands files and directories, mirrors validate_paths shape."""

    def test_describe_paths_single_file_returns_one_result(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_paths

        board = tmp_path / "board.yml"
        board.write_text(_BOARD_YAML)

        results = describe_paths([board], project=local_project(tmp_path))
        assert len(results) == 1
        assert results[0].success is True

    def test_describe_paths_directory_walks_yaml_files(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_paths

        (tmp_path / "a.yml").write_text(_BOARD_YAML)
        (tmp_path / "b.yml").write_text(_BOARD_YAML)

        results = describe_paths([tmp_path], project=local_project(tmp_path))
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_describe_paths_skips_partial_files(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_paths

        (tmp_path / "good.yml").write_text(_BOARD_YAML)
        (tmp_path / "_partial.yml").write_text(_BOARD_YAML)

        results = describe_paths([tmp_path], project=local_project(tmp_path))
        assert len(results) == 1
        assert "partial" not in str(results[0].path)

    def test_describe_paths_excludes_meta_yaml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """meta.yml is a cascade fragment, not a standalone board — walking
        it as one (as init_project scaffolds it under charts/) must not
        surface as an ERR-INTERNAL describe result."""
        from dbt_charts.agent_api.describe import describe_paths

        (tmp_path / "board.yml").write_text(_BOARD_YAML)
        (tmp_path / "meta.yml").write_text("style: {}\n")

        results = describe_paths([tmp_path], project=local_project(tmp_path))
        assert len(results) == 1
        assert results[0].success is True
        assert "meta.yml" not in str(results[0].path)

    def test_describe_paths_results_in_argv_order(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_paths

        first = tmp_path / "first.yml"
        first.write_text(_BOARD_YAML)
        second = tmp_path / "second.yml"
        second.write_text(_BOARD_YAML)

        results = describe_paths([first, second], project=local_project(tmp_path))
        assert len(results) == 2
        assert results[0].path == "first.yml"
        assert results[1].path == "second.yml"


class TestDescribePathsDispatchDriftOnNoSuffixMissingPath:
    """Deliberate drift (owner-approved): pure-suffix (``is_yaml``) dispatch
    walks a no-suffix, nonexistent argv path as an empty directory instead of
    reporting ERR-FILE-NOT-FOUND (the old ``is_dir()`` dispatch's behavior).
    Both stay a single failure result — only the error differs."""

    def test_missing_no_suffix_path_becomes_empty_walk_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_paths

        results = describe_paths(
            [tmp_path / "missing"], project=local_project(tmp_path)
        )
        assert len(results) == 1
        assert results[0].success is False
        assert "No board files found" in results[0].errors[0].message


class TestDescribeBoardPathTypes:
    """result.path is a project-relative POSIX str.

    Pydantic serializes a ``Path`` field via ``str()``, which emits
    OS-native separators on Windows (``WindowsPath.__str__`` rejoins with
    backslash) — invisible on macOS/Linux, where ``str(PosixPath)`` is
    already POSIX. Pin the property that actually prevents the regression
    on any host: the field's runtime type must be ``str``, never a
    ``PurePath`` subclass.
    """

    def test_describe_board_accepts_path_argument(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_board

        board = tmp_path / "sales.yml"
        board.write_text(
            "title: T\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n"
        )
        result = describe_board(board, project=local_project(tmp_path))
        assert isinstance(result.path, str), (
            f"result.path should be str, got {type(result.path)}"
        )
        assert result.path == "sales.yml"

    def test_describe_board_path_escape_uses_path_arg(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_board

        result = describe_board(
            Path("../../etc/passwd"), project=local_project(tmp_path)
        )
        assert result.success is False

    @pytest.mark.windows
    def test_describe_board_nested_path_has_no_os_native_separator(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A nested board's ``result.path`` is forward-slash joined on every host.

        The isinstance-only check above can't catch a ``str(Path)`` regression —
        ``str(PosixPath)`` is already forward-slash separated. Only a path with a
        separator in it tells the two apart, and only on Windows.
        """
        from dbt_charts.agent_api.describe import describe_board

        nested = tmp_path / "sub"
        nested.mkdir()
        board = nested / "x.yml"
        board.write_text(
            "title: T\nqueries:\n  q:\n    columns: [v]\n    values:\n      - [1]\n"
            "charts:\n  c:\n    query: q\n    type: kpi\n    value: v\nrows:\n  - c\n"
        )

        result = describe_board(Path("sub/x.yml"), project=local_project(tmp_path))

        assert result.success is True, result.errors
        assert result.path == "sub/x.yml"


_CHART_SCOPE_BOARD_YAML = """
title: Sales Dashboard
queries:
  revenue:
    sql: SELECT month, SUM(amount) AS total FROM orders WHERE region = '{{ region }}' GROUP BY 1
    source: analytics
  product_list:
    sql: SELECT name FROM products WHERE tier = '{{ tier }}'
    source: analytics
charts:
  trend:
    query: revenue
    type: line
    x: month
    y: total
    title: Revenue Trend
  products:
    query: product_list
    type: table
variables:
  region:
    input: select
    options:
      static: [North, South]
    default: North
  tier:
    input: select
    options:
      static: [gold, silver]
    default: gold
rows:
  - trend
  - products
"""


class TestDescribeBoardChartScope:
    """describe_board(chart=...) narrows to one chart and the context it needs."""

    def test_chart_scope_keeps_only_its_query_and_variables(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_board

        board = _write_board(tmp_path, _CHART_SCOPE_BOARD_YAML)
        result = describe_board(board, project=local_project(tmp_path), chart="trend")

        assert result.success is True, result.errors
        assert result.title == "Sales Dashboard"
        assert [c.name for c in result.charts] == ["trend"]
        assert result.charts[0].title == "Revenue Trend"
        assert [q.name for q in result.queries] == ["revenue"]
        assert [v.name for v in result.variables] == ["region"]
        assert result.layout is None

    def test_chart_scope_keeps_layer_queries(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_board

        board = _write_board(
            tmp_path,
            """
title: Layered
queries:
  by_category:
    columns: [region, revenue]
    values:
      - [East, 1]
  totals:
    columns: [region, total]
    values:
      - [East, 1]
  unrelated:
    columns: [v]
    values:
      - [1]
charts:
  stacked:
    query: by_category
    type: bar
    x: region
    y: revenue
    layers:
      - type: line
        query: totals
        y: total
  other:
    query: unrelated
    type: kpi
    value: v
rows:
  - stacked
  - other
""",
        )
        result = describe_board(board, project=local_project(tmp_path), chart="stacked")

        assert result.success is True, result.errors
        assert {q.name for q in result.queries} == {"by_category", "totals"}

    def test_unknown_chart_fails_with_available_charts(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.agent_api.describe import describe_board

        board = _write_board(tmp_path, _CHART_SCOPE_BOARD_YAML)
        result = describe_board(board, project=local_project(tmp_path), chart="trnd")

        assert result.success is False
        (err,) = result.errors
        assert err.code == "ERR-UNKNOWN-CHART"
        assert "products" in err.message and "trend" in err.message
        assert err.hint is not None and "trend" in err.hint


_TEMPLATE = """{#---
description: Draws flows.
channels:
  x:
    description: Origin column.
  y:
    description: Count column.
    multiple: true
  color:
    default: null
options:
  label_size:
    type: number
    default: 11
    description: Label font size.
  palette:
    type: color
---#}
<g/>"""

_CUSTOM_BOARD = """
queries:
  q:
    type: values
    rows:
      - {a: one, b: 2}
charts:
  flow:
    type: custom
    query: q
    template: flow
    x: a
    y: b
    options:
      palette: "#ff0000"
rows:
  - flow
"""


def _template_project(tmp_path: Path) -> FilesystemProject:
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "flow.svg.j2").write_text(_TEMPLATE)
    (tmp_path / "templates" / "plain.svg.j2").write_text("<g/>")
    return FilesystemProject(tmp_path)


class TestDescribeTemplates:
    """A template file or the templates folder describes its header contract."""

    def test_template_file_describes_channels_and_options(self, tmp_path: Path) -> None:
        from dbt_charts.agent_api.describe import describe_paths

        project = _template_project(tmp_path)
        (result,) = describe_paths([Path("templates/flow.svg.j2")], project=project)

        assert result.success is True, result.errors
        assert result.path == "templates/flow.svg.j2"
        template = result.template
        assert template is not None
        assert template.name == "flow"
        assert template.description == "Draws flows."
        channels = {c.name: c for c in template.channels}
        assert list(channels) == ["x", "y", "color"]
        assert channels["x"].required is True
        assert channels["x"].description == "Origin column."
        assert channels["y"].multiple is True
        assert channels["color"].required is False
        options = {o.name: o for o in template.options}
        assert options["label_size"].type == "number"
        assert options["label_size"].default == 11
        assert options["label_size"].required is False
        assert options["label_size"].description == "Label font size."
        assert options["palette"].required is True

    def test_templates_folder_lists_every_template(self, tmp_path: Path) -> None:
        from dbt_charts.agent_api.describe import describe_paths

        project = _template_project(tmp_path)
        results = describe_paths([Path("templates")], project=project)

        assert [r.path for r in results] == [
            "templates/flow.svg.j2",
            "templates/plain.svg.j2",
        ]
        assert all(r.success and r.template is not None for r in results)

    def test_only_files_under_templates_are_described_as_templates(
        self, tmp_path: Path
    ) -> None:
        from dbt_charts.agent_api.describe import describe_paths

        project = _template_project(tmp_path)
        (tmp_path / "templates" / "flows").mkdir()
        (tmp_path / "templates" / "flows" / "sub.svg.j2").write_text("<g/>")
        (tmp_path / "charts").mkdir()
        (tmp_path / "charts" / "stray.svg.j2").write_text("<g/>")

        walked = describe_paths([Path("templates")], project=project)
        names = [r.template.name for r in walked if r.template is not None]
        assert names == ["flow", "flows/sub", "plain"]
        root = describe_paths([Path()], project=project)
        assert all(r.template is None for r in root)
        (tmp_path / "charts" / "b.yml").write_text("title: B\nrows: []\n")
        charts_only = describe_paths([Path("charts")], project=project)
        assert all(r.template is None for r in charts_only)

        (stray,) = describe_paths([Path("charts/stray.svg.j2")], project=project)
        assert stray.success is False
        assert [e.code for e in stray.errors] == ["ERR-CUSTOM-TEMPLATE"]
        assert "not under templates/" in stray.errors[0].message

    def test_invalid_template_header_is_a_structured_error(
        self, tmp_path: Path
    ) -> None:
        from dbt_charts.agent_api.describe import describe_paths

        project = _template_project(tmp_path)
        (tmp_path / "templates" / "bad.svg.j2").write_text("{% for x in y %}")
        (result,) = describe_paths([Path("templates/bad.svg.j2")], project=project)

        assert result.success is False
        assert result.errors[0].code == "ERR-CUSTOM-TEMPLATE"
        assert "templates/bad.svg.j2" in result.errors[0].message

    def test_board_describe_names_the_charts_template(self, tmp_path: Path) -> None:
        from dbt_charts.agent_api.describe import describe_board

        project = _template_project(tmp_path)
        (tmp_path / "b.yml").write_text(_CUSTOM_BOARD)
        result = describe_board(Path("b.yml"), project=project)

        assert result.success is True, result.errors
        chart = result.charts[0]
        assert chart.type == "custom"
        assert chart.template == "flow"
        assert chart.template_description == "Draws flows."
        assert chart.inline_template is False

    def test_inline_custom_chart_is_marked_inline(self, tmp_path: Path) -> None:
        from dbt_charts.agent_api.describe import describe_board

        board = _CUSTOM_BOARD.replace("    template: flow\n", "    svg: '<g/>'\n")
        board = board.replace(
            '    x: a\n    y: b\n    options:\n      palette: "#ff0000"\n', ""
        )
        (tmp_path / "b.yml").write_text(board)
        result = describe_board(Path("b.yml"), project=FilesystemProject(tmp_path))

        assert result.success is True, result.errors
        assert result.charts[0].template is None
        assert result.charts[0].inline_template is True
        assert result.charts[0].template_description is None
