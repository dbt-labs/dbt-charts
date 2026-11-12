"""Structural rejection: ColorStyle vs GradientColorStyle family enforcement."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_geoshape_rejects_categorical() -> None:
    from dbt_charts.core.compile.models.style.theme.geoshape import GeoshapeChartStyle

    # Must fail: GeoshapeChartStyle.color is GradientColorStyle, no categorical arm
    with pytest.raises(ValidationError, match="categorical"):
        GeoshapeChartStyle.model_validate({"color": {"categorical": ["#ff0000"]}})


def test_table_rejects_categorical() -> None:
    # TableChartStylePatch.color is GradientColorStylePatch — no categorical arm.
    # Uses the all-Optional Patch (not the theme class) so the error is isolated to
    # the color field, not drowned out by unrelated required-field errors.
    from dbt_charts.core.compile.models.primitives import GradientColorStyle
    from dbt_charts.core.compile.models.style.authored import TableChartStylePatch

    with pytest.raises(ValidationError, match="categorical"):
        GradientColorStyle.model_validate({"categorical": ["#ff0000"]})
    with pytest.raises(ValidationError, match="categorical"):
        TableChartStylePatch.model_validate({"color": {"categorical": ["#ff0000"]}})


def test_kpi_rejects_categorical_and_gradient() -> None:
    # KpiChartStylePatch has no color field at all — KPI has no series axis
    # and no gradient-eligible channel, so any color shape (dict or scalar)
    # is rejected extra_forbidden. Uses the all-Optional Patch (not the theme
    # class) so the error is isolated to the color field, not drowned out by
    # unrelated required-field errors.
    from dbt_charts.core.compile.models.style.authored.kpi import KpiChartStylePatch

    with pytest.raises(ValidationError, match="color"):
        KpiChartStylePatch.model_validate({"color": {"categorical": ["#ff0000"]}})
    with pytest.raises(ValidationError, match="color"):
        KpiChartStylePatch.model_validate(
            {"color": {"gradient": {"palette": ["#fff", "#000"]}}}
        )


def test_cartesian_accepts_categorical() -> None:
    from dbt_charts.core.compile.models.primitives import ColorStyle

    cs = ColorStyle(categorical={"palette": ["#ff0000", "#0000ff"]})
    assert cs.categorical is not None
    assert cs.categorical.palette == ["#ff0000", "#0000ff"]
    assert cs.gradient is None


def test_color_style_palette_name_expanded() -> None:
    from dbt_charts.core.compile.models.primitives import ColorStyle

    cs = ColorStyle(categorical={"palette": "vivid-10"})
    assert cs.categorical is not None
    assert isinstance(cs.categorical.palette, list)
    assert len(cs.categorical.palette) == 10


def test_color_style_inherits_gradient_color_style() -> None:
    from dbt_charts.core.compile.models.primitives import (
        ColorStyle,
        GradientColorStyle,
    )

    assert issubclass(ColorStyle, GradientColorStyle)


def test_categorical_color_style_holds_palette_and_ssp() -> None:
    from dbt_charts.core.compile.models.primitives import CategoricalColorStyle

    c = CategoricalColorStyle(palette=["#ff0000"], single_series_palette=["#0000ff"])
    assert c.palette == ["#ff0000"]
    assert c.single_series_palette == ["#0000ff"]


def test_gradient_color_style_accepts_gradient_and_rejects_static() -> None:
    from dbt_charts.core.compile.models.primitives import (
        GradientColorStyle,
        ScaleTargetConfig,
    )

    sg = GradientColorStyle(gradient=ScaleTargetConfig(palette=["#fff", "#000"]))
    assert sg.gradient is not None
    with pytest.raises(ValidationError, match="static"):
        GradientColorStyle.model_validate({"static": "#ff0000"})


def test_categorical_color_style_records_requested_alias_palette() -> None:
    """Authoring a WARN-PALETTE-UNSUPPORTED anti-pattern alias (e.g. RdYlGn)
    retains the originally-requested name on the model, so a render-stage
    detector can surface the nudge without palette() emitting inline."""
    from dbt_charts.core.compile.models.primitives import CategoricalColorStyle

    c = CategoricalColorStyle(palette="RdYlGn")
    assert c.requested_alias_palette == "RdYlGn"
    assert c.palette is not None and len(c.palette) > 0


def test_categorical_color_style_non_alias_leaves_requested_alias_none() -> None:
    from dbt_charts.core.compile.models.primitives import CategoricalColorStyle

    c = CategoricalColorStyle(palette="dbt-seq-blue")
    assert c.requested_alias_palette is None


def test_categorical_color_style_unauthored_palette_leaves_requested_alias_none() -> (
    None
):
    """A layer that doesn't author palette at all must not fabricate a
    requested_alias_palette value — it needs to fall through to a lower
    layer's merged value untouched."""
    from dbt_charts.core.compile.models.primitives import CategoricalColorStyle

    c = CategoricalColorStyle(single_series_palette=["#0000ff"])
    assert c.requested_alias_palette is None


def test_categorical_color_style_rejects_authored_requested_alias_palette() -> None:
    """requested_alias_palette is an internal provenance field computed by
    _expand_palette_names — a YAML author fabricating it directly must be
    rejected loudly, not silently accepted and then honored or ignored
    depending on cascade layer (see WARN-PALETTE-UNSUPPORTED fabrication bug)."""
    from dbt_charts.core.compile.models.primitives import CategoricalColorStyle

    with pytest.raises(ValidationError, match="requested_alias_palette"):
        CategoricalColorStyle(
            palette=["#aabbcc", "#112233"], requested_alias_palette="totally-made-up"
        )


def test_categorical_color_style_revalidates_its_own_computed_provenance() -> None:
    """merge.py rebuilds a compiled model with ``model_validate()`` over a dict
    of *every* field, provenance included — so rejecting the mere presence of
    the key (rather than a fabricated value) broke every theme -> board palette
    cascade. A value the resolver itself produced must round-trip."""
    from dbt_charts.core.compile.models.primitives import CategoricalColorStyle

    resolved = CategoricalColorStyle(palette="RdYlGn")
    assert resolved.requested_alias_palette == "RdYlGn"

    assert (
        CategoricalColorStyle.model_validate(
            {
                "palette": resolved.palette,
                "single_series_palette": None,
                "requested_alias_palette": resolved.requested_alias_palette,
            }
        ).requested_alias_palette
        == "RdYlGn"
    )


def test_categorical_color_style_requested_alias_palette_excluded_from_schema() -> None:
    """The field must not appear in the authorable JSON Schema / yaml-reference —
    otherwise IDE autocomplete offers it and a user can author a bogus value."""
    from dbt_charts.core.compile.models.primitives import CategoricalColorStyle

    field_info = CategoricalColorStyle.model_fields["requested_alias_palette"]
    assert field_info.exclude is True


def test_categorical_color_style_patch_omits_requested_alias_palette() -> None:
    """The generated chart-local style patch must not carry the internal
    provenance field either — exclude=True fields must not leak into
    build_patch_model()-generated overlay types."""
    from dbt_charts.core.compile.models.factories import build_patch_model
    from dbt_charts.core.compile.models.primitives import CategoricalColorStyle

    patch_cls = build_patch_model(CategoricalColorStyle)
    assert "requested_alias_palette" not in patch_cls.model_fields
