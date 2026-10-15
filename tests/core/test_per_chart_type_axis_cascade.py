"""Tests for per-chart-type axis style override cascade.

Cascade order (bottom-most wins):
1. Theme global axis (style.charts.axis_x/y)
2. Theme chart-type axis (style.charts.bar.axis_x/y)  -- NEW
3. Per-chart authored override (board YAML chart.axis_x/y)
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from dbt_charts.core.compile.config import (
    get_default_theme_name,
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.resolved import ResolvedStyle
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context


@pytest.fixture(autouse=True)
def _reset() -> Generator[None, None, None]:
    reset_config()
    yield
    reset_config()


_NOMINAL_DATA = [
    {"category": "A", "value": 10},
    {"category": "B", "value": 20},
    {"category": "C", "value": 30},
]


def _compile_and_render_with_board(
    chart_type: str = "bar",
    chart_style: str = "",
    board_style: tuple[ResolvedStyle, ChartStyleContext] | None = None,
) -> dict:  # type: ignore[type-arg]
    """Compile a minimal board and return the categorical-axis encoding dict.

    For vertical bars, the categorical axis is VL x; for horizontal bars it is
    VL y.  We return whichever encoding holds the nominal/ordinal (categorical)
    field so axis-angle assertions are orientation-independent.
    """
    from dbt_charts.core.compile.compiler import compile
    from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

    rs, ctx = board_style if board_style is not None else (None, None)

    style_lines = f"    style:\n{chart_style}" if chart_style else ""
    board_yaml = f"""
id: test-board
source: duckdb
charts:
  c1:
    type: {chart_type}
    x: category
    y: value
{style_lines}    query:
      sql: SELECT 'A' AS category, 10 AS value
rows:
  - c1
"""
    result = compile(board_yaml)
    assert result.board is not None, f"Compile failed: {result.errors}"
    chart = list(result.board.charts.values())[0]
    spec = generate_vega_lite_spec(
        chart, _NOMINAL_DATA, board_style=rs, chart_style_context=ctx
    )
    enc = spec.get("encoding", {})
    # For horizontal bars the categorical (dbt charts x) axis is VL y; for vertical
    # it is VL x.  Return the axis dict for whichever encoding holds category.
    for key in ("x", "y"):
        entry = enc.get(key, {})
        if entry.get("type") in ("nominal", "ordinal"):
            return entry.get("axis", {})
    return enc.get("x", {}).get("axis", {})


def _board_style_with_bar_axis_x_angle(
    angle: int,
) -> tuple[ResolvedStyle, ChartStyleContext]:
    """Return (ResolvedStyle, ChartStyleContext) with bar.axis_x.labels.angle set to *angle*."""
    from dbt_charts.core.compile.models.style.authored import (
        AxisXStylePatch,
    )

    patched = get_theme_style().model_copy(
        deep=True,
        update={
            "charts": get_theme_style().charts.model_copy(
                deep=True,
                update={
                    "bar": get_theme_style(
                        get_default_theme_name()
                    ).charts.bar.model_copy(
                        update={
                            "axis_x": AxisXStylePatch.model_validate(
                                {"labels": {"angle": angle}}
                            )
                        }
                    )
                },
            )
        },
    )
    return resolve_style_and_context(patched)


# ---------------------------------------------------------------------------
# Cascade behavior tests
# ---------------------------------------------------------------------------


class TestChartTypeAxisCascade:
    def test_chart_type_axis_patch_applied_in_render(self) -> None:
        """bar.axis_x.labels.angle on the theme chart-type style emits labelAngle in the VL spec."""
        board_style = _board_style_with_bar_axis_x_angle(-55)
        axis = _compile_and_render_with_board("bar", board_style=board_style)
        assert axis.get("labelAngle") == -55, (
            f"Expected labelAngle=-55 from bar.axis_x chart-type patch; got {axis}"
        )

    def test_per_chart_authored_angle_wins_over_chart_type(self) -> None:
        """Per-chart axis_x.labels.angle (layer 12) wins over chart-type patch (layer 4)."""
        # Theme has bar.axis_x.labels.angle = -55 (layer 4).
        # Board authors angle = 90 (layer 12). Layer 12 must win.
        board_style = _board_style_with_bar_axis_x_angle(-55)
        axis = _compile_and_render_with_board(
            "bar",
            chart_style="      axis_x:\n        labels:\n          angle: 90\n",
            board_style=board_style,
        )
        assert axis.get("labelAngle") == 90, (
            f"Expected per-chart angle=90 to win over chart-type -55; got {axis}"
        )
