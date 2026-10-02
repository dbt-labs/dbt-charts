"""Tests for the support_table VALUE-COLUMN emission path (vertical category axis).

Covers the placement layer that is new for this geometry (position
resolution/validation, column edges, header layer, labelPadding widening) —
everything below it (entry union, format resolution, strip_numerals_for_values,
per_series expansion, aggregate transforms, the data-aware validator) is shared
verbatim with the row-strip path and already covered by
test_support_table_attachment.py.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedSupportTable,
    ResolvedSupportTableAggregate,
    ResolvedSupportTablePerSeries,
    ResolvedSupportTableSource,
)
from dbt_charts.core.render.chart.support_table_attachment import (
    StripAnchor,
    StripNumerals,
    _column_edges,
    _entry_column_widths,
    _visual_column_widths_and_headers,
    attach_support_table_columns,
    plain_numerals,
)
from dbt_charts.core.render.errors import RenderError


def _dt_style(**overrides):
    return get_theme_style().charts.support_table.model_copy(update=overrides)


def _table(entries):
    return ResolvedSupportTable.model_validate({"entries": entries})


def _hbar_spec(width=400, height=200):
    """Minimal horizontal-bar-shaped spec: category on y, measure on x."""
    return {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "revenue", "type": "quantitative"},
            "y": {"field": "region", "type": "nominal"},
        },
        "width": width,
        "height": height,
        "padding": {"left": 0, "right": 0, "top": 0, "bottom": 0},
    }


# Position resolution/validation moved to compile.support_table.resolve_support_table_position
# — see tests/core/compile/test_support_table.py. It runs at resolve time now,
# not render time, so every caller here already receives a concrete position.


# =============================================================================
# COLUMN EDGES (authored order, left to right, on both sides)
# =============================================================================


def test_column_edges_left_places_last_column_right_edge_at_plot_edge():
    # No gutter (the default): the raw geometry function's own behavior.
    edges = _column_edges([30.0, 50.0], "left", spec_width=400.0, plot_gutter=0.0)
    assert edges[-1][1] == pytest.approx(0.0)
    assert edges[0][0] == pytest.approx(-80.0)
    # authored order left to right on screen: column 0 is leftmost.
    assert edges[0][1] <= edges[1][0]


def test_column_edges_right_places_first_column_left_edge_at_plot_edge():
    edges = _column_edges([30.0, 50.0], "right", spec_width=400.0, plot_gutter=0.0)
    assert edges[0][0] == pytest.approx(400.0)
    assert edges[-1][1] == pytest.approx(480.0)
    assert edges[0][1] <= edges[1][0]


def test_column_edges_left_plot_gutter_pushes_the_whole_block_away_from_the_plot():
    # Defect 1: on the left side the last column's right edge is the plot-facing
    # edge, and right-aligned text touches it with nothing reserved unless an
    # explicit gutter shifts the whole block.
    edges = _column_edges([30.0, 50.0], "left", spec_width=400.0, plot_gutter=6.0)
    assert edges[-1][1] == pytest.approx(-6.0)
    assert edges[0][0] == pytest.approx(-86.0)
    assert edges[0][1] <= edges[1][0]


def test_column_edges_right_plot_gutter_pushes_the_whole_block_away_from_the_plot():
    # position="right" gets the identical explicit plot-facing gutter as
    # "left" -- the raw geometry function is symmetric either way.
    edges = _column_edges([30.0, 50.0], "right", spec_width=400.0, plot_gutter=6.0)
    assert edges[0][0] == pytest.approx(406.0)
    assert edges[-1][1] == pytest.approx(486.0)


# =============================================================================
# COLUMN EMISSION
# =============================================================================


def test_attach_columns_source_entry_emits_cell_and_header():
    spec = _hbar_spec()
    table = _table([ResolvedSupportTableSource(source="revenue", format="$.2s")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=_dt_style(position="left"),
        entry_numerals=numerals,
        value_formats=[e.format for e in table.entries],
        column_widths=[60.0],
        column_headers=["Revenue"],
        category_field="region",
        color_field=None,
        series_order=None,
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",
    )
    text_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "text"
    ]
    assert len(text_layers) == 2  # one cell + one header
    cell = next(t for t in text_layers if t["encoding"]["text"]["field"] != "__header")
    header = next(
        t for t in text_layers if t["encoding"]["text"]["field"] == "__header"
    )
    # Cell shares the category channel on y, pixel-literal x.
    assert cell["encoding"]["y"]["field"] == "region"
    assert cell["encoding"]["y"]["bandPosition"] == 0.5
    assert "value" in cell["encoding"]["x"]
    # Header shares the same right edge (x) as its value cell.
    assert header["encoding"]["x"]["value"] == cell["encoding"]["x"]["value"]
    assert header["data"]["values"][0]["__header"] == "Revenue"
    assert out["autosize"] == {"type": "pad", "contains": "padding"}


def test_attach_columns_aggregate_entry_emits_groupby_on_category_field():
    spec = _hbar_spec()
    table = _table([ResolvedSupportTableAggregate(aggregate="sum", source="revenue")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=_dt_style(position="left"),
        entry_numerals=numerals,
        value_formats=[None],
        column_widths=[60.0],
        column_headers=["Revenue"],
        category_field="region",
        color_field=None,
        series_order=None,
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",
    )
    cell = next(
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer["encoding"]["text"]["field"] != "__header"
    )
    agg_transform = next(t for t in cell["transform"] if "aggregate" in t)
    assert agg_transform["groupby"] == ["region"]


def test_attach_columns_per_series_expands_one_column_per_series():
    spec = _hbar_spec()
    spec["encoding"]["color"] = {"field": "product", "type": "nominal"}
    table = _table([ResolvedSupportTablePerSeries(per_series="revenue")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=_dt_style(position="left"),
        entry_numerals=numerals,
        value_formats=[None],
        column_widths=[40.0, 40.0],
        column_headers=["A", "B"],
        category_field="region",
        color_field="product",
        series_order=["A", "B"],
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",
    )
    text_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "text"
    ]
    headers = [
        t["data"]["values"][0]["__header"]
        for t in text_layers
        if t["encoding"]["text"]["field"] == "__header"
    ]
    assert headers == ["A", "B"]
    cells = [t for t in text_layers if t["encoding"]["text"]["field"] != "__header"]
    assert len(cells) == 2
    # Each series cell filters to its own color value.
    filter_exprs = [
        t["filter"] for cell in cells for t in cell["transform"] if "filter" in t
    ]
    assert any("'A'" in f for f in filter_exprs)
    assert any("'B'" in f for f in filter_exprs)


def test_attach_columns_per_series_without_series_order_raises():
    spec = _hbar_spec()
    table = _table([ResolvedSupportTablePerSeries(per_series="revenue")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    with pytest.raises(RenderError, match=r"(?i)series_order"):
        attach_support_table_columns(
            spec,
            support_table=table,
            style=_dt_style(position="left"),
            entry_numerals=numerals,
            value_formats=[None],
            column_widths=[],
            column_headers=[],
            category_field="region",
            color_field="product",
            series_order=None,
            dark_fills=None,
            header_fills=None,
            axis_y_orient="left",
        )


# =============================================================================
# GROUPED BAR PER_SERIES: ONE COLUMN, EACH CELL ON ITS OWN SUB-BAND
# =============================================================================


def test_attach_columns_grouped_per_series_stays_a_single_column():
    """A grouped bar (no stacking) lays every series out on its own sub-band
    within the category band, so a per_series entry mirrors that geometry:
    one column holding every series' cell, not one column per series.
    """
    spec = _hbar_spec()
    spec["encoding"]["color"] = {"field": "product", "type": "nominal"}
    spec["encoding"]["yOffset"] = {"field": "product", "type": "nominal"}
    table = _table(
        [ResolvedSupportTablePerSeries(per_series="revenue", label="Revenue")]
    )
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=_dt_style(position="left"),
        entry_numerals=numerals,
        value_formats=[None],
        column_widths=[40.0],
        column_headers=["Revenue"],
        category_field="region",
        color_field="product",
        series_order=["A", "B"],
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",
        single_column_per_series=True,
    )
    text_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "text"
    ]
    headers = [
        t["data"]["values"][0]["__header"]
        for t in text_layers
        if t["encoding"]["text"]["field"] == "__header"
    ]
    # One header for the whole entry, not one per series.
    assert headers == ["Revenue"]
    cells = [t for t in text_layers if t["encoding"]["text"]["field"] != "__header"]
    assert len(cells) == 2
    # Both cells share the same pixel-literal x -- one column.
    assert cells[0]["encoding"]["x"] == cells[1]["encoding"]["x"]
    # Each series cell still filters to its own color value.
    filter_exprs = [
        t["filter"] for cell in cells for t in cell["transform"] if "filter" in t
    ]
    assert any("'A'" in f for f in filter_exprs)
    assert any("'B'" in f for f in filter_exprs)


def test_attach_columns_grouped_per_series_cells_land_on_their_own_sub_band():
    """Each cell must pick up the parent's own yOffset channel (copied
    verbatim) so it lands on that series' sub-band -- vertically aligned with
    the bar it describes -- instead of the category band center a stacked
    bar's cells share.
    """
    spec = _hbar_spec()
    spec["encoding"]["color"] = {"field": "product", "type": "nominal"}
    spec["encoding"]["yOffset"] = {"field": "product", "type": "nominal"}
    table = _table([ResolvedSupportTablePerSeries(per_series="revenue")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=_dt_style(position="left"),
        entry_numerals=numerals,
        value_formats=[None],
        column_widths=[40.0],
        column_headers=["Revenue"],
        category_field="region",
        color_field="product",
        series_order=["A", "B"],
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",
        single_column_per_series=True,
    )
    cells = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer["encoding"]["text"]["field"] != "__header"
    ]
    assert len(cells) == 2
    for cell in cells:
        assert cell["encoding"]["yOffset"] == {"field": "product", "type": "nominal"}
        # Category band center still shared, same as any column cell.
        assert cell["encoding"]["y"]["field"] == "region"


def test_attach_columns_grouped_per_series_without_parent_offset_omits_it():
    """A color field the bar chose not to offset (e.g. 1:1 with x) carries no
    yOffset on the parent spec either -- the cells then fall back to the
    category band center, matching the bar's own marks in that case.
    """
    spec = _hbar_spec()
    spec["encoding"]["color"] = {"field": "product", "type": "nominal"}
    table = _table([ResolvedSupportTablePerSeries(per_series="revenue")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=_dt_style(position="left"),
        entry_numerals=numerals,
        value_formats=[None],
        column_widths=[40.0],
        column_headers=["Revenue"],
        category_field="region",
        color_field="product",
        series_order=["A", "B"],
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",
        single_column_per_series=True,
    )
    cells = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer["encoding"]["text"]["field"] != "__header"
    ]
    assert len(cells) == 2
    for cell in cells:
        assert cell["encoding"]["yOffset"] is None


def test_visual_column_widths_and_headers_single_column_uses_entry_label():
    table = _table([ResolvedSupportTablePerSeries(per_series="revenue", label="Sales")])
    widths, headers = _visual_column_widths_and_headers(
        table, [40.0], series_order=["A", "B"], single_column_per_series=True
    )
    assert widths == [40.0]
    assert headers == ["Sales"]


def test_visual_column_widths_and_headers_single_column_defaults_to_title_cased_field():
    table = _table([ResolvedSupportTablePerSeries(per_series="unit_revenue")])
    widths, headers = _visual_column_widths_and_headers(
        table, [40.0], series_order=["A", "B"], single_column_per_series=True
    )
    assert headers == ["Unit Revenue"]


def test_visual_column_widths_and_headers_multi_column_still_names_each_series():
    table = _table([ResolvedSupportTablePerSeries(per_series="revenue")])
    widths, headers = _visual_column_widths_and_headers(
        table, [40.0], series_order=["A", "B"], single_column_per_series=False
    )
    assert widths == [40.0, 40.0]
    assert headers == ["A", "B"]


def test_attach_columns_same_side_as_labels_widens_label_padding():
    spec = _hbar_spec()
    spec["encoding"]["y"]["axis"] = {"labelPadding": 50.0}
    table = _table([ResolvedSupportTableSource(source="revenue")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    style = _dt_style(position="left")
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=style,
        entry_numerals=numerals,
        value_formats=[None],
        column_widths=[60.0],
        column_headers=["Revenue"],
        category_field="region",
        color_field=None,
        series_order=None,
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",  # same side as position=left
    )
    base_layer = out["layer"][0]
    # 50 (existing) + 60 (column width) + the plot-facing gutter (Defect 1):
    # the axis's own labels move aside by the block's full reserved footprint.
    expected = 50.0 + 60.0 + style.row.padding.horizontal
    assert base_layer["encoding"]["y"]["axis"]["labelPadding"] == pytest.approx(
        expected
    )


def test_attach_columns_on_a_layered_base_widens_the_layer_own_axis_too():
    """`chart.layers` leaves a duplicate category encoding on layer[0]
    (`render_cartesian_overlay`); Vega-Lite renders a layered view's axis
    from that per-layer encoding, not the shared top-level one, so the
    widening must land on both or the axis label gutter silently keeps its
    unwidened width on any layered chart (the support_table column block
    then collides with the category labels).
    """
    spec = _hbar_spec()
    spec["encoding"]["y"]["axis"] = {"labelPadding": 50.0}
    # Simulate render_cartesian_overlay's output shape: a base layer carrying
    # its OWN copy of the category encoding, distinct from the shared one.
    spec["layer"] = [
        {
            "mark": spec.pop("mark"),
            "encoding": {
                "x": spec["encoding"]["x"],
                "y": {**spec["encoding"]["y"], "axis": {"labelPadding": 50.0}},
            },
        },
        {"mark": {"type": "bar"}, "encoding": {"x": spec["encoding"]["x"]}},
    ]
    table = _table([ResolvedSupportTableSource(source="revenue")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    style = _dt_style(position="left")
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=style,
        entry_numerals=numerals,
        value_formats=[None],
        column_widths=[60.0],
        column_headers=["Revenue"],
        category_field="region",
        color_field=None,
        series_order=None,
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",  # same side as position=left
    )
    expected = 50.0 + 60.0 + style.row.padding.horizontal
    assert out["encoding"]["y"]["axis"]["labelPadding"] == pytest.approx(expected)
    assert out["layer"][0]["encoding"]["y"]["axis"]["labelPadding"] == pytest.approx(
        expected
    )
    # The overlay layer (no category axis of its own) is untouched.
    assert "axis" not in out["layer"][1].get("encoding", {}).get("y", {})


def test_attach_columns_opposite_side_from_labels_leaves_label_padding_alone():
    spec = _hbar_spec()
    spec["encoding"]["y"]["axis"] = {"labelPadding": 50.0}
    table = _table([ResolvedSupportTableSource(source="revenue")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=_dt_style(position="right"),
        entry_numerals=numerals,
        value_formats=[None],
        column_widths=[60.0],
        column_headers=["Revenue"],
        category_field="region",
        color_field=None,
        series_order=None,
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",  # opposite side from position=right
    )
    base_layer = out["layer"][0]
    assert base_layer["encoding"]["y"]["axis"]["labelPadding"] == pytest.approx(50.0)


def test_attach_columns_left_position_reserves_gutter_before_the_plot():
    # The last column's right edge (the plot-facing edge for position="left")
    # must not sit at x=0 -- the gutter is the column's own trailing padding
    # PLUS the axis's own label padding (composed from the two theme tokens,
    # not a hardcoded number), so a dense field of digits stands off the
    # marks by as much as a neighboring axis label would.
    spec = _hbar_spec()
    table = _table([ResolvedSupportTableSource(source="revenue")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    style = _dt_style(position="left")
    axis_label_padding = 8.0
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=style,
        entry_numerals=numerals,
        value_formats=[None],
        column_widths=[60.0],
        column_headers=["Revenue"],
        category_field="region",
        color_field=None,
        series_order=None,
        dark_fills=None,
        header_fills=None,
        axis_y_orient="right",  # opposite side from position=left: no labelPadding widening noise
        axis_label_padding=axis_label_padding,
    )
    cell = next(
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer["encoding"]["text"]["field"] != "__header"
    )
    assert cell["encoding"]["x"]["value"] == pytest.approx(
        -(style.row.padding.horizontal + axis_label_padding)
    )


def test_attach_columns_right_position_gets_the_same_plot_side_gutter():
    # The plot-facing edge for position="right" is column 0's own left edge —
    # it gets the identical composed gutter as position="left", not a
    # narrower one relying on incidental column-width slack.
    spec = _hbar_spec()
    table = _table([ResolvedSupportTableSource(source="revenue")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    style = _dt_style(position="right")
    axis_label_padding = 8.0
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=style,
        entry_numerals=numerals,
        value_formats=[None],
        column_widths=[60.0],
        column_headers=["Revenue"],
        category_field="region",
        color_field=None,
        series_order=None,
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",
        axis_label_padding=axis_label_padding,
    )
    cell = next(
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer["encoding"]["text"]["field"] != "__header"
    )
    # Right-aligned at the column's own right edge: spec_width (400) +
    # gutter (padding.horizontal + axis_label_padding) + width (60).
    expected = 400.0 + style.row.padding.horizontal + axis_label_padding + 60.0
    assert cell["encoding"]["x"]["value"] == pytest.approx(expected)


# =============================================================================
# PER_SERIES CELLS READ ACROSS ONE CATEGORY ROW, NOT DOWN A SUB-BAND STAIRCASE
# =============================================================================


def test_per_series_columns_share_category_band_center_not_sub_band_offset():
    # Defect 2 regression: a per_series COLUMN is the transpose of a per_series
    # ROW — every cell in it must sit at the category's own band center (the
    # same y the axis label reads at), not at that series' sub-band offset.
    # The parent spec's own grouped-bar yOffset must not leak into the cells.
    spec = _hbar_spec()
    spec["encoding"]["color"] = {"field": "product", "type": "nominal"}
    spec["encoding"]["yOffset"] = {"field": "product", "type": "nominal"}
    table = _table([ResolvedSupportTablePerSeries(per_series="revenue")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=_dt_style(position="left"),
        entry_numerals=numerals,
        value_formats=[None],
        column_widths=[40.0, 40.0],
        column_headers=["A", "B"],
        category_field="region",
        color_field="product",
        series_order=["A", "B"],
        dark_fills=None,
        header_fills=None,
        axis_y_orient="right",
    )
    cells = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer["encoding"]["text"]["field"] != "__header"
    ]
    assert len(cells) == 2
    # Every cell shares the exact same y-encoding — the category band center.
    for cell in cells:
        assert cell["encoding"]["y"] == {
            "field": "region",
            "type": "nominal",
            "bandPosition": 0.5,
        }
        # Explicit opt-out, the same technique already used for "color": None
        # elsewhere in this module — an absent key would still inherit the
        # parent's yOffset and stagger each series onto its own sub-band.
        assert cell["encoding"]["yOffset"] is None


def test_attach_columns_requires_explicit_width():
    spec = _hbar_spec()
    del spec["width"]
    table = _table([ResolvedSupportTableSource(source="revenue")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    with pytest.raises(RenderError, match=r"(?i)width"):
        attach_support_table_columns(
            spec,
            support_table=table,
            style=_dt_style(position="left"),
            entry_numerals=numerals,
            value_formats=[None],
            column_widths=[60.0],
            column_headers=["Revenue"],
            category_field="region",
            color_field=None,
            series_order=None,
            dark_fills=None,
            header_fills=None,
            axis_y_orient="left",
        )


# =============================================================================
# COLUMN WIDTH MEASUREMENT (no free gutter for the declare-once anchor)
# =============================================================================


def test_entry_column_widths_measures_the_anchor_not_the_bare_cell():
    """A column has no free gutter for the declare-once anchor to overhang into.

    The row strip deliberately sizes its band to the bare cell and lets a wider
    anchor ("$441mn" vs bare "441") overhang into the y-axis gutter — safe
    there because the anchor is always the first drawn cell (see
    test_entry_dx_measures_the_bare_cell_not_the_anchor in
    test_layout_sizing_support_table_height.py). A column has no such gutter:
    every column's cells are right-aligned, so any overhang runs left into the
    previous column's own digits. The column width must therefore accommodate
    whichever cell actually paints the anchor's affix.
    """
    from dbt_charts.core.font_measure import get_font_measurer

    table = _table([ResolvedSupportTableSource(source="goal", format="$,.3s")])
    declaring = StripNumerals(
        divisor=1e6,
        digit_spec=",.0~f",
        anchor=StripAnchor.by_drawn_index_data_order(),
        prefix="$",
        suffix="mn",
        suffix_is_magnitude=True,
    )
    style = _dt_style()
    # label.font.size is an InheritSlot only filled by the chart-local
    # resolve cascade — set it explicitly for this bare-style unit test.
    style = style.model_copy(
        update={
            "label": style.label.model_copy(
                update={"font": style.label.font.model_copy(update={"size": 11.0})}
            )
        }
    )
    values = [441_000_000.0]
    widths = _entry_column_widths(
        table,
        [values],
        style,
        [declaring],
        series_order=None,
        single_column_per_series=False,
    )

    font_size = style.font.size
    measurer = get_font_measurer(style.font.family, numeric=True)
    bare_w = measurer.measure(declaring.bare_text(values[0]), font_size)
    anchor_w = measurer.measure(declaring.anchor_text(values[0]), font_size)
    assert anchor_w > bare_w, "test fixture must exercise a wider decorated form"
    # The reserved value-width component (before header/padding) must be at
    # least the anchor's own painted width — never just the bare digits.
    assert widths[0] >= anchor_w + 2.0 * style.row.padding.horizontal - 1e-6


def test_entry_column_widths_measures_the_header_when_it_is_wider_than_the_value():
    """A column can never collide because it sizes its own column — the width
    is ``max(widest formatted value, header text) + row.padding.horizontal * 2``.

    A short-digit column under a long header (e.g. "Avg Order Revenue" over
    12/8) is the case the header side of that max exists for: drop it and
    the header's right-anchored glyphs run straight through the neighboring
    column. This pins the header side specifically, using values so narrow
    that the value side of the max cannot satisfy the assertion by itself.
    """
    from dbt_charts.core.font_measure import get_font_measurer

    label = "Avg Order Revenue"
    table = _table(
        [ResolvedSupportTableSource(source="revenue", format=",.0f", label=label)]
    )
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    style = _dt_style()
    style = style.model_copy(
        update={
            "label": style.label.model_copy(
                update={"font": style.label.font.model_copy(update={"size": 11.0})}
            )
        }
    )
    values = [12.0, 8.0]
    widths = _entry_column_widths(
        table,
        [values],
        style,
        numerals,
        series_order=None,
        single_column_per_series=False,
    )

    measurer = get_font_measurer(style.label.font.family, numeric=False)
    header_w = measurer.measure(label, style.label.font.size)
    value_measurer = get_font_measurer(style.font.family, numeric=True)
    value_w = max(value_measurer.measure(str(int(v)), style.font.size) for v in values)
    assert header_w > value_w, (
        "test fixture must exercise a header wider than any value"
    )
    assert widths[0] >= header_w + 2.0 * style.row.padding.horizontal - 1e-6, (
        f"column width {widths[0]} does not account for the header's own "
        f"measured width ({header_w}) — a column headed {label!r} would let "
        "its header glyphs overprint the neighboring column"
    )


# =============================================================================
# UNIFORM INK (style.support_table.font.color wins over per-series companion ink)
#
# The decision of *whether* to compute dark_fills at all lives one layer up,
# in _apply_support_table_columns_post_pass, which is the only place that can
# compare the chart's effective_support_table_style against the board-wide
# default to tell an authored override apart from the theme's own baseline —
# see test_pipeline_column_uniform_ink_* in test_chart_support_table_pipeline.py.
# attach_support_table_columns itself just honors whatever dark_fills its
# caller passes (None or not) — this section pins that this layer never grows
# its own opinion about it.
# =============================================================================


def test_per_series_column_cells_use_dark_fills_when_font_color_unset():
    spec = _hbar_spec()
    spec["encoding"]["color"] = {"field": "product", "type": "nominal"}
    table = _table([ResolvedSupportTablePerSeries(per_series="revenue")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=_dt_style(position="left"),
        entry_numerals=numerals,
        value_formats=[None],
        column_widths=[40.0, 40.0],
        column_headers=["A", "B"],
        category_field="region",
        color_field="product",
        series_order=["A", "B"],
        dark_fills=["#aaaaaa", "#bbbbbb"],
        header_fills=None,
        axis_y_orient="left",
    )
    cells = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer["encoding"]["text"]["field"] != "__header"
    ]
    assert len(cells) == 2
    fills = [c["mark"]["fill"] for c in cells]
    assert fills == ["#aaaaaa", "#bbbbbb"]


# =============================================================================
# COLUMN RULE (style.support_table.row.rule as a vertical divider between columns)
# =============================================================================


def test_attach_columns_emits_vertical_rule_between_columns_when_width_positive():
    """row.rule becomes a vertical rule between columns — the transpose of the
    row strip's horizontal inter-row rule. This is what lets a Total column
    read as ruled off, the way a table's totals row does.
    """
    spec = _hbar_spec()
    table = _table(
        [
            ResolvedSupportTableSource(source="revenue", label="Revenue"),
            ResolvedSupportTableAggregate(
                aggregate="sum", source="revenue", label="Total"
            ),
        ]
    )
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    style = _dt_style(
        position="left",
        row=_dt_style().row.model_copy(
            update={
                "rule": _dt_style().row.rule.model_copy(
                    update={"width": 1.0, "color": "#888888"}
                )
            }
        ),
    )
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=style,
        entry_numerals=numerals,
        value_formats=[e.format for e in table.entries],
        column_widths=[50.0, 50.0],
        column_headers=["Revenue", "Total"],
        category_field="region",
        color_field=None,
        series_order=None,
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",
    )
    rule_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    ]
    # One rule between the two columns — never on the outer edges.
    assert len(rule_layers) == 1
    rule = rule_layers[0]
    assert rule["mark"]["strokeWidth"] == 1.0
    assert rule["mark"]["stroke"] == "#888888"
    # Vertical: pixel-literal x, y explicitly nulled — spans the plot's full
    # height, the transpose of the row rule's pixel-literal y with x nulled.
    assert "value" in rule["encoding"]["x"]
    assert rule["encoding"].get("y") is None
    assert "y" in rule["encoding"]
    # Sits past the raw boundary between the two columns, in the gutter —
    # never ON the boundary itself, which is where column 0's own
    # right-aligned text sits (see
    # test_attach_columns_rule_never_overprints_neighboring_column_text for
    # why: the boundary is exactly where column 0's rightmost glyph paints).
    # position="left": plot_gutter = row.padding.horizontal (6.0) shifts the
    # whole block left of the plot edge, so the raw boundary sits at
    # -(50 + 50 + 6) + 50 = -56.0, not the columns' own raw -50.0.
    boundary = -56.0
    assert (
        boundary
        < rule["encoding"]["x"]["value"]
        <= boundary + style.row.padding.horizontal
    )


@pytest.mark.parametrize("position", ["left", "right"])
def test_attach_columns_rule_never_overprints_neighboring_column_text(position):
    """Regression: the rule used to land exactly at the column's own right
    edge -- the same pixel its text right-aligns to -- so it painted
    straight through the rightmost glyph of the column to its left.

    A column's text has zero slack on the RIGHT of its own column (it right-
    aligns flush to the column's own right edge) and at least one
    row.padding.horizontal of slack on the LEFT (the column reserves 2x
    padding, per _entry_column_widths, but all of it lands on the left, per
    _column_cell_layer's x-anchor). So the only pixel range that clears both
    neighbors' painted glyphs is (this_column_right, this_column_right +
    row.padding.horizontal] -- verified here against the actual measured
    text of both columns, not the raw column boundary.
    """
    from dbt_charts.core.font_measure import get_font_measurer

    spec = _hbar_spec()
    style = _dt_style(
        position=position,
        row=_dt_style().row.model_copy(
            update={"rule": _dt_style().row.rule.model_copy(update={"width": 1.0})}
        ),
    )
    # label.font.size is an InheritSlot only filled by the chart-local
    # resolve cascade — set it explicitly for this bare-style unit test
    # (_entry_column_widths measures header text against it).
    style = style.model_copy(
        update={
            "label": style.label.model_copy(
                update={"font": style.label.font.model_copy(update={"size": 11.0})}
            )
        }
    )
    table = _table(
        [
            ResolvedSupportTableSource(source="revenue", format=",", label="Revenue"),
            ResolvedSupportTableAggregate(
                aggregate="sum", source="revenue", format=",", label="Total"
            ),
        ]
    )
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    revenue_value, total_value = 1_234.0, 588_000.0
    widths = _entry_column_widths(
        table,
        [[revenue_value], [total_value]],
        style,
        numerals,
        series_order=None,
        single_column_per_series=False,
    )
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=style,
        entry_numerals=numerals,
        value_formats=[e.format for e in table.entries],
        column_widths=widths,
        column_headers=["Revenue", "Total"],
        category_field="region",
        color_field=None,
        series_order=None,
        dark_fills=None,
        header_fills=None,
        axis_y_orient=position,
    )
    cell_x = {
        layer["encoding"]["text"]["field"]: layer["encoding"]["x"]["value"]
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer["encoding"]["text"]["field"] != "__header"
    }
    revenue_right = cell_x["__support_table_col_0"]
    total_right = cell_x["__support_table_col_1"]

    measurer = get_font_measurer(style.font.family, numeric=True)
    total_text_width = measurer.measure(
        numerals[1].bare_text(total_value), style.font.size
    )
    total_text_left_edge = total_right - total_text_width

    rule = next(
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    )
    rule_x = rule["encoding"]["x"]["value"]

    # Past Revenue's own glyphs — its text right-aligns exactly at
    # revenue_right, with zero slack on that side.
    assert rule_x > revenue_right
    # Short of Total's own leftmost glyph.
    assert rule_x < total_text_left_edge


def test_attach_columns_omits_rule_layers_when_width_zero():
    spec = _hbar_spec()
    table = _table(
        [
            ResolvedSupportTableSource(source="revenue", label="Revenue"),
            ResolvedSupportTableAggregate(
                aggregate="sum", source="revenue", label="Total"
            ),
        ]
    )
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=_dt_style(position="left"),
        entry_numerals=numerals,
        value_formats=[e.format for e in table.entries],
        column_widths=[50.0, 50.0],
        column_headers=["Revenue", "Total"],
        category_field="region",
        color_field=None,
        series_order=None,
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",
    )
    rule_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    ]
    assert rule_layers == []


# =============================================================================
# TABULAR DIGITS (column block forces a tabular typeface; measurement matches it)
# =============================================================================


def test_column_cell_forces_tabular_font_regardless_of_authored_family():
    """Column value cells always paint a tabular-figures typeface, even when
    style.support_table.font.family names a proportional one — a column of
    numbers needs vertical digit alignment the row strip never does.
    """
    from dbt_charts.core.fonts import font_is_tabular
    from dbt_charts.core.render.chart.table import _table_numeric_cell_font

    proportional_family = "Inter, system-ui, sans-serif"
    assert not font_is_tabular(proportional_family)

    spec = _hbar_spec()
    style = _dt_style(
        position="left",
        font=_dt_style().font.model_copy(update={"family": proportional_family}),
    )
    table = _table([ResolvedSupportTableSource(source="revenue", format="$.2s")])
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=style,
        entry_numerals=numerals,
        value_formats=[e.format for e in table.entries],
        column_widths=[60.0],
        column_headers=["Revenue"],
        category_field="region",
        color_field=None,
        series_order=None,
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",
    )
    cell = next(
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer["encoding"]["text"]["field"] != "__header"
    )
    forced_family = _table_numeric_cell_font(proportional_family)
    assert font_is_tabular(forced_family)
    assert cell["mark"]["font"] == forced_family
    # The header stays in the author's own (proportional) label font — only
    # digit cells are forced tabular.
    header = next(
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer["encoding"]["text"]["field"] == "__header"
    )
    assert header["mark"].get("font") != forced_family


def test_column_width_measurement_matches_the_painted_tabular_typeface():
    """Critical consequence: tabular digits are wider than proportional ones,
    so the reserved column width must be measured in the typeface actually
    painted (the forced tabular one), not the author's own proportional
    style.support_table.font.family. Measuring the narrower proportional
    typeface here would under-reserve the column and reopen the plot-side
    gutter/overhang bug.
    """
    from dbt_charts.core.font_measure import get_font_measurer
    from dbt_charts.core.render.chart.table import _table_numeric_cell_font

    proportional_family = "Inter, system-ui, sans-serif"
    style = _dt_style(
        font=_dt_style().font.model_copy(update={"family": proportional_family})
    )
    # label.font.size is an InheritSlot only filled by the chart-local
    # resolve cascade — set it explicitly for this bare-style unit test.
    style = style.model_copy(
        update={
            "label": style.label.model_copy(
                update={"font": style.label.font.model_copy(update={"size": 11.0})}
            )
        }
    )
    table = _table([ResolvedSupportTableSource(source="revenue", format=",")])
    values = [1234567.0]
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))

    widths = _entry_column_widths(
        table,
        [values],
        style,
        numerals,
        series_order=None,
        single_column_per_series=False,
    )

    font_size = style.font.size
    painted_typeface = _table_numeric_cell_font(proportional_family)
    painted_measurer = get_font_measurer(painted_typeface, numeric=True)
    proportional_measurer = get_font_measurer(proportional_family, numeric=False)
    formatted = numerals[0].bare_text(values[0])
    painted_w = painted_measurer.measure(formatted, font_size)
    proportional_w = proportional_measurer.measure(formatted, font_size)

    # The two typefaces must actually differ for this test to mean anything.
    assert painted_w != proportional_w
    # The reserved width tracks the painted (tabular) typeface, not the narrower
    # proportional one style.support_table.font.family names.
    assert widths[0] >= painted_w + 2.0 * style.row.padding.horizontal - 1e-6


def test_attach_columns_rule_explicitly_nulls_y_to_avoid_stray_domain_entry():
    """Regression, the column-block twin of the row strip's own divider/row
    rule bug: this rule layer's inline data has no category field, so an
    unset y channel silently inherited the chart's shared top-level y
    encoding (the category field), evaluated to undefined, and on the
    ordinal/nominal category scale (sort: null) added a phantom extra band.
    The rule must explicitly null the channel it does not use.
    """
    spec = _hbar_spec()
    table = _table(
        [
            ResolvedSupportTableSource(source="revenue", label="Revenue"),
            ResolvedSupportTableAggregate(
                aggregate="sum", source="revenue", label="Total"
            ),
        ]
    )
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=_dt_style(
            position="left",
            row=_dt_style().row.model_copy(
                update={"rule": _dt_style().row.rule.model_copy(update={"width": 1.0})}
            ),
        ),
        entry_numerals=numerals,
        value_formats=[e.format for e in table.entries],
        column_widths=[50.0, 50.0],
        column_headers=["Revenue", "Total"],
        category_field="region",
        color_field=None,
        series_order=None,
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",
    )
    rule_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    ]
    assert rule_layers
    for rule in rule_layers:
        assert rule["encoding"].get("y") is None
        assert "y" in rule["encoding"]


def test_attach_columns_rule_is_not_announced_to_screen_readers():
    """Regression, the column-block twin of the row strip's own aria bug: a
    decorative rule with no bound data otherwise inherits the chart's shared
    top-level tooltip/description encodings, announcing a screen reader
    label built from fields it never binds. ``aria: False`` opts the mark
    out of Vega-Lite's default field-derived description entirely.
    """
    spec = _hbar_spec()
    table = _table(
        [
            ResolvedSupportTableSource(source="revenue", label="Revenue"),
            ResolvedSupportTableAggregate(
                aggregate="sum", source="revenue", label="Total"
            ),
        ]
    )
    numerals = plain_numerals(table, "Inter", [[]] * len(table.entries))
    out = attach_support_table_columns(
        spec,
        support_table=table,
        style=_dt_style(
            position="left",
            row=_dt_style().row.model_copy(
                update={"rule": _dt_style().row.rule.model_copy(update={"width": 1.0})}
            ),
        ),
        entry_numerals=numerals,
        value_formats=[e.format for e in table.entries],
        column_widths=[50.0, 50.0],
        column_headers=["Revenue", "Total"],
        category_field="region",
        color_field=None,
        series_order=None,
        dark_fills=None,
        header_fills=None,
        axis_y_orient="left",
    )
    rule_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    ]
    assert rule_layers
    for rule in rule_layers:
        assert rule["mark"]["aria"] is False
