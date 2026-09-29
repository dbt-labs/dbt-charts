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


def test_column_scale_config_rejects_numeric_color_palette():
    """ColumnScaleConfig.color.palette must be CSS strings, not numbers."""
    from dbt_charts.core.compile.models.chart.authored import (
        ColumnScaleConfig,
        ScaleTargetConfig,
    )

    with pytest.raises(ValueError, match="must be CSS color strings"):
        ColumnScaleConfig(color=ScaleTargetConfig(palette=[0.2, 1.0]))


def test_column_scale_config_rejects_numeric_background_palette():
    """ColumnScaleConfig.background.palette must be CSS strings, not numbers."""
    from dbt_charts.core.compile.models.chart.authored import (
        ColumnScaleConfig,
        ScaleTargetConfig,
    )

    with pytest.raises(ValueError, match="must be CSS color strings"):
        ColumnScaleConfig(background=ScaleTargetConfig(palette=[0.0, 0.5, 1.0]))


def test_column_scale_config_accepts_string_palette():
    """ColumnScaleConfig accepts CSS string palettes."""
    from dbt_charts.core.compile.models.chart.authored import (
        ColumnScaleConfig,
        ScaleTargetConfig,
    )

    cfg = ColumnScaleConfig(color=ScaleTargetConfig(palette=["#ffffff", "#0000ff"]))
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


def test_gradient_scale_to_vl_scheme_name_emits_vl_scheme():
    """A Vega scheme palette produces {"scheme": <name>} — not {"range": ...}.
    This step changed how gradient_scale_to_vl decides (isinstance check instead
    of palette re-sniff), not what it emits. Pin the output, not the mechanism."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(palette="viridis")
    result = gradient_scale_to_vl(scale)
    assert result == {"scheme": "viridis"}


def test_gradient_scale_to_vl_inline_list_emits_range():
    """An inline hex stop list produces {"range": [...]}, not {"scheme": ...}."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    stops = ["#ffffff", "#000000"]
    scale = ResolvedScaleTargetConfig(palette=stops)
    result = gradient_scale_to_vl(scale)
    assert result == {"range": stops}


def test_gradient_scale_to_vl_named_palette_emits_resolved_stops_as_range():
    """A ResolvedNamedPaletteScaleTargetConfig produces {"range": <resolved_stops>}.
    This is the key shape change: the dbt charts palette name is never forwarded to
    VL as a scheme string (which silently no-ops); the baked stops are used."""
    from dbt_charts.core.compile.models.primitives import (
        ResolvedNamedPaletteScaleTargetConfig,
    )
    from dbt_charts.core.compile.resolve.style.palette import palette as resolve_palette
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    expected_stops = list(resolve_palette("dbt-seq-blue"))
    scale = ResolvedNamedPaletteScaleTargetConfig(
        palette="dbt-seq-blue",
        resolved_stops=tuple(expected_stops),
    )
    result = gradient_scale_to_vl(scale)
    assert result == {"range": expected_stops}


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


def test_diverging_domain_odd_n_reproduces_center_at_hinge():
    from dbt_charts.core.render.chart.emitters._channels import _diverging_domain

    domain = _diverging_domain(5, 0.0, 200.0, 300.0)
    assert domain == [-200.0, -100.0, 0.0, 150.0, 300.0]


def test_diverging_domain_rejects_even_n():
    """``_diverging_domain`` requires an already-odd breakpoint count: an
    even-length palette must be normalized to odd (a synthesized center
    stop) by its one caller, ``_diverging_breakpoints_truncated``, before it
    ever reaches here — placing a HALF-integer-offset pair of center
    breakpoints directly (the previous design) put the visual 50% pivot off
    the real hinge whenever the two arms had unequal widths."""
    from dbt_charts.core.render.chart.emitters._channels import _diverging_domain

    with pytest.raises(ValueError, match="odd"):
        _diverging_domain(4, 0.0, 300.0, 600.0)


def test_diverging_domain_rejects_fewer_than_three_stops():
    from dbt_charts.core.render.chart.emitters._channels import _diverging_domain

    with pytest.raises(ValueError, match="odd"):
        _diverging_domain(1, 0.0, 1.0, 1.0)


# ============================================================================
# _diverging_breakpoints_truncated — perceptual (HCL) boundary blending
# ============================================================================


def test_diverging_breakpoints_truncated_no_op_when_all_within_bounds():
    """Symmetric's own per-arm denominators never overshoot [lo, hi], so
    truncation is a no-op: the original stops pass through unmodified."""
    from dbt_charts.core.render.chart.emitters._channels import (
        _diverging_breakpoints_truncated,
    )
    from dbt_charts.core.render.chart.table_support import diverging_arm_denominators

    neg_denom, pos_denom = diverging_arm_denominators(
        0.0, -20000.0, 60000.0, "symmetric"
    )
    domain, colors = _diverging_breakpoints_truncated(
        _DIVERGING_STOPS, 0.0, -20000.0, 60000.0, neg_denom, pos_denom
    )
    assert domain == [-20000.0, -10000.0, 0.0, 30000.0, 60000.0]
    assert colors == _DIVERGING_STOPS


def test_diverging_breakpoints_truncated_drops_overshoot_and_adds_one_synthetic_stop():
    from dbt_charts.core.render.chart.emitters._channels import (
        _diverging_breakpoints_truncated,
    )
    from dbt_charts.core.render.chart.table_support import diverging_arm_denominators

    neg_denom, pos_denom = diverging_arm_denominators(
        0.0, -20000.0, 60000.0, "asymmetric"
    )
    domain, colors = _diverging_breakpoints_truncated(
        _DIVERGING_STOPS, 0.0, -20000.0, 60000.0, neg_denom, pos_denom
    )
    assert domain[0] == -20000.0
    assert domain[-1] == 60000.0
    assert len(domain) == len(colors)
    # One theoretical breakpoint (-30000) got truncated and replaced by
    # exactly one synthetic stop, not two -- domain shrinks from 5 to 4.
    assert len(domain) == 4
    hinge_idx = domain.index(0.0)
    assert colors[hinge_idx] == "#e4e4e4"
    assert colors[-1] == "#67001f"
    assert colors[0] not in _DIVERGING_STOPS  # the blended synthetic stop


def test_diverging_breakpoints_truncated_degenerate_arm_collapses_to_neutral():
    """Every breakpoint on a fully degenerate (zero-width) arm gets
    truncated except the shared center one, which already sits exactly on
    the bound -- so no synthetic stop is added, and the whole arm reads as
    one flat neutral color instead of a hair-short-of-the-bound extreme."""
    from dbt_charts.core.render.chart.emitters._channels import (
        _diverging_breakpoints_truncated,
    )
    from dbt_charts.core.render.chart.table_support import diverging_arm_denominators

    neg_denom, pos_denom = diverging_arm_denominators(0.0, 0.0, 1000.0, "symmetric")
    domain, colors = _diverging_breakpoints_truncated(
        _DIVERGING_STOPS, 0.0, 0.0, 1000.0, neg_denom, pos_denom
    )
    assert domain[0] == 0.0
    assert colors[0] == "#e4e4e4"
    assert domain[-1] == 1000.0
    assert colors[-1] == "#67001f"


def test_diverging_breakpoints_truncated_handles_non_hex_stops():
    from dbt_charts.core.render.chart.emitters._channels import (
        _diverging_breakpoints_truncated,
    )
    from dbt_charts.core.render.chart.table_support import diverging_arm_denominators

    palette = ["blue", "white", "red"]
    neg_denom, pos_denom = diverging_arm_denominators(0.0, -10.0, 1000.0, "asymmetric")
    domain, colors = _diverging_breakpoints_truncated(
        palette, 0.0, -10.0, 1000.0, neg_denom, pos_denom
    )
    assert domain[0] == -10.0
    assert domain[-1] == 1000.0
    assert colors[0] not in ("blue", "red")
    assert colors[-1] == "red"


def test_diverging_breakpoints_truncated_interpolates_alpha_for_translucent_stop():
    from dbt_charts.core.colors import parse_css_color
    from dbt_charts.core.render.chart.emitters._channels import (
        _diverging_breakpoints_truncated,
    )
    from dbt_charts.core.render.chart.table_support import diverging_arm_denominators

    palette = ["#d73027", "transparent", "#1a9850"]
    neg_denom, pos_denom = diverging_arm_denominators(0.0, -10.0, 40.0, "asymmetric")
    domain, colors = _diverging_breakpoints_truncated(
        palette, 0.0, -10.0, 40.0, neg_denom, pos_denom
    )
    assert domain[0] == -10.0
    _, _, _, alpha = parse_css_color(colors[0])
    assert 0.0 < alpha < 1.0


def test_diverging_breakpoints_truncated_even_palette_pivots_exactly_at_hinge_unequal_arms():
    """Regression: with UNEQUAL arm widths, placing the two center
    breakpoints independently (the previous even-length design) put the
    visual 50% pivot off the real hinge -- only equal-width arms coincided
    by accident. Synthesizing one HCL-blended center stop and treating the
    palette as odd-length (this function's fix) puts a SINGLE, exact
    breakpoint at hinge regardless of arm width."""
    from dbt_charts.core.colors import interpolate_hcl
    from dbt_charts.core.render.chart.emitters._channels import (
        _diverging_breakpoints_truncated,
    )
    from dbt_charts.core.render.chart.table_support import diverging_arm_denominators

    palette = ["#08306b", "#e4e4e4", "#b2182b", "#67001f"]
    neg_denom, pos_denom = diverging_arm_denominators(0.0, -10.0, 90.0, "symmetric")
    domain, colors = _diverging_breakpoints_truncated(
        palette, 0.0, -10.0, 90.0, neg_denom, pos_denom
    )
    assert domain[0] == -10.0
    assert domain[-1] == 90.0
    hinge_idx = domain.index(0.0)
    assert colors[hinge_idx] == interpolate_hcl(palette[1], palette[2], 0.5)
    assert colors[hinge_idx] not in (palette[1], palette[2])


def test_diverging_breakpoints_truncated_even_palette_degenerate_arm_still_neutral():
    """The even-length normalization must also resolve a degenerate
    (zero-width) arm cleanly: the synthesized center stop sits exactly at
    hinge, which is itself the bound once the arm is zero-width, so the
    whole arm collapses to that one blended color -- not the old bug where
    a hair-short-of-the-bound breakpoint painted the wrong (unblended,
    still one of the two original center stops') color."""
    from dbt_charts.core.colors import interpolate_hcl
    from dbt_charts.core.render.chart.emitters._channels import (
        _diverging_breakpoints_truncated,
    )
    from dbt_charts.core.render.chart.table_support import diverging_arm_denominators

    palette = ["#08306b", "#e4e4e4", "#b2182b", "#67001f"]
    neg_denom, pos_denom = diverging_arm_denominators(0.0, 0.0, 100.0, "symmetric")
    domain, colors = _diverging_breakpoints_truncated(
        palette, 0.0, 0.0, 100.0, neg_denom, pos_denom
    )
    assert domain[0] == 0.0
    assert colors[0] == interpolate_hcl(palette[1], palette[2], 0.5)
    assert domain[-1] == 100.0
    assert colors[-1] == "#67001f"


def test_diverging_breakpoints_truncated_snaps_wide_magnitude_rounding_to_exact_bound():
    """A float-rounding artifact in `hinge - (hinge - lo)` at wide
    magnitudes must not add a spurious near-duplicate synthetic boundary
    stop for a value that was always mathematically meant to equal `lo`/`hi`
    exactly."""
    from dbt_charts.core.render.chart.emitters._channels import (
        _diverging_breakpoints_truncated,
    )
    from dbt_charts.core.render.chart.table_support import diverging_arm_denominators

    palette = ["#4682b4", "#f7f7f7", "#b2182b"]
    lo, hi, hinge = -153985028.01967418, 1760650253.7423544, 1751886373.5495243
    neg_denom, pos_denom = diverging_arm_denominators(hinge, lo, hi, "symmetric")
    domain, colors = _diverging_breakpoints_truncated(
        palette, hinge, lo, hi, neg_denom, pos_denom
    )
    assert len(domain) == len(palette) == 3  # no spurious synthetic stop
    assert domain[0] == lo
    assert domain[-1] == hi
    assert colors[0] == "#4682b4"
    assert colors[-1] == "#b2182b"


def test_diverging_breakpoints_truncated_snap_guard_excludes_zero_width_three_stop_arm():
    """Regression: with exactly 3 stops, the zero-width-arm's own center
    breakpoint (index 1) sits AT hinge, which is itself lo/hi once the arm
    is degenerate — the snap guard must be a STRICT inequality
    (``lo < domain[1]``), or a degenerate arm's outer breakpoint gets
    snapped onto the SAME value as the center one, producing a duplicate x
    position in the domain array."""
    from dbt_charts.core.render.chart.emitters._channels import (
        _diverging_breakpoints_truncated,
    )
    from dbt_charts.core.render.chart.table_support import diverging_arm_denominators

    palette = ["#2166ac", "#f7f7f7", "#b2182b"]
    neg_denom, pos_denom = diverging_arm_denominators(
        1000.0, 1000.0, 2000.0, "symmetric"
    )
    domain, _colors = _diverging_breakpoints_truncated(
        palette, 1000.0, 1000.0, 2000.0, neg_denom, pos_denom
    )
    assert len(domain) == len(set(domain))  # no duplicate x position
    assert domain[0] == 1000.0


def test_diverging_breakpoints_truncated_snap_tolerance_scales_with_domain_span():
    """Regression: the snap tolerance must scale with the domain's own span
    (``hi - lo``), not with the raw magnitude of the breakpoint values —
    otherwise a genuine, substantial overshoot at a large absolute magnitude
    (here: 1.0 unit, on a domain only 2 units wide) reads as "close enough"
    to snap, discarding a real blended synthetic stop in favor of pinning
    the far extreme's own (wrong) color onto the bound."""
    from dbt_charts.core.colors import interpolate_hcl
    from dbt_charts.core.render.chart.emitters._channels import (
        _diverging_breakpoints_truncated,
    )
    from dbt_charts.core.render.chart.table_support import diverging_arm_denominators

    palette = ["#2166ac", "#f7f7f7", "#b2182b"]
    lo, hi, hinge = 1_000_000_000.0, 1_000_000_002.0, 1_000_000_000.5
    neg_denom, pos_denom = diverging_arm_denominators(hinge, lo, hi, "asymmetric")
    domain, colors = _diverging_breakpoints_truncated(
        palette, hinge, lo, hi, neg_denom, pos_denom
    )
    assert domain[0] == lo
    # A genuine HCL blend, not the raw (un-blended) extreme stop's color --
    # snapping-without-blending would have left colors[0] == palette[0].
    x0, x1 = hinge - neg_denom, hinge
    expected = interpolate_hcl(palette[0], palette[1], (lo - x0) / (x1 - x0))
    assert colors[0] == expected
    assert colors[0] != palette[0]


def test_gradient_scale_to_vl_hinge_pivots_domain_asymmetric():
    """Explicit min/max + hinge resamples a per-stop domain pivoting at
    hinge, confined to [min, max].

    Asymmetric (the default arm_mode) shares one denominator (the longer
    arm's width) across both arms, so the shorter (negative) arm's colors
    never reach full saturation — but every breakpoint still stays within
    the real bounds (never past them), so VL's clamp pins an out-of-domain
    value to the real edge's own color, not an extended virtual one.
    """
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(
        palette=_DIVERGING_STOPS, min=-20000, max=60000, hinge=0
    )
    result = gradient_scale_to_vl(scale)

    domain = result["domain"]
    assert domain[0] == -20000.0
    assert domain[-1] == 60000.0
    assert len(result["range"]) == len(domain)
    hinge_idx = domain.index(0.0)
    assert (
        result["range"][hinge_idx] == "#e4e4e4"
    )  # exact neutral stop, not approximated
    # Negative (shorter) arm never reaches the full extreme stop; positive
    # (longer) arm does, exactly at the real bound.
    assert result["range"][0] != "#08306b"
    assert result["range"][-1] == "#67001f"
    assert result["clamp"] is True
    assert "domainMin" not in result
    assert "domainMax" not in result
    # Pinned explicitly on the discrete stop-list (range) branch, where it
    # actually matters (unlike a scheme, where it's a harmless no-op).
    assert result["interpolate"] == "hcl"


def test_gradient_scale_to_vl_hinge_symmetric_arm_mode():
    """arm_mode='symmetric' stretches each arm across its own width, so both
    extremes land exactly on the real domain edges."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(
        palette=_DIVERGING_STOPS,
        min=-20000,
        max=60000,
        hinge=0,
        arm_mode="symmetric",
    )
    result = gradient_scale_to_vl(scale)

    assert result["domain"] == [-20000.0, -10000.0, 0.0, 30000.0, 60000.0]


def test_gradient_scale_to_vl_hinge_uses_data_derived_bounds():
    """No authored min/max: hinge still pivots using the real data extent —
    the board YAML from the reported issue sets no min/max at all. The
    extent is nice-widened (scale.nice defaults True), matching what
    apply_gradient_legend_endpoint_labels independently labels for the same
    unauthored-bound case — a narrower raw-extent domain here would paint
    colors that stop short of the legend's labeled ticks."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import (
        _nice_domain_ticks,
        gradient_scale_to_vl,
    )

    scale = ResolvedScaleTargetConfig(palette=_DIVERGING_STOPS, hinge=0)
    data = [{"profit": 56000}, {"profit": -20000}, {"profit": 10000}]
    result = gradient_scale_to_vl(scale, data, "profit")

    nice_ticks = _nice_domain_ticks(scale, data, "profit")
    assert nice_ticks is not None
    assert 0.0 in result["domain"]  # hinge is always an exact breakpoint
    assert result["domain"][0] == nice_ticks[0]
    assert result["domain"][-1] == nice_ticks[-1]


def test_gradient_scale_to_vl_hinge_auto_resolves_zero_crossing():
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(
        palette=_DIVERGING_STOPS, min=-20000, max=60000, hinge="auto"
    )
    result = gradient_scale_to_vl(scale)

    assert 0.0 in result["domain"]


def test_gradient_scale_to_vl_hinge_with_scheme_palette_and_bounds_builds_breakpoints():
    """A Vega scheme name has no discrete stop list, but once bounds resolve
    (authored min/max here) it still honors ``arm_mode`` — not a bare
    ``domainMid``, which has no arm_mode concept and is always symmetric.
    ``domain`` stays exactly [min, max] (never extended past the real
    bounds); the scheme's ``extent`` re-windows the color ramp instead, so
    VL's clamp still pins an out-of-domain value to the real edge."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(palette="redblue", min=-20000, max=60000, hinge=0)
    result = gradient_scale_to_vl(scale)

    assert result["domain"] == [-20000.0, 60000.0]
    assert result["clamp"] is True
    assert result["scheme"]["name"] == "redblue"
    assert result["scheme"]["extent"] == pytest.approx([1 / 3, 1.0])
    assert "domainMid" not in result


def test_gradient_scale_to_vl_hinge_with_scheme_palette_honors_symmetric_arm_mode():
    """Symmetric needs no ``extent`` re-windowing — its per-arm denominators
    equal each arm's own real width, so a plain 2-point ``domain`` +
    ``domainMid`` (VL's native mechanism) already lands both extremes
    exactly on the real bounds."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(
        palette="redblue", min=-20000, max=60000, hinge=0, arm_mode="symmetric"
    )
    result = gradient_scale_to_vl(scale)

    assert result == {
        "scheme": "redblue",
        "domain": [-20000.0, 60000.0],
        "domainMid": 0.0,
        "clamp": True,
        "interpolate": "hcl",
    }


def test_gradient_scale_to_vl_hinge_with_scheme_palette_single_sided_bound():
    """A single authored bound must not be silently dropped once the scheme
    branch bakes an explicit domain — the free edge still falls back to the
    real (non-nice-widened, matching a single-sided bound's existing
    precedence) data extent, same as a discrete palette. domain stays
    exactly [min, data max] — never extended past either real bound."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(palette="redblue", min=-20000, hinge=0)
    data = [{"v": -20000}, {"v": 56000}]
    result = gradient_scale_to_vl(scale, data, "v")

    assert result["domain"] == [-20000.0, 56000.0]
    assert result["scheme"]["extent"] == pytest.approx([0.5 - 20000 / (2 * 56000), 1.0])


def test_gradient_scale_to_vl_hinge_with_scheme_palette_no_bounds_needed():
    """A scheme palette needs no resolvable bounds at all for an explicit
    numeric hinge — VL/Vega computes the domain from the actual rendered
    mark data and just inserts the pivot into it. This is the geoshape
    choropleth's real call shape: no min/max authored, and no data passed
    (its pre-lookup-join opt-out) — the one hinge path that still works
    there, unlike a discrete stop list which genuinely needs bounds."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(palette="redblue", hinge=0)
    result = gradient_scale_to_vl(scale)

    assert result == {
        "scheme": "redblue",
        "domainMid": 0.0,
        "clamp": True,
        "interpolate": "hcl",
    }


def test_gradient_scale_to_vl_hinge_auto_with_scheme_and_no_bounds_is_noop():
    """Unlike an explicit numeric hinge, "auto" needs real bounds to decide
    zero-crossing vs. midpoint — with neither authored min/max nor data, a
    scheme palette's hinge stays unresolved and the sequential path runs."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(palette="redblue", hinge="auto")
    result = gradient_scale_to_vl(scale)

    assert result == {"scheme": "redblue"}


def test_gradient_scale_to_vl_hinge_even_length_palette_pivots_at_boundary():
    """An even-length diverging palette is valid: it has no single shared
    center stop, so a synthesized HCL 50/50 blend of the two center stops
    is inserted as a new middle color, and hinge lands EXACTLY on that
    synthesized breakpoint (not merely somewhere between the two original
    center stops — see ``_diverging_domain``'s docstring for why the
    "two independently-spaced center breakpoints" design this replaced put
    the visual pivot off the real hinge for unequal arm widths)."""
    from dbt_charts.core.colors import interpolate_hcl
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    palette = ["#08306b", "#e4e4e4", "#b2182b", "#67001f"]
    scale = ResolvedScaleTargetConfig(palette=palette, min=-1, max=1, hinge=0)
    result = gradient_scale_to_vl(scale)
    domain, colors = result["domain"], result["range"]
    assert domain[0] == -1.0
    assert domain[-1] == 1.0
    assert len(domain) == 5  # one synthesized center stop, odd-length now
    hinge_idx = domain.index(0.0)
    assert colors[hinge_idx] == interpolate_hcl(palette[1], palette[2], 0.5)


def test_gradient_scale_to_vl_hinge_even_length_palette_pivots_at_boundary_symmetric():
    """Same even-length pivot, ``arm_mode="symmetric"`` — pin both arm modes
    are still validated after the odd/>=3 restriction was lifted."""
    from dbt_charts.core.colors import interpolate_hcl
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    palette = ["#08306b", "#e4e4e4", "#b2182b", "#67001f"]
    scale = ResolvedScaleTargetConfig(
        palette=palette,
        min=-1,
        max=1,
        hinge=0,
        arm_mode="symmetric",
    )
    result = gradient_scale_to_vl(scale)
    domain, colors = result["domain"], result["range"]
    assert domain[0] == -1.0
    assert domain[-1] == 1.0
    hinge_idx = domain.index(0.0)
    assert colors[hinge_idx] == interpolate_hcl(palette[1], palette[2], 0.5)


def test_gradient_scale_to_vl_hinge_even_length_palette_pivots_exactly_with_unequal_arms():
    """Regression at the ``gradient_scale_to_vl`` level: unequal arm widths
    (min/max not symmetric around hinge) must not move the visual pivot off
    hinge for an even-length palette."""
    from dbt_charts.core.colors import interpolate_hcl
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    palette = ["#08306b", "#e4e4e4", "#b2182b", "#67001f"]
    scale = ResolvedScaleTargetConfig(
        palette=palette, min=-10, max=90, hinge=0, arm_mode="symmetric"
    )
    result = gradient_scale_to_vl(scale)
    domain, colors = result["domain"], result["range"]
    assert domain[0] == -10.0
    assert domain[-1] == 90.0
    hinge_idx = domain.index(0.0)
    assert colors[hinge_idx] == interpolate_hcl(palette[1], palette[2], 0.5)


def test_gradient_scale_to_vl_hinge_outside_bounds_extends_domain():
    """An explicit (non-"auto") hinge is never checked against the domain
    by resolve_hinge — an author can set hinge: 0 on data that never
    crosses zero. The domain must EXTEND to include it (never clamp the
    hinge onto the nearer bound, which would silently repaint the pivot
    somewhere the author never asked for)."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(
        palette=_DIVERGING_STOPS, min=500, max=1000, hinge=0
    )
    result = gradient_scale_to_vl(scale)
    domain = result["domain"]
    assert domain == sorted(domain)
    assert domain[0] == 0.0
    assert domain[-1] == 1000.0

    scheme_scale = ResolvedScaleTargetConfig(
        palette="redblue", min=500, max=1000, hinge=0
    )
    scheme_result = gradient_scale_to_vl(scheme_scale)
    assert scheme_result["domain"] == [0.0, 1000.0]
    a, b = scheme_result["scheme"]["extent"]
    assert 0.0 <= a <= b <= 1.0


def test_gradient_scale_to_vl_hinge_truncated_boundary_handles_non_hex_stops():
    """A CSS name or rgb()/hsl() palette stop must not crash the truncated-
    breakpoint path — the perceptual boundary-stop math (interpolate_hcl)
    has to parse it."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(
        palette=["blue", "white", "red"], min=-10, max=1000, hinge=0
    )
    result = gradient_scale_to_vl(scale)
    assert result["domain"][0] == -10.0
    assert result["domain"][-1] == 1000.0
    hinge_idx = result["domain"].index(0.0)
    assert result["range"][hinge_idx] == "white"  # untouched, mid stop survives as-is
    assert result["range"][-1] == "red"  # untouched, real bound reaches full stop
    assert result["range"][0] not in ("blue", "red")  # the blended synthetic stop


def test_gradient_scale_to_vl_hinge_no_bounds_passthrough_still_pivots_evenly():
    """A palette that never needs truncation (arms fit within [lo, hi]) is
    unmodified — an original non-hex stop like ``blue``/``red`` passes
    through untouched, matching how VL/CSS already renders these natively."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(
        palette=["blue", "white", "red"], min=-1, max=1, hinge=0
    )
    result = gradient_scale_to_vl(scale)
    assert result["range"] == ["blue", "white", "red"]
    assert result["domain"] == [-1.0, 0.0, 1.0]


def test_gradient_scale_to_vl_hinge_translucent_stop_passes_through():
    """A translucent stop (alpha < 1) is no longer rejected: original
    palette stops are placed directly as breakpoints (no RGB-lerp resample
    of them), so a translucent stop that survives untouched just passes
    through to VL/CSS, which already renders alpha natively. One that
    straddles a truncation boundary gets its alpha interpolated linearly
    alongside the perceptual color blend (interpolate_hcl's own contract)."""
    from dbt_charts.core.colors import parse_css_color
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(
        palette=["#d73027", "transparent", "#1a9850"], min=-10, max=40, hinge=0
    )
    result = gradient_scale_to_vl(scale)
    assert "transparent" in result["range"]
    assert result["domain"][0] == -10.0
    _, _, _, alpha = parse_css_color(result["range"][0])
    assert 0.0 < alpha < 1.0


def test_gradient_scale_to_vl_hinge_auto_uses_raw_extent_not_nice_widened():
    """ "auto" must decide zero-crossing vs. midpoint from the RAW data
    extent, matching table/KPI's own auto-hinge — not the nice-widened
    rendered domain, which can invent or shift an edge the real data
    doesn't have (regression: data [-97, -3] nice-widens to [-100, 0],
    which would incorrectly fire the zero-crossing rule and put every
    value on one arm)."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(palette=_DIVERGING_STOPS, hinge="auto")

    result = gradient_scale_to_vl(scale, [{"v": 3}, {"v": 61}], "v")
    hinge_idx = result["range"].index("#e4e4e4")
    assert result["domain"][hinge_idx] == 32.0  # raw midpoint, not 40 (nice-widened)

    result2 = gradient_scale_to_vl(scale, [{"v": -97}, {"v": -3}], "v")
    hinge_idx2 = result2["range"].index("#e4e4e4")
    assert result2["domain"][hinge_idx2] == -50.0  # raw midpoint, not 0 (nice-widened)


def test_gradient_scale_to_vl_hinge_symmetric_degenerate_arm_paints_neutral():
    """Regression: hinge (0) sits below the authored min (500) — the domain
    now EXTENDS down to include it (0, not 500) rather than clamping hinge
    up to the authored bound, so the negative arm has zero real width
    against the new [0, 1000] domain. Every breakpoint on that degenerate
    arm must be dropped except the single boundary one, which lands exactly
    on the extended lo and carries the exact neutral color — not the old
    sign-inverting bug, where a floored 1e-12 arm denominator placed a
    breakpoint a hair short of the real bound, paired with the full-
    saturation extreme color instead of neutral."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(
        palette=_DIVERGING_STOPS, min=500, max=1000, hinge=0, arm_mode="symmetric"
    )
    result = gradient_scale_to_vl(scale)

    domain = result["domain"]
    assert domain == sorted(domain)
    assert domain[0] == 0.0  # extended down to include hinge, not clamped to 500
    assert domain[-1] == 1000.0
    assert result["range"][0] == "#e4e4e4"  # degenerate arm's edge is neutral
    assert (
        result["range"][-1] == "#67001f"
    )  # the real (non-degenerate) arm still reaches full


def test_gradient_scale_to_vl_hinge_symmetric_degenerate_arm_data_equals_hinge():
    """Regression companion: hinge can still legitimately equal a real
    (not extended) bound when the data/authored bound already coincides
    with it exactly — e.g. hinge=0, data exactly [0, 100]. This must
    degenerate the same clean way as the extension case above, not via
    clamping (there is nothing to clamp here — 0 was already the true lo)."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(
        palette=_DIVERGING_STOPS, min=0, max=100, hinge=0, arm_mode="symmetric"
    )
    result = gradient_scale_to_vl(scale)

    domain = result["domain"]
    assert domain == sorted(domain)
    assert domain[0] == 0.0
    assert domain[-1] == 100.0
    assert result["range"][0] == "#e4e4e4"
    assert result["range"][-1] == "#67001f"


def test_gradient_scale_to_vl_hinge_domainMid_fallback_extends_single_sided_bound():
    """Regression: the domainMid fallback (bounds unresolved — e.g.
    geoshape with no data) must EXTEND whichever bound IS authored when
    hinge falls outside it, not clamp hinge onto that bound — an
    out-of-order domainMin/domainMid pair would still reach Vega unsorted,
    which it does not fix up on its own, but the extended pair stays
    correctly ordered by construction."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(palette="redblue", min=500, hinge=0)
    result = gradient_scale_to_vl(scale)

    assert result["domainMin"] == 0
    assert result["domainMid"] == 0


def test_gradient_scale_to_vl_no_hinge_unchanged():
    """Sequential (no hinge) scales keep emitting the old domain/domainMin
    shape — this fix must not touch that path."""
    from dbt_charts.core.compile.models.primitives import ResolvedScaleTargetConfig
    from dbt_charts.core.render.chart.emitters._channels import gradient_scale_to_vl

    scale = ResolvedScaleTargetConfig(palette=_DIVERGING_STOPS, min=-20000, max=60000)
    result = gradient_scale_to_vl(scale)

    assert result == {"range": _DIVERGING_STOPS, "domain": [-20000, 60000]}


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
