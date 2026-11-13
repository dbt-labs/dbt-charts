"""A chart that names no channel binds its query's columns by position."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart.positional import bind_channels, columns_of
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_CHART_CHANNELS_INCOMPLETE,
    ERR_CHART_CHANNELS_NO_QUERY,
    ERR_CHART_CHANNELS_UNBOUND,
)
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.render.placeholder import generate_placeholder_data

from ....._run import Run, render_run
from ...._svg_render import pinned_clock, render_board_svg_result


def _board(chart: dict[str, Any], columns: list[str], values: list[list[Any]]) -> str:
    return (
        "queries:\n"
        "  q:\n"
        f"    columns: {json.dumps(columns)}\n"
        f"    values: {json.dumps(values)}\n"
        "charts:\n"
        "  c:\n"
        f"    query: q\n"
        + "".join(f"    {key}: {json.dumps(value)}\n" for key, value in chart.items())
        + "rows:\n  - c\n"
    )


_MONTHS = [
    ["2025-01-01", "east", 10.0],
    ["2025-02-01", "east", 12.0],
    ["2025-01-01", "west", 7.0],
]
_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


# ---------------------------------------------------------------------------
# The binder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chart_type", "columns", "bound"),
    [
        ("bar", ["month", "revenue"], {"x": "month", "y": "revenue"}),
        (
            "bar",
            ["month", "region", "revenue"],
            {"x": "month", "color": "region", "y": "revenue"},
        ),
        ("line", ["month", "revenue"], {"x": "month", "y": "revenue"}),
        (
            "line",
            ["month", "region", "revenue"],
            {"x": "month", "color": "region", "y": "revenue"},
        ),
        ("area", ["month", "revenue"], {"x": "month", "y": "revenue"}),
        (
            "area",
            ["month", "region", "revenue"],
            {"x": "month", "color": "region", "y": "revenue"},
        ),
        ("scatter", ["price", "units"], {"x": "price", "y": "units"}),
        (
            "scatter",
            ["product", "price", "units"],
            {"color": "product", "x": "price", "y": "units"},
        ),
        (
            "heatmap",
            ["month", "region", "revenue"],
            {"x": "month", "y": "region", "color": "revenue"},
        ),
        ("pie", ["revenue"], {"theta": "revenue"}),
        ("pie", ["region", "revenue"], {"color": "region", "theta": "revenue"}),
        ("donut", ["region", "revenue"], {"color": "region", "theta": "revenue"}),
        ("kpi", ["revenue"], {"value": "revenue"}),
        ("histogram", ["price"], {"x": "price"}),
    ],
    ids=lambda v: (
        v if isinstance(v, str) else "-".join(v) if isinstance(v, list) else ""
    ),
)
def test_an_unbound_chart_binds_columns_by_position(
    make_chart, chart_type: str, columns: list[str], bound: dict[str, str]
) -> None:
    unbound = make_chart(chart_type, **_no_channels(chart_type))
    chart = bind_channels(unbound, columns)
    assert {key: getattr(chart, key) for key in bound} == bound


def _no_channels(chart_type: str) -> dict[str, Any]:
    """make_chart's keyword overrides that leave every positional channel unset."""
    if chart_type in ("pie", "donut"):
        return {"theta": None, "color": None}
    if chart_type == "kpi":
        return {"value": None}
    return {"x": None, "y": None}


@pytest.mark.parametrize(
    ("chart_type", "columns"),
    [
        ("bar", ["revenue"]),
        ("bar", ["month", "region", "revenue", "cost"]),
        ("line", ["month", "region", "revenue", "cost"]),
        ("scatter", ["price"]),
        ("heatmap", ["month", "revenue"]),
        ("pie", ["month", "region", "revenue"]),
        ("kpi", ["region", "revenue"]),
        ("histogram", ["price", "units"]),
    ],
    ids=lambda v: v if isinstance(v, str) else str(len(v)),
)
def test_a_column_count_outside_the_table_raises_naming_the_columns(
    make_chart, chart_type: str, columns: list[str]
) -> None:
    with pytest.raises(CompilationError) as exc:
        bind_channels(make_chart(chart_type, **_no_channels(chart_type)), columns)
    assert exc.value.code is ERR_CHART_CHANNELS_UNBOUND
    for column in columns:
        assert column in str(exc.value)


@pytest.mark.parametrize(
    ("chart_type", "named", "missing"),
    [
        ("bar", {"x": "a"}, "`y`"),
        ("bar", {"y": "a"}, "`x`"),
        ("bar", {"y": ["a", "b"]}, "`x`"),
        ("bar", {"color": "a"}, "`x` and `y`"),
        ("line", {"x": "a"}, "`y`"),
        ("area", {"y": "a"}, "`x`"),
        ("scatter", {"size": "a"}, "`x` and `y`"),
        ("bar", {"pattern": "a"}, "`x` and `y`"),
        ("heatmap", {"color": "a"}, "`x` and `y`"),
        ("pie", {"color": "a"}, "`theta`"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_naming_part_of_the_set_raises_whatever_the_columns(
    make_chart, chart_type: str, named: dict[str, Any], missing: str
) -> None:
    chart = make_chart(chart_type, **{**_no_channels(chart_type), **named})
    for columns in ([], ["a", "b"], ["a", "b", "c"]):
        with pytest.raises(CompilationError) as exc:
            bind_channels(chart, columns)
        assert exc.value.code is ERR_CHART_CHANNELS_INCOMPLETE
        assert f"but not {missing}" in str(exc.value)


def test_an_optional_channel_beside_the_set_is_complete(make_chart) -> None:
    chart = make_chart("bar", x="a", y="b", color="c")
    assert bind_channels(chart, ["a", "b", "c", "d"]) is chart


@pytest.mark.parametrize(
    "named",
    [
        {"x": "a", "y": "b"},
        {"x": "a", "y": "b", "pattern": "c"},
        {"x": "a", "y": ["b", "c"]},
    ],
    ids=["plain-grid", "pattern-ramp", "folded-measures"],
)
def test_a_heatmap_names_its_measure_through_color_pattern_or_a_fold(
    make_chart, named: dict[str, Any]
) -> None:
    chart = make_chart("heatmap", **named)
    assert bind_channels(chart, ["a", "b", "c"]) is chart


def test_a_bound_chart_is_returned_untouched_whatever_the_column_count(
    make_chart,
) -> None:
    chart = make_chart("bar", x="month", y="revenue")
    assert bind_channels(chart, ["month", "region", "revenue", "cost"]) is chart


def test_a_table_binds_nothing(make_chart) -> None:
    chart = make_chart("table", x=None, y=None)
    assert bind_channels(chart, ["a", "b", "c", "d", "e"]) is chart


def test_no_columns_leaves_the_chart_unbound(make_chart) -> None:
    chart = make_chart("bar", x=None, y=None)
    assert bind_channels(chart, []) is chart


def test_binding_is_positional_never_typed(make_chart) -> None:
    """A number in the first slot and text in the last still bind by position."""
    chart: Chart = bind_channels(make_chart("bar", x=None, y=None), ["count", "region"])
    assert (chart.x, chart.y) == ("count", "region")


# ---------------------------------------------------------------------------
# Through the pipeline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chart_type", "columns", "values", "explicit"),
    [
        (
            "bar",
            ["month", "revenue"],
            [["Jan", 10], ["Feb", 12]],
            {"x": "month", "y": "revenue"},
        ),
        (
            "bar",
            ["month", "region", "revenue"],
            _MONTHS,
            {"x": "month", "color": "region", "y": "revenue"},
        ),
        (
            "line",
            ["month", "region", "revenue"],
            _MONTHS,
            {"x": "month", "color": "region", "y": "revenue"},
        ),
        (
            "area",
            ["month", "region", "revenue"],
            _MONTHS,
            {"x": "month", "color": "region", "y": "revenue"},
        ),
        (
            "scatter",
            ["price", "units"],
            [[1.0, 4.0], [2.0, 3.0]],
            {"x": "price", "y": "units"},
        ),
        (
            "heatmap",
            ["month", "region", "revenue"],
            _MONTHS,
            {"x": "month", "y": "region", "color": "revenue"},
        ),
        (
            "pie",
            ["region", "revenue"],
            [["east", 10], ["west", 7]],
            {"color": "region", "theta": "revenue"},
        ),
        ("kpi", ["revenue"], [[1234.5]], {"value": "revenue"}),
        ("histogram", ["price"], [[1], [2], [2], [3]], {"x": "price"}),
        (
            "spark_bar",
            ["region", "count"],
            [["east", 10], ["west", 7]],
            {"y": "region", "x": "count"},
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_a_shorthand_chart_renders_identically_to_its_explicit_twin(
    chart_type: str,
    columns: list[str],
    values: list[list[Any]],
    explicit: dict[str, str],
) -> None:
    shorthand = render_board_svg_result(_board({"type": chart_type}, columns, values))
    twin = render_board_svg_result(
        _board({"type": chart_type, **explicit}, columns, values)
    )
    assert shorthand.chart_errors == [] and twin.chart_errors == []
    assert shorthand.output == twin.output


def test_a_column_count_outside_the_table_fails_the_chart_loudly() -> None:
    board = _board(
        {"type": "bar"}, ["month", "region", "revenue", "cost"], [["Jan", "east", 1, 2]]
    )
    result = render_board_svg_result(board)
    assert [e.code for e in result.chart_errors] == [ERR_CHART_CHANNELS_UNBOUND.code]
    assert "month, region, revenue, cost" in result.chart_errors[0].message


@pytest.mark.parametrize(
    ("chart_type", "named"),
    [
        ("bar", {"x": "month"}),
        ("line", {"y": "revenue"}),
        ("area", {"pattern": "region"}),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_a_half_named_chart_fails_loudly_not_blank(
    chart_type: str, named: dict[str, str]
) -> None:
    board = _board(
        {"type": chart_type, **named}, ["month", "region", "revenue"], _MONTHS
    )
    result = render_board_svg_result(board)
    assert [e.code for e in result.chart_errors] == [ERR_CHART_CHANNELS_INCOMPLETE.code]


@pytest.mark.parametrize(
    ("chart_type", "pinned"),
    [
        ("bar", {"layers": [{"type": "line", "y": "revenue"}]}),
        ("bar", {"y_start": "region"}),
        ("line", {"tooltip": ["region"]}),
        ("line", {"connect": "month"}),
        ("line", {"multiples": {"rows": "region"}}),
        ("area", {"tooltip": ["region"]}),
        ("scatter", {"tooltip": ["region"]}),
        ("pie", {"tooltip": ["region"]}),
        ("donut", {"tooltip": ["region"]}),
        ("bar", {"multiples": {"rows": "region"}}),
        ("heatmap", {"multiples": {"rows": "region"}}),
        ("bar", {"sort": {"by": "region"}}),
        ("line", {"sort": {"by": "region"}}),
        ("area", {"sort": {"by": "month"}}),
        ("heatmap", {"sort": {"by": "month"}}),
        ("line", {"support_table": [{"source": "month"}]}),
        ("bar", {"support_table": [{"aggregate": "sum", "source": "region"}]}),
        ("line", {"support_table": [{"per_series": "region", "by_measure": True}]}),
        ("area", {"support_table": [{"source": "month"}]}),
        ("bar", {"style": {"marks": {"bar": {"labels": {"field": "region"}}}}}),
        ("line", {"style": {"marks": {"point": {"labels": {"field": "region"}}}}}),
        ("area", {"style": {"marks": {"line": {"labels": {"field": "region"}}}}}),
        ("scatter", {"style": {"marks": {"point": {"labels": {"field": "region"}}}}}),
    ],
    ids=lambda v: v if isinstance(v, str) else next(iter(v)),
)
def test_a_field_naming_a_column_outside_the_set_is_half_named(
    chart_type: str, pinned: dict[str, Any]
) -> None:
    """Binding by position would read the pinned column a second time."""
    board = _board(
        {"type": chart_type, **pinned}, ["month", "region", "revenue"], _MONTHS
    )
    result = render_board_svg_result(board)
    assert [e.code for e in result.chart_errors] == [ERR_CHART_CHANNELS_INCOMPLETE.code]
    field = "labels" if "style" in pinned else next(iter(pinned))
    assert f"`{field}`" in result.chart_errors[0].message


def test_a_cascaded_label_field_is_not_the_charts_side_column() -> None:
    """The rule reads the chart's own style; a board's cascade is a styling
    choice made without this chart's query in view."""
    columns, values = ["month", "region", "revenue"], _MONTHS
    board = _board({"type": "bar"}, columns, values)
    cascade = "style:\n  charts:\n    bar:\n      marks:\n        bar:\n          labels:\n            field: region\n"
    shorthand = render_board_svg_result(cascade + board)
    twin = render_board_svg_result(
        cascade
        + _board(
            {"type": "bar", "x": "month", "color": "region", "y": "revenue"},
            columns,
            values,
        )
    )
    assert shorthand.chart_errors == [] and twin.chart_errors == []
    assert shorthand.output == twin.output


def test_a_scatter_sorted_by_a_side_column_is_half_named() -> None:
    board = _board(
        {"type": "scatter", "sort": {"by": "price"}},
        ["price", "units", "margin"],
        [[1.0, 4.0, 0.2], [2.0, 3.0, 0.4]],
    )
    result = render_board_svg_result(board)
    assert [e.code for e in result.chart_errors] == [ERR_CHART_CHANNELS_INCOMPLETE.code]


@pytest.mark.parametrize(
    ("columns", "values", "side"),
    [
        (["month", "region", "revenue"], _MONTHS, {"sort": {"by": "revenue"}}),
        (["month", "revenue"], [["Jan", 1], ["Feb", 2]], {"sort": {"by": "month"}}),
        (
            ["month", "revenue"],
            [["Jan", 1], ["Feb", 2]],
            {"support_table": [{"source": "month"}]},
        ),
        (
            ["month", "region", "revenue"],
            _MONTHS,
            {"style": {"marks": {"bar": {"labels": {"field": "revenue"}}}}},
        ),
    ],
    ids=["sort-by-the-measure", "sort-by-x", "support-table-on-x", "labels-on-measure"],
)
def test_a_side_column_the_shape_plots_anyway_binds(
    columns: list[str], values: list[list[Any]], side: dict[str, Any]
) -> None:
    """A sort by the measure, or any side column where every column is a
    required channel, is the explicit twin's chart."""
    shorthand = render_board_svg_result(
        _board({"type": "bar", **side}, columns, values)
    )
    order = {2: {"x": "month", "y": "revenue"}}.get(
        len(columns), {"x": "month", "color": "region", "y": "revenue"}
    )
    twin = render_board_svg_result(
        _board({"type": "bar", **order, **side}, columns, values)
    )
    assert shorthand.chart_errors == [] and twin.chart_errors == []
    assert shorthand.output == twin.output


def test_a_chart_whose_columns_fit_no_shape_does_not_take_its_siblings_down() -> None:
    board = (
        "queries:\n"
        "  wide:\n    columns: [month, region, revenue, cost]\n"
        '    values: [["Jan", "east", 1, 2]]\n'
        "  pair:\n    columns: [month, revenue]\n"
        '    values: [["Jan", 1]]\n'
        "charts:\n"
        "  bad:\n    type: bar\n    query: wide\n"
        "  good:\n    type: bar\n    query: pair\n"
        "rows:\n  - bad\n  - good\n"
    )
    result = render_board_svg_result(board)
    assert [e.code for e in result.chart_errors] == [ERR_CHART_CHANNELS_UNBOUND.code]
    assert "'bad'" in result.chart_errors[0].message
    twin = render_board_svg_result(
        board.replace(
            "    query: pair\n", "    query: pair\n    x: month\n    y: revenue\n"
        )
    )
    assert result.output == twin.output


def test_the_measure_slot_is_still_checked_for_numbers() -> None:
    """Positional binding never looks at types; the family's own check still does."""
    result = render_board_svg_result(
        _board({"type": "bar"}, ["month", "region"], [["Jan", "east"]])
    )
    assert [e.code for e in result.chart_errors] == ["ERR-BAR-Y-NOT-NUMERIC"]


def test_a_cartesian_shorthand_with_no_rows_is_the_zero_row_case() -> None:
    result = render_board_svg_result(_board({"type": "bar"}, ["month", "revenue"], []))
    assert result.chart_errors == []


@pytest.mark.parametrize(
    ("chart_type", "columns", "attachment"),
    [
        ("bar", ["month", "revenue"], {"annotations": [{"type": "rule", "y": 5}]}),
        ("line", ["month", "revenue"], {"annotations": [{"type": "rule", "y": 5}]}),
        ("area", ["month", "revenue"], {"annotations": [{"type": "rule", "y": 5}]}),
        ("scatter", ["price", "units"], {"annotations": [{"type": "rule", "y": 5}]}),
        ("bar", ["month", "revenue"], {"support_table": [{"source": "revenue"}]}),
        ("line", ["month", "revenue"], {"support_table": [{"source": "revenue"}]}),
        ("area", ["month", "revenue"], {"support_table": [{"source": "revenue"}]}),
    ],
    ids=lambda v: (
        v if isinstance(v, str) else (next(iter(v)) if isinstance(v, dict) else "")
    ),
)
def test_a_zero_row_shorthand_draws_its_empty_frame_without_attachments(
    chart_type: str, columns: list[str], attachment: dict[str, Any]
) -> None:
    """An annotation or support table resolves against channels a zero-row
    result never bound; the empty frame draws without them."""
    result = render_board_svg_result(
        _board({"type": chart_type, **attachment}, columns, [])
    )
    assert result.chart_errors == []
    bare = render_board_svg_result(_board({"type": chart_type}, columns, []))
    assert result.output == bare.output


@pytest.mark.parametrize("chart_type", ["kpi", "pie", "bar"])
def test_a_shorthand_chart_with_no_query_names_the_missing_query(
    chart_type: str,
) -> None:
    board = f"charts:\n  c:\n    type: {chart_type}\nrows:\n  - c\n"
    result = render_board_svg_result(board)
    assert [e.code for e in result.chart_errors] == [ERR_CHART_CHANNELS_NO_QUERY.code]


def test_a_pie_with_no_rows_to_bind_from_fails_loudly() -> None:
    result = render_board_svg_result(_board({"type": "pie"}, ["region", "revenue"], []))
    assert [e.code for e in result.chart_errors] == [ERR_CHART_CHANNELS_UNBOUND.code]
    assert "no rows" in result.chart_errors[0].message


def test_a_kpi_with_no_rows_fails_the_way_a_named_one_does() -> None:
    result = render_board_svg_result(_board({"type": "kpi"}, ["revenue"], []))
    assert [e.code for e in result.chart_errors] == ["ERR-KPI-NO-DATA"]


def test_a_direct_resolve_binds_the_chart(make_chart) -> None:
    data = [{"month": "Jan", "revenue": 10}, {"month": "Feb", "revenue": 12}]
    resolved = resolve(
        make_chart("bar", x=None, y=None), data, chart_style_context=_BOARD_CTX
    )
    assert (resolved.x, resolved.y) == ("month", "revenue")


def test_board_wide_category_colors_count_a_shorthand_chart() -> None:
    board = (
        "queries:\n"
        "  ab:\n    columns: [month, region, revenue]\n"
        '    values: [["Jan", "A", 1], ["Jan", "B", 2]]\n'
        "  ca:\n    columns: [month, region, revenue]\n"
        '    values: [["Jan", "C", 3], ["Jan", "A", 4]]\n'
        "charts:\n"
        "  one:\n    type: bar\n    query: ab\n    x: month\n    y: revenue\n"
        "    color: region\n"
        "  two:\n    type: bar\n    query: ab\n    x: month\n    y: revenue\n"
        "    color: region\n"
        "  three:\n    type: bar\n    query: ca\n"
        "rows:\n  - one\n  - two\n  - three\n"
    )
    result = render_board_svg_result(board)
    assert result.chart_errors == []
    twin = render_board_svg_result(
        board.replace(
            "    query: ca\n",
            "    query: ca\n    x: month\n    color: region\n    y: revenue\n",
        )
    )
    assert result.output == twin.output


def test_a_shorthand_chart_inside_a_nested_board_is_bound_board_wide() -> None:
    """The board-wide passes read a nested board's charts too."""
    board = (
        "queries:\n"
        "  ab:\n    columns: [month, region, revenue]\n"
        '    values: [["Jan", "A", 1], ["Jan", "B", 2]]\n'
        "charts:\n"
        "  one:\n    type: bar\n    query: ab\n    x: month\n    y: revenue\n"
        "    color: region\n"
        "rows:\n"
        "  - one\n"
        "  - charts:\n"
        "      three:\n        type: bar\n        query: ab\n"
        "    rows:\n      - three\n"
    )
    result = render_board_svg_result(board)
    assert result.chart_errors == []
    twin = render_board_svg_result(
        board.replace(
            "        query: ab\n",
            "        query: ab\n        x: month\n        color: region\n"
            "        y: revenue\n",
        )
    )
    assert result.output == twin.output


def test_a_support_table_inherits_the_measure_format_on_a_shorthand_chart() -> None:
    chart = {
        "style": {"axis_y": {"labels": {"format": "$,.0f"}}},
        "support_table": [{"source": "revenue"}],
    }
    columns, values = ["month", "revenue"], [["Jan", 1234.5], ["Feb", 2345.6]]
    shorthand = render_board_svg_result(
        _board({"type": "bar", **chart}, columns, values)
    )
    twin = render_board_svg_result(
        _board({"type": "bar", "x": "month", "y": "revenue", **chart}, columns, values)
    )
    assert shorthand.chart_errors == [] and twin.chart_errors == []
    assert "$" in twin.output
    assert shorthand.output == twin.output


def test_the_text_format_reads_the_bound_y_range() -> None:
    chart = {"style": {"axis_y": {"labels": {"format": "$,.0f"}}}}
    columns, values = ["month", "revenue"], [["Jan", 1234.5], ["Feb", 2345.6]]

    def text(extra: dict[str, Any]) -> str:
        result = compile(_board({"type": "line", **chart, **extra}, columns, values))
        assert result.board is not None, result.errors
        run = Run(
            result.board,
            env=build_exec_env(FilesystemProject(Path.cwd())),
            clock=pinned_clock,
        )
        return render_run(result.board, run, format="text").output

    twin = text({"x": "month", "y": "revenue"})
    assert "$1,235" in twin
    assert text({}) == twin


def test_an_automatic_link_reaches_a_shorthand_chart(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A link is offered only on a real source the schema resolver can profile."""
    import sqlite3

    db_path = tmp_path / "orders.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE orders (id INTEGER, status TEXT, amount INTEGER)")
    conn.executemany(
        "INSERT INTO orders VALUES (?, ?, ?)", [(1, "open", 3), (2, "won", 5)]
    )
    conn.commit()
    conn.close()
    (tmp_path / "dbt_charts.yml").write_text(
        f"sources:\n  dw:\n    type: sqlite\n    path: {db_path}\n"
    )

    def render(channels: str) -> str:
        result = compile(
            "links:\n  auto: true\n"
            "queries:\n  q:\n    source: dw\n"
            "    sql: SELECT status, amount FROM main.orders\n"
            f"charts:\n  c:\n    type: bar\n    query: q\n{channels}"
            "rows:\n  - c\n"
        )
        assert result.board is not None, result.errors
        run = Run(
            result.board,
            env=build_exec_env(local_project(tmp_path)),
            clock=pinned_clock,
        )
        rendered = render_run(result.board, run, format="svg")
        assert rendered.chart_errors == []
        return rendered.output

    twin = render("    x: status\n    y: amount\n")
    assert "/data/dw/main/orders/" in twin
    assert render("") == twin


def test_a_heatmap_placeholder_binds_x_to_its_columns(make_chart) -> None:
    chart = make_chart("heatmap", x=None, y=None)
    rows = generate_placeholder_data("heatmap", chart).rows
    bound = bind_channels(chart, columns_of(rows))
    assert (bound.x, bound.y, bound.color) == ("x", "y", "value")


def test_a_mark_without_labels_is_not_a_side_column_source() -> None:
    """`marks.area` carries no `labels`; the label-field rule skips it."""
    chart = {"type": "area", "style": {"marks": {"area": {"opacity": 0.5}}}}
    columns = ["month", "region", "revenue"]
    shorthand = render_board_svg_result(_board(chart, columns, _MONTHS))
    twin = render_board_svg_result(
        _board(
            {**chart, "x": "month", "color": "region", "y": "revenue"}, columns, _MONTHS
        )
    )
    assert shorthand.chart_errors == [] and twin.chart_errors == []
    assert shorthand.output == twin.output


def test_a_three_column_shorthand_chart_takes_no_single_series_slot() -> None:
    """The rhythm slot goes to a chart that paints with the single-series ink;
    the shorthand chart binds `color`, so the chart after it keeps the slot its
    explicit twin leaves it."""
    board = (
        "theme: paper\n"
        "queries:\n"
        "  ab:\n    columns: [month, region, revenue]\n"
        '    values: [["Jan", "A", 1], ["Jan", "B", 2]]\n'
        "  pair:\n    columns: [month, revenue]\n"
        '    values: [["Jan", 1], ["Feb", 2]]\n'
        "charts:\n"
        "  a:\n    type: bar\n    query: ab\n"
        "  b:\n    type: bar\n    query: pair\n    x: month\n    y: revenue\n"
        "rows:\n  - a\n  - b\n"
    )
    shorthand = render_board_svg_result(board)
    twin = render_board_svg_result(
        board.replace(
            "    query: ab\n",
            "    query: ab\n    x: month\n    color: region\n    y: revenue\n",
        )
    )
    assert shorthand.chart_errors == [] and twin.chart_errors == []
    assert shorthand.output == twin.output


_NAMED_PAIR = (
    "theme: paper\n"
    "queries:\n"
    "  pair:\n    columns: [month, revenue]\n"
    '    values: [["Jan", 1], ["Feb", 2]]\n'
    "charts:\n"
    "  a:\n    type: bar\n    query: pair\n    x: month\n    y: revenue\n"
    "  b:\n    type: bar\n    query: pair\n    x: month\n    y: revenue\n"
)


def _expected_inks(yaml_text: str) -> dict[str, str]:
    """Each chart's ink as normalize's slots give it, lowercased."""
    board = compile(yaml_text).board
    palette = board.chart_style_context.single_series_palette
    return {
        chart_id: palette[chart.rhythm_slot % len(palette)].lower()
        for chart_id, chart in board.charts.items()
    }


def _palette_stops_painted(svg: str, yaml_text: str) -> set[str]:
    palette = compile(yaml_text).board.chart_style_context.single_series_palette
    lowered = svg.lower()
    return {stop.lower() for stop in palette if stop.lower() in lowered}


def test_a_chart_placed_twice_paints_one_ink_in_both_placements() -> None:
    board = _NAMED_PAIR + "rows:\n  - a\n  - b\n  - a\n"
    inks = _expected_inks(board)
    assert len(set(inks.values())) == 2
    rendered = render_board_svg_result(board)
    assert rendered.chart_errors == []
    assert _palette_stops_painted(rendered.output, board) == set(inks.values())


def test_a_focused_chart_keeps_its_single_series_slot() -> None:
    board = _NAMED_PAIR + "rows:\n  - a\n  - b\n"
    ink = _expected_inks(board)["b"]
    on_board = render_board_svg_result(board)
    focused = render_board_svg_result(board + "chart_focus: b\n")
    assert on_board.chart_errors == [] and focused.chart_errors == []
    assert ink in on_board.output.lower()
    assert ink in focused.output.lower()


def test_a_shorthand_chart_placed_twice_matches_its_twin_beside_a_color_binder() -> (
    None
):
    """A shorthand chart placed twice at differing widths is one chart, so the
    recount a color-binding sibling triggers counts it once and both placements
    paint the ink its explicit twin paints."""
    board = (
        "theme: paper\n"
        "queries:\n"
        "  pair:\n    columns: [month, revenue]\n"
        '    values: [["Jan", 1], ["Feb", 2]]\n'
        "  ab:\n    columns: [month, region, revenue]\n"
        '    values: [["Jan", "A", 1], ["Jan", "B", 2]]\n'
        "charts:\n"
        "  s:\n    type: bar\n    query: pair\n"
        "  k:\n    type: bar\n    query: ab\n"
        "  r:\n    type: bar\n    query: pair\n    x: month\n    y: revenue\n"
        "grid:\n  columns: 3\n  items:\n"
        "    - item: s\n      width: 1\n"
        "    - item: k\n      width: 2\n"
        "    - item: r\n      width: 1\n"
        "    - item: s\n      width: 2\n"
    )
    shorthand = render_board_svg_result(board)
    twin = render_board_svg_result(
        board.replace(
            "    query: pair\n  k:",
            "    query: pair\n    x: month\n    y: revenue\n  k:",
        ).replace(
            "    query: ab\n",
            "    query: ab\n    x: month\n    color: region\n    y: revenue\n",
        )
    )
    assert shorthand.chart_errors == [] and twin.chart_errors == []
    assert shorthand.output == twin.output
