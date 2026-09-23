"""Tests for the support_table VL emission (render/chart/support_table_attachment.py).

Covers the emission contract: layer count/shape per row, divider rule,
shared x-encoding, pixel-literal y-positioning.
"""

from __future__ import annotations

import math
from typing import Literal

import pytest

from dbt_charts.core.compile.config import (
    get_chart_rendering,
    get_theme_style,
)
from dbt_charts.core.compile.models.chart.authored import (
    ChartSupportTable,
    ChartSupportTableAggregate,
    ChartSupportTablePerSeries,
    ChartSupportTableSource,
)
from dbt_charts.core.compile.models.primitives import RuleStyle
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.render.chart.support_table_attachment import (
    attach_support_table,
    plain_numerals,
    support_table_strip_height,
)


def _attach(
    spec,
    support_table,
    style=None,
    axis_y_orient: Literal["left", "right"] = "right",
    suppress_series_labels=False,
    axis_offset_value: float | None = None,
):
    """Test shim: attach_support_table with a default resolved charts_style."""
    return attach_support_table(
        spec,
        support_table=support_table,
        entry_numerals=plain_numerals(
            support_table, None, "Inter", [[]] * len(support_table.entries)
        )
        if support_table
        else (),
        style=style or get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        axis_y_orient=axis_y_orient,
        suppress_series_labels=suppress_series_labels,
        axis_offset_value=axis_offset_value,
    )


def _attach_per_series(
    spec,
    support_table,
    series_order,
    dark_fills=None,
    axis_y_orient: Literal["left", "right"] = "right",
    suppress_series_labels=False,
):
    """Shim for per-series attach_support_table calls."""

    return attach_support_table(
        spec,
        support_table=support_table,
        entry_numerals=plain_numerals(
            support_table, None, "Inter", [[]] * len(support_table.entries)
        )
        if support_table
        else (),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        series_order=series_order,
        dark_fills=dark_fills,
        axis_y_orient=axis_y_orient,
        suppress_series_labels=suppress_series_labels,
    )


def _base_spec():
    return {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "ordinal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
        "height": 300,
        "width": 600,
    }


def _cell_text_layers(spec):
    return [
        layer
        for layer in spec["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") != "__label"
    ]


def _charts_style():
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    return resolve_chart_style_context(get_theme_style())


def _axis_label_padding(charts_style):
    """Test-side stand-in for resolved_chart.style.axis_y.labels.padding.

    attach_support_table no longer computes this itself (render boundary
    guard) — production callers read it off the already-resolved chart;
    these low-level unit tests fabricate charts_style directly instead of
    a full ResolvedChart, so they derive the same value via the cascade
    helper the resolver itself uses.
    """
    return resolved_axis_style(
        charts_style, "axis_y", "quantitative", chart_type="", label_authored=False
    ).labels.padding


def _dt_style(**overrides):
    """Build a SupportTableStyle from the default theme with optional overrides."""
    return get_theme_style().charts.support_table.model_copy(update=overrides)


def _label_layers(spec):
    return [
        layer
        for layer in spec["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]


def _per_series_layered_spec():
    """Minimal layered spec mimicking a line chart with color encoding.

    Line/area charts emit a ``layer`` spec at the top level with an inherited
    top-level encoding.color = {field: series}.  Per_series label stubs sit as
    sibling layers with own inline data lacking that field — the bug's breeding
    ground.
    """
    return {
        "layer": [
            {
                "mark": {"type": "line"},
                "encoding": {
                    "x": {"field": "month", "type": "temporal"},
                    "y": {"field": "revenue", "type": "quantitative"},
                },
            }
        ],
        "encoding": {
            "x": {"field": "month", "type": "temporal"},
            "y": {"field": "revenue", "type": "quantitative"},
            "color": {"field": "series", "type": "nominal"},
        },
        "height": 300,
        "width": 600,
    }


def _multi_base_layer_spec():
    """Mimics a real line chart's multi-layer base -- halo, foreground stroke,
    and an overlay that carries its OWN explicit ``description`` (the shape
    ``emitters/_layers.py``'s ``emit_line_layer`` and a combo overlay
    actually produce). ``_wrap_base_as_layer`` returns an already-layered
    spec like this one UNCHANGED, so all three entries below are base
    layers ``attach_support_table`` must never touch -- only layers it
    itself appends afterward may lose ``description``.
    """
    return {
        "layer": [
            {
                "mark": {"type": "line", "opacity": 0.3},
                "encoding": {
                    "x": {"field": "month", "type": "temporal"},
                    "y": {"field": "revenue", "type": "quantitative"},
                },
            },
            {
                "mark": {"type": "line"},
                "encoding": {
                    "x": {"field": "month", "type": "temporal"},
                    "y": {"field": "revenue", "type": "quantitative"},
                },
            },
            {
                "mark": {"type": "point", "opacity": 0},
                "encoding": {
                    "x": {"field": "month", "type": "temporal"},
                    "y": {"field": "revenue", "type": "quantitative"},
                    "description": {"value": {"expr": "'overlay-own-description'"}},
                },
            },
        ],
        "encoding": {
            "x": {"field": "month", "type": "temporal"},
            "y": {"field": "revenue", "type": "quantitative"},
            "color": {"field": "series", "type": "nominal"},
            "description": {"value": {"expr": "'shared-base-description'"}},
        },
        "height": 300,
        "width": 600,
    }


def _per_series_spec():
    """Base spec for a stacked bar with color encoding."""
    return {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "ordinal"},
            "y": {"field": "revenue", "type": "quantitative"},
            "color": {"field": "category", "type": "nominal"},
        },
        "height": 300,
        "width": 600,
    }


def _table(entries):
    return ChartSupportTable.model_validate({"entries": entries})


def _temporal_spec():
    """Base Vega-Lite spec with temporal x encoding."""
    return {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "date", "type": "temporal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
        "height": 300,
        "width": 600,
    }


def test_attach_returns_untouched_when_support_table_is_none():
    spec = _base_spec()
    out = _attach(spec, None)
    assert out == spec  # pure no-op


def test_attach_is_idempotent_on_empty_entries():
    # ChartSupportTable forbids empty lists at validation time, but defensively:
    # caller passing a non-ChartSupportTable None-equivalent should still no-op.
    spec = _base_spec()
    out = _attach(spec, None)
    assert "layer" not in out or out.get("layer") == spec.get("layer")


def test_attach_source_row_emits_text_layer_with_shared_x():
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue", format="$.2s")])
    out = _attach(spec, table)
    # Attachment converts the base spec into a layered composition. The base
    # mark/encoding becomes the first layer; attached rows follow.
    assert "layer" in out
    layers = out["layer"]
    # At least base + 1 row layer (+ optional divider).
    assert len(layers) >= 2
    # Find the attached text layer (has a calculate transform on __support_table_*).
    text_layers = _cell_text_layers(out)
    assert len(text_layers) == 1
    tl = text_layers[0]
    # x-encoding shares field with base chart
    assert tl["encoding"]["x"]["field"] == "month"
    # y is a pixel-literal (position-dependent; separate position tests cover placement)
    assert "value" in tl["encoding"]["y"]
    # text is piped through a computed format field
    assert tl["encoding"]["text"]["field"].startswith("__support_table_")


def test_attach_source_row_emits_format_calculate_transform():
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue", format="$.2s")])
    out = _attach(spec, table)
    text_layers = _cell_text_layers(out)
    tl = text_layers[0]
    transforms = tl.get("transform", [])
    # There is exactly one calculate transform referencing format() on the source.
    calcs = [t for t in transforms if "calculate" in t]
    assert len(calcs) == 1
    assert 'format(datum["revenue"]' in calcs[0]["calculate"]
    # Inline d3 spec passes verbatim (three-way contract: no trim for inline d3).
    assert "'$.2s'" in calcs[0]["calculate"]


def test_attach_source_row_format_calculate_renders_invalid_values_as_dash():
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue", format="$.2s")])
    out = _attach(spec, table)
    text_layers = _cell_text_layers(out)
    tl = text_layers[0]
    calc = next(t["calculate"] for t in tl.get("transform", []) if "calculate" in t)
    assert 'isValid(datum["revenue"])' in calc
    assert 'isFinite(datum["revenue"])' in calc
    assert "'-'" in calc


def test_attach_source_row_without_format_uses_raw_value():
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="sample_size")])
    out = _attach(spec, table)
    text_layers = _cell_text_layers(out)
    tl = text_layers[0]
    # No format: the value still flows through the missing-cell guard.
    assert tl["encoding"]["text"]["field"].startswith("__support_table_")
    calc = next(t["calculate"] for t in tl.get("transform", []) if "calculate" in t)
    assert 'datum["sample_size"]' in calc
    assert "'-'" in calc


def test_attach_source_row_with_non_identifier_source_uses_bracket_access():
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="acted upon")])
    out = _attach(spec, table)
    text_layers = _cell_text_layers(out)
    tl = text_layers[0]
    calc = next(t["calculate"] for t in tl.get("transform", []) if "calculate" in t)
    # Dot access on a space-containing field name is invalid JS
    # (`datum.acted upon` is not parseable); bracket access with a JSON-escaped
    # field name works for any source column name, identifier or not.
    assert 'datum["acted upon"]' in calc
    assert "datum.acted upon" not in calc


def test_attach_source_row_without_label_uses_source_display_name():
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="successful_launches")])
    out = _attach(spec, table)
    stub_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(stub_layers) == 1
    assert stub_layers[0]["data"]["values"] == [{"__label": "Successful Launches"}]


def test_attach_aggregate_row_emits_aggregate_transform_grouped_by_x():
    spec = _base_spec()
    table = _table(
        [ChartSupportTableAggregate(aggregate="sum", source="revenue", format="$.2s")]
    )
    out = _attach(spec, table)
    text_layers = _cell_text_layers(out)
    assert len(text_layers) == 1
    tl = text_layers[0]
    transforms = tl.get("transform", [])
    aggs = [t for t in transforms if "aggregate" in t]
    assert len(aggs) == 1
    agg = aggs[0]
    assert agg["aggregate"][0]["op"] == "sum"
    assert agg["aggregate"][0]["field"] == "revenue"
    assert agg["groupby"] == ["month"]


def test_attach_aggregate_row_format_calculate_renders_invalid_values_as_dash():
    spec = _base_spec()
    table = _table(
        [ChartSupportTableAggregate(aggregate="sum", source="revenue", format="$.2s")]
    )
    out = _attach(spec, table)
    text_layers = _cell_text_layers(out)
    tl = text_layers[0]
    calc = next(t["calculate"] for t in tl.get("transform", []) if "calculate" in t)
    assert 'isValid(datum["__support_table_0_val"])' in calc
    assert 'isFinite(datum["__support_table_0_val"])' in calc
    assert "'-'" in calc


def test_attach_aggregate_row_without_label_uses_source_display_name():
    spec = _base_spec()
    table = _table(
        [ChartSupportTableAggregate(aggregate="sum", source="total_mass_kg")]
    )
    out = _attach(spec, table)
    stub_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(stub_layers) == 1
    assert stub_layers[0]["data"]["values"] == [{"__label": "Total Mass Kg"}]


def test_attach_aggregate_translates_avg_to_mean_at_lowering_layer():
    # Spec G4: authoring surface is exact (`avg`). lowering.md §3: compiler
    # translates avg → Vega mean. Authors never see `mean`.
    spec = _base_spec()
    table = _table([ChartSupportTableAggregate(aggregate="avg", source="revenue")])
    out = _attach(spec, table)
    text_layers = _cell_text_layers(out)
    transforms = text_layers[0]["transform"]
    aggs = [t for t in transforms if "aggregate" in t]
    assert aggs[0]["aggregate"][0]["op"] == "mean"


def test_attach_aggregate_translates_count_distinct_to_distinct():
    spec = _base_spec()
    table = _table(
        [ChartSupportTableAggregate(aggregate="count_distinct", source="user_id")]
    )
    out = _attach(spec, table)
    text_layers = _cell_text_layers(out)
    transforms = text_layers[0]["transform"]
    aggs = [t for t in transforms if "aggregate" in t]
    assert aggs[0]["aggregate"][0]["op"] == "distinct"


def test_attach_emits_divider_rule_when_width_positive():
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue")])
    style = _dt_style(divider=RuleStyle(width=1.5, continuous=True))
    out = _attach(spec, table, style=style)
    rule_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    ]
    assert len(rule_layers) == 1
    rl = rule_layers[0]
    assert rl["mark"]["strokeWidth"] == 1.5


def test_attach_divider_rule_explicitly_nulls_x_to_avoid_stray_domain_entry():
    """Regression: the divider rule's own inline data has no x-field, so an
    unset x channel silently inherited the chart's top-level shared x
    encoding (Vega-Lite merges a layer spec's encoding with the shared
    top-level one) — datum.<x field> then evaluated to undefined, and on an
    ordinal/nominal x scale (sort: null) Vega added "undefined" as a real
    extra category, drawn as a phantom tick/bar. The rule must explicitly
    null the channel it does not use, the same opt-out this module already
    uses for yOffset/color elsewhere.
    """
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue")])
    style = _dt_style(divider=RuleStyle(width=1.5, continuous=True))
    out = _attach(spec, table, style=style)
    rule_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    ]
    assert len(rule_layers) == 1
    assert rule_layers[0]["encoding"].get("x") is None
    assert "x" in rule_layers[0]["encoding"]


def test_attach_divider_rule_is_not_announced_to_screen_readers():
    """Regression: a decorative rule with no bound data still inherited the
    chart's shared top-level tooltip/description encodings, so a screen
    reader announced a label built from fields this layer never binds
    (garbage text, not a missing one). ``aria: False`` opts the mark out of
    Vega-Lite's default field-derived accessibility description entirely.
    """
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue")])
    style = _dt_style(divider=RuleStyle(width=1.5, continuous=True))
    out = _attach(spec, table, style=style)
    rule_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    ]
    assert rule_layers
    for rule in rule_layers:
        assert rule["mark"]["aria"] is False


def test_attach_omits_divider_rule_when_width_zero():
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue")])
    style = _dt_style(divider=RuleStyle(width=0.0, continuous=True))
    out = _attach(spec, table, style=style)
    rule_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    ]
    assert len(rule_layers) == 0


def test_attach_row_text_font_style_reaches_mark():
    """font.style authored on support_table.font reaches the row-value text
    mark as VL's fontStyle, mirroring family/size/weight/color."""
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue", label="Revenue")])
    base = _dt_style()
    style = _dt_style(font=base.font.model_copy(update={"style": "italic"}))
    out = _attach(spec, table, style=style)
    text_layers = _cell_text_layers(out)
    assert text_layers[0]["mark"]["fontStyle"] == "italic"


def test_attach_label_stub_font_style_reaches_mark():
    """font.style authored on support_table.label.font reaches the row-label
    text mark as VL's fontStyle, mirroring family/size/weight/color."""
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue", label="Revenue")])
    base = _dt_style()
    style = base.model_copy(
        update={
            "label": base.label.model_copy(
                update={"font": base.label.font.model_copy(update={"style": "italic"})}
            )
        }
    )
    out = _attach(spec, table, style=style)
    label_layers = _label_layers(out)
    assert label_layers[0]["mark"]["fontStyle"] == "italic"


def test_attach_preserves_base_chart_as_first_layer():
    spec = _base_spec()
    table = _table(
        [
            ChartSupportTableSource(source="revenue"),
            ChartSupportTableAggregate(aggregate="sum", source="revenue"),
        ]
    )
    out = _attach(spec, table)
    first = out["layer"][0]
    # Base chart bar mark preserved.
    assert first["mark"]["type"] == "bar"
    assert first["encoding"]["x"]["field"] == "month"


def test_attach_multiple_rows_emits_one_layer_per_row():
    spec = _base_spec()
    table = _table(
        [
            ChartSupportTableSource(source="a"),
            ChartSupportTableSource(source="b"),
            ChartSupportTableAggregate(aggregate="sum", source="a"),
        ]
    )
    out = _attach(spec, table)
    text_layers = _cell_text_layers(out)
    # 3 row text layers; label stubs are separate text layers.
    assert len(text_layers) == 3


def test_attach_label_stub_layer_defaults_from_source_when_label_is_omitted():
    spec = _base_spec()
    table = _table(
        [
            ChartSupportTableSource(source="a", label="A"),
            ChartSupportTableSource(source="b"),  # label defaults from source
            ChartSupportTableSource(source="c", label="C"),
        ]
    )
    out = _attach(spec, table)
    # Stub layers carry the label text via inline-data __label field (both
    # right-cap and left-stub use this shape after the y-axis-column rewire).
    stub_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert [layer["data"]["values"][0]["__label"] for layer in stub_layers] == [
        "A",
        "B",
        "C",
    ]


def test_attach_does_not_mutate_spec_padding():
    # Padding allocation is the caller's responsibility (single source of
    # truth in render_standard_vega_spec). The attachment leaves padding alone.
    spec = _base_spec()
    spec["padding"] = {"top": 5, "bottom": 10, "left": 5, "right": 5}
    table = _table([ChartSupportTableSource(source="revenue")])
    out = _attach(spec, table)
    assert out["padding"] == {"top": 5, "bottom": 10, "left": 5, "right": 5}


def test_attach_no_op_when_support_table_is_none_does_not_require_height():
    # The "support_table is None" branch must not enforce height — no strip is
    # being attached; the spec is returned untouched.
    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "ordinal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
    }
    out = _attach(spec, None)
    assert out == spec


def test_attach_text_layer_uses_pixel_value_y():
    # `{"y": {"expr": "..."}}` is treated as a SCALED data value by Vega-Lite,
    # not a pixel literal — that's why the v1 emitter (which used "height + N")
    # collapsed every row to one y position. Pixel-literal `{"y": {"value": N}}`
    # is the form that places marks in absolute pixel space regardless of position.
    # RJ's chart-lab references (e.g. area-multi-2-base.vl.json) use this form.
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue")])
    # Use explicit position=bottom so the test is position-independent.
    style = _dt_style(position="bottom")
    out = _attach(spec, table, style=style, axis_offset_value=0.0)
    text_layers = _cell_text_layers(out)
    y_enc = text_layers[0]["encoding"]["y"]
    assert "value" in y_enc, "y must be a pixel-literal value, not an expression"
    assert "expr" not in y_enc, (
        "expr-form is data-scaled in Vega-Lite; must not be used"
    )
    # position:bottom places the strip below the plot (spec.height = 300 from _base_spec).
    assert y_enc["value"] > 300


def test_attach_synthesizes_height_when_width_present_but_height_missing():
    # Optional callers (warnings detector, diagnostic scripts) reach the
    # attachment without a board-level layout sizer; spec carries width but
    # no height. Synthesize a height from charts_style.aspect_ratio clamped
    # to [min_height, max_height] so pixel-y placement still has an anchor.
    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "ordinal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
        "width": 400,
    }
    table = _table([ChartSupportTableSource(source="revenue")])
    out = _attach(spec, table)
    assert isinstance(out["height"], (int, float))
    assert out["height"] > 0


def test_attach_requires_explicit_spec_width_when_height_missing():
    # Without width OR height, no anchor exists to project the aspect ratio
    # onto — fail loudly per Design Philosophy #4, stamped ERR-INPUT-INVALID.
    from dbt_charts.core.render.errors import RenderError

    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "ordinal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
    }
    table = _table([ChartSupportTableSource(source="revenue")])
    with pytest.raises(RenderError, match="explicit width") as excinfo:
        _attach(spec, table)
    assert excinfo.value.code is not None
    assert excinfo.value.code.code == "ERR-INPUT-INVALID"


def test_attach_requires_explicit_spec_width_when_right_cap_label():
    # Pixel-literal x for right-cap labels depends on spec.width. Without it
    # the y-axis-tick column has no anchor — fail loudly per Design Philosophy #4,
    # stamped ERR-INPUT-INVALID.
    from dbt_charts.core.render.errors import RenderError

    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "ordinal"},
            "y": {"field": "revenue", "type": "quantitative"},
        },
        "height": 300,
    }
    table = _table([ChartSupportTableSource(source="a", label="A")])
    with pytest.raises(RenderError, match="explicit width") as excinfo:
        attach_support_table(
            spec,
            support_table=table,
            entry_numerals=plain_numerals(
                table, None, "Inter", [[]] * len(table.entries)
            ),
            style=get_theme_style().charts.support_table,
            charts_style=_charts_style(),
            axis_label_padding=_axis_label_padding(_charts_style()),
            axis_y_orient="right",  # right-orient requires spec_width
        )
    assert excinfo.value.code is not None
    assert excinfo.value.code.code == "ERR-INPUT-INVALID"


def test_attach_requires_charts_style_when_support_table_present():
    # charts_style feeds x-axis label offset computation; None with a
    # non-empty support_table has no anchor to compute from.
    from dbt_charts.core.render.errors import RenderError

    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue")])
    with pytest.raises(RenderError, match="charts_style") as excinfo:
        attach_support_table(
            spec,
            support_table=table,
            entry_numerals=plain_numerals(
                table, None, "Inter", [[]] * len(table.entries)
            ),
            style=get_theme_style().charts.support_table,
            charts_style=None,
            axis_label_padding=0.0,
            axis_y_orient="right",
        )
    assert excinfo.value.code is not None
    assert excinfo.value.code.code == "ERR-INPUT-INVALID"


def test_per_series_requires_series_order():
    # Per_series entries need the caller-resolved, ordered series list — the
    # attachment cannot invent stack order from the color encoding domain alone.
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries
    from dbt_charts.core.render.errors import RenderError

    spec = _per_series_spec()
    table = _table([ChartSupportTablePerSeries(per_series="revenue")])
    with pytest.raises(RenderError, match="series_order") as excinfo:
        attach_support_table(
            spec,
            support_table=table,
            entry_numerals=plain_numerals(
                table, None, "Inter", [[]] * len(table.entries)
            ),
            style=get_theme_style().charts.support_table,
            charts_style=_charts_style(),
            axis_label_padding=_axis_label_padding(_charts_style()),
            series_order=None,
            axis_y_orient="right",
        )
    assert excinfo.value.code is not None
    assert excinfo.value.code.code == "ERR-INPUT-INVALID"


def test_per_series_row_layers_requires_explicit_spec_width_when_right_cap_label():
    # Per_series right-cap label positioning is pixel-literal against
    # spec.width; without it there's no anchor for the label column.
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries
    from dbt_charts.core.render.errors import RenderError

    spec = _per_series_spec()
    del spec["width"]
    table = _table([ChartSupportTablePerSeries(per_series="revenue", label="A")])
    with pytest.raises(RenderError, match="explicit.*width") as excinfo:
        attach_support_table(
            spec,
            support_table=table,
            entry_numerals=plain_numerals(
                table, None, "Inter", [[]] * len(table.entries)
            ),
            style=get_theme_style().charts.support_table,
            charts_style=_charts_style(),
            axis_label_padding=_axis_label_padding(_charts_style()),
            series_order=["category"],
            axis_y_orient="right",
        )
    assert excinfo.value.code is not None
    assert excinfo.value.code.code == "ERR-INPUT-INVALID"


def test_per_series_requires_color_encoding():
    # Per_series row layers key off the parent chart's color field; without
    # one there is no series to slice rows by.
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries
    from dbt_charts.core.render.errors import RenderError

    spec = _base_spec()  # no color encoding
    table = _table([ChartSupportTablePerSeries(per_series="revenue")])
    with pytest.raises(RenderError, match="color") as excinfo:
        attach_support_table(
            spec,
            support_table=table,
            entry_numerals=plain_numerals(
                table, None, "Inter", [[]] * len(table.entries)
            ),
            style=get_theme_style().charts.support_table,
            charts_style=_charts_style(),
            axis_label_padding=_axis_label_padding(_charts_style()),
            series_order=["Apparel"],
            axis_y_orient="right",
        )
    assert excinfo.value.code is not None
    assert excinfo.value.code.code == "ERR-INPUT-INVALID"


def test_attach_shared_x_encoding_has_no_axis_null():
    # axis: None crashes vl-convert with TypeError in parseAxesAndHeaders.
    from dbt_charts.core.render.chart.support_table_attachment import _shared_x_encoding

    enc = _shared_x_encoding({"field": "month", "type": "ordinal"}, mark_is_bar=False)
    assert "axis" not in enc or enc["axis"] is not None


def test_shared_x_encoding_propagates_time_unit():
    # timeUnit must be forwarded so strip layers reconcile with parent on
    # temporal+timeUnit encodings (forward-compat with time-unit task).
    from dbt_charts.core.render.chart.support_table_attachment import _shared_x_encoding

    enc = _shared_x_encoding(
        {"field": "date", "type": "temporal", "timeUnit": "yearmonth"},
        mark_is_bar=False,
    )
    assert enc["timeUnit"] == "yearmonth"
    assert enc["field"] == "date"
    assert enc["type"] == "temporal"


def test_attach_multiple_rows_each_use_distinct_pixel_y():
    # Each row must occupy its own pixel y; otherwise the rows visually stack
    # at the same position (the bug PR #1799 unmasked on visual inspection).
    spec = _base_spec()
    table = _table(
        [
            ChartSupportTableSource(source="a"),
            ChartSupportTableSource(source="b"),
            ChartSupportTableSource(source="c"),
        ]
    )
    # Use explicit position=bottom to keep this test position-independent.
    style = _dt_style(position="bottom")
    out = _attach(spec, table, style=style, axis_offset_value=0.0)
    text_layers = _cell_text_layers(out)
    assert len(text_layers) == 3
    ys = [layer["encoding"]["y"]["value"] for layer in text_layers]
    assert len(set(ys)) == 3, f"rows must have distinct pixel y values, got {ys}"
    # Rows render top-to-bottom in list order (spec G5).
    assert ys == sorted(ys), f"row pixel-y must increase by index, got {ys}"
    # All below the plot.
    assert all(y > spec["height"] for y in ys)


def test_attach_emits_row_rule_layer_between_rows_when_width_positive():
    # Spec §4.3: row.rule.width/color styles a rule between adjacent rows.
    # Default width is 0 (no rule) so it's normally absent. When width > 0
    # the emitter must produce N-1 rule layers for N rows.
    spec = _base_spec()
    table = _table(
        [
            ChartSupportTableSource(source="a"),
            ChartSupportTableSource(source="b"),
            ChartSupportTableSource(source="c"),
        ]
    )
    # Disable the strip-top divider so we can identify inter-row rules
    # without filtering on stroke width — the test is then robust to
    # width tweaks rather than coupled to a specific value.
    style = _dt_style(
        divider=RuleStyle(width=0, continuous=True),
        row=_dt_style().row.model_copy(
            update={"rule": RuleStyle(width=0.5, color="#ddd", continuous=True)}
        ),
    )
    out = _attach(spec, table, style=style)
    rule_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    ]
    assert len(rule_layers) == 2, (
        f"expected 2 inter-row rules for 3 rows (divider disabled), "
        f"got {len(rule_layers)}"
    )
    for rule in rule_layers:
        assert rule["mark"].get("strokeWidth") == 0.5
        assert rule["mark"].get("stroke") == "#ddd"


def test_attach_row_rule_explicitly_nulls_x_to_avoid_stray_domain_entry():
    """Same regression as the divider rule: an inter-row rule's own inline
    data has no x-field, so the channel must be explicitly nulled rather
    than left unset — otherwise it silently inherits the shared top-level x
    encoding and Vega adds a phantom "undefined" category to an ordinal x
    domain.
    """
    spec = _base_spec()
    table = _table(
        [ChartSupportTableSource(source="a"), ChartSupportTableSource(source="b")]
    )
    style = _dt_style(
        row=_dt_style().row.model_copy(
            update={"rule": RuleStyle(width=0.5, continuous=True)}
        )
    )
    out = _attach(spec, table, style=style)
    rule_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    ]
    assert rule_layers
    for rule in rule_layers:
        assert rule["encoding"].get("x") is None
        assert "x" in rule["encoding"]


def test_attach_row_rule_is_not_announced_to_screen_readers():
    """Same regression as the divider rule: a decorative inter-row rule must
    opt out of Vega-Lite's default field-derived accessibility description."""
    spec = _base_spec()
    table = _table(
        [ChartSupportTableSource(source="a"), ChartSupportTableSource(source="b")]
    )
    style = _dt_style(
        row=_dt_style().row.model_copy(
            update={"rule": RuleStyle(width=0.5, continuous=True)}
        )
    )
    out = _attach(spec, table, style=style)
    rule_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    ]
    assert rule_layers
    for rule in rule_layers:
        assert rule["mark"]["aria"] is False


def test_attach_omits_row_rule_layers_when_width_zero():
    spec = _base_spec()
    table = _table(
        [ChartSupportTableSource(source="a"), ChartSupportTableSource(source="b")]
    )
    out = _attach(spec, table)  # default style: divider.width=0 + row.rule.width=0
    rule_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    ]
    # No rules at all — divider off by default (matches RJ's chart-lab),
    # row.rule off by default.
    assert len(rule_layers) == 0


def test_attach_label_left_orient_anchors_at_negative_axis_padding():
    # When axis_y_orient=left, label x is -axis_y.labels.padding (left gutter,
    # matching the left y-axis tick label column). It must NOT be at
    # row.padding.horizontal (inside the plot).

    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="a", label="A")])
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        axis_y_orient="left",
    )
    stub_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and isinstance(layer.get("data"), dict)
        and any("__label" in row for row in layer["data"].get("values", []))
    ]
    assert len(stub_layers) == 1
    axis_pad = resolved_axis_style(
        _charts_style(), "axis_y", "quantitative", chart_type="", label_authored=False
    ).labels.padding
    assert stub_layers[0]["encoding"]["x"]["value"] == -axis_pad, (
        f"left-orient label x must be -axis_y.labels.padding={-axis_pad}; "
        f"got {stub_layers[0]['encoding']['x']['value']}"
    )


def test_attach_label_right_orient_uses_pixel_literal_x():
    # Right-cap label must use a pixel-literal x anchored at the y-axis column
    # (spec.width + axis_y.labels.padding), not a data-bound x with window filter.
    # Old behavior placed it at last-bar centroid + dx, which collided with y-axis ticks.
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="a", label="A")])
    # Default axis_y_orient="right" (dbt charts convention)
    out = _attach(spec, table)
    # New shape: inline data {"values": [{"__label": ...}]}, text: {"field": "__label"}
    stub_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(stub_layers) == 1, (
        f"expected one right-cap label layer, got {len(stub_layers)}"
    )
    layer = stub_layers[0]
    x_enc = layer["encoding"]["x"]
    # Pixel-literal — no data field, no window transform.
    assert "value" in x_enc, "right-cap label x must be a pixel-literal {value: <px>}"
    assert "field" not in x_enc, "right-cap label must not bind to a data field"
    transforms = layer.get("transform", [])
    assert not any("window" in t for t in transforms), (
        "pixel-literal label no longer needs a window row_number filter"
    )
    # Positioned at or past the right edge of the plot.
    assert x_enc["value"] >= spec["width"]


def test_default_axis_y_orient_emits_right_cap_no_left_edge_collision():
    # Regression: after #2443 thinned cells to label-period openers, the
    # leftmost cell lands near x=0, colliding with the left-cap stub. The
    # default axis_y_orient="right" must route through _label_stub_right_layer
    # so the label sits at spec_width + axis_y.labels.padding — never at
    # row.padding.horizontal (inside the plot).
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="a", label="Revenue")])
    # Default axis_y_orient="right" — no override needed.
    out = _attach(spec, table)
    stub_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(stub_layers) == 1, f"expected one label layer, got {len(stub_layers)}"
    x_val = stub_layers[0]["encoding"]["x"]["value"]
    assert x_val >= spec["width"], (
        f"default (right-orient) label must be a right-cap (x >= spec_width={spec['width']}); "
        f"got x={x_val} — left-edge collision not fixed"
    )
    # And the left-cap (left-orient) sits in the left gutter, never inside the plot.
    out_left = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        axis_y_orient="left",
    )
    stub_layers_left = [
        layer
        for layer in out_left["layer"]
        if layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(stub_layers_left) == 1
    x_left = stub_layers_left[0]["encoding"]["x"]["value"]
    assert x_left < 0, (
        f"left-orient label must be in the left gutter (x < 0); got x={x_left}"
    )


def test_agg_op_map_keys_are_all_authoring_surface_ops():
    # Every authoring surface aggregate must have a VL lowering mapping.
    from typing import get_args

    from dbt_charts.core.compile.models.chart.authored import (
        ChartSupportTableAggregateOp,
    )
    from dbt_charts.core.render.chart.support_table_attachment import _AGG_OP_TO_VL

    authoring_ops = set(get_args(ChartSupportTableAggregateOp))
    assert set(_AGG_OP_TO_VL.keys()) == authoring_ops


def test_attach_does_not_mutate_input_spec_encoding():
    """Caller's spec.encoding must survive (no .pop on shared dict)."""
    spec_in = _base_spec()
    spec_in["encoding"]["tooltip"] = [
        {"field": "month", "type": "ordinal"},
        {"field": "revenue", "type": "quantitative", "format": ".2s"},
    ]
    encoding_keys_before = set(spec_in["encoding"].keys())

    _attach(spec_in, _table([{"source": "revenue"}]))

    assert set(spec_in["encoding"].keys()) == encoding_keys_before, (
        f"attach_support_table mutated the input spec.encoding; before="
        f"{encoding_keys_before}, after={set(spec_in['encoding'].keys())}"
    )
    assert "tooltip" in spec_in["encoding"], (
        "input spec.encoding.tooltip was removed; attach must not mutate caller dict"
    )


def test_attach_promotes_tooltip_to_spec_level_for_strip_inheritance():
    """Tooltip at spec-level so strip layers inherit (no per-layer override)."""
    spec_in = _base_spec()
    spec_in["encoding"]["tooltip"] = [
        {"field": "month", "type": "ordinal"},
        {"field": "revenue", "type": "quantitative"},
    ]
    out = _attach(spec_in, _table([{"source": "revenue"}]))
    spec_tooltip = out.get("encoding", {}).get("tooltip")
    assert spec_tooltip is not None, (
        "spec.encoding.tooltip should be promoted up so strip layers inherit"
    )
    fields = {t.get("field") for t in spec_tooltip}
    assert {"month", "revenue"} <= fields
    base_layer_encoding = out["layer"][0]["encoding"]
    assert "tooltip" not in base_layer_encoding, (
        "tooltip should live at spec level, not on the bar layer (avoids "
        "per-layer override which would block inheritance to strip layers)"
    )


def test_temporal_x_strip_anchor_depends_on_mark():
    # temporal+timeUnit: a BAR spans the time band, so its cell centers on the
    # band (0.5) + dx. A LINE/AREA point sits on its exact date (the grid tick),
    # so its cell must ride that per-point position — NO bandPosition. Anchoring
    # a line cell to the band offsets it half a band AND pulls the band _end into
    # the x-domain, widening the axis past the data.
    from dbt_charts.core.render.chart.support_table_attachment import _shared_x_encoding

    temporal_enc = {"field": "week", "type": "temporal", "timeUnit": "yearweek"}

    bar_enc = _shared_x_encoding(temporal_enc, mark_is_bar=True)
    assert bar_enc.get("bandPosition") == 0.5, (
        "a temporal+timeUnit BAR strip centers on the band (0.5) + dx"
    )

    line_enc = _shared_x_encoding(temporal_enc, mark_is_bar=False)
    assert "bandPosition" not in line_enc, (
        "a temporal+timeUnit line/area strip must ride the per-point position "
        "(no bandPosition) so cells land on the grid tick and the axis stays "
        "within the data range"
    )
    assert line_enc["timeUnit"] == "yearweek"


def test_attach_sampling_step_gt1_adds_window_filter_to_text_layers():
    # When sampling_step > 1, each text layer must carry a window + filter
    # transform chain. The bar/line base layer must NOT have the transform.

    spec = _temporal_spec()
    table = _table([ChartSupportTableSource(source="revenue")])
    step = math.ceil(
        97 / get_chart_rendering().support_table.chart_support_table_max_x_ticks
    )
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        sampling_step=step,
        axis_y_orient="right",
    )
    # Base chart layer has no sampling transform.
    base_layer = out["layer"][0]
    assert not any(
        "__support_table_row_index" in str(t) for t in base_layer.get("transform", [])
    )
    # Text layers carry the window + filter chain.
    text_layers = _cell_text_layers(out)
    assert text_layers
    for tl in text_layers:
        transforms = tl.get("transform", [])
        window_transforms = [t for t in transforms if "window" in t]
        filter_transforms = [t for t in transforms if "filter" in t]
        assert window_transforms, "text layer must have a window transform for sampling"
        assert filter_transforms, "text layer must have a filter transform for sampling"
        filter_expr = filter_transforms[-1]["filter"]
        assert "__support_table_row_index" in filter_expr
        assert str(step) in filter_expr


def test_attach_sampling_step_1_does_not_add_window_filter():
    # sampling_step=1 (default) must not add any window/filter to text layers.
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue")])
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        sampling_step=1,
        axis_y_orient="right",
    )
    text_layers = _cell_text_layers(out)
    for tl in text_layers:
        transforms = tl.get("transform", [])
        assert not any("window" in t for t in transforms)
        assert not any(
            "filter" in t and "__support_table_row_index" in str(t) for t in transforms
        )


def test_ordinal_x_strip_gets_explicit_band_position_0_5():
    """Ordinal strip x must explicitly emit bandPosition:0.5.

    bandPosition:0.5 anchors the text mark at the band center. Without it VL
    defaults to 0.5 too, but making it explicit pins behavior against future
    VL default changes and pairs with row_dxs to implement table-parity centering.
    """
    from dbt_charts.core.render.chart.support_table_attachment import _shared_x_encoding

    enc = _shared_x_encoding({"field": "product", "type": "ordinal"}, mark_is_bar=False)
    assert enc.get("bandPosition") == 0.5, (
        "ordinal strip x encoding must explicitly emit bandPosition:0.5"
    )


def test_nominal_x_strip_gets_explicit_band_position_0_5():
    """Nominal x (same band-scale category as ordinal) must also get bandPosition:0.5."""
    from dbt_charts.core.render.chart.support_table_attachment import _shared_x_encoding

    enc = _shared_x_encoding(
        {"field": "category", "type": "nominal"}, mark_is_bar=False
    )
    assert enc.get("bandPosition") == 0.5


def test_quantitative_x_strip_does_not_get_band_position():
    """Quantitative (continuous) x must not get bandPosition — not a band scale."""
    from dbt_charts.core.render.chart.support_table_attachment import _shared_x_encoding

    enc = _shared_x_encoding(
        {"field": "score", "type": "quantitative"}, mark_is_bar=False
    )
    assert "bandPosition" not in enc


def test_temporal_x_without_time_unit_does_not_get_band_position():
    """Temporal without timeUnit is a continuous scale — no bandPosition."""
    from dbt_charts.core.render.chart.support_table_attachment import _shared_x_encoding

    enc = _shared_x_encoding({"field": "date", "type": "temporal"}, mark_is_bar=False)
    assert "bandPosition" not in enc


def test_attach_aggregate_sampling_places_window_after_aggregate():
    # Regression: sampling window+filter must come AFTER the aggregate transform
    # for aggregate entries. Prepending before aggregate would sum only a subset
    # of multi-row-per-x data, producing silently wrong cell values.

    spec = _temporal_spec()
    table = _table([ChartSupportTableAggregate(aggregate="sum", source="revenue")])
    step = math.ceil(
        80 / get_chart_rendering().support_table.chart_support_table_max_x_ticks
    )
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        sampling_step=step,
        axis_y_orient="right",
    )
    text_layers = _cell_text_layers(out)
    assert text_layers
    for tl in text_layers:
        transforms = tl.get("transform", [])
        agg_idxs = [i for i, t in enumerate(transforms) if "aggregate" in t]
        window_idxs = [i for i, t in enumerate(transforms) if "window" in t]
        assert agg_idxs, "aggregate entry must have an aggregate transform"
        assert window_idxs, "aggregate entry with sampling must have a window transform"
        assert max(agg_idxs) < min(window_idxs), (
            "aggregate transform must precede the sampling window+filter so the "
            "aggregate sees all rows before sampling thins them"
        )


def test_ordinal_x_strip_text_includes_dx_for_band_centering():
    # Bug B: with align:right at bandPosition:0.5 the right edge of each cell
    # sits at the band center, making values look shifted left of the bar.
    # Fix: the text mark must carry a positive dx so the column center aligns
    # with the band midpoint (same invariant as _compute_lane_positions in
    # table.py: number_x = band_center + max_w / 2).

    spec = _base_spec()  # ordinal x
    table = _table([ChartSupportTableSource(source="revenue", format="$.2s")])
    dx_per_entry = [8.0]  # half of some measured max width
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        entry_dx=dx_per_entry,
        axis_y_orient="right",
    )
    text_layers = _cell_text_layers(out)
    assert len(text_layers) == 1
    mark = text_layers[0]["mark"]
    assert "dx" in mark, (
        "ordinal x text mark must carry dx when entry_dx is supplied so the "
        "number lane center aligns with the band midpoint"
    )
    assert mark["dx"] == 8.0


def test_attach_entry_dx_none_omits_dx_from_mark():
    # When entry_dx is None (no centering requested), dx must not appear in
    # the mark — this preserves the old behavior for callers that don't
    # supply widths (e.g. tests that don't have data).

    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue")])
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        entry_dx=None,
        axis_y_orient="right",
    )
    text_layers = _cell_text_layers(out)
    assert len(text_layers) == 1
    assert "dx" not in text_layers[0]["mark"], (
        "dx must be absent when entry_dx=None — compile layer has no measured widths"
    )


def test_per_series_expands_to_n_text_layers():
    """One per_series entry with 3 series expands to 3 text layers."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    spec = _per_series_spec()
    table = _table([ChartSupportTablePerSeries(per_series="revenue", format="$,.0f")])
    series = ["Apparel", "Electronics", "Home"]
    out = _attach_per_series(spec, table, series_order=series)
    text_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") != "__label"
    ]
    assert len(text_layers) == 3


def test_per_series_format_calculate_renders_invalid_values_as_dash():
    """Normal per_series rows must share the same missing-cell formatter."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    spec = _per_series_spec()
    table = _table([ChartSupportTablePerSeries(per_series="revenue", format="$,.0f")])
    out = _attach_per_series(spec, table, series_order=["Apparel"])
    text_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") != "__label"
    ]
    calc = next(
        t["calculate"] for t in text_layers[0].get("transform", []) if "calculate" in t
    )
    assert 'isValid(datum["__support_table_0_val"])' in calc
    assert 'isFinite(datum["__support_table_0_val"])' in calc
    assert "'-'" in calc


def test_per_series_no_format_calculate_renders_invalid_values_as_dash():
    """Normal per_series rows without format must still guard missing cells."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    spec = _per_series_spec()
    table = _table([ChartSupportTablePerSeries(per_series="revenue")])
    out = _attach_per_series(spec, table, series_order=["Apparel"])
    text_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") != "__label"
    ]
    calc = next(
        t["calculate"] for t in text_layers[0].get("transform", []) if "calculate" in t
    )
    assert 'isValid(datum["__support_table_0_val"])' in calc
    assert 'isFinite(datum["__support_table_0_val"])' in calc
    assert "'-'" in calc


def test_per_series_layers_in_stack_order():
    """Row layers appear in the provided series_order (stack order)."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    spec = _per_series_spec()
    table = _table([ChartSupportTablePerSeries(per_series="revenue")])
    # Provide explicit stack order (reverse alpha = VL's default stacked bar order).
    # Use explicit position=bottom so ordering direction (y ascending) is deterministic.
    series = ["Home", "Electronics", "Apparel"]
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=_dt_style(position="bottom"),
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        series_order=series,
        axis_y_orient="right",
        axis_offset_value=0.0,
    )
    text_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") != "__label"
    ]
    assert len(text_layers) == 3
    # For position:bottom, pixel y increases (rows go top to bottom, away from plot).
    ys = [layer["encoding"]["y"]["value"] for layer in text_layers]
    assert ys == sorted(ys), f"per_series rows must be ordered top-to-bottom, got {ys}"


def test_by_measure_format_calculate_renders_invalid_values_as_dash():
    """by_measure rows must not bypass the shared missing-cell formatter."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    spec = _per_series_spec()
    table = _table(
        [
            ChartSupportTablePerSeries(
                per_series="quarterly_revenue", by_measure=True, format="$,.0f"
            )
        ]
    )
    out = _attach_per_series(spec, table, series_order=[])
    text_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") != "__label"
    ]
    calc = next(
        t["calculate"] for t in text_layers[0].get("transform", []) if "calculate" in t
    )
    assert 'isValid(datum["quarterly_revenue"])' in calc
    assert 'isFinite(datum["quarterly_revenue"])' in calc
    assert "'-'" in calc


def test_per_series_each_layer_has_filter_transform_for_its_series():
    """Each per_series row layer must have a filter that selects its series."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    spec = _per_series_spec()
    table = _table([ChartSupportTablePerSeries(per_series="revenue")])
    series = ["Apparel", "Electronics"]
    out = _attach_per_series(spec, table, series_order=series)
    text_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") != "__label"
    ]
    assert len(text_layers) == 2
    filter_exprs = []
    for layer in text_layers:
        filters = [t for t in layer.get("transform", []) if "filter" in t]
        assert filters, "per_series row must have a filter transform"
        filter_exprs.append(filters[-1]["filter"])
    # Each filter must reference its respective series name.
    assert any("Apparel" in f for f in filter_exprs)
    assert any("Electronics" in f for f in filter_exprs)


def test_per_series_layer_has_groupby_aggregate_transform():
    """per_series rows must aggregate groupby [x, color] to avoid double-counting."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    spec = _per_series_spec()
    table = _table([ChartSupportTablePerSeries(per_series="revenue")])
    series = ["Apparel", "Electronics"]
    out = _attach_per_series(spec, table, series_order=series)
    text_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") != "__label"
    ]
    for layer in text_layers:
        agg_transforms = [t for t in layer.get("transform", []) if "aggregate" in t]
        assert agg_transforms, (
            "per_series row must have an aggregate transform groupby [x, color]"
        )
        groupby = agg_transforms[0]["groupby"]
        assert "month" in groupby
        assert "category" in groupby


def test_per_series_label_layers_have_colored_fill():
    """Each per_series label layer must carry the caller-supplied dark_fills entry.

    attach_support_table no longer resolves bright->dark-companion colors itself
    (that's baked once onto ResolvedChartsStyle.dark_companion_palette at
    resolve time — see
    dbt-charts/tests/core/compile/test_resolved_charts_style_dark_companion_palette.py);
    this only exercises that the already-resolved colors it's handed land on
    the right label layer, in order.
    """
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    spec = _per_series_spec()
    table = _table([ChartSupportTablePerSeries(per_series="revenue")])
    series = ["Apparel", "Electronics"]
    dark_fills = ["#111111", "#222222"]
    out = _attach_per_series(spec, table, series_order=series, dark_fills=dark_fills)
    # Label layers are text layers with __label field or with fill set per series.
    # For per_series, label layers embed data with __series field and have fill in mark.
    label_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(label_layers) == 2, (
        f"expected 2 series label layers, got {len(label_layers)}"
    )
    fills = [lyr["mark"].get("fill") for lyr in label_layers]
    assert fills == dark_fills


def test_per_series_right_orient_label_emits_right_cap():
    """Per_series rows with axis_y_orient=right emit series-name labels at the
    right-cap x position (spec_width + axis_y.labels.padding)."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable

    table = ChartSupportTable.model_validate({"entries": [{"per_series": "revenue"}]})
    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "ordinal"},
            "y": {"field": "revenue", "type": "quantitative"},
            "color": {"field": "category", "type": "nominal"},
        },
        "height": 200,
        "width": 400,
    }
    cs = _charts_style()
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=cs,
        axis_label_padding=_axis_label_padding(cs),
        series_order=["A", "B"],
        dark_fills=["#00c8ee", "#00ad75"],
        axis_y_orient="right",
    )
    label_layers = [
        layer
        for layer in out["layer"]
        if layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(label_layers) == 2, (
        f"expected 2 label layers (one per series), got {len(label_layers)}"
    )
    for layer in label_layers:
        x_val = layer["encoding"]["x"]["value"]
        assert x_val >= spec["width"], (
            f"per_series right-orient label must be at x >= spec_width={spec['width']}; "
            f"got x={x_val}"
        )


def test_per_series_emits_inter_row_rules_within_block():
    """The strip's height accounting reserves inter-row spacing for every
    adjacent visual pair (`row.rule.width * (n_rows - 1)`). The per_series
    branch must emit a rule between each pair of expanded series rows,
    not just at the trailing seam — otherwise rule-on themes show gaps
    where rules should be drawn within the per_series block."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable

    base_style = _dt_style()
    style_with_rules = base_style.model_copy(
        update={
            "row": base_style.row.model_copy(
                update={"rule": RuleStyle(width=1.0, color="#888888", continuous=True)}
            )
        }
    )
    table = ChartSupportTable.model_validate({"entries": [{"per_series": "revenue"}]})
    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "ordinal"},
            "y": {"field": "revenue", "type": "quantitative"},
            "color": {"field": "category", "type": "nominal"},
        },
        "height": 200,
        "width": 400,
    }
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=style_with_rules,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        series_order=["A", "B", "C"],
        dark_fills=None,
        axis_y_orient="right",
    )
    rule_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "rule"
    ]
    # Default divider.width is 0 so no divider rule. Expect 2 within-block
    # rules between the 3 series rows. No trailing rule because per_series
    # is the last entry.
    assert len(rule_layers) == 2, (
        f"expected 2 within-block rules between the 3 per_series rows; "
        f"got {len(rule_layers)} rule layers"
    )


def test_per_series_sampling_consistent_across_series():
    """Sampling must produce the same x positions for every series in a
    per_series block. Sampling-before-filter would window over the
    (x, series) cross-product where ties on x have unspecified row-number
    order, so series A could keep x1/x3/x5 while series B keeps x2/x4/x6.
    Pin the transform order so the per-series filter runs first."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable

    table = ChartSupportTable.model_validate({"entries": [{"per_series": "revenue"}]})
    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "temporal"},
            "y": {"field": "revenue", "type": "quantitative"},
            "color": {"field": "category", "type": "nominal"},
        },
        "height": 200,
        "width": 400,
    }
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=_dt_style(),
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        sampling_step=2,
        series_order=["A", "B"],
        dark_fills=None,
        axis_y_orient="right",
    )
    text_layers = _cell_text_layers(out)
    # For each per_series cell layer, find the indices of its filter and
    # window transforms. The filter must come BEFORE the window+filter
    # sampling chain so each series samples its own 1-row-per-x stream.
    saw_per_series = False
    for layer in text_layers:
        transforms = layer.get("transform", [])
        filter_idx = next(
            (
                i
                for i, t in enumerate(transforms)
                if "filter" in t
                and isinstance(t.get("filter"), str)
                and "===" in t["filter"]
            ),
            None,
        )
        window_idx = next((i for i, t in enumerate(transforms) if "window" in t), None)
        if filter_idx is None or window_idx is None:
            continue
        saw_per_series = True
        assert filter_idx < window_idx, (
            "per-series filter must run BEFORE the sampling window so each "
            "series samples its own 1-row-per-x stream — sampling-before-filter "
            "windows over the (x, series) cross-product with unspecified "
            f"tie-order. Got filter at index {filter_idx}, window at {window_idx}."
        )
    assert saw_per_series, "expected at least one per_series text layer"


def test_per_series_label_stub_layers_have_color_none_in_encoding():
    """Label stub layers must have encoding.color = None.

    Without this override VL inherits the top-level color encoding, adds null
    to the categorical domain for layers whose own data lacks the series field,
    and shifts palette[0] to null — causing dark-companion off-by-one.
    """
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable

    spec = _per_series_layered_spec()
    table = ChartSupportTable.model_validate({"entries": [{"per_series": "revenue"}]})
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        series_order=["Alpha", "Beta"],
        dark_fills=["#00c8ee", "#00ad75"],
        axis_y_orient="right",
    )
    # The outer spec is still layered; collect all layers recursively at depth=1.
    all_layers = out.get("layer", [])
    label_stub_layers = [
        layer
        for layer in all_layers
        if layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert label_stub_layers, (
        "expected at least one label stub layer in per_series output"
    )
    for layer in label_stub_layers:
        encoding_color = layer.get("encoding", {}).get("color", "__missing__")
        assert encoding_color is None, (
            f"label stub layer must have encoding.color=None to prevent VL from "
            f"adding null to the categorical color domain; got {encoding_color!r}"
        )


def test_per_series_label_stub_left_orient_has_color_none():
    """Left-orient label stubs (axis_y_orient=left) must also carry encoding.color=None."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable

    spec = _per_series_layered_spec()
    table = ChartSupportTable.model_validate({"entries": [{"per_series": "revenue"}]})
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        series_order=["Alpha", "Beta"],
        dark_fills=["#00c8ee", "#00ad75"],
        axis_y_orient="left",
    )
    all_layers = out.get("layer", [])
    label_stub_layers = [
        layer
        for layer in all_layers
        if layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert label_stub_layers, "expected at least one label stub layer"
    for layer in label_stub_layers:
        encoding_color = layer.get("encoding", {}).get("color", "__missing__")
        assert encoding_color is None, (
            f"left-orient label stub must have encoding.color=None; "
            f"got {encoding_color!r}"
        )


def test_aggregate_cell_layers_have_color_none_in_encoding():
    """Aggregate cell layers must have encoding.color = None.

    Without this override VL inherits the top-level color encoding, sees null
    for the series field in post-aggregate data (groupby does not include the
    color field), adds null to the categorical domain, and shifts palette[0]
    to null — causing endpoint label and support-table dark-companion off-by-one.
    """
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable

    spec = _per_series_layered_spec()
    table = ChartSupportTable.model_validate(
        {
            "entries": [
                {
                    "aggregate": "sum",
                    "source": "revenue",
                    "label": "Total",
                }
            ]
        }
    )
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        axis_y_orient="right",
    )
    all_layers = out.get("layer", [])
    # Aggregate cell layers have a transform list that contains an "aggregate" key.
    agg_cell_layers = [
        layer
        for layer in all_layers
        if any("aggregate" in t for t in layer.get("transform", []))
    ]
    assert agg_cell_layers, "expected at least one aggregate cell layer in output"
    for layer in agg_cell_layers:
        encoding_color = layer.get("encoding", {}).get("color", "__missing__")
        assert encoding_color is None, (
            f"aggregate cell layer must have encoding.color=None to prevent VL "
            f"from adding null to the categorical color domain; got {encoding_color!r}"
        )


def test_attached_layers_have_description_none_in_encoding():
    """Regression: dbt-labs/dbt-charts#29.

    ``attach_support_table`` runs as a post-pass on the already-assembled
    Vega-Lite spec. When the base chart is already multi-layered by the time
    it gets there (line/area's halo/hover-point trio, a combo overlay, ...),
    the structured tooltip feature has already stamped ``description`` on
    the shared OUTER encoding sibling to ``layer:`` — every sibling layer
    inherits it. A support_table cell's own transform (aggregate/window/
    calculate) produces a datum shaped nothing like the base chart's own
    row, so the inherited description expr reads undefined color/series/
    value fields off it, rendering a phantom "undefined NaN" row in the
    x-unified tooltip on hover.
    """
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable

    spec = _per_series_layered_spec()
    spec["encoding"]["description"] = {
        "value": {"expr": "'⁡' + datum.month + ';⁢' + datum.series"}
    }
    table = ChartSupportTable.model_validate(
        {"entries": [{"aggregate": "sum", "source": "revenue", "label": "Total"}]}
    )
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        axis_y_orient="right",
    )
    all_layers = out.get("layer", [])
    assert len(all_layers) > 1, "expected support_table attachment to append layers"
    # This fixture's base is a single layer, so layer 0 is the base chart's
    # own wrapped mark and every layer after it is a strip decoration --
    # see test_multi_layer_base_chart_keeps_its_own_description_untouched
    # for the multi-base-layer case, where that split is NOT by index 1.
    for layer in all_layers[1:]:
        encoding_description = layer.get("encoding", {}).get(
            "description", "__missing__"
        )
        assert encoding_description is None, (
            "support_table-attached layer must have encoding.description=None "
            "to prevent it from inheriting the chart's structured-tooltip "
            "description and showing a phantom undefined/NaN row on hover "
            f"(dbt-labs/dbt-charts#29); got {encoding_description!r}"
        )


def test_multi_layer_base_chart_keeps_its_own_description_untouched():
    """Regression: dbt-labs/dbt-charts#29 follow-up.

    ``_wrap_base_as_layer`` returns an already-layered spec UNCHANGED (see
    its own docstring), so a real line/area chart's multi-layer base (halo,
    foreground stroke, invisible hover-point overlay -- see
    ``emitters/_layers.py``'s ``emit_line_layer``) arrives at
    ``attach_support_table`` as MULTIPLE pre-existing layers, not just one.
    A fix that scopes its ``description: None`` override by *index* (e.g.
    "every layer but the first") instead of by *which layers this module
    itself appended* strips the real, correct description off the base
    chart's own 2nd/3rd layers too -- deleting real x-unified tooltip rows,
    not just the phantom one. This asserts every ORIGINAL base layer is
    returned untouched: one relying on the inherited shared description (no
    ``description`` key of its own) keeps relying on it, and one carrying
    its OWN explicit description (a combo overlay -- see
    ``translate.py``'s ``_translate_layer``) keeps that value verbatim.
    """
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable

    spec = _multi_base_layer_spec()
    table = ChartSupportTable.model_validate(
        {"entries": [{"aggregate": "sum", "source": "revenue", "label": "Total"}]}
    )
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        axis_y_orient="right",
    )
    all_layers = out.get("layer", [])
    assert len(all_layers) > 3, "expected support_table attachment to append layers"

    halo, foreground, overlay = all_layers[0], all_layers[1], all_layers[2]
    for base_layer in (halo, foreground):
        assert "description" not in base_layer.get("encoding", {}), (
            "a base layer relying on the inherited shared description must "
            f"not gain its own encoding.description override; got {base_layer}"
        )
    overlay_description = overlay.get("encoding", {}).get("description")
    assert overlay_description == {"value": {"expr": "'overlay-own-description'"}}, (
        "a base layer's OWN explicit description (e.g. a combo overlay) must "
        f"survive unchanged; got {overlay_description!r}"
    )

    # Every layer support_table attachment itself appended still opts out.
    for appended_layer in all_layers[3:]:
        appended_description = appended_layer.get("encoding", {}).get(
            "description", "__missing__"
        )
        assert appended_description is None, (
            "support_table-attached layer must have encoding.description=None; "
            f"got {appended_description!r}"
        )


def test_right_orient_label_anchors_at_spec_width_plus_axis_padding():
    """Right-oriented axis: row label x = spec_width + axis_y.labels.padding, align=left."""
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue", label="Revenue")])
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        axis_y_orient="right",
    )
    label_layers = [
        layer
        for layer in out["layer"]
        if layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(label_layers) == 1
    layer = label_layers[0]
    axis_pad = resolved_axis_style(
        _charts_style(), "axis_y", "quantitative", chart_type="", label_authored=False
    ).labels.padding
    expected_x = spec["width"] + axis_pad
    assert layer["encoding"]["x"]["value"] == expected_x, (
        f"right-oriented label x must be spec_width+axis_y.labels.padding="
        f"{expected_x}; got {layer['encoding']['x']['value']}"
    )
    assert layer["mark"]["align"] == "left", (
        "right-oriented axis tick labels use text-anchor start (left-align); "
        f"row label must match; got align={layer['mark']['align']}"
    )


def test_left_orient_label_anchors_at_negative_axis_padding():
    """Left-oriented axis: row label x = -axis_y.labels.padding, align=right."""
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue", label="Revenue")])
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        axis_y_orient="left",
    )
    label_layers = [
        layer
        for layer in out["layer"]
        if layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(label_layers) == 1
    layer = label_layers[0]
    axis_pad = resolved_axis_style(
        _charts_style(), "axis_y", "quantitative", chart_type="", label_authored=False
    ).labels.padding
    expected_x = -axis_pad
    assert layer["encoding"]["x"]["value"] == expected_x, (
        f"left-oriented label x must be -axis_y.labels.padding={expected_x}; "
        f"got {layer['encoding']['x']['value']}"
    )
    assert layer["mark"]["align"] == "right", (
        "left-oriented axis tick labels use text-anchor end (right-align); "
        f"row label must match; got align={layer['mark']['align']}"
    )


def test_right_orient_label_sets_mark_limit():
    """Right-cap label mark.limit must equal _LABEL_STUB_LIMIT_PX (readable, no legend reach)."""
    from dbt_charts.core.render.chart.support_table_attachment import (
        _LABEL_STUB_LIMIT_PX,
    )

    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue", label="Revenue")])
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        axis_y_orient="right",
    )
    label_layers = [
        layer
        for layer in out["layer"]
        if layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(label_layers) == 1
    mark = label_layers[0]["mark"]
    assert "limit" in mark, (
        "right-cap label mark must have 'limit' to prevent autosize shrink"
    )
    assert mark["limit"] == _LABEL_STUB_LIMIT_PX, (
        f"mark.limit must be _LABEL_STUB_LIMIT_PX={_LABEL_STUB_LIMIT_PX}; "
        f"got {mark['limit']}"
    )


def test_left_orient_label_sets_mark_limit():
    """Left-cap label mark.limit must equal _LABEL_STUB_LIMIT_PX (readable, no legend reach)."""
    from dbt_charts.core.render.chart.support_table_attachment import (
        _LABEL_STUB_LIMIT_PX,
    )

    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue", label="Revenue")])
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        axis_y_orient="left",
    )
    label_layers = [
        layer
        for layer in out["layer"]
        if layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(label_layers) == 1
    mark = label_layers[0]["mark"]
    assert "limit" in mark, (
        "left-cap label mark must have 'limit' to prevent autosize shrink"
    )
    assert mark["limit"] == _LABEL_STUB_LIMIT_PX, (
        f"mark.limit must be _LABEL_STUB_LIMIT_PX={_LABEL_STUB_LIMIT_PX}; "
        f"got {mark['limit']}"
    )


def test_default_card_padding_right_cap_label_is_readable():
    """Right-cap label with default card_padding (16 px) must NOT truncate to '...'.

    Pre-fix (7c9a0541d): mark.limit = max(1, padding[right] - axis_pad) = max(1, 16 - 6) = 10 px.
                         Any label text exceeds 10 px and renders as '...'.
    Fix: mark.limit = _LABEL_STUB_LIMIT_PX (80 px) — readable for all typical row labels
         without reaching into the right-side legend zone.
    """
    from dbt_charts.core.render.chart.support_table_attachment import (
        _LABEL_STUB_LIMIT_PX,
    )

    spec = _base_spec()  # spec_width = 600
    table = _table([ChartSupportTableSource(source="revenue", label="Revenue")])
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        axis_y_orient="right",
    )
    label_layers = [
        layer
        for layer in out["layer"]
        if layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(label_layers) == 1
    mark = label_layers[0]["mark"]
    limit = mark.get("limit")
    assert limit is not None, "right-cap label mark must have 'limit'"
    assert limit >= 50, (
        f"mark.limit={limit} is too small — label text would truncate to '...'. "
        f"Expected _LABEL_STUB_LIMIT_PX={_LABEL_STUB_LIMIT_PX}."
    )


def test_per_series_right_orient_label_anchors_at_right_cap():
    """Per-series labels with axis_y_orient=right emit right-cap x >= spec_width."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable

    table = ChartSupportTable.model_validate({"entries": [{"per_series": "revenue"}]})
    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "ordinal"},
            "y": {"field": "revenue", "type": "quantitative"},
            "color": {"field": "category", "type": "nominal"},
        },
        "height": 200,
        "width": 400,
    }
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        series_order=["A", "B"],
        dark_fills=["#00c8ee", "#00ad75"],
        axis_y_orient="right",
    )
    label_layers = [
        layer
        for layer in out["layer"]
        if layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(label_layers) == 2
    for layer in label_layers:
        assert layer["encoding"]["x"]["value"] >= spec["width"], (
            f"per_series right-orient label x must be >= spec_width={spec['width']}; "
            f"got {layer['encoding']['x']['value']}"
        )
        assert layer["mark"]["align"] == "left"


def test_per_series_left_orient_label_anchors_at_left_gutter():
    """Per-series labels with axis_y_orient=left emit negative x (left gutter)."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTable

    table = ChartSupportTable.model_validate({"entries": [{"per_series": "revenue"}]})
    spec = {
        "mark": {"type": "bar"},
        "encoding": {
            "x": {"field": "month", "type": "ordinal"},
            "y": {"field": "revenue", "type": "quantitative"},
            "color": {"field": "category", "type": "nominal"},
        },
        "height": 200,
        "width": 400,
    }
    out = attach_support_table(
        spec,
        support_table=table,
        entry_numerals=plain_numerals(table, None, "Inter", [[]] * len(table.entries)),
        style=get_theme_style().charts.support_table,
        charts_style=_charts_style(),
        axis_label_padding=_axis_label_padding(_charts_style()),
        series_order=["A", "B"],
        dark_fills=["#00c8ee", "#00ad75"],
        axis_y_orient="left",
    )
    label_layers = [
        layer
        for layer in out["layer"]
        if layer.get("encoding", {}).get("text", {}).get("field") == "__label"
    ]
    assert len(label_layers) == 2
    for layer in label_layers:
        assert layer["encoding"]["x"]["value"] < 0, (
            "per_series left-orient label x must be negative (in left gutter); "
            f"got {layer['encoding']['x']['value']}"
        )
        assert layer["mark"]["align"] == "right"


def test_attach_layered_spec_falls_back_to_first_layer_x():
    """When top-level encoding lacks x but every layer carries the same x, the
    attachment anchors the strip to the shared layer-x field."""
    spec = {
        "encoding": {},
        "height": 300,
        "width": 600,
        "layer": [
            {
                "mark": {"type": "bar"},
                "encoding": {
                    "x": {"field": "month", "type": "ordinal"},
                    "y": {"field": "revenue", "type": "quantitative"},
                },
            },
            {
                "mark": {"type": "bar"},
                "encoding": {
                    "x": {"field": "month", "type": "ordinal"},
                    "y": {"field": "target", "type": "quantitative"},
                },
            },
        ],
    }
    table = _table([ChartSupportTableSource(source="revenue", format="$.2s")])
    out = _attach(spec, table)
    text_layers = _cell_text_layers(out)
    assert len(text_layers) == 1
    assert text_layers[0]["encoding"]["x"]["field"] == "month"


def test_attach_layered_spec_with_no_x_anywhere_raises_chart_data_error():
    """No top-level x and no layer carries x → ChartDataError (no anchor)."""
    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    spec = {
        "encoding": {},
        "height": 300,
        "width": 600,
        "layer": [
            {
                "mark": {"type": "bar"},
                "encoding": {"y": {"field": "revenue", "type": "quantitative"}},
            },
        ],
    }
    table = _table([ChartSupportTableSource(source="revenue", format="$.2s")])
    with pytest.raises(ChartDataError, match="x-encoding"):
        _attach(spec, table)


def test_attach_layered_spec_with_disagreeing_layer_x_raises_chart_data_error():
    """Layers carrying different x fields → ChartDataError (refuses silent
    misalignment). Layer-0's x must not become the anchor when layer-1 uses a
    different x field; otherwise the strip would land under x-buckets that
    don't exist on the other layer's dataset."""
    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    spec = {
        "encoding": {},
        "height": 300,
        "width": 600,
        "layer": [
            {
                "mark": {"type": "bar"},
                "encoding": {
                    "x": {"field": "month", "type": "ordinal"},
                    "y": {"field": "revenue", "type": "quantitative"},
                },
            },
            {
                "mark": {"type": "bar"},
                "encoding": {
                    "x": {"field": "week", "type": "ordinal"},
                    "y": {"field": "signups", "type": "quantitative"},
                },
            },
        ],
    }
    table = _table([ChartSupportTableSource(source="revenue", format="$.2s")])
    with pytest.raises(ChartDataError, match="per-layer"):
        _attach(spec, table)


def test_attach_layered_spec_with_independent_x_scale_raises_chart_data_error():
    """resolve.scale.x == 'independent' means per-layer datasets; no shared x →
    ChartDataError. Even if layers happen to spell the same x field name, the
    independent scale means the strip cannot anchor coherently."""
    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    spec = {
        "encoding": {},
        "height": 300,
        "width": 600,
        "resolve": {"scale": {"x": "independent"}},
        "layer": [
            {
                "mark": {"type": "bar"},
                "encoding": {
                    "x": {"field": "month", "type": "ordinal"},
                    "y": {"field": "revenue", "type": "quantitative"},
                },
            },
            {
                "mark": {"type": "bar"},
                "encoding": {
                    "x": {"field": "month", "type": "ordinal"},
                    "y": {"field": "target", "type": "quantitative"},
                },
            },
        ],
    }
    table = _table([ChartSupportTableSource(source="revenue", format="$.2s")])
    with pytest.raises(ChartDataError, match="independent"):
        _attach(spec, table)


def test_bottom_position_row_y_is_below_spec_height():
    # position: bottom must keep the current behavior: strip below the plot.
    # Row y > spec_height (300 from _base_spec) is the invariant.
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue")])
    style = _dt_style(position="bottom")
    out = _attach(spec, table, style=style, axis_offset_value=0.0)
    text_layers = _cell_text_layers(out)
    assert len(text_layers) >= 1
    for tl in text_layers:
        assert tl["encoding"]["y"]["value"] > spec["height"], (
            f"position:bottom row y {tl['encoding']['y']['value']} "
            f"must be > spec_height {spec['height']}"
        )


def test_top_position_row_y_is_above_plot():
    # position: top must place every row ABOVE the plot (negative y in spec coords).
    spec = _base_spec()
    table = _table([ChartSupportTableSource(source="revenue")])
    style = _dt_style(position="top")
    out = _attach(spec, table, style=style)
    text_layers = [
        layer
        for layer in out["layer"]
        if isinstance(layer.get("mark"), dict) and layer["mark"].get("type") == "text"
    ]
    assert len(text_layers) >= 1
    for tl in text_layers:
        assert tl["encoding"]["y"]["value"] < 0, (
            f"position:top row y {tl['encoding']['y']['value']} must be "
            "negative (above plot)"
        )


def test_top_position_multiple_rows_have_distinct_pixel_y():
    # Each row must still occupy a distinct y coordinate when position is top.
    spec = _base_spec()
    table = _table(
        [
            ChartSupportTableSource(source="a"),
            ChartSupportTableSource(source="b"),
            ChartSupportTableSource(source="c"),
        ]
    )
    style = _dt_style(position="top")
    out = _attach(spec, table, style=style)
    text_layers = _cell_text_layers(out)
    assert len(text_layers) == 3
    ys = [layer["encoding"]["y"]["value"] for layer in text_layers]
    assert len(set(ys)) == 3, f"rows must have distinct pixel y values, got {ys}"
    # All rows above the plot (negative y in Vega-Lite plot coords).
    assert all(y < 0 for y in ys), f"all top-position rows must have y < 0, got {ys}"
    # Row 0 is closest to the plot top (least negative y = bottom of top strip).
    # Row n-1 is furthest above the plot (most negative y = top of top strip).
    # y decreases (becomes more negative) as index increases.
    assert ys == sorted(ys, reverse=True), (
        f"row pixel-y must decrease by index (more negative = further from plot), got {ys}"
    )


def test_top_position_inter_row_rule_y_is_between_adjacent_rows():
    """Inter-row rules for position:top must land between the two rows they separate.

    For top, all row y values are negative (above the plot). The rule between
    row 0 (closest to plot, least negative) and row 1 (further above, more negative)
    must have a y value strictly between them: row1_y < rule_y < row0_y.

    This is a regression test for the bug where the formula added +row_h/2
    instead of subtracting it, producing a rule between the divider and row 0
    rather than between row 0 and row 1.
    """
    from dbt_charts.core.render.chart.support_table_attachment import (
        _inter_row_rule_y_pixel,
        _row_y_pixel,
    )

    style = _dt_style(
        position="top",
        row=_dt_style().row.model_copy(
            update={"rule": RuleStyle(width=1.0, continuous=True)}
        ),
    )
    cs = _charts_style()
    spec_height = 300.0

    row0_y = _row_y_pixel(0, style, cs, spec_height)
    row1_y = _row_y_pixel(1, style, cs, spec_height)
    rule_y = _inter_row_rule_y_pixel(0, style, cs, spec_height)

    # For position:top, rows go upward (more negative): row1 is above row0.
    assert row1_y < row0_y < 0, (
        f"top position: row1_y={row1_y} must be more negative than row0_y={row0_y}"
    )
    assert row1_y < rule_y < row0_y, (
        f"inter-row rule y={rule_y} must be between row1={row1_y} and row0={row0_y}"
    )


def test_value_cells_align_toward_label_column():
    """Value alignment mirrors the label side: labels on the left (axis_y orient
    left) lean values left toward them; labels on the right lean values right."""
    table = _table(
        [ChartSupportTableAggregate(source="revenue", aggregate="sum", format="$,.0f")]
    )
    left_cell = _cell_text_layers(_attach(_base_spec(), table, axis_y_orient="left"))[0]
    right_cell = _cell_text_layers(_attach(_base_spec(), table, axis_y_orient="right"))[
        0
    ]
    assert left_cell["mark"]["align"] == "left"
    assert right_cell["mark"]["align"] == "right"


def test_per_series_value_cells_align_toward_label_column():
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    table = _table([ChartSupportTablePerSeries(per_series="revenue", format="$,.0f")])
    left = _cell_text_layers(
        _attach_per_series(
            _per_series_spec(), table, series_order=["Apparel"], axis_y_orient="left"
        )
    )[0]
    assert left["mark"]["align"] == "left"


def test_per_series_labels_emitted_by_default():
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    table = _table([ChartSupportTablePerSeries(per_series="revenue")])
    out = _attach_per_series(
        _per_series_spec(), table, series_order=["Apparel", "Home"]
    )
    assert len(_label_layers(out)) == 2


def test_per_series_labels_suppressed_when_redundant():
    """When the chart already shows series names (legend / endpoints), the
    per_series row labels duplicate them and must be omitted — cells stay."""
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    table = _table([ChartSupportTablePerSeries(per_series="revenue")])
    out = _attach_per_series(
        _per_series_spec(),
        table,
        series_order=["Apparel", "Home"],
        suppress_series_labels=True,
    )
    assert _label_layers(out) == []
    assert len(_cell_text_layers(out)) == 2


def test_by_measure_label_suppressed_when_redundant():
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries

    table = _table([ChartSupportTablePerSeries(per_series="revenue", by_measure=True)])
    out = _attach_per_series(
        _per_series_spec(),
        table,
        series_order=["revenue"],
        suppress_series_labels=True,
    )
    assert _label_layers(out) == []
    assert len(_cell_text_layers(out)) == 1


def test_aggregate_label_not_suppressed_by_series_label_flag():
    """suppress_series_labels only gates per_series (series-name) labels.
    Aggregate/source labels are metric names absent from any legend — kept."""
    table = _table(
        [ChartSupportTableAggregate(source="revenue", aggregate="sum", label="Total")]
    )
    out = _attach(_base_spec(), table, suppress_series_labels=True)
    assert len(_label_layers(out)) == 1


def test_support_table_strip_height_returns_zero_for_no_table():
    assert support_table_strip_height(None, _dt_style(), _charts_style()) == 0.0


def test_support_table_strip_height_scales_with_row_count():
    style = _dt_style()
    cs = _charts_style()
    one = support_table_strip_height(
        _table([ChartSupportTableSource(source="a")]), style, cs
    )
    three = support_table_strip_height(
        _table(
            [
                ChartSupportTableSource(source="a"),
                ChartSupportTableSource(source="b"),
                ChartSupportTableSource(source="c"),
            ]
        ),
        style,
        cs,
    )
    assert one > 0
    assert three > one


def test_support_table_strip_height_includes_axis_offset_for_bottom():
    # For position:bottom, the reservation must cover the gap between plot and strip
    # (axis labels + axis title), not just the strip rows themselves —
    # otherwise padding.bottom would under-allocate and the strip would
    # land in space the layout planner did not reserve.
    from dbt_charts.core.compile.support_table import axis_offset
    from dbt_charts.core.render.chart.support_table_attachment import _strip_height

    style = _dt_style(position="bottom")
    cs = _charts_style()
    table = _table([ChartSupportTableSource(source="a")])
    h = support_table_strip_height(
        table, style, axis_offset(cs, style, x_label_authored=False, chart_type="")
    )
    expected = axis_offset(
        cs, style, x_label_authored=False, chart_type=""
    ) + _strip_height(style, 1)
    assert h == expected
    assert (
        axis_offset(cs, style, x_label_authored=False, chart_type="") > 0
    )  # default theme has visible x-axis labels


def test_per_series_height_budget_counts_n_rows():
    """support_table_strip_height must count per_series as N rows, not 1."""
    style = _dt_style()
    cs = _charts_style()

    # 3 normal source rows.
    three_source = support_table_strip_height(
        _table(
            [
                ChartSupportTableSource(source="a"),
                ChartSupportTableSource(source="b"),
                ChartSupportTableSource(source="c"),
            ]
        ),
        style,
        cs,
    )

    # 1 per_series entry that expands to 3 rows.
    one_per_series = support_table_strip_height(
        _table([ChartSupportTablePerSeries(per_series="revenue")]),
        style,
        cs,
        series_count=3,
    )

    assert one_per_series == three_source, (
        f"per_series with 3 series must reserve same height as 3 source rows; "
        f"got per_series={one_per_series:.1f}, three_source={three_source:.1f}"
    )


def test_per_series_height_raises_without_series_count():
    """Silent fallback to 1 row would under-size the strip and clip
    the expanded rows below the reserved padding — must raise instead."""
    from dbt_charts.core.render.errors import RenderError

    style = _dt_style()
    cs = _charts_style()
    table = _table([ChartSupportTablePerSeries(per_series="revenue")])
    with pytest.raises(RenderError, match="series_count"):
        support_table_strip_height(table, style, cs)
    with pytest.raises(RenderError, match="series_count"):
        support_table_strip_height(table, style, cs, series_count=0)


def test_bottom_position_strip_height_drives_padding_bottom():
    # support_table_strip_height for position:bottom returns axis_offset + strip_height.
    from dbt_charts.core.compile.support_table import axis_offset
    from dbt_charts.core.render.chart.support_table_attachment import _strip_height

    style = _dt_style(position="bottom")
    cs = _charts_style()
    table = _table([ChartSupportTableSource(source="a")])
    h = support_table_strip_height(
        table, style, axis_offset(cs, style, x_label_authored=False, chart_type="")
    )
    expected = axis_offset(
        cs, style, x_label_authored=False, chart_type=""
    ) + _strip_height(style, 1)
    assert h == expected
    assert (
        axis_offset(cs, style, x_label_authored=False, chart_type="") > 0
    )  # default theme has visible x-axis labels


def test_top_position_strip_height_excludes_axis_offset():
    # For position: top, the x-axis is below the plot — no axis gap is needed
    # above the strip. support_table_strip_height must return _strip_height only.
    from dbt_charts.core.compile.support_table import axis_offset
    from dbt_charts.core.render.chart.support_table_attachment import _strip_height

    style = _dt_style(position="top")
    cs = _charts_style()
    bottom_style = _dt_style(position="bottom")
    table = _table([ChartSupportTableSource(source="a")])
    h = support_table_strip_height(table, style, None)
    # Must equal strip_height alone — no axis_offset contribution.
    assert h == _strip_height(style, 1)
    # And must be less than the bottom-position equivalent.
    bottom_h = support_table_strip_height(
        table,
        bottom_style,
        axis_offset(cs, bottom_style, x_label_authored=False, chart_type=""),
    )
    assert h < bottom_h, (
        f"top-position strip height {h} should be < bottom-position {bottom_h} "
        f"(no axis_offset for top)"
    )


def test_strip_height_and_row_y_use_label_max_lines():
    # support_table_strip_height and _row_y_pixel must both honor label_max_lines
    # for position:bottom (the x-axis offset is between plot and strip).
    # For position:top, label_max_lines does not affect placement (axis is below).
    from dbt_charts.core.compile.support_table import axis_offset
    from dbt_charts.core.render.chart.support_table_attachment import _row_y_pixel

    cs = _charts_style()
    # Explicitly use position=bottom since label_max_lines only affects bottom placement.
    style_2 = _dt_style(position="bottom")
    style_1 = style_2.model_copy(update={"label_max_lines": 1})
    table = _table([ChartSupportTableSource(source="a")])
    spec_height = 300.0

    height_2 = support_table_strip_height(
        table, style_2, axis_offset(cs, style_2, x_label_authored=False, chart_type="")
    )
    height_1 = support_table_strip_height(
        table, style_1, axis_offset(cs, style_1, x_label_authored=False, chart_type="")
    )

    label_size = resolved_axis_style(
        cs, "axis_x", "band", chart_type="", label_authored=False
    ).labels.font.size
    assert abs((height_2 - height_1) - label_size) < 0.5, (
        "strip_height with label_max_lines=2 must exceed label_max_lines=1 "
        f"by exactly one label line ({label_size}px); "
        f"got diff={height_2 - height_1:.1f}"
    )

    y2 = _row_y_pixel(
        0,
        style_2,
        axis_offset(cs, style_2, x_label_authored=False, chart_type=""),
        spec_height,
    )
    y1 = _row_y_pixel(
        0,
        style_1,
        axis_offset(cs, style_1, x_label_authored=False, chart_type=""),
        spec_height,
    )
    assert abs((y2 - y1) - label_size) < 0.5, (
        "row y-pixel with label_max_lines=2 must be lower than label_max_lines=1 "
        f"by exactly one label line ({label_size}px); got diff={y2 - y1:.1f}"
    )


def _big_ordinal_data(n: int) -> list[dict[str, str | float]]:
    return [{"month": f"M{i:02d}", "revenue": float(i)} for i in range(n)]


def _big_quant_data(n: int) -> list[dict[str, float]]:
    return [{"x_val": float(i), "revenue": float(i)} for i in range(n)]


def _big_temporal_data(n: int) -> list[dict[str, str | float]]:
    """n distinct date strings, one row each."""
    import datetime

    start = datetime.date(2016, 1, 1)
    return [
        {
            "date": (start + datetime.timedelta(days=30 * i)).isoformat(),
            "revenue": float(i),
        }
        for i in range(n)
    ]


def test_validate_temporal_over_40_returns_step_and_does_not_raise():
    # Temporal x with 97 rows: validator passes and returns step > 1.
    from dbt_charts.core.render.chart.support_table_attachment import (
        validate_support_table_against_data,
    )

    data = _big_temporal_data(97)
    table = _table([ChartSupportTableSource(source="revenue")])
    step = validate_support_table_against_data(table, "date", data, x_type="temporal")
    assert step == math.ceil(
        97 / get_chart_rendering().support_table.chart_support_table_max_x_ticks
    )
    assert step > 1


def test_validate_quantitative_over_40_returns_step_and_does_not_raise():
    # Quantitative x with 80 rows: same path as temporal.
    from dbt_charts.core.render.chart.support_table_attachment import (
        validate_support_table_against_data,
    )

    data = _big_quant_data(80)
    table = _table([ChartSupportTableSource(source="revenue")])
    step = validate_support_table_against_data(
        table, "x_val", data, x_type="quantitative"
    )
    assert step == math.ceil(
        80 / get_chart_rendering().support_table.chart_support_table_max_x_ticks
    )
    assert step > 1


def test_validate_ordinal_over_40_still_raises():
    # Ordinal x with 50 rows: fail-closed behavior must remain.
    from dbt_charts.core.render.chart.support_table_attachment import (
        validate_support_table_against_data,
    )
    from dbt_charts.core.render.errors import RenderError

    data = _big_ordinal_data(50)
    table = _table([ChartSupportTableSource(source="revenue")])
    with pytest.raises(RenderError, match="40") as excinfo:
        validate_support_table_against_data(table, "month", data, x_type="ordinal")
    assert excinfo.value.code is not None
    assert excinfo.value.code.code == "ERR-INPUT-INVALID"


def test_validate_no_x_type_over_40_still_raises():
    # No x_type (default None) should keep fail-closed behavior.
    from dbt_charts.core.render.chart.support_table_attachment import (
        validate_support_table_against_data,
    )
    from dbt_charts.core.render.errors import RenderError

    data = _big_ordinal_data(50)
    table = _table([ChartSupportTableSource(source="revenue")])
    with pytest.raises(RenderError, match="40") as excinfo:
        validate_support_table_against_data(table, "month", data)
    assert excinfo.value.code is not None
    assert excinfo.value.code.code == "ERR-INPUT-INVALID"


def test_validate_temporal_exactly_41_returns_step_2():
    # Boundary: 41 temporal rows → ceil(41/40) = 2.
    from dbt_charts.core.render.chart.support_table_attachment import (
        validate_support_table_against_data,
    )

    data = _big_temporal_data(41)
    table = _table([ChartSupportTableSource(source="revenue")])
    step = validate_support_table_against_data(table, "date", data, x_type="temporal")
    assert step == 2


def test_validate_temporal_exactly_40_returns_step_1():
    # At the cap exactly: no sampling needed.
    from dbt_charts.core.render.chart.support_table_attachment import (
        validate_support_table_against_data,
    )

    data = _big_temporal_data(40)
    table = _table([ChartSupportTableSource(source="revenue")])
    step = validate_support_table_against_data(table, "date", data, x_type="temporal")
    assert step == 1


def test_validate_temporal_over_40_still_checks_source_column_existence():
    # Regression: the early-return path used to skip the source-column-presence
    # check on temporal/quantitative axes. A typo in source: must still raise.
    from dbt_charts.core.render.chart.support_table_attachment import (
        validate_support_table_against_data,
    )
    from dbt_charts.core.render.errors import RenderError

    data = _big_temporal_data(97)  # 97 rows, sampling fires
    # Source column is "revenu" (typo) — not in the data dict.
    table = _table([ChartSupportTableSource(source="revenu")])
    with pytest.raises(RenderError, match="revenu") as excinfo:
        validate_support_table_against_data(table, "date", data, x_type="temporal")
    assert excinfo.value.code is not None
    assert excinfo.value.code.code == "ERR-INPUT-INVALID"


def test_validate_temporal_over_40_still_checks_multi_row_ambiguity():
    # Regression: early-return also skipped the ambiguous-aggregation guard.
    # A bare source: entry on multi-row-per-x temporal data must still raise.
    import datetime

    from dbt_charts.core.render.chart.support_table_attachment import (
        validate_support_table_against_data,
    )
    from dbt_charts.core.render.errors import RenderError

    # 97 distinct dates, 2 rows per date (multi-row-per-x).
    start = datetime.date(2016, 1, 1)
    data = []
    for i in range(97):
        d = (start + datetime.timedelta(days=30 * i)).isoformat()
        data.append({"date": d, "revenue": float(i), "segment": "A"})
        data.append({"date": d, "revenue": float(i) + 1, "segment": "B"})
    table = _table([ChartSupportTableSource(source="revenue")])  # bare source, not agg
    with pytest.raises(RenderError, match="ambiguous") as excinfo:
        validate_support_table_against_data(table, "date", data, x_type="temporal")
    assert excinfo.value.code is not None
    assert excinfo.value.code.code == "ERR-INPUT-INVALID"


def test_validate_by_measure_raises_on_multi_row_per_x():
    # Regression: by_measure entries read datum[field] directly with no aggregation
    # transform. On multi-row-per-x data Vega-Lite would render an arbitrary value
    # for each x (one mark per data row, all at the same y pixel). The
    # ambiguous-aggregation guard must reject this — by_measure is NOT exempt from
    # the multi-row check.
    from dbt_charts.core.compile.models.chart.authored import ChartSupportTablePerSeries
    from dbt_charts.core.render.chart.support_table_attachment import (
        validate_support_table_against_data,
    )
    from dbt_charts.core.render.errors import RenderError

    data = [
        {"month": "Jan", "revenue": 100.0},
        {"month": "Jan", "revenue": 200.0},  # two rows for same x → ambiguous
        {"month": "Feb", "revenue": 150.0},
    ]
    table = _table([ChartSupportTablePerSeries(per_series="revenue", by_measure=True)])
    with pytest.raises(RenderError, match="ambiguous") as excinfo:
        validate_support_table_against_data(table, "month", data)
    assert excinfo.value.code is not None
    assert excinfo.value.code.code == "ERR-INPUT-INVALID"


# =============================================================================
# Width-aware thinning + format inheritance (DFT_CORE-SUPPORT_TABLE_STRIP_OVERLAPS)
# =============================================================================


def _18_month_lex_data(revenue: float = 29_531_973.40) -> list[dict]:
    """18 distinct lex-sortable year-month strings with large currency values."""
    return [
        {
            "month": f"202{3 + (i // 12):d}-{(i % 12) + 1:02d}",
            "revenue": revenue + i * 1_000_000,
        }
        for i in range(18)
    ]


def _5_categorical_data(revenue: float = 29_531_973.40) -> list[dict]:
    """5 non-chronological ordinal x values with wide currency values."""
    return [
        {"category": cat, "revenue": revenue}
        for cat in ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
    ]


def test_wide_currency_cells_dense_lex_ordinal_axis_triggers_width_thinning():
    # 18 lex-sortable months with $29M-scale currency values at a spec_width
    # where the per-band budget is less than the measured cell width, but wide
    # enough that the month-cadence axis labels ("Jan"/"Feb"/…) still clear —
    # the cadence ladder does not coarsen to quarter, so the strip's own
    # width-based sampling (not the cadence period-filter) is what must thin it.
    # Before the fix: the reset at ~line 1899 forces sampling_step back to 1 when
    # the ordinal axis does not use parity (short labels fit fine).
    # After the fix: width-gate prevents the reset when cells don't fit.
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    data = _18_month_lex_data()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "wide_currency_bar",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            # Wide currency format: "$29,531,973.40" (~14 chars, ~90px)
            "support_table": {
                "entries": [{"source": "revenue", "format": "$,.2f", "label": "Rev"}]
            },
            "style": {"orientation": "vertical"},
        }
    )
    # spec_width=900: per-band = 900/18 = 50px, much less than a currency cell
    # width but enough for month-abbreviation axis labels to clear — cadence
    # stays at month grain (no period-filter), so the width-gate alone must
    # drive the strip's own thinning.
    spec = generate_vega_lite_spec(chart, data, width=900, height=300)

    text_layers = [
        layer
        for layer in spec.get("layer", [])
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and "field" in layer.get("encoding", {}).get("x", {})
    ]
    assert text_layers, "support_table strip must produce text layers"
    for layer in text_layers:
        transforms = layer.get("transform", [])
        has_window = any("window" in t for t in transforms)
        assert has_window, (
            "18 lex-sortable months with $29M-scale currency cells at spec_width=900 "
            "must thin the strip via sampling_step>1 (window+filter transforms). "
            "The width-gate must block the reset even when axis labels fit (no parity). "
            f"Got transforms: {transforms}"
        )


def test_non_chronological_ordinal_wide_cells_not_width_sampled():
    # Fail-closed: non-chronological ordinal x values must NOT be width-sampled
    # regardless of cell width or spec_width. Dropping unordered categories is
    # silent data loss. The existing >40-row raise path must also be preserved.
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    data = _5_categorical_data()
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "categorical_bar",
            "type": "bar",
            "x": "category",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {"entries": [{"source": "revenue", "format": "$,.2f"}]},
            "style": {"orientation": "vertical"},
        }
    )
    # spec_width=100: per-band = 100/5 = 20px, well under currency cell width.
    # If width-sampling were applied, sampling_step would be > 1, smearing categories.
    # Must NOT apply for non-chronological ordinal — dropping categories is silent data loss.
    spec = generate_vega_lite_spec(chart, data, width=100, height=300)

    text_layers = [
        layer
        for layer in spec.get("layer", [])
        if isinstance(layer.get("mark"), dict)
        and layer["mark"].get("type") == "text"
        and "field" in layer.get("encoding", {}).get("x", {})
    ]
    for layer in text_layers:
        transforms = layer.get("transform", [])
        has_window = any("window" in t for t in transforms)
        assert not has_window, (
            "Non-chronological ordinal x ('Alpha', 'Beta', …) must NOT be "
            "width-sampled — dropping unordered categories is silent data loss. "
            f"Got transforms: {transforms}"
        )


def test_strip_cell_no_format_on_y_measure_inherits_number():
    # Strip cell with no authored format and source == y field must inherit
    # the theme's compact number rather than rendering raw/full-precision.
    # When format is None, _vl_format_calc emits `datum.field ? datum.field : '-'`
    # (no format() call). After the fix, the entry inherits number and
    # _vl_format_calc emits `format(datum.field, '...')`.
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    data = [
        {"month": "Jan", "revenue": 29_531_973.40},
        {"month": "Feb", "revenue": 31_000_000.0},
    ]
    # No explicit format on the support_table entry, no authored style.number_format.
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "no_format_bar",
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"source": "revenue"}]
            },  # format intentionally omitted
            "style": {"orientation": "vertical"},
        }
    )
    spec = generate_vega_lite_spec(chart, data, width=400, height=300)

    # Collect all calculate transforms from the strip layers
    calc_transforms: list[str] = []
    for layer in spec.get("layer", []):
        for t in layer.get("transform", []) or []:
            if "calculate" in t:
                calc_transforms.append(t["calculate"])

    assert calc_transforms, "support_table strip must emit calculate transforms"

    # After the fix: at least one calculate transform uses format() for the
    # y-measure column, not raw datum access.
    has_format_call = any("format(" in expr for expr in calc_transforms)
    assert has_format_call, (
        "Strip cell with no authored format on a numeric y measure must inherit "
        "number so the calculate transform uses format(...). "
        "Without the fix, the transform renders raw/full-precision values. "
        f"Got calculate expressions: {calc_transforms}"
    )


def test_strip_cell_no_format_on_string_y_not_grafted_with_numeric_format():
    # Regression for the render-time inheritance HIGH: a NON-numeric y column
    # with no authored format must keep format=None so _vl_format_calc emits the
    # raw `datum ? datum : '-'` path — NOT format(datum, '.3~s'), which Vega
    # evaluates to NaN on a string and renders every cell as "-".
    #
    # Bar/line/area reject non-numeric y at resolve; scatter bakes a categorical
    # y as nominal (dot plot), so it is the reachable non-numeric-y path that also
    # supports support_table.
    from pydantic import TypeAdapter

    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    data = [
        {"score": 1.0, "grade": "A"},
        {"score": 2.0, "grade": "B"},
        {"score": 3.0, "grade": "C"},
    ]
    # y is a string column (dot plot); the support_table source == y is non-numeric.
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "string_y_scatter",
            "type": "scatter",
            "x": "score",
            "y": "grade",
            "query": SqlQuery(sql="SELECT 1", source="test_db"),
            "query_name": "q",
            "support_table": {
                "entries": [{"source": "grade"}]
            },  # format omitted, non-numeric
        }
    )
    spec = generate_vega_lite_spec(chart, data, width=400, height=300)

    grade_calcs: list[str] = []
    for layer in spec.get("layer", []):
        for t in layer.get("transform", []) or []:
            if "calculate" in t and "grade" in t["calculate"]:
                grade_calcs.append(t["calculate"])
    assert grade_calcs, "expected a strip calculate transform for the 'grade' column"
    for expr in grade_calcs:
        assert "format(" not in expr, (
            "A non-numeric y column must NOT inherit a d3 numeric format — "
            "format(<string>, '...') renders NaN as '-'. Expected the raw "
            f"datum path. Got: {expr}"
        )


# ---------------------------------------------------------------------------
# Dual-axis nest_zero_rule wrapper: color-encoding lookups must recurse
# ---------------------------------------------------------------------------
#
# A dual-axis base/layer's own zero-baseline rule (`nest_zero_rule`,
# emitters/_cartesian.py) can wrap the entry in an extra `mark="layered"`
# level with no encoding of its own. `_emitted_color_encodings` and
# `_suppress_series_legend` both read a layer's color encoding to drive the
# support table's row-swatch colors and legend suppression; a shallow,
# single-level scan finds the wrapper's empty encoding and silently misses
# the real one nested one level deeper.


def _dual_axis_wrapped_bar_layer() -> dict:
    """A VL fragment shaped exactly like `nest_zero_rule`'s wrapper output
    for a bar base: an outer `mark="layered"` entry with no encoding of its
    own, wrapping the real bar mark (color-split) and its zero-rule."""
    return {
        "mark": "layered",
        "layer": [
            {
                "mark": "bar",
                "encoding": {
                    "color": {
                        "field": "product",
                        "legend": {"title": "Product"},
                        "scale": {"domain": ["A", "B"], "range": ["#111", "#222"]},
                    }
                },
            },
            {"mark": "rule", "encoding": {"color": {"value": "#000"}}},
        ],
    }


def test_emitted_color_encodings_recurses_into_nest_zero_rule_wrapper() -> None:
    """A revert to a shallow single-level scan would silently drop the
    base's color encoding once nest_zero_rule wraps it, corrupting the
    support table's per-series fill colors."""
    from dbt_charts.core.render.chart.support_table_attachment import (
        _emitted_color_encodings,
    )

    spec = {"layer": [_dual_axis_wrapped_bar_layer()]}

    field_encodings = [
        enc for enc in _emitted_color_encodings(spec) if enc.get("field") == "product"
    ]
    assert len(field_encodings) == 1, (
        "expected exactly one color encoding for the 'product' field, found "
        f"{len(field_encodings)}"
    )
    assert field_encodings[0]["scale"]["domain"] == ["A", "B"]


def test_suppress_series_legend_finds_color_through_nest_zero_rule_wrapper() -> None:
    """A revert to a shallow single-level scan would leave the color
    legend's `field` invisible, so the redundant series legend a
    per_series support table strips would no longer be suppressed."""
    from dbt_charts.core.render.chart.support_table_attachment import (
        _suppress_series_legend,
    )

    spec: dict = {"layer": [_dual_axis_wrapped_bar_layer()]}

    _suppress_series_legend(spec)

    color_enc = spec["layer"][0]["layer"][0]["encoding"]["color"]
    assert color_enc["legend"] is None
