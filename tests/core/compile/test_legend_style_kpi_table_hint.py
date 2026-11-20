"""Tests for the actionable error hint on chart-level style.legend for kpi/table charts.

kpi and table charts never paint a legend. The chart-level ``style.key`` key
was removed in the current (unreleased) cycle. Unlike the theme-level
``style.charts.kpi.legend`` path (which is auto-stripped by ``dct migrate``),
the chart-level path cannot be expressed as a Deletion because the tail
``("style", "legend")`` is still valid for painting families. Instead, a clear
error with a removal hint is raised so authors know exactly what to do.
"""

from __future__ import annotations

from dbt_charts.core.compile.compiler import compile as compile_board


def _board_with_kpi_style_legend() -> str:
    return """
title: T
queries:
  q:
    source: db
    sql: SELECT 100 AS revenue
charts:
  k:
    type: kpi
    query: q
    value: revenue
    style:
      key:
        type: legend
rows:
  - k
"""


def _board_with_table_style_legend() -> str:
    return """
title: T
queries:
  q:
    source: db
    sql: SELECT 1 AS a
charts:
  t:
    type: table
    query: q
    columns: [a]
    style:
      key:
        type: legend
rows:
  - t
"""


def test_kpi_chart_style_legend_raises_with_hint() -> None:
    """style.key on a kpi chart raises with a hint naming the problem and fix."""
    result = compile_board(_board_with_kpi_style_legend())

    assert not result.success
    all_text = " ".join((e.message or "") + " " + (e.hint or "") for e in result.errors)
    assert "kpi" in all_text
    assert "legend" in all_text
    assert "remove" in all_text.lower()


def test_table_chart_style_legend_raises_with_hint() -> None:
    """style.key on a table chart raises with a hint naming the problem and fix."""
    result = compile_board(_board_with_table_style_legend())

    assert not result.success
    all_text = " ".join((e.message or "") + " " + (e.hint or "") for e in result.errors)
    assert "table" in all_text
    assert "legend" in all_text
    assert "remove" in all_text.lower()
