"""Tests for smart-default labelExpr based on temporal x-axis time_unit."""

from __future__ import annotations

from typing import Any

import pytest
import vl_convert as vlc

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context
from dbt_charts.core.render.chart.time_unit_detect import (
    BUCKETED_CALENDAR_UNITS,
    default_label_expr_for,
    resolve_label_time_unit,
    tooltip_header_date_expr,
    vl_time_unit,
)
from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

_BOARD_STYLE = resolve_chart_style_context(get_theme_style())


def _eval_expr(expr: str, iso: str) -> str:
    """Paint ``expr`` over one ``{d: iso}`` row through Vega and read the text."""
    spec: dict[str, Any] = {
        "data": {"values": [{"d": iso}]},
        "transform": [{"calculate": expr, "as": "out"}],
        "mark": "text",
        "encoding": {"text": {"field": "out", "type": "nominal"}},
    }
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("marktype") == "text":
                found.extend(item["text"] for item in node.get("items", []))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(vlc.vegalite_to_scenegraph(spec))
    (text,) = found
    return text


# ---------------------------------------------------------------------------
# Temporal data for testing
# ---------------------------------------------------------------------------


MONTHLY_DATA = [
    {"date": "2024-01-01", "value": 10},
    {"date": "2024-02-01", "value": 20},
    {"date": "2024-03-01", "value": 15},
]

# ---------------------------------------------------------------------------
# Unit tests for the helper function
# ---------------------------------------------------------------------------


class TestDefaultLabelExprHelper:
    @pytest.mark.parametrize(
        ("iso", "time_unit", "expected"),
        [
            ("2024-03-03", "yearweek", "3–9 Mar 2024"),
            ("2024-03-31", "yearweek", "31 Mar – 6 Apr 2024"),
            ("2023-12-31", "yearweek", "31 Dec 2023 – 6 Jan 2024"),
            ("2024-03-05", "", "5 Mar 2024"),
            ("2024-03-05", "yearmonthdate", "5 Mar 2024"),
            ("2024-03-01", "yearmonth", "Mar 2024"),
        ],
    )
    def test_tooltip_header_reads_like_the_date_chip(self, iso, time_unit, expected):
        """Week headers are the bucket's date range, dates are day-first: the
        same shapes as the date-variable chip."""
        assert _eval_expr(tooltip_header_date_expr("datum.d", time_unit), iso) == (
            expected
        )

    def test_label_time_unit_inherits_encoding_unit(self):
        assert resolve_label_time_unit("year", None) == "year"
        assert resolve_label_time_unit("yearquarter", None) == "yearquarter"
        assert resolve_label_time_unit("yearmonth", None) == "yearmonth"

    def test_yearweek_and_yearmonthdate_inherit_encoding_unit(self):
        assert resolve_label_time_unit("yearweek", None) == "yearweek"
        assert resolve_label_time_unit("yearmonthdate", None) == "yearmonthdate"

    def test_author_label_time_unit_overrides_default(self):
        assert resolve_label_time_unit("yearweek", "yearweek") == "yearweek"
        assert resolve_label_time_unit("yearmonth", "yearquarter") == "yearquarter"

    def test_label_time_unit_none_disables_smart_default(self):
        assert resolve_label_time_unit("yearmonth", "none") is None

    def test_yearmonth_returns_expression(self):
        expr = default_label_expr_for(
            "yearmonth", "yearmonth", fiscal_year_start_month=1
        )
        assert expr is not None
        assert "utcmonth(toDate(datum.value))" in expr
        assert "utcFormat" in expr

    def test_yearquarter_returns_expression(self):
        expr = default_label_expr_for(
            "yearquarter", "yearquarter", fiscal_year_start_month=1
        )
        assert expr is not None
        assert "floor(utcmonth(toDate(datum.value))/3)" in expr

    def test_yearweek_with_default_month_labels_blanks_non_month_openers(self):
        expr = default_label_expr_for(
            "yearweek", "yearmonth", fiscal_year_start_month=1
        )
        assert expr is not None
        assert "utcFormat(toDate(datum.value), '%b')" in expr
        assert "utcmonth(toDate(datum.value)) === 0" in expr
        assert "utcFormat(toDate(datum.value), '%Y')" in expr
        assert "W%V" not in expr
        assert "utcdate(toDate(datum.value)) <= 7" in expr
        assert ": ''" in expr

    def test_yearweek_identity_label_shows_two_row_day_numbers(self):
        expr = default_label_expr_for("yearweek", "yearweek", fiscal_year_start_month=1)
        assert expr is not None
        assert "'%-d'" in expr
        assert '"\'"' in expr
        assert "'%y'" in expr
        assert "utcdate(toDate(datum.value)) <= 7" in expr

    def test_continuous_yearweek_identity_labels_the_monday_bucket(self):
        expr = default_label_expr_for(
            "yearweek",
            "yearweek",
            fiscal_year_start_month=1,
            ticks_are_buckets=False,
        )
        assert expr is not None
        assert "'%-d'" in expr
        assert "W%V" not in expr
        assert "utcOffset('day', toDate(datum.value), 1)" in expr

    def test_continuous_yearweek_month_labels_keep_month_opening_ticks(self):
        expr = default_label_expr_for(
            "yearweek",
            "yearmonth",
            fiscal_year_start_month=1,
            ticks_are_buckets=False,
        )
        assert expr is not None
        assert "utcOffset('day', toDate(datum.value), 1)" not in expr
        assert "utcdate(toDate(datum.value)) <= 7" in expr

    def test_year_returns_simple_format(self):
        expr = default_label_expr_for("year", "year", fiscal_year_start_month=1)
        assert expr is not None
        assert "%Y" in expr

    def test_yearmonthdate_returns_expression(self):
        expr = default_label_expr_for(
            "yearmonthdate", "yearmonthdate", fiscal_year_start_month=1
        )
        assert expr is not None
        assert "utcdate(toDate(datum.value))" in expr
        assert "'%-d'" in expr
        assert "utcdate(toDate(datum.value)) === 1" in expr

    def test_continuous_yearmonthdate_matches_yearweek_apostrophe_year(self):
        # Continuous daily ticks (a genuinely temporal x-axis) must format
        # the day-number label row the same way weekly labels do: "%b'%y",
        # not a separate two-row "%-d %b" / "%Y" vocabulary.
        expr = default_label_expr_for(
            "yearmonthdate",
            "yearmonthdate",
            fiscal_year_start_month=1,
            ticks_are_buckets=False,
        )
        assert expr is not None
        assert "'%-d'" in expr
        assert '"\'"' in expr
        assert "'%y'" in expr
        assert "'%Y'" not in expr

    def test_daily_week_visibility_uses_mondays(self):
        expr = default_label_expr_for(
            "yearmonthdate", "yearweek", "yearweek", fiscal_year_start_month=1
        )
        assert expr is not None
        assert "utcday(toDate(datum.value)) === 1" in expr
        assert "'%-d'" in expr

    def test_monthly_data_with_authored_quarter_labels_uses_quarter_vocabulary(self):
        expr = default_label_expr_for("yearmonth", "yearquarter", "yearquarter", 1)
        assert expr is not None
        assert "'Q'" in expr
        assert "'%b'" not in expr
        assert "utcmonth(toDate(datum.value)) % 3 === 0" in expr
        assert ": ''" in expr

    def test_first_visible_label_prints_year_when_not_year_opener(self):
        expr = default_label_expr_for(
            "yearmonth",
            "yearmonth",
            "yearquarter",
            1,
            anchor_index=2,
        )
        assert expr is not None
        assert "datum.index === 2" in expr
        assert "utcFormat(toDate(datum.value), '%Y')" in expr

    def test_weekly_anchor_matches_the_visible_month(self):
        expr = default_label_expr_for(
            "yearweek",
            "yearmonth",
            "yearmonth",
            1,
            anchor_value="2024-03-04",
        )
        assert expr is not None
        assert (
            "utcFormat(toDate(datum.value), '%Y-%m') === "
            "utcFormat(toDate(\"2024-03-04\"), '%Y-%m')"
        ) in expr

    def test_weekly_anchor_survives_native_month_tick_normalization(self):
        expr = default_label_expr_for(
            "yearweek",
            "yearmonth",
            "yearquarter",
            1,
            anchor_value="2025-04-07",
        )
        assert expr is not None
        assert (
            "utcFormat(toDate(datum.value), '%Y-%m') === "
            "utcFormat(toDate(\"2025-04-07\"), '%Y-%m')"
        ) in expr

    def test_monthly_data_with_month_format_and_year_visibility_keeps_month_names(self):
        expr = default_label_expr_for("yearmonth", "yearmonth", "year")
        assert expr is not None
        assert "'%b'" in expr and "'%Y'" in expr
        assert "utcmonth(toDate(datum.value)) === 0" in expr
        assert "'Q'" not in expr

    def test_quarterly_data_keeps_quarter_tokens(self):
        # Genuinely quarterly data: each tick IS a quarter, so 'Q1'/'Q2' on-tick
        # is a correct point label — unchanged by the month-name clamp.
        expr = default_label_expr_for("yearquarter", "yearquarter")
        assert expr is not None
        assert "'Q'" in expr

    def test_temporal_path_uses_utc_to_avoid_local_tz_drift(self):
        # Vega temporal scales emit `scale.type: "utc"`. Local-TZ `month()`
        # would shift Jan-UTC into Dec-local in any negative-offset timezone,
        # so the cadence gate (`month % 3 === 0` for quarterly) stamps every
        # tick blank. UTC components keep gate predicates aligned with what
        # `utcFormat` displays.
        expr = default_label_expr_for(
            "yearmonth", "yearquarter", fiscal_year_start_month=1
        )
        assert expr is not None
        assert "month(datum.value)" not in expr
        assert "timeFormat(datum.value" not in expr

    def test_none_time_unit_returns_none(self):
        assert default_label_expr_for(None, None, fiscal_year_start_month=1) is None

    def test_auto_time_unit_returns_none(self):
        assert (
            default_label_expr_for("yearmonth", "auto", fiscal_year_start_month=1)
            is None
        )

    def test_none_time_unit_returns_none_for_none_string(self):
        assert (
            default_label_expr_for("yearmonth", "none", fiscal_year_start_month=1)
            is None
        )

    def test_time_part_units_return_none(self):
        for unit in ("monthofyear", "dayofweek", "dayofmonth", "hourofday"):
            assert default_label_expr_for(unit, unit, fiscal_year_start_month=1) is None

    def test_fiscal_year_opener_gets_year_yearmonth(self):
        expr = default_label_expr_for(
            "yearmonth", "yearmonth", fiscal_year_start_month=1
        )
        assert expr is not None
        assert "utcmonth(toDate(datum.value)) === 0" in expr
        assert "datum.index === 0" in expr

    def test_fiscal_year_opener_gets_year_yearquarter(self):
        expr = default_label_expr_for(
            "yearquarter", "yearquarter", fiscal_year_start_month=1
        )
        assert expr is not None
        assert "utcmonth(toDate(datum.value)) === 0" in expr
        assert "datum.index === 0" in expr

    def test_fiscal_year_opener_gets_year_yearweek(self):
        expr = default_label_expr_for("yearweek", "yearweek", fiscal_year_start_month=1)
        assert expr is not None
        assert "datum.index === 0" in expr

    def test_fiscal_year_opener_gets_year_yearmonthdate(self):
        expr = default_label_expr_for(
            "yearmonthdate", "yearmonthdate", fiscal_year_start_month=1
        )
        assert expr is not None
        assert "datum.index === 0" in expr

    # Regression: on the continuous temporal path (ticks_are_buckets=False),
    # the anchor comparison must use the encoding grain, not the visibility
    # grain. A visibility-grain comparison is a period test — it matches every
    # tick that falls inside the anchor period, stamping year-context on all of
    # them rather than just the first.

    def test_continuous_temporal_yearmonthdate_anchor_uses_day_grain(self) -> None:
        # yearmonthdate encoding + yearmonth visibility: old code compared at
        # %Y-%m, matching all 31 days in March. Must compare at %Y-%m-%d.
        expr = default_label_expr_for(
            "yearmonthdate",
            "yearmonthdate",
            "yearmonth",
            1,
            anchor_value="2024-03-01",
            ticks_are_buckets=False,
        )
        assert expr is not None
        assert (
            "utcFormat(toDate(datum.value), '%Y-%m-%d') === "
            "utcFormat(toDate(\"2024-03-01\"), '%Y-%m-%d')"
        ) in expr
        assert "'%Y-%m')" not in expr

    def test_continuous_temporal_yearquarter_anchor_uses_month_grain(self) -> None:
        # yearquarter encoding + year visibility: old code compared at %Y,
        # matching all four quarters of 2023. Must compare at %Y-%m.
        expr = default_label_expr_for(
            "yearquarter",
            "yearquarter",
            "year",
            1,
            anchor_value="2023-01-01",
            ticks_are_buckets=False,
        )
        assert expr is not None
        assert (
            "utcFormat(toDate(datum.value), '%Y-%m') === "
            "utcFormat(toDate(\"2023-01-01\"), '%Y-%m')"
        ) in expr
        assert "utcFormat(toDate(datum.value), '%Y') ===" not in expr

    def test_continuous_temporal_yearweek_anchor_uses_week_grain(self) -> None:
        # yearweek encoding + yearmonth visibility: old code compared at %Y-%m,
        # matching all weeks in March. Must compare at %Y-%U.
        expr = default_label_expr_for(
            "yearweek",
            "yearweek",
            "yearmonth",
            1,
            anchor_value="2024-03-04",
            ticks_are_buckets=False,
        )
        assert expr is not None
        assert "'%Y-%U'" in expr
        # Bucket path (ticks_are_buckets=True) would use %Y-%m — must not.
        assert (
            "utcFormat(utcOffset('day', toDate(datum.value), 1), '%Y-%m') ==="
            not in expr
        )

    def test_non_opener_first_tick_escapes_visibility_gate_for_year_context(self):
        expr = default_label_expr_for(
            "yearweek", "yearweek", "yearmonth", fiscal_year_start_month=1
        )
        assert expr is not None
        assert expr.startswith("(datum.index === 0")
        assert "utcdate(toDate(datum.value)) <= 7" in expr

    def test_yearweek_month_labels_use_fiscal_shifted_month_gate(self):
        # yearweek encoding + yearmonth label cadence, non-January fiscal start.
        # The month opener gate inside _week_label uses _fiscal_month_expr, so
        # for fiscal_year_start_month=4 (April) the gate must shift the calendar
        # month by -3 rather than testing raw utcmonth === 0.
        expr = default_label_expr_for(
            "yearweek", "yearmonth", fiscal_year_start_month=4
        )
        assert expr is not None
        # Fiscal-shifted form: (utcmonth(...) - 3 + 12) % 12
        assert "- 3 + 12) % 12" in expr, (
            "non-January fiscal start must use shifted month gate, got: " + repr(expr)
        )
        # Must NOT use the plain calendar-January gate that would mis-anchor at Jan
        assert "utcmonth(toDate(datum.value)) === 0" not in expr

    def test_yearmonthdate_month_labels_use_fiscal_shifted_month_gate(self):
        # Same check for daily encoding (yearmonthdate) with yearmonth cadence.
        expr = default_label_expr_for(
            "yearmonthdate", "yearmonth", fiscal_year_start_month=4
        )
        assert expr is not None
        assert "- 3 + 12) % 12" in expr, (
            "non-January fiscal start must use shifted month gate, got: " + repr(expr)
        )
        assert "utcmonth(toDate(datum.value)) === 0" not in expr

    def test_steep_tilt_inlines_yearmonth_year_context(self) -> None:
        # At a full-vertical tilt, the rotated second row would spill
        # horizontally into the neighboring tick's label — flow the year
        # inline on one row instead of stacking it, year before month.
        expr = default_label_expr_for(
            "yearmonth", "yearmonth", fiscal_year_start_month=1, steep_tilt=True
        )
        assert expr is not None
        assert "[" not in expr
        assert (
            "utcFormat(toDate(datum.value), '%Y') + ' ' + "
            "utcFormat(toDate(datum.value), '%b')" in expr
        )

    def test_steep_tilt_inlines_yearquarter_year_context(self) -> None:
        expr = default_label_expr_for(
            "yearquarter", "yearquarter", fiscal_year_start_month=1, steep_tilt=True
        )
        assert expr is not None
        assert "[" not in expr
        assert "'Q'" in expr

    def test_steep_tilt_inlines_yearmonthdate_year_context(self) -> None:
        # Day-grain: day number leads, month/year context trails — a leading
        # year would break the day-month reading order ("2024 1 Jan").
        expr = default_label_expr_for(
            "yearmonthdate", "yearmonthdate", fiscal_year_start_month=1, steep_tilt=True
        )
        assert expr is not None
        assert "[" not in expr
        assert expr.startswith("utcFormat(toDate(datum.value), '%-d') + (")

    def test_steep_tilt_inlines_continuous_yearmonthdate(self) -> None:
        # Continuous daily labels use the same apostrophe-year vocabulary as
        # week labels; steep_tilt flows that onto one row instead of two.
        expr = default_label_expr_for(
            "yearmonthdate",
            "yearmonthdate",
            fiscal_year_start_month=1,
            steep_tilt=True,
            ticks_are_buckets=False,
        )
        assert expr is not None
        assert "[" not in expr
        assert "'%-d'" in expr
        assert '"\'"' in expr
        assert "'%y'" in expr

    def test_default_tilt_keeps_two_row_array(self) -> None:
        expr = default_label_expr_for(
            "yearmonth", "yearmonth", fiscal_year_start_month=1
        )
        assert expr is not None
        assert "[" in expr


# ---------------------------------------------------------------------------
# Integration tests: smart default flows into the emitted VL spec
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _spec_axis_for(
    time_unit: str | None,
    label_expr: str | None = None,
    label_time_unit: str | None = None,
    axis_type: str | None = None,
) -> dict:
    """Compile and render a line chart with the given time_unit and optional expr."""
    from dbt_charts.core.compile.compiler import compile

    style_block = "    style:\n      axis_x:\n"
    if time_unit is not None:
        style_block += f"        time_unit: {time_unit}\n"
    if axis_type is not None:
        style_block += f"        type: {axis_type}\n"
    if label_expr is not None or label_time_unit is not None:
        style_block += "        labels:\n"
        if label_time_unit is not None:
            style_block += f"          time_unit: {label_time_unit}\n"
        if label_expr is not None:
            style_block += f"          expr: '{label_expr}'\n"

    board_yaml = f"""
id: test-board
source: duckdb
charts:
  c1:
    type: line
    x: date
    y: value
{style_block}    query:
      sql: SELECT '2024-01-01' AS date, 10 AS value
rows:
  - c1
"""
    result = compile(board_yaml)
    assert result.board is not None, f"Compile failed: {result.errors}"
    board = result.board
    chart = list(board.charts.values())[0]
    _rc = resolve(chart, MONTHLY_DATA, chart_style_context=_BOARD_STYLE)
    spec = generate_vega_lite_spec(chart, MONTHLY_DATA, width=400, height=200)
    # Get axis config from the spec
    encoding = spec.get("encoding") or spec.get("layer", [{}])[0].get("encoding", {})
    return encoding.get("x", {}).get("axis", {})


class TestSmartDefaultLabelExprInSpec:
    def test_yearmonth_emits_smart_label_expr(self):
        # labelExpr is emitted on the temporal escape-hatch path (type: temporal).
        axis = _spec_axis_for("yearmonth", axis_type="temporal")
        assert "labelExpr" in axis
        assert "utcmonth(toDate(datum.value))" in axis["labelExpr"]

    def test_yearquarter_emits_smart_label_expr(self):
        axis = _spec_axis_for("yearquarter", axis_type="temporal")
        assert "labelExpr" in axis
        assert "floor(utcmonth(toDate(datum.value))/3)" in axis["labelExpr"]

    def test_label_time_unit_override_uses_quarter_vocabulary(self):
        axis = _spec_axis_for(
            "yearmonth", label_time_unit="yearquarter", axis_type="temporal"
        )
        assert "labelExpr" in axis
        assert "'Q'" in axis["labelExpr"]
        assert "'%b'" not in axis["labelExpr"]
        assert "utcmonth(toDate(datum.value)) % 3 === 0" in axis["labelExpr"]  # gate
        assert ": ''" in axis["labelExpr"]

    def test_label_time_unit_none_disables_smart_label_expr(self):
        axis = _spec_axis_for("yearmonth", label_time_unit="none", axis_type="temporal")
        assert "labelExpr" not in axis

    def test_author_expr_overrides_smart_default(self):
        axis = _spec_axis_for(
            "yearmonth",
            label_expr="custom_expr",
            label_time_unit="yearquarter",
            axis_type="temporal",
        )
        assert axis.get("labelExpr") == "custom_expr"

    def test_ordinal_bar_prints_year_on_first_visible_label(self):
        from dbt_charts.core.compile.compiler import compile

        board_yaml = """
id: test-board
source: duckdb
charts:
  c1:
    type: bar
    x: week
    y: value
    style:
      axis_x:
        labels:
          time_unit: yearmonth
    query:
      sql: SELECT '2024-01-08' AS week, 10 AS value
rows:
  - c1
"""
        result = compile(board_yaml)
        assert result.board is not None, f"Compile failed: {result.errors}"
        chart = list(result.board.charts.values())[0]
        weekly_data = [
            {"week": "2024-01-08", "value": 10},
            {"week": "2024-01-15", "value": 20},
            {"week": "2024-01-22", "value": 15},
            {"week": "2024-01-29", "value": 25},
        ]
        _rc = resolve(chart, weekly_data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, weekly_data, width=400, height=200)
        x_enc = spec["encoding"]["x"]
        assert x_enc["type"] == "ordinal"
        axis = x_enc.get("axis", {})
        assert "labelExpr" in axis
        assert (
            "utcFormat(toDate(datum.value), '%Y-%m') === "
            "utcFormat(toDate(\"2024-01-08\"), '%Y-%m')"
        ) in axis["labelExpr"]
        assert "datum.index" not in axis["labelExpr"]
        assert "utcdate(toDate(datum.value)) <= 7" in axis["labelExpr"]

    def test_ordinal_bar_weekly_identity_label_does_not_shift_bucket_date(self):
        # Regression: bar's ordinal x-axis keeps the real per-row Monday
        # bucket dates in `values` — never Vega's own continuous, Sunday-
        # anchored `utcyearweek` ticks — so the day-number label must read
        # the bucket's own date, not date+1.
        from dbt_charts.core.compile.compiler import compile

        board_yaml = """
id: test-board
source: duckdb
charts:
  c1:
    type: bar
    x: week
    y: value
    query:
      sql: SELECT '2024-01-01' AS week, 10 AS value
rows:
  - c1
"""
        result = compile(board_yaml)
        assert result.board is not None, f"Compile failed: {result.errors}"
        chart = list(result.board.charts.values())[0]
        weekly_data = [
            {"week": "2024-01-01", "value": 10},
            {"week": "2024-01-08", "value": 20},
            {"week": "2024-01-15", "value": 15},
            {"week": "2024-01-22", "value": 25},
            {"week": "2024-01-29", "value": 30},
        ]
        _rc = resolve(chart, weekly_data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, weekly_data, width=400, height=200)
        x_enc = spec["encoding"]["x"]
        assert x_enc["type"] == "ordinal"
        axis = x_enc.get("axis", {})
        assert "labelExpr" in axis
        assert "utcOffset" not in axis["labelExpr"]
        assert axis.get("values") == [
            "2024-01-01",
            "2024-01-08",
            "2024-01-15",
            "2024-01-22",
            "2024-01-29",
        ]

    def test_continuous_line_weekly_identity_label_shifts_sunday_anchored_tick(self):
        # Sibling of the bar case above: a genuinely continuous temporal axis
        # (line) gets ticks Vega computes itself at the Sunday-anchored
        # `utcyearweek` grain, so the day-number label must shift them
        # forward a day to read the represented Monday bucket. This must
        # stay true even as the bar case above stays unshifted.
        from dbt_charts.core.compile.compiler import compile

        board_yaml = """
id: test-board
source: duckdb
charts:
  c1:
    type: line
    x: week
    y: value
    query:
      sql: SELECT '2024-01-01' AS week, 10 AS value
rows:
  - c1
"""
        result = compile(board_yaml)
        assert result.board is not None, f"Compile failed: {result.errors}"
        chart = list(result.board.charts.values())[0]
        weekly_data = [
            {"week": "2024-01-01", "value": 10},
            {"week": "2024-01-08", "value": 20},
            {"week": "2024-01-15", "value": 15},
            {"week": "2024-01-22", "value": 25},
            {"week": "2024-01-29", "value": 30},
        ]
        _rc = resolve(chart, weekly_data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, weekly_data, width=400, height=200)
        x_enc = spec["encoding"]["x"]
        assert x_enc["type"] == "temporal"
        axis = x_enc.get("axis", {})
        assert "labelExpr" in axis
        assert "utcOffset('day', toDate(datum.value), 1)" in axis["labelExpr"]

    def test_no_smart_default_without_time_unit_on_nominal_data(self):
        """Non-temporal data: no time_unit resolved → no smart labelExpr."""
        from dbt_charts.core.compile.compiler import compile
        from dbt_charts.core.compile.config import reset_config
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        reset_config()
        # Use categorical (non-temporal) data so auto-detection returns None
        board_yaml = """
id: test-board
source: duckdb
charts:
  c1:
    type: line
    x: category
    y: value
    query:
      sql: SELECT 'A' AS category, 10 AS value
rows:
  - c1
"""
        result = compile(board_yaml)
        assert result.board is not None
        chart = list(result.board.charts.values())[0]
        nominal_data = [{"category": "A"}, {"category": "B"}, {"category": "C"}]
        _rc = resolve(chart, nominal_data, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, nominal_data, width=400, height=200)
        encoding = spec.get("encoding") or {}
        axis = encoding.get("x", {}).get("axis", {})
        # Nominal x with no time_unit → no smart labelExpr
        assert "labelExpr" not in axis


# ---------------------------------------------------------------------------
# Layer 2: vl_time_unit returns utc-prefixed variants for chronological grains
# ---------------------------------------------------------------------------


class TestVlTimeUnit:
    @pytest.mark.parametrize("grain", sorted(BUCKETED_CALENDAR_UNITS))
    def test_chronological_grain_returns_utc_variant(self, grain: str) -> None:
        assert vl_time_unit(grain) == f"utc{grain}"

    @pytest.mark.parametrize(
        ("raw_unit", "expected_vl"),
        [
            ("monthofyear", "month"),
            ("dayofweek", "day"),
            ("dayofmonth", "date"),
            ("dayofyear", "dayofyear"),
            ("hourofday", "hours"),
        ],
    )
    def test_time_part_units_pass_through_unchanged(
        self, raw_unit: str, expected_vl: str
    ) -> None:
        assert vl_time_unit(raw_unit) == expected_vl


class TestTemporalEscapeHatchEmitsUtcTimeUnit:
    def test_yearmonth_temporal_escape_hatch_emits_utcyearmonth(self) -> None:
        from dbt_charts.core.compile.compiler import compile
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        reset_config()
        board_yaml = """
id: test-board
source: duckdb
charts:
  c1:
    type: line
    x: date
    y: value
    style:
      axis_x:
        type: temporal
        time_unit: yearmonth
    query:
      sql: SELECT '2024-01-01' AS date, 10 AS value
rows:
  - c1
"""
        result = compile(board_yaml)
        assert result.board is not None
        chart = list(result.board.charts.values())[0]
        _rc = resolve(chart, MONTHLY_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MONTHLY_DATA, width=400, height=200)
        encoding = spec.get("encoding") or spec.get("layer", [{}])[0].get(
            "encoding", {}
        )
        assert encoding.get("x", {}).get("timeUnit") == "utcyearmonth"

    def test_yearquarter_temporal_escape_hatch_emits_utcyearquarter(self) -> None:
        from dbt_charts.core.compile.compiler import compile
        from dbt_charts.core.render.chart.vega_lite import generate_vega_lite_spec

        reset_config()
        board_yaml = """
id: test-board
source: duckdb
charts:
  c1:
    type: line
    x: date
    y: value
    style:
      axis_x:
        type: temporal
        time_unit: yearquarter
    query:
      sql: SELECT '2024-01-01' AS date, 10 AS value
rows:
  - c1
"""
        result = compile(board_yaml)
        assert result.board is not None
        chart = list(result.board.charts.values())[0]
        _rc = resolve(chart, MONTHLY_DATA, chart_style_context=_BOARD_STYLE)
        spec = generate_vega_lite_spec(chart, MONTHLY_DATA, width=400, height=200)
        encoding = spec.get("encoding") or spec.get("layer", [{}])[0].get(
            "encoding", {}
        )
        assert encoding.get("x", {}).get("timeUnit") == "utcyearquarter"
