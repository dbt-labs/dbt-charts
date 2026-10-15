"""TDD: axis → axis_x/y/quantitative cascade for ALL fields, at render time.

charts.axis_x/axis_y/axis_quantitative carry SkipInheritSlots, not InheritSlot:
apply_inherit never fills their unset leaves from the shared `axis` global at
compile time — they stay sparse, authored-only overlays. The cascade happens at
render/emit time instead, via resolved_axis_style() (compile/resolve/style/axis_cascade.py),
which merges axis_x/y/quantitative onto axis in the canonical layer order.

So: setting a field on charts.axis reaches axis_x/y/quantitative through
resolved_axis_style(), not by reading charts.axis_x/y/quantitative directly.
"""

from __future__ import annotations

from typing import TypeVar

import pytest
from pydantic import BaseModel

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_ModelT = TypeVar("_ModelT", bound=BaseModel)


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _omit(model: _ModelT, field_name: str) -> _ModelT:
    values = {
        name: None if name == field_name else getattr(model, name)
        for name in type(model).model_fields
    }
    return type(model).model_construct(
        _fields_set=model.model_fields_set - {field_name},
        **values,
    )


def test_axis_grid_color_cascades_to_axis_x():
    """axis.grid.color reaches axis_x when axis_x.grid.color is omitted."""
    base = get_theme_style("clarity")
    axis_patch = base.charts.axis.model_copy(
        update={"grid": base.charts.axis.grid.model_copy(update={"color": "#aabbcc"})}
    )
    axis_x_patch = base.charts.axis_x.model_copy(
        update={"grid": _omit(base.charts.axis_x.grid, "color")}
    )
    patched = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={"axis": axis_patch, "axis_x": axis_x_patch}
            )
        }
    )
    resolved = resolve_chart_style_context(patched)
    # Use "ordinal" (not "quantitative") so axis_quantitative's own Layer-3
    # authored grid.color doesn't mask the axis_x/axis cascade under test.
    emitted = resolved_axis_style(
        resolved, "axis_x", "ordinal", chart_type="", labeled=False
    )
    assert emitted.grid.color == "#aabbcc"


def test_axis_grid_threshold_color_cascades_to_measure_axis():
    """axis_y.grid.threshold.color reaches the resolved y-axis, isolated
    from a sibling channel.

    threshold now lives on the shared BaseAxisGridStyle (moved off the
    deleted y-only MeasureGridStyle), so every axis slot carries it. Setting
    a distinctive color on axis_y and clearing axis_quantitative's own grid
    color verifies the axis_y layer — not some other layer — provides it,
    and that a sibling channel (axis_x) with no channel-level override does
    not pick up the same distinctive value.
    """
    from dbt_charts.core.compile.models.style.theme import AxisGridThresholdStyle

    base = get_theme_style("clarity")
    new_threshold = AxisGridThresholdStyle(color="#ff0000", width=1, visible=True)
    axis_y_patch = base.charts.axis_y.model_copy(
        update={
            "grid": base.charts.axis_y.grid.model_copy(
                update={"threshold": new_threshold}
            )
        }
    )
    axis_quantitative_patch = base.charts.axis_quantitative.model_copy(
        update={
            "grid": base.charts.axis_quantitative.grid.model_copy(
                update={"color": None}
            )
        }
    )
    patched = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "axis_y": axis_y_patch,
                    "axis_quantitative": axis_quantitative_patch,
                }
            )
        }
    )
    resolved = resolve_chart_style_context(patched)
    emitted_y = resolved_axis_style(
        resolved, "axis_y", "quantitative", chart_type="", labeled=False
    )
    assert emitted_y.grid.threshold.color == "#ff0000"
    # x-axis has no channel-level override, so it inherits the shared `axis`
    # slot's threshold instead of the y-only distinctive value — no longer
    # structurally absent the way grid.zero was pre-generalization.
    emitted_x = resolved_axis_style(
        resolved, "axis_x", "quantitative", chart_type="", labeled=False
    )
    assert emitted_x.grid.threshold.color != "#ff0000"


def test_axis_grid_threshold_visible_false_resolves_on_x_axis():
    """threshold.visible: false is now authorable on axis_x — a slot that
    could not carry the block at all before this change — and resolves to visible=False rather
    than raising or silently ignoring the override.
    """
    from dbt_charts.core.compile.models.style.theme import AxisGridThresholdStyle

    base = get_theme_style("clarity")
    axis_x_patch = base.charts.axis_x.model_copy(
        update={
            "grid": base.charts.axis_x.grid.model_copy(
                update={"threshold": AxisGridThresholdStyle(visible=False)}
            )
        }
    )
    patched = base.model_copy(
        update={"charts": base.charts.model_copy(update={"axis_x": axis_x_patch})}
    )
    resolved = resolve_chart_style_context(patched)
    emitted_x = resolved_axis_style(
        resolved, "axis_x", "ordinal", chart_type="", labeled=False
    )
    assert emitted_x.grid.threshold.visible is False


def test_axis_grid_visible_cascades_to_child_axes():
    """axis.grid.visible cascades to child axes when omitted."""
    base = get_theme_style("clarity")
    axis_patch = base.charts.axis.model_copy(
        update={"grid": base.charts.axis.grid.model_copy(update={"visible": False})}
    )

    def _clear_visible(ax):
        return ax.model_copy(update={"grid": _omit(ax.grid, "visible")})

    patched = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "axis": axis_patch,
                    "axis_x": _clear_visible(base.charts.axis_x),
                    "axis_y": _clear_visible(base.charts.axis_y),
                    "axis_quantitative": _clear_visible(base.charts.axis_quantitative),
                }
            )
        }
    )
    resolved = resolve_chart_style_context(patched)
    emitted_x = resolved_axis_style(
        resolved, "axis_x", "quantitative", chart_type="", labeled=False
    )
    emitted_y = resolved_axis_style(
        resolved, "axis_y", "quantitative", chart_type="", labeled=False
    )
    assert emitted_x.grid.visible is False
    assert emitted_y.grid.visible is False


def test_child_axis_explicit_value_wins_over_cascade():
    """An explicit value in axis_x beats the cascaded value from axis."""
    base = get_theme_style("clarity")
    axis_patch = base.charts.axis.model_copy(
        update={"grid": base.charts.axis.grid.model_copy(update={"color": "#d0e458"})}
    )
    axis_x_patch = base.charts.axis_x.model_copy(
        update={"grid": base.charts.axis_x.grid.model_copy(update={"color": "#1b7d57"})}
    )
    patched = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={"axis": axis_patch, "axis_x": axis_x_patch}
            )
        }
    )
    resolved = resolve_chart_style_context(patched)
    emitted = resolved_axis_style(
        resolved, "axis_x", "ordinal", chart_type="", labeled=False
    )
    assert emitted.grid.color == "#1b7d57"


def test_axis_line_visible_cascades():
    """axis.line.visible cascades to child axes."""
    base = get_theme_style("clarity")
    axis_patch = base.charts.axis.model_copy(
        update={"line": base.charts.axis.line.model_copy(update={"visible": True})}
    )

    def _clear_visible(ax):
        return ax.model_copy(update={"line": _omit(ax.line, "visible")})

    patched = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "axis": axis_patch,
                    "axis_x": _clear_visible(base.charts.axis_x),
                    "axis_y": _clear_visible(base.charts.axis_y),
                }
            )
        }
    )
    resolved = resolve_chart_style_context(patched)
    emitted_x = resolved_axis_style(
        resolved, "axis_x", "quantitative", chart_type="", labeled=False
    )
    emitted_y = resolved_axis_style(
        resolved, "axis_y", "quantitative", chart_type="", labeled=False
    )
    assert emitted_x.line.visible is True
    assert emitted_y.line.visible is True


@pytest.mark.parametrize("theme", ["stark", "clarity", "paper", "vivid", "neon"])
def test_axis_line_color_is_not_transparent_when_visible(theme: str) -> None:
    """A board authoring `style.charts.axis.line.visible: true` must draw a
    real, visible domain line under every shipped theme -- including
    `stark`, the structural root every user-facing theme extends and one
    of the five that lost its own bound color.

    `axis.line.color` is a hidden-chrome default `_base.yaml` keeps
    `transparent` only for the line's *own* inert, invisible-by-default
    state (`visible: false`) -- every theme used to bind its own real color
    for the moment an author turns the line on. Losing that binding makes
    an authored, documented field (`line.visible`) silently no-op instead
    of drawing anything.
    """
    base = get_theme_style(theme)
    axis_patch = base.charts.axis.model_copy(
        update={"line": base.charts.axis.line.model_copy(update={"visible": True})}
    )
    patched = base.model_copy(
        update={"charts": base.charts.model_copy(update={"axis": axis_patch})}
    )
    resolved = resolve_chart_style_context(patched)
    emitted = resolved_axis_style(
        resolved, "axis_x", "ordinal", chart_type="", labeled=False
    )
    assert emitted.line.visible is True
    assert emitted.line.color != "transparent"


def test_axis_ticks_visible_cascades():
    """axis.ticks.visible cascades to child axes."""
    base = get_theme_style("clarity")
    axis_patch = base.charts.axis.model_copy(
        update={"ticks": base.charts.axis.ticks.model_copy(update={"visible": True})}
    )

    def _clear_visible(ax):
        return ax.model_copy(update={"ticks": _omit(ax.ticks, "visible")})

    patched = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "axis": axis_patch,
                    "axis_y": _clear_visible(base.charts.axis_y),
                    "axis_quantitative": _clear_visible(base.charts.axis_quantitative),
                }
            )
        }
    )
    resolved = resolve_chart_style_context(patched)
    emitted_y = resolved_axis_style(
        resolved, "axis_y", "quantitative", chart_type="", labeled=False
    )
    assert emitted_y.ticks.visible is True


def test_base_axis_with_null_grid_color_raises():
    """Every cascade source must provide grid.color — missing from all layers raises.

    The editorial theme provides grid.color at both the shared axis and the
    per-channel axis_y layers. Nulling all sources leaves the resolved axis
    without a grid.color, which build_resolved_axis (called by resolved_axis_style)
    rejects at emit time.
    """
    base = get_theme_style("clarity")

    def _null_color(ax):
        return ax.model_copy(
            update={"grid": ax.grid.model_copy(update={"color": None})}
        )

    patched = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "axis": _null_color(base.charts.axis),
                    "axis_x": _null_color(base.charts.axis_x),
                    "axis_y": _null_color(base.charts.axis_y),
                    "axis_quantitative": _null_color(base.charts.axis_quantitative),
                }
            )
        }
    )
    resolved = resolve_chart_style_context(patched)
    with pytest.raises(ValueError, match="grid.color"):
        resolved_axis_style(
            resolved, "axis_y", "quantitative", chart_type="", labeled=False
        )


def test_paper_resolves_without_error():
    """paper theme resolves cleanly and every axis emits a threshold.

    threshold now lives on the shared BaseAxisGridStyle. paper authors its
    color once, on the shared ``axis`` slot; axis_y and axis_x both inherit
    it through the cascade — neither is structurally excluded from the
    block anymore.
    """
    style = get_theme_style("paper")
    resolved = resolve_chart_style_context(style)
    emitted_y = resolved_axis_style(
        resolved, "axis_y", "quantitative", chart_type="", labeled=False
    )
    emitted_x = resolved_axis_style(
        resolved, "axis_x", "quantitative", chart_type="", labeled=False
    )
    # Compare against the resolved theme slot itself (charts.axis.grid.threshold
    # is paper's one authored color; axis_x/axis_y stay sparse overlays that
    # never carry it directly -- see the module docstring), not emitted_y
    # against emitted_x: that pairwise comparison would still pass even if
    # paper's own threshold block were deleted and both axes instead fell
    # back to some unrelated, merely-equal default.
    theme_threshold_color = style.charts.axis.grid.threshold.color
    assert theme_threshold_color is not None
    assert emitted_y.grid.threshold.color == theme_threshold_color
    assert emitted_x.grid.threshold.color == theme_threshold_color
