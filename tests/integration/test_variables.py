"""Integration tests for variable resolution.

Tests variable substitution in queries, titles, and filters.
"""

import re
from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render import render

from .._run import Run

_MEMORY_DUCKDB_SOURCES = ProjectSourcesConfig(
    sources={"test_profile": {"type": "duckdb", "path": ":memory:"}}
)


def _control_tag(svg: str) -> str:
    """The opening tag of the drawn `region` control — where its state lives."""
    match = re.search(r'<g data-dbt-variable="region"[^>]*>', svg)
    assert match, "no drawn control for `region`"
    return match.group(0)


def _options_attr(svg: str) -> str:
    match = re.search(r' data-dbt-options="[^"]*"', svg)
    assert match, "no option payload"
    return match.group(0)


class TestVariableControlsRendering:
    """The server draws every control into the board; a host only binds them.

    So the picture is the same whoever asks for it — `controls=True` changes no
    geometry and no drawing. What it changes is the behavior payload hung off
    the drawn control: the option list a runtime opens, which an artifact has
    nothing to open with.
    """

    def test_root_level_variables_render_controls(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Variable controls render when variables are defined at the root board level."""
        yaml_content = """
title: Root Variables
variables:
  region:
    input: select
    options:
      static: [North, South, East, West]
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  chart1:
    query: sales
    type: bar
    x: month
    y: revenue
rows:
  - chart1
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None

        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
        )
        svg_output = render(
            result.board, executor.ctx, executor.ledger, format="svg"
        ).output
        assert isinstance(svg_output, str)

        # The server draws the control into the board, so it is in the SVG —
        # no foreignObject, no injected layer, nothing measured in page pixels.
        assert "<foreignObject" not in svg_output
        assert "data-dbt-variables-box" in svg_output
        assert 'data-dbt-variable="region"' in svg_output
        # Unset, the control draws the no-filter state.
        assert "All" in svg_output
        # But not the domain behind it: nothing here can open a menu, and every
        # region in the column is data this picture does not show.
        assert "data-dbt-options" not in svg_output

        # Asking for controls draws nothing new — the control was already there.
        # It attaches the options a runtime needs to open the menu over it.
        with_controls = render(
            result.board, executor.ctx, executor.ledger, format="svg", controls=True
        )
        assert isinstance(with_controls.output, str)
        assert 'data-dbt-variable="region"' in with_controls.output
        assert "North" in with_controls.output
        assert "All" in with_controls.output
        # The drawn control's own tag is identical apart from that payload —
        # same box, same geometry, same committed value. (Whole-SVG equality
        # would not hold; Vega mints fresh clip-path ids on every render.)
        assert _control_tag(with_controls.output).replace(
            _options_attr(with_controls.output), ""
        ) == _control_tag(svg_output)

    def test_nested_row_variables_render_controls(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Variable controls render within a nested board that defines its own variables.

        Variables defined in a nested row item (inline nested board) must render
        as controls INSIDE that board, not hoisted to the root variable bar.
        The root board.variables is empty; the nested board.variables has the var.
        """
        yaml_content = """
title: Nested Variables
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
rows:
  - variables:
      region:
        input: select
        options:
          static: [North, South, East, West]
    charts:
      chart1:
        query: sales
        type: bar
        x: month
        y: revenue
    rows:
      - chart1
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None
        # variable_registry should contain the nested variable
        assert result.board.variable_registry is not None
        assert "region" in result.board.variable_registry
        # root board.variables must be empty — controls live in the nested board
        assert "region" not in result.board.variables

        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
        )
        svg_output = render(
            result.board, executor.ctx, executor.ledger, format="svg"
        ).output
        assert isinstance(svg_output, str)

        # The nested board renders its own strip, so its anchor box is the one
        # a host mounts controls over.
        assert "data-dbt-variables-box" in svg_output
        assert "All" in svg_output

        # The strip must be INSIDE the nested board SVG, not in a root variable
        # bar. The nested board starts with <svg width=; the anchor follows it.
        nested_svg_pos = svg_output.find("<svg width=")
        anchor_pos = svg_output.find("data-dbt-variables-box")
        assert nested_svg_pos > 0, "no nested board SVG found"
        assert anchor_pos > nested_svg_pos, (
            "variables band appears before nested board SVG — strip is wrongly at root"
        )

    def test_nested_row_variables_render_the_read_only_strip(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """The nested strip is converter-safe: text plus a measurable anchor."""
        yaml_content = """
title: Nested Variables Canonical
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
rows:
  - variables:
      region:
        input: select
        options:
          static: [North, South, East, West]
    charts:
      chart1:
        query: sales
        type: bar
        x: month
        y: revenue
    rows:
      - chart1
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None
        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
        )
        svg = render(result.board, executor.ctx, executor.ledger, format="svg").output
        assert isinstance(svg, str)

        assert 'data-dbt-variables-static="true"' in svg
        # The anchor sits outside the text group so it stays measurable once the
        # runtime hides the text.
        anchor_pos = svg.find("data-dbt-variables-box")
        static_pos = svg.find('data-dbt-variables-static="true"')
        assert 0 < anchor_pos < static_pos
        assert "<foreignObject" not in svg

    def test_canonical_variable_svg_converts_to_png_and_pdf(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """The same progressively enhanced SVG remains converter-safe."""
        yaml_content = """
title: Variable Export
variables:
  region:
    input: select
    default: North
    options:
      static: [North, South]
text: Exported region
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None
        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
        )

        png = render(result.board, executor.ctx, executor.ledger, format="png").output
        pdf = render(result.board, executor.ctx, executor.ledger, format="pdf").output

        assert isinstance(png, bytes) and png.startswith(b"\x89PNG")
        assert isinstance(pdf, bytes) and pdf.startswith(b"%PDF")


class TestVariableResolution:
    """Tests for variable resolution."""

    def test_variable_in_title(self, local_project: Callable[..., FilesystemProject]):
        """Test variable substitution in chart title."""
        yaml_content = """
title: Variable Test
variables:
  year:
    input: number
    default: 2023
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  chart1:
    query: sales
    type: bar
    x: month
    y: revenue
    title: Sales for {{ year }}
rows:
  - chart1
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None

        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
        )

        svg_output = render(
            result.board, executor.ctx, executor.ledger, format="svg"
        ).output
        assert isinstance(svg_output, str)
        assert "2023" in svg_output

        svg_output_custom = render(
            result.board,
            executor.ctx,
            executor.ledger,
            format="svg",
            variables={"year": 2024},
        ).output
        assert isinstance(svg_output_custom, str)
        assert "2024" in svg_output_custom

    def test_variable_in_sql_query(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test variable substitution in SQL query."""
        yaml_content = """
title: SQL Variable Test
variables:
  min_value:
    input: number
    default: 2
queries:
  test_query:
    type: sql
    sql: SELECT * FROM (SELECT 1 as id UNION ALL SELECT 2 UNION ALL SELECT 3) WHERE id >= {{ min_value }}
    source: test_profile
charts:
  chart1:
    query: test_query
    type: table
rows:
  - chart1
"""
        result = compile(yaml_content, project_sources=_MEMORY_DUCKDB_SOURCES)
        assert result.success
        assert result.board is not None

        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
        )

        data = executor.execute_query("test_query", use_cache=False)
        assert len(data) == 2  # id >= 2: [2, 3]

        data_custom = executor.execute_query(
            "test_query", use_cache=False, variables={"min_value": 3}
        )
        assert len(data_custom) == 1  # id >= 3: [3]

    def test_multiple_variables(self, local_project: Callable[..., FilesystemProject]):
        """Test multiple variables in same dashboard."""
        yaml_content = """
title: Multiple Variables
variables:
  year:
    input: number
    default: 2023
  quarter:
    input: number
    default: 1
queries:
  sales:
    type: values
    rows:
      - {month: "2023-01", revenue: 100000}
      - {month: "2023-02", revenue: 110000}
      - {month: "2023-03", revenue: 120000}
charts:
  chart1:
    query: sales
    type: bar
    x: month
    y: revenue
    title: Q{{ quarter }} {{ year }} Sales
rows:
  - chart1
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None

        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
        )

        svg_output = render(
            result.board, executor.ctx, executor.ledger, format="svg"
        ).output
        assert isinstance(svg_output, str)
        assert "2023" in svg_output
        assert "Q1" in svg_output or "Q 1" in svg_output

        svg_output_custom = render(
            result.board,
            executor.ctx,
            executor.ledger,
            format="svg",
            variables={"year": 2024, "quarter": 2},
        ).output
        assert isinstance(svg_output_custom, str)
        assert "2024" in svg_output_custom

    def test_variable_defaults(self, local_project: Callable[..., FilesystemProject]):
        """Test variable default values."""
        yaml_content = """
title: Variable Defaults
variables:
  year:
    input: number
    default: 2023
  name:
    input: text
    default: Sales
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  chart1:
    query: sales
    type: bar
    x: month
    y: revenue
    title: "{{ name }} Dashboard {{ year }}"
rows:
  - chart1
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None

        # Check defaults are set
        assert result.board.variable_defaults["year"] == 2023
        assert result.board.variable_defaults["name"] == "Sales"

        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
        )

        svg_output = render(
            result.board, executor.ctx, executor.ledger, format="svg"
        ).output
        assert isinstance(svg_output, str)
        assert "2023" in svg_output
        assert "Sales" in svg_output

    def test_variable_inheritance(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test variable inheritance in nested boards."""
        yaml_content = """
title: Parent Board
variables:
  year:
    input: number
    default: 2023
queries:
  sales:
    type: values
    rows:
      - {month: Jan, revenue: 100}
      - {month: Feb, revenue: 150}
charts:
  chart1:
    query: sales
    type: bar
    x: month
    y: revenue
rows:
  - title: "Child Board {{ year }}"
    rows:
      - chart1
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None

        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
        )

        svg_output = render(
            result.board, executor.ctx, executor.ledger, format="svg"
        ).output
        assert isinstance(svg_output, str)
        assert "2023" in svg_output

    def test_nested_board_variable_content_updates_on_change(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Variable defined in a nested board must update its content at render time.

        Regression: interactive variable defaults were being baked into content
        strings at compile time via _resolve_dict_templates, preventing the Jinja
        template from being re-evaluated when the user provides a different value.
        """
        yaml_content = """
title: Greeting Demo
rows:
  - title: Greeting Board
    variables:
      name:
        label: Your Name
        input: input
        default: World
    rows:
      - text: |
          Hello, {{ name }}!
"""
        result = compile(yaml_content)
        assert result.success
        assert result.board is not None

        # The compiled content must still contain the Jinja template, not the default
        nested_board = result.board.layout.items[0].board
        assert nested_board is not None
        inner_board = nested_board.layout.items[0].board
        assert inner_board is not None
        assert "{{ name }}" in inner_board.text, (
            f"Template was baked in at compile time: {inner_board.text!r}"
        )

        executor = Run(
            result.board,
            env=build_exec_env(local_project(Path.cwd())),
        )

        # Default render
        svg_default = render(
            result.board, executor.ctx, executor.ledger, format="svg"
        ).output
        assert isinstance(svg_default, str)
        assert "Hello, World" in svg_default

        # Changed variable
        svg_changed = render(
            result.board,
            executor.ctx,
            executor.ledger,
            format="svg",
            variables={"name": "Alice"},
        ).output
        assert isinstance(svg_changed, str)
        assert "Hello, Alice" in svg_changed, (
            "Inner board content did not update when variable changed"
        )


class TestDateVariableExecution:
    """The Executor coerces date variables before binding (runtime boundary)."""

    _BOARD = """
title: Date Filter
variables:
  cutoff:
    input: date
    default: "2024-06-15"
queries:
  events:
    sql: >
      SELECT d FROM (VALUES (DATE '2024-01-01'), (DATE '2024-06-01'),
      (DATE '2024-12-01')) AS t(d) WHERE d <= '{{ cutoff }}' ORDER BY d
    source: test_profile
charts:
  c1:
    query: events
    type: table
rows:
  - c1
"""

    def _executor(self) -> Run:
        result = compile(self._BOARD, project_sources=_MEMORY_DUCKDB_SOURCES)
        assert result.success, result.errors
        assert result.board is not None
        return Run(
            result.board,
            env=build_exec_env(FilesystemProject(Path.cwd())),
        )

    def test_default_date_filters_correctly(self) -> None:
        rows = self._executor().execute_query("events", {})
        assert [str(r["d"]) for r in rows] == ["2024-01-01", "2024-06-01"]

    def test_user_date_filters_correctly(self) -> None:
        rows = self._executor().execute_query("events", {"cutoff": "2024-12-31"})
        assert len(rows) == 3
