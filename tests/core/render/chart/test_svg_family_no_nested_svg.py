"""Tests: internal SVG-family chart renderers must not produce a nested <svg>.

The shared chart wrapper (<g class="dbt-chart">) already owns sizing, metadata,
and accessibility.  Internal renderers (kpi, spark_bar, table, callout) should
embed their content directly inside that wrapper without adding a redundant
standalone <svg> viewport. The same applies to the ChartDataError fallback
path, which wraps any chart's callout card via render_callout_svg.

Vega/Vega-Lite charts are the only case that legitimately remains as a nested
<svg> — they arrive as opaque scenegraphs and are not rewritten.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import TypeAdapter

from dbt_charts.core.compile.config import (
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.chart.rendering import render_chart_item

_DUMMY_QUERY = SqlQuery(sql="SELECT 1", source="test")
_RESOLVED_STYLE = resolve_style(get_theme_style())
_CHART_CTX = resolve_chart_style_context(get_theme_style())
_SVG_OPEN_TAG = re.compile(r"<svg\b")
_DBT_CHART_CLASS = re.compile(r'class="[^"]*\bdbt-chart\b[^"]*"')


def _executor(data: list[dict[str, Any]]) -> Executor:
    ex = MagicMock(spec=Executor)
    ex.execute_chart.return_value = data
    ex.execute_query.return_value = data
    return ex


def _chart(chart_type: str, **extra: Any) -> Chart:
    defaults: dict[str, Any] = {"id": f"test_{chart_type}", "type": chart_type}
    if chart_type != "callout":
        defaults["query"] = _DUMMY_QUERY
        defaults["query_name"] = "q"
    defaults.update(extra)
    return TypeAdapter(Chart).validate_python(dict(**defaults))


def _rc(chart: Chart, ctx=None):
    """Return a ResolvedChart for tests that go through render_chart_item."""
    return resolve(chart, [], chart_style_context=ctx or _CHART_CTX)


_INTERNAL_CASES = [
    ("kpi", {"value": "revenue"}, [{"revenue": 42000}], (300, 200)),
    (
        "spark_bar",
        {"x": "count", "y": "label"},
        [{"label": "A", "count": 10}],
        (300, 200),
    ),
    ("table", {}, [{"name": "Alice", "score": 95}], (400, 300)),
    ("callout", {"message": "Something went wrong"}, [], (300, 200)),
]


@pytest.mark.parametrize(("chart_type", "kwargs", "data", "size"), _INTERNAL_CASES)
def test_internal_renderer_has_no_nested_svg(
    chart_type: str,
    kwargs: dict[str, Any],
    data: list[dict[str, Any]],
    size: tuple[int, int],
) -> None:
    """KPI, spark_bar, table, callout: no redundant nested <svg>, wrapper present."""
    chart = _chart(chart_type, **kwargs)
    width, height = size
    svg, _ = render_chart_item(
        _rc(chart),
        _executor(data),
        {},
        width,
        height,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )
    assert not _SVG_OPEN_TAG.search(svg)
    assert _DBT_CHART_CLASS.search(svg)
    assert f'data-chart-width="{width}' in svg


def test_callout_wrapper_carries_callout_class_and_id() -> None:
    chart = _chart("callout", message="Something went wrong")
    svg, _ = render_chart_item(
        _rc(chart),
        _executor([]),
        {},
        300,
        200,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )
    assert "dbt-chart-callout" in svg
    assert 'id="chart-test_callout"' in svg


# ---------------------------------------------------------------------------
# ChartDataError fallback on a Vega-family chart — no nested <svg>
#
# Regression: the unwrap decision can't key off the chart type alone, because
# render_callout_svg is also invoked from the except ChartDataError path
# in render_chart_item, where `chart` is still the original (e.g. "line")
# chart. Without handling that, a bar/line chart that fails data validation
# would render a nested callout <svg> inside <g class="dbt-chart">.
# ---------------------------------------------------------------------------


def test_vega_chart_data_error_fallback_has_no_nested_svg() -> None:
    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    chart = _chart("line", x="x", y="y", title="Line chart that errors")
    executor = MagicMock(spec=Executor)
    executor.execute_query.side_effect = ChartDataError("no data", chart_id="test_line")
    svg, _ = render_chart_item(
        _rc(chart),
        executor,
        {},
        400,
        300,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )
    assert not _SVG_OPEN_TAG.search(svg)
    assert "dbt-chart-callout" in svg


# ---------------------------------------------------------------------------
# Vega positive control — Vega/Vega-Lite output MUST remain as a nested <svg>.
#
# Guards against an accidental broadening of the unwrap guard (e.g. dropping
# the Vega branch or flipping the condition). We mock render_chart_to_svg so
# the test doesn't require vl-convert in the test environment.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chart_type", ["line", "bar"])
def test_vega_chart_retains_standalone_nested_svg(chart_type: str) -> None:
    from dbt_charts.core.render.converters import chart as converters_chart

    chart = _chart(chart_type, x="x", y="y")
    canned_vega_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300" '
        'viewBox="0 0 400 300"><g class="mark-rect role-mark">'
        '<rect x="0" y="0" width="40" height="30"/></g></svg>'
    )
    with patch.object(
        converters_chart,
        "render_vega_spec",
        return_value=canned_vega_svg,
    ):
        svg, _ = render_chart_item(
            _rc(chart),
            _executor([{"x": 1, "y": 2}]),
            {},
            400,
            300,
            resolved_style=_RESOLVED_STYLE,
            render_cache={},
        )
    assert _SVG_OPEN_TAG.search(svg)
    assert _DBT_CHART_CLASS.search(svg)
    assert 'class="mark-rect role-mark"' in svg


# ---------------------------------------------------------------------------
# PAINTS_MARKS parity — must equal exactly what get_emitter() accepts.
#
# `_wrap_rendered_chart_svg`'s `is_internal_svg` predicate above reads
# `chart_type not in PAINTS_MARKS`: a closed positive list. A chart family
# registered with get_emitter() but missing from PAINTS_MARKS is silently
# treated as non-painting, and its standalone Vega <svg> wrapper (carrying
# vl-convert's width/height/viewBox) gets stripped. This test pins the two
# registries together so that mistake fails loudly instead of rendering
# marks with no viewport.
# ---------------------------------------------------------------------------


def test_paints_marks_matches_get_emitter_registry() -> None:
    """PAINTS_MARKS must equal exactly the chart_type strings get_emitter()
    dispatches an emitter for.

    Builds an unvalidated (`model_construct`) instance of every member of the
    `ResolvedChart` discriminated union — enough for get_emitter()'s
    isinstance-based `match` to classify it — and compares the chart_type
    literals of the accepted members against PAINTS_MARKS. A new VL family
    registered in get_emitter() without a matching PAINTS_MARKS update fails
    here first, not silently at render time.
    """
    import typing

    from dbt_charts.core.compile.models.chart.normalized import PAINTS_MARKS
    from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
    from dbt_charts.core.render.chart.emitters import get_emitter
    from dbt_charts.core.render.errors import RenderError

    member_classes = typing.get_args(typing.get_args(ResolvedChart)[0])

    emitter_chart_types: set[str] = set()
    for cls in member_classes:
        try:
            get_emitter(cls.model_construct())
        except RenderError:
            continue
        emitter_chart_types.update(
            typing.get_args(cls.model_fields["chart_type"].annotation)
        )

    assert emitter_chart_types == PAINTS_MARKS, (
        "PAINTS_MARKS is out of sync with get_emitter()'s registry — add the "
        "new family's chart_type string(s) to PAINTS_MARKS in "
        "compile/models/chart/normalized/_base.py"
    )


# ---------------------------------------------------------------------------
# Accessibility: aria-label, no native-tooltip <title> on wrappers
# ---------------------------------------------------------------------------


def test_kpi_render_chart_item_uses_aria_label_not_title() -> None:
    chart = _chart("kpi", value="revenue", label="Total Revenue")
    svg, _ = render_chart_item(
        _rc(chart),
        _executor([{"revenue": 42000}]),
        {},
        300,
        200,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )
    # aria-label on the wrapper <g> carries the accessible label.
    # The wrapper must NOT emit a <title> as its first child (triggers browser native tooltip).
    assert 'aria-label="Total Revenue"' in svg
    assert 'aria-label="Total Revenue"><title>' not in svg


def test_chart_notes_not_in_aria_label() -> None:
    """Note must not appear in aria-label on the chart wrapper <g>.

    Browsers (Firefox) render aria-label on SVG <g> elements as a native hover
    tooltip. The accessible label carries only the chart title; the note is
    stored solely in data-chart-notes for programmatic access.
    """
    chart = _chart("kpi", value="revenue", label="Revenue", notes="some text")
    svg, _ = render_chart_item(
        _rc(chart),
        _executor([{"revenue": 42000}]),
        {},
        300,
        200,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )
    import re

    # aria-label carries the title only — no notes.
    assert 'aria-label="Revenue"' in svg
    aria_labels = re.findall(r'aria-label="([^"]*)"', svg)
    assert not any("some text" in lbl for lbl in aria_labels), (
        f"Note leaked into aria-label: {[lbl for lbl in aria_labels if 'some text' in lbl]}"
    )
    # No <title> tooltip either.
    assert "<title>Revenue — some text</title>" not in svg
    assert "<title>some text</title>" not in svg


# ---------------------------------------------------------------------------
# render_layout_item with notes: no <title>, has data-layout-notes
# and aria-label on the wrapper.
# ---------------------------------------------------------------------------


def test_render_layout_item_notes_no_tooltip() -> None:
    """Layout-item notes must be stored in data-layout-notes only.

    aria-label on a bare SVG <g> causes Firefox to render a native hover tooltip.
    The layout-item wrapper must NOT carry aria-label or <title> when notes
    is set — those both trigger the browser tooltip. data-layout-notes is
    the right carrier for programmatic access.
    """
    import re

    from dbt_charts.core.compile.models.board.resolved import ResolvedLayoutItem
    from dbt_charts.core.render.chart.rendering import render_layout_item

    chart = _chart("kpi", value="revenue", label="Revenue")
    rc = _rc(chart)
    item = ResolvedLayoutItem(
        type="chart",
        chart=rc,
        board=None,
        x=0.0,
        y=0.0,
        width=300.0,
        height=200.0,
        notes="Quarterly revenue KPI",
    )
    svg, _ = render_layout_item(
        item,
        _executor([{"revenue": 42000}]),
        {},
        card_gap=8.0,
        available_width=300.0,
        available_height=200.0,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
        painted_canvas=None,
    )
    assert 'data-layout-notes="Quarterly revenue KPI"' in svg
    # No tooltip sources on the layout-item wrapper.
    assert "<title>Quarterly revenue KPI</title>" not in svg
    aria_labels = re.findall(r'aria-label="([^"]*)"', svg)
    assert not any("Quarterly revenue KPI" in lbl for lbl in aria_labels), (
        f"Layout item notes leaked into aria-label: "
        f"{[lbl for lbl in aria_labels if 'Quarterly revenue KPI' in lbl]}"
    )


# ---------------------------------------------------------------------------
# KPI and table inner <text><title> elements — no native tooltip triggers
# ---------------------------------------------------------------------------


def test_kpi_title_block_no_inner_title_when_not_truncated() -> None:
    """Short KPI title that fits without truncation must not emit an inner <title>."""
    chart = _chart("kpi", value="revenue", label="Total Revenue")
    svg, _ = render_chart_item(
        _rc(chart),
        _executor([{"revenue": 42000}]),
        {},
        300,
        200,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )
    assert "<title>Total Revenue</title>" not in svg


def test_kpi_title_block_emits_inner_title_when_truncated() -> None:
    """Long KPI title truncated to '…' must emit <title> with the full text so
    users can hover to see the complete title."""
    long_title = "Total Revenue for Enterprise Segment by Partner Region Q4 2025"
    chart = _chart("kpi", value="revenue", label=long_title)
    svg, _ = render_chart_item(
        _rc(chart),
        _executor([{"revenue": 42000}]),
        {},
        120,
        200,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )
    assert "…" in svg, "title should be truncated at this width"
    assert f"<title>{long_title}</title>" in svg


def test_table_column_header_has_no_inner_title_element() -> None:
    """Table column header <text> elements must not contain <title> children.

    Column headers render via <tspan> children (single or wrapped lines).
    The full display_name is always shown — no '...' truncation — so <title>
    is redundant and triggers the native browser tooltip.
    """
    chart = _chart("table", title="")
    svg, _ = render_chart_item(
        _rc(chart),
        _executor([{"name": "Alice", "score": 95}]),
        {},
        400,
        300,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )
    # Both single-line and multi-line header paths must be free of inner <title>.
    assert "<title>Name</title>" not in svg
    assert "<title>Score</title>" not in svg


def test_table_title_block_no_inner_title_when_not_truncated() -> None:
    """Short table title that fits without truncation must not emit an inner <title>."""
    chart = _chart("table", title="Sales Data")
    svg, _ = render_chart_item(
        _rc(chart),
        _executor([{"name": "Alice", "score": 95}]),
        {},
        400,
        300,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )
    assert "<title>Sales Data</title>" not in svg


def test_table_title_block_emits_inner_title_when_truncated() -> None:
    """Long table title truncated to '…' must emit <title> with the full text."""
    long_title = (
        "Revenue performance by enterprise segment and partner region "
        "covering all global accounts in fiscal quarter four of 2025"
    )
    chart = _chart("table", title=long_title)
    svg, _ = render_chart_item(
        _rc(chart),
        _executor([{"name": "Alice", "score": 95}]),
        {},
        220,
        300,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )
    assert "…" in svg, "title should be truncated at this width"
    assert f"<title>{long_title}</title>" in svg


def _clip_resolved_style() -> tuple[ResolvedStyle, Any]:
    """ResolvedStyle + ChartStyleContext with title.overflow=clip for regression tests."""
    new_title = _RESOLVED_STYLE.chart_defaults.title.model_copy(
        update={"overflow": "clip"}
    )
    new_chart_defaults = dataclasses.replace(
        _RESOLVED_STYLE.chart_defaults, title=new_title
    )
    clip_rs = dataclasses.replace(_RESOLVED_STYLE, chart_defaults=new_chart_defaults)
    clip_ctx = dataclasses.replace(_CHART_CTX, title=new_title)
    return clip_rs, clip_ctx


def test_kpi_title_block_clip_mode_emits_inner_title_when_clipped() -> None:
    """KPI with title.overflow=clip must emit inner <title> when text is clipped
    (no ellipsis emitted, but text IS shortened — the old '…' sniff misses this)."""
    long_title = "Total Revenue for Enterprise Segment by Partner Region Q4 2025"
    chart = _chart("kpi", value="revenue", label=long_title)
    clip_rs, clip_ctx = _clip_resolved_style()
    svg, _ = render_chart_item(
        _rc(chart, clip_ctx),
        _executor([{"revenue": 42000}]),
        {},
        120,
        200,
        resolved_style=clip_rs,
        render_cache={},
    )
    assert "…" not in svg, "clip mode must not emit ellipsis"
    # Clipped text is shorter than the original title
    assert long_title not in svg or f"<title>{long_title}</title>" in svg, (
        "full title text should only appear inside inner <title>, not as visible text"
    )
    assert f"<title>{long_title}</title>" in svg, (
        "clip mode must emit inner <title> so users can hover to read full title"
    )


def test_table_title_block_clip_mode_emits_inner_title_when_clipped() -> None:
    """Table with title.overflow=clip must emit inner <title> when text is clipped
    (no ellipsis emitted, but text IS shortened — the old '…' sniff misses this)."""
    long_title = "Revenue performance by enterprise segment and partner region Q4 2025"
    chart = _chart("table", title=long_title)
    clip_rs, clip_ctx = _clip_resolved_style()
    svg, _ = render_chart_item(
        _rc(chart, clip_ctx),
        _executor([{"name": "Alice", "score": 95}]),
        {},
        220,
        300,
        resolved_style=clip_rs,
        render_cache={},
    )
    assert "…" not in svg, "clip mode must not emit ellipsis"
    assert f"<title>{long_title}</title>" in svg, (
        "clip mode must emit inner <title> so users can hover to read full title"
    )


def test_kpi_render_chart_item_height_still_matches_svg_content() -> None:
    chart = _chart("kpi", value="revenue")
    svg, height = render_chart_item(
        _rc(chart),
        _executor([{"revenue": 42000}]),
        {},
        300,
        200,
        resolved_style=_RESOLVED_STYLE,
        render_cache={},
    )
    assert height > 0
    assert 'data-chart-height="' in svg
