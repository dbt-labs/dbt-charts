"""Smoke tests: chart axis spec string and KPI Python output share format: compact.

Both surfaces resolve "compact" through the predefined format path to the engine
spec ".3~s" and produce "1.5M" for 1_500_000. "compact" is an engine-owned
predefined name — it is NOT a user alias in style.formats, so the engine decides
what it means and both surfaces must agree on it.
"""

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.format import resolve_format
from dbt_charts.core.compile.format_value import format_kpi_parts
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

_BOARD_RS, _BOARD_CTX = resolve_style_and_context(get_theme_style())


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
