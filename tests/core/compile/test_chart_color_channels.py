"""Tests for chart-level style channel parsing and normalization.

TDD: these tests were written before the implementation.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.chart.authored import (
    ScaleTargetConfig,
)

# ============================================================================
# ScaleTargetConfig with float palette
# ============================================================================


def test_scale_target_config_float_palette():
    cfg = ScaleTargetConfig.model_validate({"palette": [0.2, 1.0]})
    assert cfg.palette == [0.2, 1.0]


def test_scale_target_config_str_palette():
    cfg = ScaleTargetConfig.model_validate({"palette": ["#fff", "#000"]})
    assert cfg.palette == ["#fff", "#000"]


# ============================================================================
# ScaleTargetConfig named Vega scheme validation
# ============================================================================


def test_scale_target_config_accepts_known_scheme_name():
    cfg = ScaleTargetConfig.model_validate({"palette": "blues"})
    assert cfg.palette == "blues"


def test_scale_target_config_accepts_dbt_charts_named_palette():
    """A dbt charts named palette (not a Vega scheme) is also a valid string —
    the same field backs table/KPI conditional formatting, which resolves
    this vocabulary via resolve_palette_stops rather than a VL scheme name."""
    cfg = ScaleTargetConfig.model_validate({"palette": "dbt-seq-blue"})
    assert cfg.palette == "dbt-seq-blue"


def test_scale_target_config_has_no_resolved_stops_field():
    """ScaleTargetConfig structurally has no resolved_stops — the field lives
    only on ResolvedNamedPaletteScaleTargetConfig. Accessing it raises
    AttributeError; the authored class must not carry a None sentinel that
    callers could forget to check."""
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.primitives import (
        ResolvedScaleTargetConfig,
    )

    # Constructing either class with resolved_stops is forbidden (extra="forbid").
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ScaleTargetConfig.model_validate(
            {"palette": "dbt-seq-blue", "resolved_stops": ("#fff",)}
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ResolvedScaleTargetConfig.model_validate(
            {"palette": "dbt-seq-blue", "resolved_stops": ("#fff",)}
        )


def test_bake_scale_target_stops_resolves_a_dbt_charts_named_palette():
    """bake_scale_target_stops constructs ResolvedNamedPaletteScaleTargetConfig
    with fresh resolved_stops for a named dbt charts palette. Regression: before
    the authored/resolved split, only some construction paths baked resolved_stops,
    so a heatmap/geoshape gradient using a dbt charts name crashed at render."""
    from dbt_charts.core.compile.models.primitives import (
        ResolvedNamedPaletteScaleTargetConfig,
        bake_scale_target_stops,
    )
    from dbt_charts.core.compile.resolve.style.palette import (
        palette as resolve_named_palette,
    )

    cfg = ScaleTargetConfig.model_validate({"palette": "dbt-seq-blue"})
    baked = bake_scale_target_stops(cfg)
    assert isinstance(baked, ResolvedNamedPaletteScaleTargetConfig)
    assert baked.resolved_stops == tuple(resolve_named_palette("dbt-seq-blue"))


def test_bake_scale_target_stops_produces_plain_resolved_for_scheme_and_inline():
    """A Vega scheme name and an inline stop list produce ResolvedScaleTargetConfig
    (no resolved_stops field) — not the named-palette variant. The new
    implementation constructs rather than passes through, so object identity
    is intentionally gone; the invariant is the returned type and field values."""
    from dbt_charts.core.compile.models.primitives import (
        ResolvedNamedPaletteScaleTargetConfig,
        ResolvedScaleTargetConfig,
        bake_scale_target_stops,
    )

    scheme_cfg = ScaleTargetConfig.model_validate({"palette": "blues"})
    scheme_baked = bake_scale_target_stops(scheme_cfg)
    assert type(scheme_baked) is ResolvedScaleTargetConfig
    assert not isinstance(scheme_baked, ResolvedNamedPaletteScaleTargetConfig)
    assert scheme_baked.palette == "blues"

    inline_cfg = ScaleTargetConfig.model_validate({"palette": ["#fff", "#000"]})
    inline_baked = bake_scale_target_stops(inline_cfg)
    assert type(inline_baked) is ResolvedScaleTargetConfig
    assert not isinstance(inline_baked, ResolvedNamedPaletteScaleTargetConfig)
    assert inline_baked.palette == ["#fff", "#000"]


def test_scale_target_config_dash_suffixed_name_hints_at_bucketing():
    """'blues-9' (VL's sampled-discrete shorthand, which dbt charts does not
    forward) is *the* mistake this validator exists to catch: reaching for a
    dash-suffixed discrete scheme is exactly what an author does when they
    want buckets. The error must name the valid continuous scheme and say
    bucketed/quantized scales aren't supported yet, not just reject the
    string — pinned exactly so this hint can't silently regress to a generic
    'unknown palette' message."""
    with pytest.raises(
        ValueError,
        match=(
            r"palette 'blues-9' requests a 9-step discrete variant of the "
            r"'blues' scheme\. dbt charts does not support bucketed/quantized "
            r"color scales yet — use the continuous scheme 'blues' instead\."
        ),
    ):
        ScaleTargetConfig.model_validate({"palette": "blues-9"})

    with pytest.raises(ValueError, match="continuous scheme 'viridis'"):
        ScaleTargetConfig.model_validate({"palette": "viridis-7"})


def test_scale_target_config_defers_a_name_that_could_be_a_palette_role():
    """A bare name may be a theme palette role, so it is not rejected here.

    The theme's `palettes:` map is not final at validation time, so `category`
    and a typo are indistinguishable. `expand_palette_refs` makes the call once
    the cascade completes. A name carrying `:N`/`_r` shorthand is never a role,
    so it still fails immediately (see the dash-suffix test above)."""
    cfg = ScaleTargetConfig.model_validate({"palette": "not-a-real-scheme"})
    assert cfg.palette == "not-a-real-scheme"


def test_scale_target_config_patch_rejects_unsupported_scheme_name():
    """ScaleTargetConfigPatch (the authored-overlay shape normalize_board()
    actually constructs from raw YAML) must carry the same validation as the
    strict ScaleTargetConfig — build_patch_model_ext only carries over
    validators declared on the patch's base class, so this pins that the
    palette check was wired through base_cls, not just added to the strict
    class where it would only fire at resolve time."""
    from dbt_charts.core.compile.models.primitives import ScaleTargetConfigPatch

    with pytest.raises(ValueError, match="bucketed/quantized"):
        ScaleTargetConfigPatch.model_validate({"palette": "blues-9"})


def test_scale_target_config_bad_scheme_in_board_yaml_yields_diagnostic_not_raw_pydantic():
    """An author authoring 'style.color.gradient.palette: blues-9' directly in
    board YAML must get a dbt charts diagnostic (ERR-VALIDATION-FIELD, a doc
    pointer, a source location) from compile() — not an uncaught
    pydantic.ValidationError with a pydantic.dev link, which happens when the
    palette check only fires on ScaleTargetConfig at resolve time instead of
    on the authored patch during normalize_board()."""
    from dbt_charts.core.compile.compiler import compile as dbt_charts_compile

    yaml_content = """
title: Bad scheme
queries:
  q:
    columns: [state_code, unemployment]
    values:
      - ["9", 4.1]
charts:
  choropleth_bad:
    query: q
    type: map
    geo:
      source: us-states
    lookup: state_code
    value: unemployment
    style:
      color:
        gradient:
          palette: blues-9
rows:
  - choropleth_bad
"""
    result = dbt_charts_compile(yaml_content, file="bad_scheme.yaml")
    assert result.errors, "expected a compile error, not a successful compile"
    error = result.errors[0]
    assert error.code == "ERR-VALIDATION-FIELD", (
        f"expected a dbt charts diagnostic, got code={error.code!r}"
    )
    assert "bucketed/quantized" in error.message


# ============================================================================
# parse_style_channel
# ============================================================================


def test_parse_style_channel_string_shorthand():
    from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel

    ch = parse_style_channel("segment", "color")
    assert ch.mode == "series"
    assert ch.data_field == "segment"
    assert ch.channel == "color"


def test_parse_style_channel_field_dict():
    from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel

    ch = parse_style_channel({"column": "segment"}, "color")
    assert ch.mode == "series"
    assert ch.data_field == "segment"


def test_parse_style_channel_gradient_mode():
    from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel

    ch = parse_style_channel(
        {"column": "arr", "scale": {"palette": ["#fff", "#000"]}}, "color"
    )
    assert ch.mode == "gradient"
    assert ch.data_field == "arr"
    assert ch.scale is not None
    assert ch.scale.palette == ["#fff", "#000"]


def test_parse_style_channel_float_palette_gradient():
    from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel

    ch = parse_style_channel(
        {"column": "arr", "scale": {"palette": [0.2, 1.0]}}, "opacity"
    )
    assert ch.mode == "gradient"
    assert ch.scale.palette == [0.2, 1.0]


def test_parse_style_channel_literal_mode():
    from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel

    ch = parse_style_channel({"value": "#ff0000"}, "color")
    assert ch.mode == "literal"
    assert ch.literal_value == "#ff0000"


def test_parse_style_channel_mutual_exclusion_value_plus_field():
    from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel

    with pytest.raises(ValueError, match="'value' cannot be combined"):
        parse_style_channel({"value": "#red", "column": "col"}, "color")


def test_parse_style_channel_when_key_is_unknown():
    """Per-channel ``when:`` is no longer in the channel grammar. It is
    rejected as an unknown key by the generic extra-key check."""
    from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel

    with pytest.raises(ValueError, match="unknown keys"):
        parse_style_channel(
            {"column": "col", "when": [{"gt": 0, "value": "#red"}]},
            "color",
        )


# ============================================================================
# validate_channel_fields
# ============================================================================


def test_validate_channel_fields_raises_on_missing_field():
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.compile.resolve.chart.channel import validate_channel_fields

    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="missing_col")
    with pytest.raises(ValueError, match="'color'.*'missing_col'"):
        validate_channel_fields({"color": ch}, {"arr", "segment"})


def test_validate_channel_fields_ok_when_field_present():
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.compile.resolve.chart.channel import validate_channel_fields

    ch = ResolvedStyleChannel(channel="color", mode="series", data_field="segment")
    validate_channel_fields({"color": ch}, {"arr", "segment"})  # no raise


def test_validate_channel_fields_skips_literal_mode():
    from dbt_charts.core.compile.models.chart.resolved import ResolvedStyleChannel
    from dbt_charts.core.compile.resolve.chart.channel import validate_channel_fields

    ch = ResolvedStyleChannel(channel="color", mode="literal", literal_value="#red")
    validate_channel_fields(
        {"color": ch}, {"arr"}
    )  # no raise — literal has no field ref


# ============================================================================
# normalize_chart_channels
# ============================================================================


class _FakeChart:
    """Minimal stand-in for a compiled chart object.

    Defaults mirror the normalized Chart model's defaults so 2-arg getattr
    calls in channel.py work without raising AttributeError.
    """

    background: object = None  # KPI-only gradient background channel
    support: object = None  # KPI-only support block

    def __init__(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_normalize_chart_channels_color_gradient():
    from dbt_charts.core.compile.resolve.chart.channel import normalize_chart_channels

    chart = _FakeChart(
        color={"column": "arr", "scale": {"palette": ["#fff", "#000"]}},
    )
    channels = normalize_chart_channels(chart, {"arr"})
    assert "color" in channels
    assert channels["color"].mode == "gradient"


def test_normalize_chart_channels_validates_field_ref():
    from dbt_charts.core.compile.resolve.chart.channel import normalize_chart_channels

    chart = _FakeChart(
        color={"column": "nonexistent", "scale": {"palette": ["#a", "#b"]}},
    )
    with pytest.raises(ValueError, match="'color'.*'nonexistent'"):
        normalize_chart_channels(chart, {"arr", "segment"})


def test_label_field_rejected_by_compiled_chart():
    """label field is not in v1 scope — Chart should reject it."""
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.chart.normalized import BarChart
    from dbt_charts.core.compile.models.query.normalized import SqlQuery

    with pytest.raises(ValidationError):
        BarChart(
            id="c1",
            query=SqlQuery(sql="SELECT 1", source="test"),
            type="bar",
            label={"color": "#red"},
        )


def test_unknown_dict_channel_raises():
    """A dict with unknown keys raises immediately (not 'must specify field or value')."""
    from dbt_charts.core.compile.resolve.chart.channel import normalize_chart_channels

    chart = _FakeChart(
        type="bar",
        color={"fld": "arr"},  # typo — "fld" not "field"
    )
    with pytest.raises(ValueError, match="unknown keys"):
        normalize_chart_channels(chart, {"arr"})


def test_table_conditional_formatting_unknown_column_raises():
    """table's unknown-CF-column guard survives the mark-fill branch collapse.

    validate_conditional_formatting_columns runs unconditionally ahead of the
    _project_conditional_formatting_inputs branch — table is the only family
    that depends on it (every other family it once fired for is now a hard
    parse-time ValidationError, never reaching this projector at all).
    """
    from dbt_charts.core.compile.resolve.chart.channel import normalize_chart_channels

    chart = _FakeChart(
        type="table",
        columns=None,
        conditional_formatting={
            "nonexistent": {"when": [{"gt": 100, "background": "#ff0000"}]}
        },
    )
    with pytest.raises(ValueError, match="conditional_formatting targets column"):
        normalize_chart_channels(chart, {"arr", "segment"})


def test_parse_style_channel_extra_key_rejected():
    """A dict with a valid key plus a typo raises on the extra key."""
    from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel

    with pytest.raises(ValueError, match="unknown keys.*scal"):
        parse_style_channel(
            {"column": "arr", "scal": {"palette": ["#a", "#b"]}}, "color"
        )


def test_color_channel_with_numeric_palette_raises():
    """color channel with numeric palette should raise — expects color strings."""
    from dbt_charts.core.compile.resolve.chart.channel import normalize_chart_channels

    chart = _FakeChart(
        type="bar",
        color={"column": "arr", "scale": {"palette": [0.2, 1.0]}},
    )
    with pytest.raises(ValueError, match="color palette.*strings"):
        normalize_chart_channels(chart, {"arr"})


def test_geo_gradient_color_raises():
    """Gradient color on geo chart type raises — geo only supports series/literal."""
    from dbt_charts.core.compile.resolve.chart.channel import normalize_chart_channels

    for geo_type in ("map", "geoshape", "point_map", "bubble_map"):
        chart = _FakeChart(
            type=geo_type,
            color={"column": "value", "scale": {"palette": ["#fff", "#000"]}},
        )
        with pytest.raises(ValueError, match="only support series or literal"):
            normalize_chart_channels(chart, {"value"})


def test_geo_gradient_via_scale_also_raises():
    """Gradient color (via scale) on geo chart type raises — geo only
    supports series/literal colors."""
    from dbt_charts.core.compile.resolve.chart.channel import normalize_chart_channels

    chart = _FakeChart(
        type="map",
        color={"column": "value", "scale": {"palette": ["#a", "#b"]}},
    )
    with pytest.raises(ValueError, match="only support series or literal"):
        normalize_chart_channels(chart, {"value"})


def test_geo_series_color_ok():
    """Series color on geo chart type does not raise."""
    from dbt_charts.core.compile.resolve.chart.channel import normalize_chart_channels

    chart = _FakeChart(type="map", color="region")
    channels = normalize_chart_channels(chart, {"region"})
    assert channels["color"].mode == "series"


# ============================================================================
# AuthoredChart rejects label field
# ============================================================================


def test_label_field_rejected_by_chart_patch():
    """AuthoredChart rejects 'label' field via before-validator (not just Chart)."""
    from pydantic import TypeAdapter, ValidationError

    from dbt_charts.core.compile.models.chart.authored import (
        AuthoredChart,
    )

    adapter = TypeAdapter(AuthoredChart)
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "bar", "label": "Revenue"})


# ============================================================================
# ColumnScaleConfig: numeric palette rejected on color properties
# ============================================================================


@pytest.mark.parametrize("target", ["color", "background"])
def test_column_scale_config_rejects_numeric_palette(target: str) -> None:
    """ColumnScaleConfig palettes are CSS strings, not relative float stops."""
    from dbt_charts.core.compile.models.chart.authored import ColumnScaleConfig

    with pytest.raises(ValueError, match="Input should be a valid string"):
        ColumnScaleConfig.model_validate({target: {"palette": [0.2, 1.0]}})


def test_column_scale_config_accepts_string_palette():
    """ColumnScaleConfig accepts CSS string palettes."""
    from dbt_charts.core.compile.models.chart.authored import ColumnScaleConfig

    cfg = ColumnScaleConfig.model_validate(
        {"color": {"palette": ["#ffffff", "#0000ff"]}}
    )
    assert cfg.color is not None
    assert cfg.color.palette == ["#ffffff", "#0000ff"]


# ============================================================================
# B1: Literal channel rejects value: None
# ============================================================================


def test_literal_channel_rejects_null_value():
    """parse_style_channel with value: None raises — use a non-null value."""
    from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel

    with pytest.raises(ValueError, match="cannot be None"):
        parse_style_channel({"value": None}, "color")


# ============================================================================
# B2: Conditional channel value type-checked per channel semantics
# ============================================================================


def test_parse_style_channel_scale_must_be_dict():
    """scale must be a mapping — string scheme name is not accepted."""
    from dbt_charts.core.compile.resolve.chart.channel import parse_style_channel

    with pytest.raises(ValueError, match="'scale' must be a mapping"):
        parse_style_channel({"column": "arr", "scale": "viridis"}, "color")


# ============================================================================
# C1: KPI gradient requires explicit min/max
# ============================================================================


def test_kpi_gradient_without_explicit_bounds_ok():
    """KPI gradient with data-domain (no explicit min/max) compiles cleanly.

    The renderer's ``interpolate_scale_color`` returns the middle palette stop
    when the single-row KPI domain collapses to (v, v). That's a sensible
    fallback matching Looker's "show some color" behavior on KPIs with named
    min/max constraints. Authors who want sharper colors set min/max
    explicitly.
    """
    from dbt_charts.core.compile.resolve.chart.channel import normalize_chart_channels

    chart = _FakeChart(
        type="kpi",
        color={"column": "revenue", "scale": {"palette": ["#fff", "#00f"]}},
    )
    channels = normalize_chart_channels(chart, {"revenue"})
    assert channels["color"].mode == "gradient"
    assert channels["color"].scale is not None
    assert channels["color"].scale.min is None
    assert channels["color"].scale.max is None


def test_kpi_gradient_with_explicit_bounds_ok():
    """KPI gradient channel with explicit min and max does not raise."""
    from dbt_charts.core.compile.resolve.chart.channel import normalize_chart_channels

    chart = _FakeChart(
        type="kpi",
        color={
            "column": "revenue",
            "scale": {"palette": ["#fff", "#00f"], "min": 0, "max": 1_000_000},
        },
    )
    channels = normalize_chart_channels(chart, {"revenue"})
    assert channels["color"].mode == "gradient"


# ============================================================================
# gradient_scale_to_vl — VL output is shape-preserving after the split
# ============================================================================


# ============================================================================
# gradient_scale_to_vl — hinge pivots the diverging scale
# Regression for https://github.com/dbt-labs/dbt-charts/issues/48: hinge was
# parsed and validated but never read when building the VL color scale, so
# a diverging gradient always pivoted at the domain midpoint instead of the
# authored hinge value.
# ============================================================================


_DIVERGING_STOPS = ["#08306b", "#2166ac", "#e4e4e4", "#b2182b", "#67001f"]


# ============================================================================
# _diverging_domain — odd-length breakpoint placement (single exact-hinge
# center, explicit per-arm denominators)
# ============================================================================


# ============================================================================
# _diverging_breakpoints_truncated — perceptual (HCL) boundary blending
# ============================================================================


# ============================================================================
# Regression: domain=None preserved through bake and ResolvedScaleTargetConfig guard
# ============================================================================


def test_bake_scale_target_stops_preserves_explicit_domain_none():
    """domain=None must survive the authored→resolved upgrade in bake_scale_target_stops.

    ScaleTargetConfig.domain defaults to 'data'; an explicit domain=None is
    meaningful ('use explicit min/max'). Previously, model_dump(exclude_none=True)
    silently dropped it and revalidation re-applied the 'data' default, discarding
    the author's intent before any downstream consumer saw it."""
    from dbt_charts.core.compile.models.primitives import bake_scale_target_stops

    cfg = ScaleTargetConfig(palette="blues", domain=None, min=0.0, max=1.0)
    baked = bake_scale_target_stops(cfg)
    assert baked.domain is None, (
        f"explicit domain=None was silently reset to {baked.domain!r} during bake"
    )

    named_cfg = ScaleTargetConfig(
        palette="dbt-seq-blue", domain=None, min=0.0, max=10.0
    )
    baked_named = bake_scale_target_stops(named_cfg)
    assert baked_named.domain is None, (
        f"explicit domain=None was silently reset to {baked_named.domain!r} during named-palette bake"
    )


def test_resolved_scale_target_config_rejects_unbaked_named_palette():
    """ResolvedScaleTargetConfig must reject a string palette that is not a Vega
    scheme name — such a palette should have been baked by bake_scale_target_stops
    to produce ResolvedNamedPaletteScaleTargetConfig instead.

    Without this guard, gradient_scale_to_vl emits {'scheme': 'dbt-seq-blue'},
    an invalid VL spec that no renderer can resolve."""
    from pydantic import ValidationError

    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig

    with pytest.raises(ValidationError, match="named dbt-charts palette"):
        ResolvedScaleTargetConfig.model_validate({"palette": "dbt-seq-blue"})

    # Vega scheme names and inline lists are accepted on the plain variant.
    scheme = ResolvedScaleTargetConfig.model_validate({"palette": "blues"})
    assert scheme.palette == "blues"
    inline = ResolvedScaleTargetConfig.model_validate({"palette": ["#fff", "#000"]})
    assert inline.palette == ["#fff", "#000"]
