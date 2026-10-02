"""Structural rejection of legend/tooltip on kpi/table and tooltip on painting families.

Tests match the "Proposed YAML surface" section of the task worksheet:
- legend/tooltip rejected on kpi/table's per-chart authored patch (KpiChartStylePatch,
  TableChartStylePatch) — the style: block in a board chart.
- tooltip rejected on every painting family's authored patch (ChartStylePatch and
  per-family patches: bar, line, area, scatter, heatmap, histogram, pie, geoshape,
  point_map).
- Theme-layer rejection via StylePatch: style.charts.kpi.aspect_ratio,
  style.charts.table.min_height, style.charts.kpi.legend, style.charts.table.legend,
  style.charts.{any-family}.tooltip — regression for the @cache divergence bug.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# ============================================================================
# Per-chart authored style rejection — legend on kpi/table, tooltip everywhere
# ============================================================================


def test_legend_rejected_on_kpi_authored_patch() -> None:
    """style.legend on an authored kpi chart raises extra_forbidden."""
    from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch

    with pytest.raises(ValidationError) as exc_info:
        KpiChartStylePatch.model_validate({"legend": {"position": {"edge": "bottom"}}})
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)


def test_legend_rejected_on_table_authored_patch() -> None:
    """style.legend on an authored table chart raises extra_forbidden."""
    from dbt_charts.core.compile.models.style.authored import TableChartStylePatch

    with pytest.raises(ValidationError) as exc_info:
        TableChartStylePatch.model_validate({"legend": {"visible": False}})
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)


def test_tooltip_rejected_on_kpi_authored_patch() -> None:
    """style.tooltip on an authored kpi chart raises extra_forbidden."""
    from dbt_charts.core.compile.models.style.authored import KpiChartStylePatch

    with pytest.raises(ValidationError) as exc_info:
        KpiChartStylePatch.model_validate({"tooltip": {"font": {"size": 11}}})
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)


def test_tooltip_rejected_on_table_authored_patch() -> None:
    """style.tooltip on an authored table chart raises extra_forbidden."""
    from dbt_charts.core.compile.models.style.authored import TableChartStylePatch

    with pytest.raises(ValidationError) as exc_info:
        TableChartStylePatch.model_validate({"tooltip": {"font": {"size": 11}}})
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)


# ============================================================================
# Tooltip rejected on all nine painting families' authored patches
# ============================================================================


@pytest.mark.parametrize(
    "patch_name",
    [
        # bar — also covers histogram (HistogramChart authors as BarChart / BarChartStylePatch)
        "BarChartStylePatch",
        "LineChartStylePatch",
        "AreaChartStylePatch",
        "ScatterChartStylePatch",
        "HeatmapChartStylePatch",
        "PieChartStylePatch",
        "GeoshapeChartStylePatch",
        "PointMapChartStylePatch",
    ],
)
def test_tooltip_rejected_on_painting_family_authored_patch(patch_name: str) -> None:
    """style.tooltip raises extra_forbidden on every painting family's authored patch."""
    import dbt_charts.core.compile.models.style.authored as authored_mod

    patch_cls = getattr(authored_mod, patch_name)
    with pytest.raises(ValidationError) as exc_info:
        patch_cls.model_validate({"tooltip": {"font": {"size": 11}}})
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors), (
        f"{patch_name}: expected extra_forbidden, got {errors}"
    )


# ============================================================================
# Theme-level rejection via StylePatch — cache-divergence regression
# ============================================================================


def test_style_patch_kpi_aspect_ratio_rejected() -> None:
    """Regression: style.charts.kpi.aspect_ratio accepted today via @cache divergence.

    Before the fix, build_patch_model_ext's @cache returned differently-shaped
    classes for the explicit exclude= call vs. the recursive derivation for
    style.charts.kpi. After the fix, the structural split makes the field absent
    by construction — no divergence possible.
    """
    from dbt_charts.core.compile.models.style.authored import StylePatch

    with pytest.raises(ValidationError) as exc_info:
        StylePatch.model_validate({"charts": {"kpi": {"aspect_ratio": 1.5}}})
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)


def test_style_patch_table_min_height_rejected() -> None:
    """Regression: style.charts.table.min_height accepted today via @cache divergence."""
    from dbt_charts.core.compile.models.style.authored import StylePatch

    with pytest.raises(ValidationError) as exc_info:
        StylePatch.model_validate({"charts": {"table": {"min_height": 100}}})
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)


def test_style_patch_kpi_legend_rejected() -> None:
    """style.charts.kpi.legend raises extra_forbidden at the theme layer."""
    from dbt_charts.core.compile.models.style.authored import StylePatch

    with pytest.raises(ValidationError) as exc_info:
        StylePatch.model_validate(
            {"charts": {"kpi": {"legend": {"position": {"edge": "bottom"}}}}}
        )
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)


def test_style_patch_table_legend_rejected() -> None:
    """style.charts.table.legend raises extra_forbidden at the theme layer."""
    from dbt_charts.core.compile.models.style.authored import StylePatch

    with pytest.raises(ValidationError) as exc_info:
        StylePatch.model_validate({"charts": {"table": {"legend": {"visible": False}}}})
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors)


@pytest.mark.parametrize(
    "family",
    [
        "bar",
        "line",
        "area",
        "scatter",
        "heatmap",
        "histogram",
        "pie",
        "geoshape",
        "point_map",
    ],
)
def test_style_patch_painting_family_tooltip_rejected(family: str) -> None:
    """style.charts.<family>.tooltip raises extra_forbidden — per-family tooltip is dead."""
    from dbt_charts.core.compile.models.style.authored import StylePatch

    with pytest.raises(ValidationError) as exc_info:
        StylePatch.model_validate(
            {"charts": {family: {"tooltip": {"font": {"size": 11}}}}}
        )
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors), (
        f"style.charts.{family}.tooltip: expected extra_forbidden, got {errors}"
    )
