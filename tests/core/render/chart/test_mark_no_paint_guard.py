"""Regression coverage for the chart-painted-no-marks guard.

``_render_chart_item_inner`` raises ``RenderError.from_code(ERR_CHART_PAINTED_NO_MARKS,
...)`` when a plotting-family chart's query returns rows, at least one measure value
is genuinely non-zero, and every mark it drew is degenerate — a chart-shaped hole with
no error and no warning. All-zero measure data is a faithful blank render (the engine
painting exactly what the data said) and must not error. These tests pin: a linked
chart (marks wrapped in ``<a xlink:href>``) must not be misread as blank; a chart with
one legitimate zero-valued bar among several must not be flagged as fully blank; an
all-zero-valued chart (single- and multi-series) must render without error; and a
time series with non-zero x values but an all-zero measure must render without error.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile as compile_yaml
from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.execute.file_source_materializer import FileSourceMaterializer
from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache
from dbt_charts.core.render.chart.mark_extents import (
    all_marks_degenerate,
    mark_extents,
)
from dbt_charts.core.render.chart.rendering import render_chart_item

from ...._paths import DBT_CHARTS_DIR

EXAMPLES_DIR = DBT_CHARTS_DIR / "examples"

_RESOLVED_STYLE = resolve_style(get_theme_style())
_CHART_CTX = resolve_chart_style_context(get_theme_style())


def _link_examples_board_chart(chart_id: str) -> tuple[Chart, Executor]:
    project = FilesystemProject(EXAMPLES_DIR / "playground")
    board_path = project.root / "charts" / "reference" / "link-examples.yml"
    result = compile_yaml(
        board_path.read_text(encoding="utf-8"),
        base_dir=project.directory(),
        project_sources=project.sources,
    )
    assert result.success, result.errors
    board = result.board
    assert board is not None
    executor = Executor(
        board,
        query_registry=result.query_registry,
        adapter_registry=build_adapter_registry(
            project,
            read_only=True,
            allow_external_access_in_readonly=True,
            duckdb_config={"enable_external_access": True},
        ),
        file_materializer=FileSourceMaterializer(project, TrivialDuckDBCache()),
    )
    return board.charts[chart_id], executor


def test_linked_bar_chart_measures_nonzero_marks_and_does_not_error() -> None:
    """`link:` wraps every bar's path in `<a xlink:href>`; the guard must read
    through it, not mistake the linked chart for blank.
    """
    for chart_id in ("static_link", "dynamic_path_link"):
        chart, executor = _link_examples_board_chart(chart_id)
        data = executor.execute_query(chart.query_name)
        assert data, f"{chart_id}: query returned no rows"
        resolved_chart = resolve(chart, data, chart_style_context=_CHART_CTX)

        svg, _height = render_chart_item(
            resolved_chart,
            executor,
            variables={},
            available_width=300,
            available_height=200,
            resolved_style=_RESOLVED_STYLE,
            render_cache={},
        )

        assert "ERR-CHART-PAINTED-NO-MARKS" not in svg, f"{chart_id}: guard misfired"
        wrapped_svg = f'<svg xmlns="http://www.w3.org/2000/svg">{svg}</svg>'
        extents = mark_extents(wrapped_svg)
        assert extents and extents[0].count > 0, f"{chart_id}: measured 0 marks"
        assert extents[0].max_width > 0, f"{chart_id}: max_width measured 0"
        assert extents[0].max_height > 0, f"{chart_id}: max_height measured 0"


def test_guard_fires_and_routes_through_the_error_card(
    make_chart: Callable[..., Any],
) -> None:
    """Positive control for the guard's real firing path.

    Every other test in this file asserts the guard stays silent. This one
    pins the wiring itself: predicate -> raise -> `_render_callout_block`
    error card. Patches `render_chart_artifact` (the SVG-producing call in
    `_render_chart_item_inner`) to return a marks-free SVG for a chart whose
    rows carry a real non-zero measure, so the guard has no honest reason not
    to fire. Deliberately not one of the five real corpus defects — those
    have their own bug briefs and will be fixed, which would silently gut
    this test.
    """
    from dbt_charts.core.render.converters import chart as converters_chart

    data = [{"cat": "A", "val": 10}, {"cat": "B", "val": 20}]
    chart = make_chart("bar", x="cat", y="val")
    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = data

    marks_free_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200" '
        'viewBox="0 0 300 200"></svg>'
    )
    with patch.object(
        converters_chart, "render_chart_artifact", return_value=marks_free_svg
    ):
        svg, _height = render_chart_item(
            resolve(chart, data, chart_style_context=_CHART_CTX),
            executor,
            variables={},
            available_width=300,
            available_height=200,
            resolved_style=_RESOLVED_STYLE,
            render_cache={},
        )

    assert "ERR-CHART-PAINTED-NO-MARKS" in svg
    assert chart.id in svg
    assert f"{len(data)} row" in svg


def _render_chart(
    make_chart: Callable[..., Any],
    data: list[dict[str, Any]],
    chart_type: str = "bar",
    **chart_kwargs: Any,
) -> str:
    chart = make_chart(chart_type, **chart_kwargs)
    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = data
    svg, _height = render_chart_item(
        resolve(chart, data, chart_style_context=_CHART_CTX),
        executor,
        variables={},
        available_width=300,
        available_height=200,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )
    return svg


def _render_bar_chart(
    make_chart: Callable[..., Any], data: list[dict[str, Any]]
) -> str:
    return _render_chart(make_chart, data, "bar", x="cat", y="val")


def test_bar_chart_with_one_zero_valued_bar_among_several_still_renders(
    make_chart: Callable[..., Any],
) -> None:
    """The 'every mark degenerate' quantifier is load-bearing: a single zero bar
    among healthy ones is correct data, not a broken render.
    """
    data = [
        {"cat": "A", "val": 10},
        {"cat": "B", "val": 0},
        {"cat": "C", "val": 5},
    ]
    svg = _render_bar_chart(make_chart, data)
    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg


def test_bar_chart_with_every_bar_zero_valued_renders_without_error(
    make_chart: Callable[..., Any],
) -> None:
    """All-zero measure data is a faithful blank render (RJ, 2026-08-05): the
    engine painted exactly what the data said, so this must not error.
    """
    data = [{"cat": "A", "val": 0}, {"cat": "B", "val": 0}]
    svg = _render_bar_chart(make_chart, data)
    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg


def test_time_series_all_zero_measure_renders_without_error(
    make_chart: Callable[..., Any],
) -> None:
    """Non-zero x values (dates) with an all-zero measure must not false-positive
    under a crude "any numeric column" scan — the measure channel, not the date
    column, decides.
    """
    data = [
        {"day": "2026-01-01", "val": 0},
        {"day": "2026-01-02", "val": 0},
        {"day": "2026-01-03", "val": 0},
    ]
    svg = _render_chart(make_chart, data, "line", x="day", y="val")
    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg


def test_multi_series_one_series_nonzero_still_renders(
    make_chart: Callable[..., Any],
) -> None:
    """One all-zero series among several with real values renders normally."""
    data = [
        {"cat": "A", "val_a": 0, "val_b": 10},
        {"cat": "B", "val_a": 0, "val_b": 5},
    ]
    svg = _render_chart(make_chart, data, "bar", x="cat", y=["val_a", "val_b"])
    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg


def test_multi_series_all_series_zero_renders_without_error(
    make_chart: Callable[..., Any],
) -> None:
    """Every series all-zero is still a faithful blank, not a defect."""
    data = [
        {"cat": "A", "val_a": 0, "val_b": 0},
        {"cat": "B", "val_a": 0, "val_b": 0},
    ]
    svg = _render_chart(make_chart, data, "bar", x="cat", y=["val_a", "val_b"])
    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg


def test_grouped_bar_on_quantitative_x_paints_visible_marks(
    make_chart: Callable[..., Any],
) -> None:
    """Color-grouped bar (stack='none') on a quantitative x must draw real bars.

    VL auto-stacks a bar mark whenever a discrete channel (color) accompanies
    the quantitative measure, even with `xOffset` present and no aggregate —
    collapsing every bar to zero height against a y-domain sized for the
    grouped (non-stacked) case (reproduces at 3+ color groups — 2 renders
    fine, a VL quirk). The fix suppresses that default explicitly with
    `y.stack: null`; without it this test trips the guard above instead of
    drawing anything.
    """
    data = [
        {"x_num": 1, "val": 10, "series": "A"},
        {"x_num": 1, "val": 20, "series": "B"},
        {"x_num": 1, "val": 15, "series": "C"},
        {"x_num": 2, "val": 12, "series": "A"},
        {"x_num": 2, "val": 22, "series": "B"},
        {"x_num": 2, "val": 17, "series": "C"},
    ]
    svg = _render_chart(
        make_chart, data, "bar", x="x_num", y="val", color="series", stack="none"
    )
    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg
    wrapped_svg = f'<svg xmlns="http://www.w3.org/2000/svg">{svg}</svg>'
    extents = mark_extents(wrapped_svg)
    assert extents and extents[0].max_height > 0, "bars rendered zero height"


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_grouped_bar_with_numeric_color_paints_visible_marks(
    make_chart: Callable[..., Any], orientation: str
) -> None:
    """A numeric/boolean `color:` resolves to a quantitative VL type, but
    `xOffset`/`yOffset` (grouped-bar sub-banding) must still get a discrete
    type — a continuous scale has no band for ``bandwidth(...)`` to size
    against, which degenerates every bar to zero width/height.
    """
    data = [
        {"cat": "A", "val": 10, "flag": 1},
        {"cat": "A", "val": 15, "flag": 0},
        {"cat": "B", "val": 12, "flag": 1},
        {"cat": "B", "val": 18, "flag": 0},
    ]
    svg = _render_chart(
        make_chart,
        data,
        "bar",
        x="cat",
        y="val",
        color="flag",
        stack="none",
        style={"orientation": orientation},
    )
    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg
    wrapped_svg = f'<svg xmlns="http://www.w3.org/2000/svg">{svg}</svg>'
    assert not all_marks_degenerate(wrapped_svg), "bars rendered zero width/height"
    extents = mark_extents(wrapped_svg)
    assert extents and extents[0].max_width > 0, "bars rendered zero width"
    assert extents[0].max_height > 0, "bars rendered zero height"


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_grouped_bar_with_boolean_color_paints_visible_marks(
    make_chart: Callable[..., Any], orientation: str
) -> None:
    """Same as above for a boolean color column — `bool` is an `int`
    subclass, so it hits the same quantitative-color-scale path as a numeric
    field (see ``is_vega_numeric_value`` in ``core/utils.py``).
    """
    data = [
        {"cat": "A", "val": 10, "flag": True},
        {"cat": "A", "val": 15, "flag": False},
        {"cat": "B", "val": 12, "flag": True},
        {"cat": "B", "val": 18, "flag": False},
    ]
    svg = _render_chart(
        make_chart,
        data,
        "bar",
        x="cat",
        y="val",
        color="flag",
        stack="none",
        style={"orientation": orientation},
    )
    assert "ERR-CHART-PAINTED-NO-MARKS" not in svg
    wrapped_svg = f'<svg xmlns="http://www.w3.org/2000/svg">{svg}</svg>'
    assert not all_marks_degenerate(wrapped_svg), "bars rendered zero width/height"
    extents = mark_extents(wrapped_svg)
    assert extents and extents[0].max_width > 0, "bars rendered zero width"
    assert extents[0].max_height > 0, "bars rendered zero height"
