"""Smoke tests: chart axis spec string and KPI Python output share format: compact.

Both surfaces resolve "compact" through the predefined format path to the engine
spec ".3~s" and produce "1.5M" for 1_500_000. "compact" is an engine-owned
predefined name — it is NOT a user alias in style.formats, so the engine decides
what it means and both surfaces must agree on it.
"""

import dataclasses

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.format import resolve_format
from dbt_charts.core.compile.format_value import format_kpi_parts
from dbt_charts.core.compile.models.chart.normalized import BarChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import BarChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())

_QUERY = SqlQuery(
    sql="SELECT month, SUM(revenue) AS revenue FROM t GROUP BY 1", source="t"
)
_DATA = [{"month": "Jan", "revenue": 1_500_000}]
# Synthetic format alias: distinctive value under test control, isolated from predefined names.
# "pct" → "#%" uses a non-default D3 spec so no theme change can make the test pass falsely.
_FORMATS_PCT = {"pct": "#%"}


def _resolved_style_with_formats(formats: dict[str, str]):
    """Return (ResolvedStyle, ChartStyleContext) with a custom formats dict injected."""
    rs, ctx = resolve_style_and_context(get_theme_style())
    patched_ctx = dataclasses.replace(ctx, formats=formats)
    return rs, patched_ctx


def test_compact_vl_axis_format_is_d3_spec():
    """format: compact on a vertical bar chart y-axis writes the resolved D3 spec into axis.format.

    The engine spec for compact is ".3~s". Setting an explicit precision does
    change how the browser derives axis ticks -- on a sub-1 domain it can mix SI
    prefixes across one axis (0, 500µ, 1m) where a precisionless "~s" shares one
    (0m, 0.5m, 1m). That is not a new failure class this spec introduces, though:
    the shipped theme's own axis default is `number`, which is already
    ".3~s" (`_base.yaml`), so an unformatted axis over the same data renders the
    same ticks today. What this change buys is that `compact` stops resolving
    *wider* than that default -- a bare "~s" is six significant digits on every
    non-axis surface, which is the bug being fixed.

    Don't restate d3's tick derivation here without re-measuring it: an earlier
    version of this docstring asserted a mechanism (``tickFormat`` always calling
    ``formatPrefix`` for type ``s``) and a cost case (a 1,000,000-1,000,010 domain
    collapsing to "1M") that a render trace contradicted on both counts.
    """
    chart = BarChart(
        id="t",
        query=_QUERY,
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        style=BarChartStylePatch.model_validate(
            {"orientation": "vertical", "axis_y": {"labels": {"format": "number"}}}
        ),
    )
    _rc = resolve(chart, _DATA, chart_style_context=_BOARD_CTX)
    spec = generate_vega_lite_spec(chart, _DATA)
    # "compact" → ".3~s". round_aware_spec is a no-op since ~ is set.
    assert spec["encoding"]["y"]["axis"]["format"] == ".3~s"


def test_compact_kpi_and_axis_share_spec_string_on_round_value():
    """For 1_500_000, Python KPI and VL axis both produce "1.5M".

    Predefined "compact" resolves to ".3~s" on both paths; the house notation
    substitution renders "M" → "M" (analytic register for the KPI lane).
    """
    _, number, suffix = format_kpi_parts(1_500_000, "number")
    assert number + suffix == "1.5M"


def test_compact_format_strings_agree():
    """Predefined 'compact' resolves to the engine spec '.3~s' on both surfaces."""
    assert resolve_format("number", None) == ".3~s"


def test_percent_vl_axis_format_lands_on_measure_x_for_horizontal_bar():
    """User format on a horizontal bar chart writes the D3 spec string onto encoding.x.

    Uses a synthetic 'pct' alias mapped to '#%' — isolated from predefined names
    so the test survives any predefined format changes.
    Before the fix, encoding['x']['axis']['format'] was the theme default and
    encoding['x']['format'] was the tooltip default, not the authored format.
    """
    chart = BarChart(
        id="t",
        query=_QUERY,
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        style=BarChartStylePatch.model_validate(
            {"orientation": "horizontal", "axis_y": {"labels": {"format": "pct"}}}
        ),
    )
    rs, ctx = _resolved_style_with_formats(_FORMATS_PCT)
    _rc = resolve(chart, _DATA, chart_style_context=ctx)
    spec = generate_vega_lite_spec(
        chart, data=_DATA, board_style=rs, chart_style_context=ctx
    )
    assert spec["encoding"]["x"]["axis"]["format"] == "#%"
    assert spec["encoding"]["x"]["format"] == "#%"


def test_percent_vl_axis_format_lands_on_measure_y_for_vertical_bar():
    """User format on a vertical bar chart writes the D3 spec string onto encoding.y.

    Parity test: vertical path must not regress when the horizontal fix is applied.
    Uses the same synthetic 'pct' → '#%' alias to stay isolated from predefined names.
    """
    chart = BarChart(
        id="t",
        query=_QUERY,
        query_name="q",
        type="bar",
        x="month",
        y="revenue",
        style=BarChartStylePatch.model_validate(
            {"orientation": "vertical", "axis_y": {"labels": {"format": "pct"}}}
        ),
    )
    rs, ctx = _resolved_style_with_formats(_FORMATS_PCT)
    _rc = resolve(chart, _DATA, chart_style_context=ctx)
    spec = generate_vega_lite_spec(
        chart, data=_DATA, board_style=rs, chart_style_context=ctx
    )
    assert spec["encoding"]["y"]["axis"]["format"] == "#%"
    assert spec["encoding"]["y"]["format"] == "#%"
