"""Regression: a FormatConfig affix reaching a chart with no structured tooltip must be
rejected at resolve, not silently dropped. ``StructuredTooltipFeature.applies_to``
(``render/chart/features/ structured_tooltip.py``) excludes a handful of chart shapes
from the hand-built ``description`` expression that composes an authored affix the
same way an axis labelExpr does: a faceted ``multiples:`` chart, a wide (multi-
measure) scatter, a layered/combo scatter, and histogram.
"""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

_DATA = [
    {"month": "Jan", "revenue": 100.0, "cost": 50.0, "region": "east"},
    {"month": "Feb", "revenue": 200.0, "cost": 60.0, "region": "west"},
]

_AFFIX_FORMAT = {"spec": ",.0f", "prefix": "EUR "}


def _resolve(chart_def: dict, board_style_patch: Any = None) -> object:
    ctx = resolve_chart_style_context(
        get_theme_style(),
        *([board_style_patch] if board_style_patch is not None else []),
    )
    compiled = normalize_chart("c1", chart_def, {}, sources={})
    return resolve(compiled, _DATA, ctx)


def test_bar_with_multiples_and_affix_is_rejected() -> None:
    """A plain bar with multiples: and an affixed style.number_format is
    rejected, since no panel's tooltip could paint the affix."""
    with pytest.raises(CompilationError) as exc_info:
        _resolve(
            {
                "type": "bar",
                "x": "month",
                "y": "revenue",
                "multiples": {"columns": "region"},
                "style": {"number_format": _AFFIX_FORMAT},
            }
        )
    assert exc_info.value.code.code == "ERR-FORMAT-AFFIX-NATIVE-TOOLTIP-UNSUPPORTED"


def test_bar_with_multiples_and_no_affix_still_compiles() -> None:
    """A faceted chart with no affix authored has nothing to reject."""
    _resolve(
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "multiples": {"columns": "region"},
        }
    )


def test_plain_bar_with_affix_still_compiles() -> None:
    """A non-faceted bar has a real structured tooltip."""
    _resolve(
        {
            "type": "bar",
            "x": "month",
            "y": "revenue",
            "style": {"number_format": _AFFIX_FORMAT},
        }
    )


def test_histogram_with_affix_is_rejected() -> None:
    """Histogram is unconditionally excluded from the structured tooltip
    (StructuredTooltipFeature.applies_to's own chart_type check).
    """
    with pytest.raises(CompilationError) as exc_info:
        _resolve(
            {
                "type": "histogram",
                "x": "revenue",
                "style": {"number_format": _AFFIX_FORMAT},
            }
        )
    assert exc_info.value.code.code == "ERR-FORMAT-AFFIX-NATIVE-TOOLTIP-UNSUPPORTED"


def test_histogram_with_no_affix_still_compiles() -> None:
    """A histogram with no affix authored has nothing to reject."""
    _resolve({"type": "histogram", "x": "revenue"})


def test_wide_scatter_with_affix_is_rejected() -> None:
    """A wide (multi-y) scatter's chart.y is the synthetic fold column, excluded from
    the structured tooltip.
    """
    with pytest.raises(CompilationError) as exc_info:
        _resolve(
            {
                "type": "scatter",
                "x": "month",
                "y": ["revenue", "cost"],
                "style": {"number_format": _AFFIX_FORMAT},
            }
        )
    assert exc_info.value.code.code == "ERR-FORMAT-AFFIX-NATIVE-TOOLTIP-UNSUPPORTED"


def test_scatter_with_layers_and_affix_is_rejected() -> None:
    """A layered/combo scatter is excluded from the structured tooltip too."""
    with pytest.raises(CompilationError) as exc_info:
        _resolve(
            {
                "type": "scatter",
                "x": "revenue",
                "y": "cost",
                "layers": [{"type": "scatter", "x": "revenue", "y": "cost"}],
                "style": {"number_format": _AFFIX_FORMAT},
            }
        )
    assert exc_info.value.code.code == "ERR-FORMAT-AFFIX-NATIVE-TOOLTIP-UNSUPPORTED"


def test_plain_scatter_with_affix_still_compiles() -> None:
    """A single-series scatter keeps its structured tooltip -- must not raise."""
    _resolve(
        {
            "type": "scatter",
            "x": "revenue",
            "y": "cost",
            "style": {"number_format": _AFFIX_FORMAT},
        }
    )


@pytest.mark.parametrize("chart_type", ["bar", "line", "area"])
def test_wide_bar_line_area_with_affix_still_compiles(chart_type: str) -> None:
    """A wide (multi-measure) bar/line/area is NOT excluded from the structured tooltip."""
    _resolve(
        {
            "type": chart_type,
            "x": "month",
            "y": ["revenue", "cost"],
            "style": {"number_format": _AFFIX_FORMAT},
        }
    )


def test_rejection_names_the_tooltip_slot_when_the_affix_arrives_via_it() -> None:
    """The rejected chart authors no style.number_format/chart.format of its own."""
    from dbt_charts.core.compile.models.style.authored import StylePatch

    board_style_patch = StylePatch.model_validate(
        {
            "formats": {"eur": _AFFIX_FORMAT},
            "charts": {"tooltip": {"format": "eur"}},
        }
    )
    with pytest.raises(CompilationError) as exc_info:
        _resolve(
            {
                "type": "bar",
                "x": "month",
                "y": "revenue",
                "multiples": {"columns": "region"},
            },
            board_style_patch,
        )
    assert exc_info.value.code.code == "ERR-FORMAT-AFFIX-NATIVE-TOOLTIP-UNSUPPORTED"
    assert exc_info.value.field_path == "style.charts.tooltip.format"
