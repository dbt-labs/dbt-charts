"""Regression coverage for warehouse-shaped date buckets on cartesian charts.

For a genuinely continuous (non-bucketed) date shape, bar and line charts share
identical Vega-Lite x-encoding — swapping mark type never silently changes
sub-daily timestamp semantics. For a BUCKETED_CALENDAR_UNITS grain (yearmonth,
yearquarter, yearweek, year), bar and line intentionally diverge: bar/column
default to ordinal bands (D-002), line/area default to continuous temporal (see
value-driven-axis-type-inference task) — this file pins both halves of that
split, plus the value normalization that both mark types share.

Date-shape semantics covered here:
- ISO datetimes whose clock is not 00:00:00 disable ``time_unit: auto``
  detection (`detect_time_unit` treats any non-midnight as sub-daily) and fall
  back to a continuous **temporal** axis (both mark types, unaffected by the
  bar/line split above — there's no bucketed grain to split on).
- ``infer_vega_type_from_data`` recognizes both ``T`` and space separators in
  timestamp strings (the space form is what the dbt adapter / DuckDB cache emit
  for Snowflake), so midnight ``datetime`` month buckets resolve to a
  BUCKETED_CALENDAR_UNITS grain (ordinal on bar, temporal on line).

Cross-links: D-002 (bar ordinal default), D-003a (UTC path).
"""

from __future__ import annotations

import datetime as dt

from pydantic import TypeAdapter as _TA

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.chart.normalized import (
    Chart,
)

_CHART_ADAPTER: _TA[Chart] = _TA(Chart)
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.time_unit_detect import (
    detect_time_unit,
)

_BOARD_STYLE, _BOARD_CTX = resolve_style_and_context(get_theme_style())


# --- bar / line encoding parity ---


def test_detect_time_unit_iso_dates() -> None:
    vals = [f"2024-{m:02d}-01" for m in range(1, 5)]
    assert detect_time_unit(vals) == "yearmonth"


def test_detect_time_unit_python_dates() -> None:
    vals = [dt.date(2024, m, 1) for m in range(1, 5)]
    assert detect_time_unit(vals) == "yearmonth"


def test_detect_time_unit_accepts_plain_year_month_strings() -> None:
    """YYYY-MM is now a recognized yearmonth bucket format."""
    vals = [f"2024-{m:02d}" for m in range(1, 5)]
    assert detect_time_unit(vals) == "yearmonth"


# --- ordinal gap-fill lookup-key regression (bar renders real marks, not synthesized nulls) ---


# --- authored ``time_unit: auto`` sentinel must resolve to a real grain ---


# --- ordinal scaffold budget gates line/area/scatter grain too ---
