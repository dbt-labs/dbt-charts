"""Board titles and chart text read query results through ``queries.<q>``."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.core.compile import compile, focus_on_chart
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import Project
from dbt_charts.core.render import render
from dbt_charts.core.render.board_resolve import (
    build_resolved_board,
    build_resolved_board_static,
)
from dbt_charts.core.render.board_to_dict import board_to_dict
from dbt_charts.core.render.render_result import RenderResult

_QUERIES = """
queries:
  summary:
    columns: [net_revenue, vs_plan]
    values:
      - [4210000.5, 0.042]
  by_region:
    columns: [region, revenue]
    values:
      - [EMEA, 1800000]
      - [AMER, 1500000]
"""

_FIXED_WIDTH = "style:\n  frame:\n    width: 600\n"

_FULL = (
    """title: "Q3 revenue {{ queries.summary.one.net_revenue | format('currency') }}"
"""
    + _QUERIES
    + """
charts:
  regions:
    query: by_region
    type: bar
    x: region
    y: revenue
    title: "{{ queries.by_region.rows | length }} regions"
    subtitle: "{{ queries.summary.one.vs_plan | format('percent_delta') }} vs plan"
  headline:
    query: summary
    type: kpi
    value: net_revenue
    label: "Net revenue ({{ 'ahead' if queries.summary.one.vs_plan >= 0 else 'behind' }})"
  note:
    type: callout
    title: "Plan {{ queries.summary.one.vs_plan | format('percent') }}"
    message: "{{ queries.by_region.rows[0].region }} leads"
rows:
  - headline
  - regions
  - note
"""
)


def _executor(
    yaml: str, local_project: Callable[..., Project], base_dir: object = None
) -> Executor:
    result = compile(yaml) if base_dir is None else compile(yaml, base_dir=base_dir)
    assert result.success, result.errors
    assert result.board is not None
    return Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )


def _render(
    yaml: str, local_project: Callable[..., Project], fmt: str = "svg"
) -> RenderResult:
    executor = _executor(yaml, local_project)
    return render(executor.board, executor, format=fmt)


def _plain(output: object) -> str:
    assert isinstance(output, str)
    plain = html.unescape(re.sub(r"<[^>]+>", " ", output))
    return re.sub(r"\s+", " ", plain)


def _same_picture(first: str, second: str) -> bool:
    """Whether two renders draw the same board, ignoring the render timestamp."""
    stamp = re.compile(r' data-rendered-at="[^"]*"')
    return stamp.sub("", first) == stamp.sub("", second)


def _text(yaml: str, local_project: Callable[..., Project]) -> str:
    result = _render(yaml, local_project)
    assert result.board_error is None, result.board_error
    assert not result.chart_errors, result.chart_errors
    return _plain(result.output)


def test_every_surface_reads_query_results(
    local_project: Callable[..., Project],
) -> None:
    executor = _executor(_FULL, local_project)
    result = render(executor.board, executor, format="svg")
    assert result.board_error is None, result.board_error
    assert not result.chart_errors, result.chart_errors
    assert isinstance(result.output, str)
    assert 'data-dbt-page-title="Q3 revenue $4.21mn"' in result.output
    assert 'data-chart-title="2 regions"' in result.output
    assert 'data-chart-title="Net revenue (ahead)"' in result.output
    plain = _plain(result.output)
    assert "Plan 4.2%" in plain
    assert "EMEA leads" in plain
    resolved, _ = build_resolved_board(executor.board, executor, {}, render_first=False)
    assert resolved.charts["regions"].subtitle == "+4.2% vs plan"


def test_board_title_paints_the_resolved_text(
    local_project: Callable[..., Project],
) -> None:
    data = _render(
        'title: "{{ queries.by_region.rows | length }} regions"\ntext: body\n'
        + _FIXED_WIDTH
        + _QUERIES,
        local_project,
    )
    literal = _render(
        "title: 2 regions\ntext: body\n" + _FIXED_WIDTH + _QUERIES, local_project
    )
    assert data.board_error is None, data.board_error
    assert isinstance(data.output, str) and isinstance(literal.output, str)
    assert _same_picture(data.output, literal.output)


def test_html_page_title_reads_query_results(
    local_project: Callable[..., Project],
) -> None:
    result = _render(_FULL, local_project, "html")
    assert result.board_error is None, result.board_error
    assert isinstance(result.output, str)
    assert "<title>Q3 revenue $4.21mn</title>" in result.output


def test_nested_board_title_reads_query_results(
    local_project: Callable[..., Project],
) -> None:
    def board(title: str) -> str:
        return (
            _FIXED_WIDTH
            + _QUERIES
            + f"""
rows:
  - title: "{title}"
    text: body
    cols: []
"""
        )

    data = _render(board("{{ queries.by_region.rows | length }} nested"), local_project)
    literal = _render(board("2 nested"), local_project)
    assert data.board_error is None, data.board_error
    assert isinstance(data.output, str) and isinstance(literal.output, str)
    assert _same_picture(data.output, literal.output)


def test_chart_title_using_only_a_variable_renders(
    local_project: Callable[..., Project],
) -> None:
    text = _text(
        _QUERIES
        + """
variables:
  region:
    input: select
    default: EMEA
    options:
      static: [EMEA, AMER]
charts:
  c:
    query: by_region
    type: bar
    x: region
    y: revenue
    title: "Region {{ region }}"
rows:
  - c
""",
        local_project,
    )
    assert "Region EMEA" in text


def test_chart_text_error_is_that_charts_in_card_error(
    local_project: Callable[..., Project],
) -> None:
    result = _render(
        _QUERIES
        + """
charts:
  bad:
    query: summary
    type: kpi
    value: net_revenue
    label: "{{ queries.summary.one.net_revnue }}"
  good:
    query: by_region
    type: bar
    x: region
    y: revenue
    title: fine
rows:
  - bad
  - good
""",
        local_project,
    )
    assert result.board_error is None
    assert [e.code for e in result.chart_errors] == ["ERR-TEXT-QUERY-UNKNOWN-COLUMN"]
    assert isinstance(result.output, str)
    assert 'data-chart-id="good"' in result.output


@pytest.mark.parametrize("field", ["title", "subtitle"])
def test_chart_title_and_subtitle_errors_are_in_card_errors(
    field: str, local_project: Callable[..., Project]
) -> None:
    result = _render(
        _QUERIES
        + f"""
charts:
  bad:
    query: by_region
    type: bar
    x: region
    y: revenue
    {field}: "{{{{ queries.by_region.one.region }}}}"
rows:
  - bad
""",
        local_project,
    )
    assert result.board_error is None
    assert [e.code for e in result.chart_errors] == ["ERR-TEXT-QUERY-ROW-COUNT"]


def test_callout_error_is_an_in_card_error(
    local_project: Callable[..., Project],
) -> None:
    result = _render(
        _QUERIES
        + """
charts:
  bad:
    type: callout
    message: "{{ queries.by_region.one.region }}"
rows:
  - bad
""",
        local_project,
    )
    assert result.board_error is None
    assert [e.code for e in result.chart_errors] == ["ERR-TEXT-QUERY-ROW-COUNT"]


@pytest.mark.parametrize(
    ("title", "code"),
    [
        ("{{ queries.by_region.one.region }}", "ERR-TEXT-QUERY-ROW-COUNT"),
        ("{{ queries.summary.one.net_revnue }}", "ERR-TEXT-QUERY-UNKNOWN-COLUMN"),
        ("{{ queries.summary.net_revenue }}", "ERR-TEXT-QUERY-ACCESS"),
        ("{{ 3 | format('nope') }}", "ERR-TEXT-FORMAT-USAGE"),
    ],
)
def test_board_title_error_is_the_board_error(
    title: str, code: str, local_project: Callable[..., Project]
) -> None:
    result = _render(f'title: "{title}"\ntext: body\n{_QUERIES}', local_project)
    assert result.board_error is not None
    assert result.board_error.code == code


def test_unknown_query_in_a_title_names_the_surface() -> None:
    result = compile(f"title: '{{{{ queries.nope.one.x }}}}'\ntext: body\n{_QUERIES}")
    assert [e.code for e in result.errors] == ["ERR-TEXT-UNKNOWN-QUERY"]
    assert "Board title references unknown query 'nope'" in result.errors[0].message


def test_unknown_query_in_chart_text_names_the_surface() -> None:
    result = compile(
        f"""{_QUERIES}
charts:
  c:
    query: summary
    type: kpi
    value: net_revenue
    label: '{{{{ queries.nope.one.x }}}}'
rows:
  - c
"""
    )
    assert [e.code for e in result.errors] == ["ERR-TEXT-UNKNOWN-QUERY"]
    assert "Chart 'c' label references unknown query 'nope'" in result.errors[0].message


def test_board_to_dict_carries_resolved_titles(
    local_project: Callable[..., Project],
) -> None:
    executor = _executor(_FULL, local_project)
    out = board_to_dict(executor.board, executor, {})
    assert out["title"] == "Q3 revenue $4.21mn"
    chart_titles = [item["chart"].get("title") for item in out["items"]]
    assert "2 regions" in chart_titles
    assert "Plan 4.2%" in chart_titles


def test_static_skeleton_keeps_raw_titles() -> None:
    result = compile(_FULL)
    assert result.board is not None
    resolved = build_resolved_board_static(result.board)
    titles = {
        item.chart.id: item.chart.title
        for item in resolved.layout.items
        if item.chart is not None
    }
    assert titles["regions"] == "{{ queries.by_region.rows | length }} regions"


@pytest.mark.parametrize(
    ("chart_yaml", "chart_id"),
    [
        (
            """
  c:
    query: summary
    type: kpi
    value: net_revenue
    label: "{{ region }}"
""",
            "c",
        ),
        (
            """
  c:
    type: callout
    message: "{{ region }}"
""",
            "c",
        ),
        (
            """
  c:
    query: by_region
    type: bar
    x: region
    y: revenue
    title: "{{ queries.by_region_filtered.rows | length }}"
""",
            "c",
        ),
    ],
    ids=["kpi-label", "callout-message", "data-title-query-reads-variable"],
)
def test_chart_focus_keeps_variables_text_reads(chart_yaml: str, chart_id: str) -> None:
    yaml = (
        _QUERIES
        + """
  by_region_filtered:
    sql: SELECT 1 AS n WHERE '{{ region }}' = 'EMEA'
    source: db
variables:
  region:
    input: select
    default: EMEA
    options:
      static: [EMEA, AMER]
charts:"""
        + chart_yaml
        + "rows:\n  - c\n"
    )
    result = compile(yaml)
    assert result.success, result.errors
    assert result.board is not None
    assert "region" in result.board.charts[chart_id].variable_dependencies
    focused = focus_on_chart(result.board, chart_id)
    assert "region" in focused.variables


def test_chart_focus_with_a_formatted_board_title_compiles() -> None:
    result = compile(
        "title: \"{{ 3 | format('currency') }}\"\nchart_focus: c\n"
        + _QUERIES
        + "charts:\n  c:\n    query: summary\n    type: kpi\n    value: net_revenue\n"
        "rows:\n  - c\n"
    )
    assert result.success, result.errors


def test_imported_callout_message_reads_the_source_files_query(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    (tmp_path / "src.yml").write_text(
        """
queries:
  src_q:
    columns: [label, n]
    values:
      - [from source, 1]
charts:
  note:
    type: callout
    message: "{{ queries.src_q.one.label }}"
""",
        encoding="utf-8",
    )
    project = local_project(root=tmp_path)
    result = compile(
        "charts:\n  note: src.yml.charts.note\nrows:\n  - note\n",
        base_dir=project.directory(),
    )
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    out = render(executor.board, executor, format="svg")
    assert out.board_error is None, out.board_error
    assert not out.chart_errors, out.chart_errors
    assert "from source" in _plain(out.output)


def test_hug_board_measures_a_data_title_at_the_text_preferred_width() -> None:
    from dbt_charts.core.compile.resolve.style.markdown_style import (
        title_max_content_width,
    )

    result = compile(
        "title: \"{{ queries.summary.one.net_revenue | format('currency') }}\"\n"
        + _QUERIES
        + "text: ' '\n"
    )
    assert result.board is not None
    board: Board = result.board
    style = board.resolved_style
    assert title_max_content_width(
        board.title, board.level, style, {}, board.base_dir
    ) == float(style.text.preferred_width)


def test_chart_focus_width_ignores_a_data_title(
    local_project: Callable[..., Project],
) -> None:
    def focused_width(title: str) -> float:
        board = compile(
            f'title: "{title}"\nchart_focus: c\n'
            + _QUERIES
            + """
charts:
  c:
    query: by_region
    type: bar
    x: region
    y: revenue
rows:
  - c
"""
        ).board
        assert board is not None
        executor = Executor(
            board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=None,
        )
        resolved, _ = build_resolved_board(board, executor, {}, render_first=False)
        return next(i.width for i in resolved.layout.items if i.chart)

    data_title = "{{ queries.summary.one.net_revenue | format('currency') }}"
    assert focused_width(data_title) == focused_width("plain")


def test_terminal_renders_data_titles(
    local_project: Callable[..., Project],
) -> None:
    result = _render(_FULL, local_project, "terminal")
    assert result.board_error is None, result.board_error
    out = result.output
    assert isinstance(out, str)
    assert "Q3 revenue $4.21mn" in out
    assert "2 regions" in out
    assert "Net revenue (ahead)" in out


def test_terminal_undefined_variable_in_a_title_renders_empty(
    local_project: Callable[..., Project],
) -> None:
    result = _render(
        "title: 'Hello {{ nope }}!'\ntext: body\n", local_project, "terminal"
    )
    assert result.board_error is None, result.board_error
    assert isinstance(result.output, str)
    assert "Hello !" in result.output


_TYPE_ERROR_EXPR = "{{ queries.summary.one.net_revenue + 'a' }}"


def test_type_error_in_a_chart_title_is_an_in_card_error(
    local_project: Callable[..., Project],
) -> None:
    result = _render(
        _QUERIES
        + f"""
charts:
  bad:
    query: by_region
    type: bar
    x: region
    y: revenue
    title: "{_TYPE_ERROR_EXPR}"
  good:
    query: by_region
    type: bar
    x: region
    y: revenue
    title: fine
rows:
  - bad
  - good
""",
        local_project,
    )
    assert result.board_error is None, result.board_error
    assert [e.code for e in result.chart_errors] == ["ERR-TEXT-TYPE-ERROR"]
    assert isinstance(result.output, str)
    assert 'data-chart-id="good"' in result.output


@pytest.mark.parametrize("field", ["title", "text"])
def test_type_error_in_board_title_or_text_is_the_board_error(
    field: str, local_project: Callable[..., Project]
) -> None:
    other = "text: body\n" if field == "title" else ""
    result = _render(f'{field}: "{_TYPE_ERROR_EXPR}"\n{other}{_QUERIES}', local_project)
    assert result.board_error is not None
    assert result.board_error.code == "ERR-TEXT-TYPE-ERROR"


_INLINE = (
    _QUERIES
    + """
  by_region_filtered:
    sql: SELECT 1 AS n WHERE '{{ region }}' = 'EMEA'
    source: db
variables:
  region:
    input: select
    default: EMEA
    options:
      static: [EMEA, AMER]
"""
)


def test_inline_chart_unknown_text_query_is_a_compile_error() -> None:
    result = compile(
        _INLINE
        + """
rows:
  - type: kpi
    query: summary
    value: net_revenue
    label: "{{ queries.nope.one.x }}"
"""
    )
    assert [e.code for e in result.errors] == ["ERR-TEXT-UNKNOWN-QUERY"]
    assert "Chart 'rows.0' label" in result.errors[0].message


def test_inline_chart_text_query_variables_join_its_dependencies() -> None:
    result = compile(
        _INLINE
        + """
rows:
  - type: kpi
    query: summary
    value: net_revenue
    label: "{{ queries.by_region_filtered.rows | length }}"
"""
    )
    assert result.success, result.errors
    assert result.board is not None
    [chart] = result.board.charts.values()
    assert "region" in chart.variable_dependencies


def _source_project(tmp_path: Path, chart_yaml: str) -> str:
    (tmp_path / "src.yml").write_text(
        """
queries:
  src_q:
    columns: [label, n]
    values:
      - [from source, 1]
charts:
"""
        + chart_yaml,
        encoding="utf-8",
    )
    return "charts:\n  c: src.yml.charts.c\nrows:\n  - c\n"


def test_imported_chart_unknown_text_query_names_the_surface(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    board = _source_project(
        tmp_path,
        '  c:\n    type: callout\n    message: "{{ queries.nope.one.label }}"\n',
    )
    result = compile(board, base_dir=local_project(root=tmp_path).directory())
    assert [e.code for e in result.errors] == ["ERR-TEXT-UNKNOWN-QUERY"]
    assert "Chart 'c' message references unknown query 'nope'" in (
        result.errors[0].message
    )


def test_imported_chart_title_reads_the_source_files_query(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    board = _source_project(
        tmp_path,
        "  c:\n    type: bar\n    query: src_q\n    x: label\n    y: n\n"
        '    title: "{{ queries.src_q.one.label }}"\n',
    )
    project = local_project(root=tmp_path)
    result = compile(board, base_dir=project.directory())
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    resolved, _ = build_resolved_board(result.board, executor, {}, render_first=False)
    assert resolved.charts["c"].title == "from source"


def test_collect_all_query_names_includes_title_and_subtitle_only_queries() -> None:
    from dbt_charts.core.execute.collect import collect_all_query_names

    titled = compile(
        "title: '{{ queries.summary.one.net_revenue }}'\ntext: x\n" + _QUERIES
    )
    assert titled.board is not None
    assert "summary" in collect_all_query_names(titled.board)
    subtitled = compile(
        _QUERIES
        + """
charts:
  c:
    query: by_region
    type: bar
    x: region
    y: revenue
    subtitle: "{{ queries.summary.one.vs_plan }}"
rows:
  - c
"""
    )
    assert subtitled.board is not None
    assert "summary" in collect_all_query_names(subtitled.board)


def test_terminal_nested_board_uses_its_own_format_aliases(
    local_project: Callable[..., Project],
) -> None:
    result = _render(
        _QUERIES
        + """
rows:
  - title: "{{ queries.summary.one.net_revenue | format('mine') }}"
    text: body
    style:
      formats:
        mine: currency
    cols: []
""",
        local_project,
        "terminal",
    )
    assert result.board_error is None, result.board_error
    assert isinstance(result.output, str)
    assert "$4.21mn" in result.output


def test_imported_chart_text_reads_a_qualified_query_ref(
    tmp_path: Path, local_project: Callable[..., Project]
) -> None:
    (tmp_path / "lib.yml").write_text(
        "queries:\n  lib_q:\n    columns: [label]\n    values:\n      - [from lib]\n",
        encoding="utf-8",
    )
    (tmp_path / "src.yml").write_text(
        """
charts:
  note:
    type: callout
    message: "{{ queries['lib.yml#lib_q'].one.label }}"
""",
        encoding="utf-8",
    )
    project = local_project(root=tmp_path)
    result = compile(
        "charts:\n  note: src.yml.charts.note\nrows:\n  - note\n",
        base_dir=project.directory(),
    )
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(project),
        query_registry=result.query_registry,
    )
    out = render(executor.board, executor, format="svg")
    assert out.board_error is None, out.board_error
    assert not out.chart_errors, out.chart_errors
    assert "from lib" in _plain(out.output)


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param(
            "variables:\n  show:\n    input: checkbox\n    default: false\n"
            "rows:\n  - text: shown\n"
            '  - visible: "{{ show }}"\n'
            '    title: "{{ queries.empty_q.one.x }}"\n    text: body\n',
            id="hidden-section",
        ),
        pytest.param(
            "rows:\n  - text: shown\n  - tabs:\n      items:\n"
            "        - title: First\n          text: fine\n"
            '        - title: "{{ queries.empty_q.one.x }}"\n'
            "          text: body\n",
            id="inactive-tab",
        ),
        pytest.param(
            "rows:\n  - text: shown\n  - visible: false\n    cols:\n"
            '      - title: "{{ queries.empty_q.one.x }}"\n        text: a\n'
            "      - title: B\n        text: b\n",
            id="hidden-cols-section",
        ),
    ],
)
def test_a_hidden_nested_board_title_is_never_resolved(
    layout: str, local_project: Callable[..., Project]
) -> None:
    empty = "  empty_q:\n    columns: [x]\n    values: []\n"
    result = _render(_QUERIES + empty + layout, local_project)
    assert result.board_error is None, result.board_error
