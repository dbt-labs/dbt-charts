"""Shared-scale magnitude handling on the support_table strip.

A strip row declares its unit once, on the first cell it actually draws, and
leaves the rest bare — the ANCHOR half of ``core/text/numeral_scale.py``, which
until now only the vertical ruler used.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style

# --- Decimal-point alignment on a plain trim-`f` column ---


# --- CRITICAL 1: format provenance gate ---


# --- Fix 1: raw d3 specs now anchor (provenance gate removed) ---


# --- Fix 2: predefined-name formats now anchor on left-oriented axes too ---
# (orientation gate removed; provenance gate -- predefined names only -- kept)


# --- Fix 3: tier_distance refusal parity with table ---


# --- SVG integration: verify the unit fires in the actual rendered output ---


def test_raw_spec_does_not_paint_anchored_unit_in_svg():
    """A raw d3 spec must NOT anchor in the actual SVG output -- repeats per-cell.

    End-to-end: compile -> resolve -> render -> vl_convert -> SVG text.
    Skipped when vl_convert is not installed.
    """
    pytest.importorskip("vl_convert")

    from pydantic import TypeAdapter

    from dbt_charts.core.compile.config import get_theme_style, reset_config
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )
    from dbt_charts.core.render.chart.vega_lite import render_chart

    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "test_raw_spec_svg",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"source": "revenue", "format": "$,.3s", "label": "Rev"}]
            },
            "style": {"orientation": "vertical"},
        }
    )
    data = [
        {"month": "Jan", "revenue": 441e6},
        {"month": "Feb", "revenue": 448e6},
        {"month": "Mar", "revenue": 456e6},
    ]
    svg = render_chart(
        chart,
        resolve_style(get_theme_style()),
        resolve_chart_style_context(get_theme_style()),
        data,
        format="svg",
        width=600,
        height=320,
    )
    reset_config()
    # A raw spec never anchors: d3 picks its own SI tier per cell ("M"), and
    # the house "mn" narrative suffix -- only ever emitted by the anchor path
    # -- must not appear anywhere.
    assert "mn" not in svg, (
        "a raw d3 spec must not anchor; 'mn' should never appear for a raw literal"
    )


def _render_share_strip(
    fmt: str, shares: list[float], extra_entries: list[dict] | None = None
) -> str:
    pytest.importorskip("vl_convert")
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )
    from dbt_charts.core.render.chart.vega_lite import render_chart

    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "hang_suffix",
            "type": "bar",
            "x": "month",
            "y": "share",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [*(extra_entries or []), {"source": "share", "format": fmt}]
            },
            "style": {"orientation": "vertical"},
        }
    )
    data = [
        {"month": f"m{i:02d}", "share": v, "big": 1000.0 + i}
        for i, v in enumerate(shares)
    ]
    svg = render_chart(
        chart,
        resolve_style(get_theme_style()),
        resolve_chart_style_context(get_theme_style()),
        data,
        format="svg",
        width=600,
        height=320,
    )
    reset_config()
    return svg


def test_cells_wider_than_their_band_record_an_overflow():
    """27 bands in 600px leave ~22px each; "142" plus padding needs far more."""
    from dbt_charts.core.render.chart.text_truncation import collect_text_truncations

    with collect_text_truncations() as sink:
        _render_share_strip("percent_whole", [1.42] * 27)
    assert [t.surface for t in sink["hang_suffix"]] == ["support_table_cell_overflow"]


def test_cells_that_fit_their_band_record_nothing():
    from dbt_charts.core.render.chart.text_truncation import collect_text_truncations

    with collect_text_truncations() as sink:
        _render_share_strip("percent_whole", [0.03, 0.05, 0.07])
    surfaces = {t.surface for records in sink.values() for t in records}
    assert "support_table_cell_overflow" not in surfaces


def _render_per_series_label(orientation: str, stack: str | None) -> str:
    pytest.importorskip("vl_convert")
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )
    from dbt_charts.core.render.chart.vega_lite import render_chart

    reset_config()
    style: dict = {"orientation": orientation}
    if stack:
        style["stack"] = stack
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "ps_label",
            "type": "bar",
            "x": "month",
            "y": "share",
            "color": "seg",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"per_series": "share", "label": "Mine"}]},
            "style": style,
        }
    )
    data = [
        {"month": m, "seg": s, "share": 0.1}
        for m in ("a", "b", "c")
        for s in ("x", "y")
    ]
    try:
        return render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="svg",
            width=600,
            height=320,
        )
    finally:
        reset_config()


@pytest.mark.parametrize(
    ("orientation", "stack"),
    [("vertical", None), ("vertical", "zero"), ("horizontal", "zero")],
)
def test_per_series_label_is_rejected_where_series_name_the_rows(orientation, stack):
    """A strip row or stacked-bar column is named by its series; a label is ignored."""
    from dbt_charts.core.compile.errors import CompilationError

    with pytest.raises(CompilationError, match=r"per_series: 'share'.*label") as exc:
        _render_per_series_label(orientation, stack)
    assert exc.value.field_path == "charts.ps_label.support_table.entries[0].label"


def test_per_series_label_still_names_a_grouped_bar_column():
    assert "Mine" in _render_per_series_label("horizontal", None)


def test_a_hung_suffix_that_collides_with_the_next_cell_records_an_overflow():
    """22 bands leave ~27px: the bare digits fit, the anchor's hung "%" does not."""
    from dbt_charts.core.render.chart.text_truncation import collect_text_truncations

    with collect_text_truncations() as sink:
        _render_share_strip("percent_whole", [0.03] * 22)
    assert [t.surface for t in sink["hang_suffix"]] == ["support_table_cell_overflow"]


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_by_measure_label_is_never_rejected(orientation):
    """by_measure names its single row, so the label is always read."""
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
        resolve_style,
    )
    from dbt_charts.core.render.chart.vega_lite import render_chart

    reset_config()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "bm_label",
            "type": "bar",
            "x": "month",
            "y": "share",
            "color": "seg",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [
                    {"per_series": "share", "by_measure": True, "label": "Mine"}
                ]
            },
            "style": {"orientation": orientation, "stack": "zero"},
        }
    )
    data = [{"month": m, "seg": "x", "share": 0.1} for m in ("a", "b")]
    try:
        render_chart(
            chart,
            resolve_style(get_theme_style()),
            resolve_chart_style_context(get_theme_style()),
            data,
            format="svg",
            width=600,
            height=320,
        )
    finally:
        reset_config()


def test_a_wide_row_and_a_hung_suffix_in_different_rows_do_not_add_up():
    """The widest bare cell (an integer row) and the "%" belong to different
    rows; each cell fits the ~46px band, so nothing collides and nothing warns.
    Summing the two maxima would demand ~54px.
    """
    from dbt_charts.core.render.chart.text_truncation import collect_text_truncations

    with collect_text_truncations() as sink:
        _render_share_strip(
            "percent_whole",
            [0.03] * 13,
            extra_entries=[{"source": "big", "format": ",d"}],
        )
    surfaces = {t.surface for records in sink.values() for t in records}
    assert "support_table_cell_overflow" not in surfaces
