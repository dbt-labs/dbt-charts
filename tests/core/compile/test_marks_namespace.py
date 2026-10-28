"""Tests for the marks: namespace.

All tests in this file fail before the model layer is rewritten;
they serve as the TDD contract for the implementation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Family marks containers — extra="forbid" rejects wrong marks
# ---------------------------------------------------------------------------


def test_bar_chart_marks_style_rejects_unknown_mark():
    """BarChartMarksStyle rejects a mark that bar charts never emit."""
    from dbt_charts.core.compile.models.style.theme import BarChartMarksStyle

    with pytest.raises(ValidationError):
        BarChartMarksStyle(geoshape={"stroke": {"color": "#000"}})


def test_global_marks_style_rejects_unknown_key():
    """GlobalMarksStyle rejects keys that are not VL mark types."""
    from dbt_charts.core.compile.models.style.theme import GlobalMarksStyle

    with pytest.raises(ValidationError):
        GlobalMarksStyle(nonexistent={"opacity": 0.5})


# ---------------------------------------------------------------------------
# Three-tier cascade: global < family < board
# ---------------------------------------------------------------------------


def test_family_marks_cascade_tier_ordering():
    """Family mark override shadows global; board override shadows family.

    The cascade contract:
        tier-1 global default → tier-2 family override → tier-3 board override
    Per-field shadowing: setting one field in tier-2 does not wipe tier-1 fields
    on the same mark.
    """
    from dbt_charts.core.compile.models.style.theme import BarMarkStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_mark

    # Global tier: bar.size=10, bar.padding=5
    global_bar = BarMarkStyle.model_validate({"size": 10.0, "padding": 5.0})

    # Family tier sets bar.size=20 but leaves padding unset → should inherit 5.0
    family_bar = BarMarkStyle.model_validate({"size": 20.0})

    merged = resolve_mark(global_bar, family_bar)

    assert merged.size == 20.0, "family tier should override global size"
    assert merged.padding == 5.0, (
        "global padding should survive when family doesn't set it"
    )


def test_global_default_flows_to_family_with_no_override():
    """When a family has no marks overrides, global defaults flow through unchanged."""
    from dbt_charts.core.compile.models.style.theme import BarMarkStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_mark

    global_bar = BarMarkStyle.model_validate({"size": 15.0, "padding": 3.0})

    # None family override → global passes through unchanged
    merged = resolve_mark(global_bar, None)

    assert merged.size == 15.0
    assert merged.padding == 3.0


# ---------------------------------------------------------------------------
# label → text rename
# ---------------------------------------------------------------------------


def test_label_to_text_rename_chartstyle_rejects_label():
    """ChartsStyle rejects 'label' — it was renamed to marks.text."""
    from dbt_charts.core.compile.models.style.theme import ChartsStyle

    with pytest.raises(ValidationError):
        ChartsStyle.model_validate({"label": {"font": {}}})


def test_global_marks_style_accepts_text():
    """GlobalMarksStyle has a 'text' field (the renamed label slot)."""
    from dbt_charts.core.compile.models.style.theme import (
        GlobalMarksStyle,
        TextMarkStyle,
    )

    g = GlobalMarksStyle.model_validate({})
    assert hasattr(g, "text"), "GlobalMarksStyle must have a 'text' field"
    assert isinstance(g.text, TextMarkStyle)


# ---------------------------------------------------------------------------
# Legacy flat mark keys rejected on ChartsStyle
# ---------------------------------------------------------------------------


def test_legacy_flat_mark_keys_rejected_on_charts_style():
    """ChartsStyle no longer accepts top-level mark-type keys (moved to marks: block)."""
    from dbt_charts.core.compile.models.style.theme import ChartsStyle

    # These were top-level VL mark slots; all moved to ChartsStyle.marks.*
    for key in ("circle", "square", "tick", "rule", "trail", "rect"):
        with pytest.raises(ValidationError):
            ChartsStyle.model_validate({key: {}})


# ---------------------------------------------------------------------------
# Non-graphical families and pie-total placement
# ---------------------------------------------------------------------------


def test_pie_total_stays_flat_not_in_marks():
    """PieChartMarksStyle has no total field; extra='forbid' rejects it."""
    from dbt_charts.core.compile.models.style.theme import PieChartMarksStyle

    # total is a chart-level field (not a mark); PieChartMarksStyle rejects it.
    with pytest.raises((ValidationError, TypeError)):
        PieChartMarksStyle.model_validate({"total": {}})


# ---------------------------------------------------------------------------
# _ChartStyleBase geometry cascade from ChartsStyle
# ---------------------------------------------------------------------------


def test_family_style_aspect_ratio_cascades_from_charts_style():
    """After cascade, bar.aspect_ratio is populated from ChartsStyle.aspect_ratio.

    Uses a distinctive sentinel value to prove cascade rather than comparing
    to the default (which would be a theme-pinning violation).
    """
    from dbt_charts.core.compile.config import (
        get_theme_style,
        reset_config,
    )
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    reset_config()
    base = get_theme_style()
    _SENTINEL = 99.99
    patched = base.model_copy(
        update={"charts": base.charts.model_copy(update={"aspect_ratio": _SENTINEL})}
    )
    merged = resolve_chart_style_context(patched)
    # After cascade, per-family aspect_ratio must equal the sentinel we injected.
    assert merged.bar.aspect_ratio == _SENTINEL
    assert merged.line.aspect_ratio == _SENTINEL
    assert merged.scatter.aspect_ratio == _SENTINEL


def test_global_marks_slice_labels_populated():
    """Global marks.slice.labels is populated in the default theme (moved from pie.marks.slice)."""
    from dbt_charts.core.compile.config import get_theme_style, reset_config

    reset_config()
    compiled = get_theme_style("clarity")
    assert compiled.charts.marks.slice.labels is not None, (
        "marks.slice.labels must be populated in the global marks block"
    )


# ---------------------------------------------------------------------------
# Global marks cascade into family marks
# ---------------------------------------------------------------------------


def test_global_marks_cascade_into_family_pie_slice():
    """After cascade, pie.marks.slice is pre-filled from global marks.slice.

    Before the fix, pie.marks.slice was None unless the theme explicitly set a
    family-level override. A chart-local labels override then hit the
    base_val=None branch in merge_onto_base and failed to validate SliceLabelsStyle
    because line_height was missing.
    """
    from dbt_charts.core.compile.config import get_theme_style, reset_config
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    reset_config()
    merged = resolve_chart_style_context(get_theme_style("clarity"))
    pie_slice = merged.pie.marks.slice
    assert pie_slice is not None, "pie.marks.slice must be pre-filled from global marks"
    assert pie_slice.labels is not None
    assert pie_slice.labels.line_height is not None


def test_chart_local_pie_slice_labels_partial_override_preserves_global_defaults():
    """A board-level pie.marks.slice.labels override that sets only offset and font
    must not discard global line_height.

    Regression: build_chart_style_context used to hit merge_onto_base(base=None, patch=...) for
    pie.marks.slice, serialize the patch with exclude_none=True, then fail to validate
    SliceLabelsStyle because line_height was missing.
    """
    from dbt_charts.core.compile.config import reset_config
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.models.style.authored import PieChartStylePatch
    from dbt_charts.core.compile.resolve.style.chart_context import (
        build_chart_style_context,
    )

    reset_config()
    board_resolved = (
        None  # falls back to default theme inside build_chart_style_context
    )

    chart_style = PieChartStylePatch.model_validate(
        {
            "marks": {
                "slice": {
                    "labels": {
                        "offset": 4,
                        "font": {"size": 10},
                    }
                }
            }
        }
    )

    resolved = build_chart_style_context(
        board_resolved, PieChart(id="t", type="pie", theta="x", style=chart_style)
    )
    slice_labels = resolved.pie.marks.slice.labels
    assert slice_labels is not None
    assert slice_labels.offset == 4.0, "board override must take effect"
    assert slice_labels.line_height is not None, "global line_height must survive"


# ---------------------------------------------------------------------------
# Theme corpus smoke
# ---------------------------------------------------------------------------


def test_bar_total_label_style_constructs_empty() -> None:
    """BarTotalLabelStyle constructs with no args.

    Scalar fields (visible, format, dx, dy) default to None (cascade sentinels).
    font defaults to an empty FontStyle (InheritSlot -- not None; sub-fields fill
    from charts.font at cascade time, and are None-defaulted sentinels before that).
    """
    from dbt_charts.core.compile.models.primitives import FontStyle
    from dbt_charts.core.compile.models.style.theme import BarTotalLabelStyle

    tl = BarTotalLabelStyle()
    assert tl.visible is None
    assert tl.format is None
    assert tl.dx is None
    assert tl.dy is None
    assert isinstance(tl.font, FontStyle)
    # Sub-fields are cascade sentinels -- all None before InheritSlot cascade.
    assert tl.font.color is None


def test_bar_total_label_format_cascades_from_global_marks() -> None:
    """A distinctive format injected at charts.marks.bar.total_label.format
    flows through resolve_style to the resolved bar mark style.

    Uses the distinctive-value-plus-propagation pattern: inject a sentinel
    value and confirm the same value survives cascade.  Never compare to a
    literal theme default (theme-pinning violation).
    """
    from dbt_charts.core.compile.config import get_theme_style, reset_config
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    reset_config()
    compiled = get_theme_style()
    _SENTINEL_FORMAT = ",.3f"
    new_total = compiled.charts.marks.bar.total_label.model_copy(
        update={"format": _SENTINEL_FORMAT}
    )
    new_bar = compiled.charts.marks.bar.model_copy(update={"total_label": new_total})
    new_marks = compiled.charts.marks.model_copy(update={"bar": new_bar})
    patched = compiled.model_copy(
        update={"charts": compiled.charts.model_copy(update={"marks": new_marks})}
    )
    resolved = resolve_chart_style_context(patched)
    assert resolved.bar.marks.bar.total_label.format == _SENTINEL_FORMAT, (
        "total_label.format must survive resolve_chart_style_context cascade"
    )


def test_theme_corpus_smoke():
    """Every built-in theme compiles successfully after the marks migration."""
    from dbt_charts.core.compile.config import (
        get_theme_style,
        list_built_in_themes,
        reset_config,
    )
    from dbt_charts.core.compile.models.style.theme import Style

    reset_config()
    errors: list[str] = []
    for name in list_built_in_themes():
        if name.startswith("diagnostics-") or name.startswith("_"):
            continue
        try:
            theme = get_theme_style(name)
            assert isinstance(theme, Style)
        except Exception as exc:  # noqa: BLE001 — batch-collects all compile failures
            errors.append(f"{name}: {exc}")
    assert not errors, "Themes failed to compile:\n" + "\n".join(errors)
