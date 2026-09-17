"""Tests for temporal value formatting in table cells.

Covers the render-time contract for Python date/datetime objects and ISO
timestamp strings arriving from DuckDB fetchall() — values that previously
fell through to str(value) and produced machine-readable output instead of
human-readable dates.

Timezone policy (documented here per acceptance criteria):
  - Python datetime.date and naive datetime.datetime: treated as calendar-local
    (no TZ conversion). Taken as-is.
  - ISO strings "YYYY-MM-DD" and "YYYY-MM-DDTHH:MM:SS": parsed in UTC
    so the display is identical under any runtime TZ.
  - timezone-aware datetime objects and ISO strings carrying a TZ offset:
    normalized to UTC before formatting, to match naive-string behavior. A
    date-only spec reads the UTC date (crossing a day boundary where the
    original offset would not); a time directive reads the UTC time of day.
    A genuinely date-only value (no time component at all) has no time to
    convert and is taken as its own calendar date.
"""

import datetime

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.normalized import TableChart
from dbt_charts.core.compile.models.chart.resolved.table import ResolvedTableChart
from dbt_charts.core.compile.models.style.authored import TableChartStylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.render.chart.table import render_table_svg
from dbt_charts.core.render.chart.table_support import (
    format_table_cell_value,
    is_temporal_value,
)
from dbt_charts.core.utils import is_date_like

# The default format that must be present in stark.yaml as date_short.
_DATE_SHORT_FORMAT = "%-d %b %Y"


class TestIsTemporalValue:
    """Unit tests for the is_temporal_value() predicate."""

    def test_date_object(self) -> None:
        assert is_temporal_value(datetime.date(2024, 1, 15)) is True

    def test_datetime_object(self) -> None:
        assert is_temporal_value(datetime.datetime(2024, 1, 15, 13, 30, 0)) is True

    def test_datetime_aware_object(self) -> None:
        aware = datetime.datetime(2024, 1, 15, 13, 30, 0, tzinfo=datetime.timezone.utc)
        assert is_temporal_value(aware) is True

    def test_iso_date_string(self) -> None:
        assert is_temporal_value("2024-01-15") is True

    def test_iso_timestamp_string(self) -> None:
        assert is_temporal_value("2024-01-15T13:30:00") is True

    def test_iso_timestamp_with_microseconds(self) -> None:
        assert is_temporal_value("2024-01-15T13:30:00.123456") is True

    def test_integer_is_not_temporal(self) -> None:
        assert is_temporal_value(42) is False

    def test_float_is_not_temporal(self) -> None:
        assert is_temporal_value(3.14) is False

    def test_none_is_not_temporal(self) -> None:
        assert is_temporal_value(None) is False

    def test_plain_string_is_not_temporal(self) -> None:
        assert is_temporal_value("hello") is False

    def test_bool_is_not_temporal(self) -> None:
        assert is_temporal_value(True) is False

    def test_year_string_not_temporal(self) -> None:
        # "2024" as a bare year is not an ISO temporal value —
        # it's numeric (matched by the numeric cell path).
        assert is_temporal_value("2024") is False


class TestFormatTableCellValueTemporal:
    """format_table_cell_value applies date_short when no column format is set."""

    def test_date_object_default_format(self) -> None:
        # date(2024, 1, 15) → "15 Jan 2024" (%-d %b %Y)
        result = format_table_cell_value(
            datetime.date(2024, 1, 15), None, {"date_short": _DATE_SHORT_FORMAT}
        )
        assert result == "15 Jan 2024"

    def test_datetime_object_default_format(self) -> None:
        # datetime without custom spec → date component only ("date_short")
        result = format_table_cell_value(
            datetime.datetime(2024, 1, 15, 13, 30, 0),
            None,
            {"date_short": _DATE_SHORT_FORMAT},
        )
        assert result == "15 Jan 2024"

    def test_iso_date_string_default_format(self) -> None:
        result = format_table_cell_value(
            "2024-01-15", None, {"date_short": _DATE_SHORT_FORMAT}
        )
        assert result == "15 Jan 2024"

    def test_iso_timestamp_string_default_format(self) -> None:
        result = format_table_cell_value(
            "2024-01-15T13:30:00", None, {"date_short": _DATE_SHORT_FORMAT}
        )
        assert result == "15 Jan 2024"

    def test_iso_timestamp_microseconds(self) -> None:
        result = format_table_cell_value(
            "2024-01-15T13:30:00.123456",
            None,
            {"date_short": _DATE_SHORT_FORMAT},
        )
        assert result == "15 Jan 2024"

    def test_explicit_d3_time_format(self) -> None:
        # Author sets format: "%b %d, %Y" in style.columns
        result = format_table_cell_value(
            datetime.date(2024, 1, 15),
            "%b %d, %Y",
            {"date_short": _DATE_SHORT_FORMAT},
        )
        assert result == "Jan 15, 2024"

    def test_time_directive_reflects_actual_time_of_day(self) -> None:
        # A datetime (object or ISO string) carries a real time component --
        # a %H:%M directive must read it, not the midnight a date-only
        # reduction would produce.
        result = format_table_cell_value(
            datetime.datetime(2024, 1, 15, 14, 30, 0),
            "%H:%M",
            {"date_short": _DATE_SHORT_FORMAT},
        )
        assert result == "14:30"

    def test_time_directive_on_iso_timestamp_string(self) -> None:
        result = format_table_cell_value(
            "2024-01-15T14:30:00",
            "%H:%M",
            {"date_short": _DATE_SHORT_FORMAT},
        )
        assert result == "14:30"

    def test_time_directive_on_iso_date_only_string_has_no_time(self) -> None:
        # A date-only value has no time to reflect -- midnight is correct
        # here, not a bug (there is no real time component to lose).
        result = format_table_cell_value(
            "2024-01-15",
            "%H:%M",
            {"date_short": _DATE_SHORT_FORMAT},
        )
        assert result == "00:00"

    def test_explicit_format_on_iso_string(self) -> None:
        result = format_table_cell_value(
            "2024-01-15",
            "%Y-%m-%d",
            {"date_short": _DATE_SHORT_FORMAT},
        )
        assert result == "2024-01-15"

    def test_invalid_temporal_format_spec_raises(self) -> None:
        # %Q is not a valid strftime directive — must raise, not silently pass through.
        with pytest.raises(ValueError, match="%Q"):
            format_table_cell_value(
                datetime.date(2024, 1, 15),
                "%Q",
                {"date_short": _DATE_SHORT_FORMAT},
            )

    def test_non_temporal_value_unchanged_str(self) -> None:
        # Regular string passes through unchanged
        result = format_table_cell_value(
            "hello", None, {"date_short": _DATE_SHORT_FORMAT}
        )
        assert result == "hello"

    def test_numeric_value_not_treated_as_temporal(self) -> None:
        # Integer: not temporal — formats via the theme's number spec
        # (SI here), not the date path.
        result = format_table_cell_value(
            1234, None, {"date_short": _DATE_SHORT_FORMAT, "number": ".3~s"}
        )
        assert result == "1.23 K"

    def test_single_digit_day_no_leading_zero(self) -> None:
        # %-d suppresses leading zero: "5 Jan 2024" not "05 Jan 2024"
        result = format_table_cell_value(
            datetime.date(2024, 1, 5), None, {"date_short": _DATE_SHORT_FORMAT}
        )
        assert result == "5 Jan 2024"

    def test_timezone_aware_datetime(self) -> None:
        # Aware datetime that crosses UTC day boundary: PST 23:00 Jan 15 = UTC Jan 16.
        # This exercises the astimezone(UTC) path — UTC midnight can't catch it.
        pst = datetime.timezone(datetime.timedelta(hours=-8))
        aware = datetime.datetime(2024, 1, 15, 23, 0, 0, tzinfo=pst)
        result = format_table_cell_value(
            aware, None, {"date_short": _DATE_SHORT_FORMAT}
        )
        assert result == "16 Jan 2024"

    def test_timezone_aware_datetime_time_directive_reads_utc_time(self) -> None:
        # An aware value is normalized to UTC before formatting (matching the
        # date-only path's day-boundary behavior above) -- a time directive
        # must read the UTC wall clock (07:00), not the original tz's (23:00).
        pst = datetime.timezone(datetime.timedelta(hours=-8))
        aware = datetime.datetime(2024, 1, 15, 23, 0, 0, tzinfo=pst)
        result = format_table_cell_value(aware, "%H:%M", {})
        assert result == "07:00"

    def test_timezone_aware_datetime_zone_directive_reads_utc(self) -> None:
        # %Z/%z now resolve against the UTC-normalized value (it previously
        # read the offset-less date-only reduction, so %Z rendered empty).
        pst = datetime.timezone(datetime.timedelta(hours=-8))
        aware = datetime.datetime(2024, 1, 15, 23, 0, 0, tzinfo=pst)
        result = format_table_cell_value(aware, "%H:%M %Z", {})
        assert result == "07:00 UTC"

    def test_timezone_aware_iso_string_crosses_day_boundary(self) -> None:
        # ISO string with TZ offset: 23:00 PST on Jan 15 = 07:00 UTC on Jan 16.
        # Must render as "16 Jan 2024", not "15 Jan 2024" (which the old truncating
        # parser produced by dropping the -08:00 suffix).
        result = format_table_cell_value(
            "2024-01-15T23:00:00-08:00",
            None,
            {"date_short": _DATE_SHORT_FORMAT},
        )
        assert result == "16 Jan 2024"

    def test_timezone_aware_iso_string_z_suffix(self) -> None:
        # ISO string with Z suffix must be treated as UTC.
        result = format_table_cell_value(
            "2024-01-15T13:00:00Z",
            None,
            {"date_short": _DATE_SHORT_FORMAT},
        )
        assert result == "15 Jan 2024"

    def test_non_temporal_format_on_temporal_column_raises(self) -> None:
        # A d3/numeric format spec applied to a date must raise — not silently
        # fall back to str(value). "validate and error fast" non-negotiable.
        with pytest.raises(
            ChartDataError, match="does not match its cell values"
        ) as exc:
            format_table_cell_value(
                datetime.date(2024, 1, 15),
                "compact",
                {"date_short": _DATE_SHORT_FORMAT},
            )
        # Pins the diagnostic code identity, not just the message: a plain
        # ChartDataError(...) with no code defaults to ERR_INTERNAL and would
        # pass the message-only assertion above.
        assert exc.value.code is not None
        assert exc.value.code.code == "ERR-TABLE-FORMAT-KIND-MISMATCH"

    def test_temporal_format_on_numeric_column_raises(self) -> None:
        with pytest.raises(
            ChartDataError, match="does not match its cell values"
        ) as exc:
            format_table_cell_value(14, "time_short")
        assert exc.value.code is not None
        assert exc.value.code.code == "ERR-TABLE-FORMAT-KIND-MISMATCH"

    def test_temporal_format_on_numeric_string_raises(self) -> None:
        # The except (ValueError, TypeError) swallow just below this branch
        # in the source would otherwise hide this exact mismatch.
        with pytest.raises(
            ChartDataError, match="does not match its cell values"
        ) as exc:
            format_table_cell_value("14", "time_short")
        assert exc.value.code is not None
        assert exc.value.code.code == "ERR-TABLE-FORMAT-KIND-MISMATCH"

    def test_date_like_numeric_string_under_time_format_unaffected(self) -> None:
        # A bare-year string ("2024") is date-like, not a genuine numeric
        # mismatch: a mixed date column can carry one such outlier row (see
        # render/chart/AGENTS.md invariant 11), and it must keep rendering
        # unchanged rather than raising like a real numeric value would.
        result = format_table_cell_value("2024", "time_short")
        assert result == "2024"

    def test_date_like_numeric_string_with_numeric_format_still_formats(self) -> None:
        result = format_table_cell_value("2024", "$,.0f")
        assert result == "$2,024"

    def test_literal_prefixed_strftime_spec_not_misdetected_as_non_temporal(
        self,
    ) -> None:
        # A strftime spec with literal text before its first directive (e.g.
        # authors writing "Week %W") must not be rejected as a mismatched
        # d3-format spec: is_time_format scans the whole string, not just
        # its first character.
        result = format_table_cell_value(datetime.date(2024, 1, 15), "Week %W")
        assert result == "Week 03"

    def test_numeric_fill_percent_spec_not_misdetected_as_temporal(self) -> None:
        # d3-format's `%` is also a legal *fill* character (e.g. "%>10.2f"),
        # so a leading "%" alone must not be read as a strftime spec.
        result = format_table_cell_value(1234.5, "%>10.2f")
        assert result == "%%%1234.50"

    def test_date_short_works_without_formats_dict(self) -> None:
        # "date_short" is predefined — no formats dict needed.
        # Whether formats={} or formats=None, the predefined spec is used.
        result_empty = format_table_cell_value(datetime.date(2024, 1, 15), None, {})
        result_none = format_table_cell_value(datetime.date(2024, 1, 15), None, None)
        assert result_empty == "15 Jan 2024"
        assert result_none == "15 Jan 2024"

    def test_postgres_space_separated_timestamp_detected(self) -> None:
        # DuckDB sometimes returns "YYYY-MM-DD HH:MM:SS" (space separator, no T)
        # when the result is fetched as a string. is_temporal_value must detect it.
        assert is_temporal_value("2024-01-15 13:30:00") is True

    def test_postgres_space_timestamp_formats_correctly(self) -> None:
        result = format_table_cell_value(
            "2024-01-15 13:30:00", None, {"date_short": _DATE_SHORT_FORMAT}
        )
        assert result == "15 Jan 2024"

    def test_invalid_date_string_rejected(self) -> None:
        # "2024-13-99" matches the ISO regex shape but is not a real date.
        # is_temporal_value must reject it after post-regex validation.
        assert is_temporal_value("2024-13-99") is False

    def test_invalid_date_not_formatted(self) -> None:
        # An invalid date string should fall through to str() not raise.
        result = format_table_cell_value(
            "2024-13-99", None, {"date_short": _DATE_SHORT_FORMAT}
        )
        assert result == "2024-13-99"


class TestIsDateLikeWithTemporalObjects:
    """is_date_like must accept Python date/datetime so layout decisions are correct."""

    def test_date_object_is_date_like(self) -> None:
        # After this PR, DuckDB returns datetime.date objects directly.
        # is_date_like must return True so right-align and no-wrap apply.
        assert is_date_like(datetime.date(2024, 1, 15)) is True

    def test_datetime_object_is_date_like(self) -> None:
        assert is_date_like(datetime.datetime(2024, 1, 15, 13, 30, 0)) is True

    def test_integer_not_date_like(self) -> None:
        assert is_date_like(42) is False

    def test_string_date_still_works(self) -> None:
        assert is_date_like("15 Jan 2024") is True

    def test_parity_with_inspect_strftime_output(self) -> None:
        """Regression: render output matches the old inspect strftime literal."""
        d = datetime.date(2024, 5, 19)
        result = format_table_cell_value(d, None, {"date_short": _DATE_SHORT_FORMAT})
        assert result == "19 May 2024"


class TestFormatTableCellValueStringWithFormatConfig:
    """CSV adapters deliver every value as a string; a format_config on the column
    must still produce formatted output (e.g. currency) rather than the raw string."""

    def test_string_numeric_with_currency_format(self) -> None:
        """String '1234.5' + currency format_config → formatted currency string."""
        result = format_table_cell_value("1234.5", "$,.2f")
        assert result == "$1,234.50"

    def test_string_integer_with_comma_format(self) -> None:
        """String '42000' + comma format_config → '42,000'."""
        result = format_table_cell_value("42000", ",")
        assert result == "42,000"

    def test_non_numeric_string_with_format_config_falls_through(self) -> None:
        """Non-numeric string 'N/A' with format_config → raw string (no crash)."""
        result = format_table_cell_value("N/A", "$,.2f")
        assert result == "N/A"


def _render_table_svg(data: list[dict[str, object]], column_format: str) -> str:
    """Compile+resolve+render a single-column table board for board-level
    (not unit-level format_table_cell_value) assertions."""
    board_rs, board_ctx = resolve_style_and_context(
        get_theme_style(get_default_theme_name())
    )
    chart = TableChart(
        id="t1",
        type="table",
        style=TableChartStylePatch.model_validate(
            {"columns": {"value": {"format": column_format}}}
        ),
    )
    resolved = resolve(chart, data, chart_style_context=board_ctx)
    assert isinstance(resolved, ResolvedTableChart)
    return render_table_svg(resolved, data, width=400, board_style=board_rs)


class TestTemporalFormatOnNumericColumnBoardLevel:
    """format_table_cell_value's guard is bypassed by table.py's own direct
    format_kpi_parts call in _compute_lane_positions (shared-scale/lane
    measurement, which scans every row, unlike measure_column_demands's
    50-row sample). This pins the board-level render path, not just the
    unit-level formatter -- and the sibling class below pins that the same
    guard does NOT misfire on a legitimate date/timestamp column."""

    def test_late_numeric_row_under_time_format_raises(self) -> None:
        # measure_column_demands only samples data[:50], so a numeric cell
        # at row 51 is exactly the window its own guard misses.
        data: list[dict[str, object]] = [{"value": None} for _ in range(50)]
        data.append({"value": 14})
        with pytest.raises(
            ChartDataError, match="does not match its cell values"
        ) as exc:
            _render_table_svg(data, "time_short")
        assert exc.value.code is not None
        assert exc.value.code.code == "ERR-TABLE-FORMAT-KIND-MISMATCH"


class TestTimeFormatOnLegitimateDateColumnBoardLevel:
    """The board-level guard in _compute_lane_positions must never misfire
    on a real date/timestamp column: it fires only for a value that
    survives float(raw) and isinstance(num_val, (int, float)), so None, a
    non-numeric string, and a timestamp string with a time component (which
    is_date_like alone does not recognize) all skip it and reach
    format_table_cell_value's own date-formatting path instead."""

    def test_date_column_with_null_row_renders(self) -> None:
        data: list[dict[str, object]] = [{"value": "2024-01-15"}, {"value": None}]
        svg = _render_table_svg(data, "%Y-%m-%d")
        assert "2024-01-15" in svg

    def test_timestamp_string_column_under_date_short_renders(self) -> None:
        data: list[dict[str, object]] = [
            {"value": "2024-03-15 09:30:00"},
            {"value": "2024-03-16 10:00:00"},
        ]
        svg = _render_table_svg(data, "date_short")
        assert "15 Mar 2024" in svg

    def test_date_column_with_text_sentinel_renders(self) -> None:
        data: list[dict[str, object]] = [{"value": "2024-01-15"}, {"value": "N/A"}]
        svg = _render_table_svg(data, "%Y-%m-%d")
        assert "2024-01-15" in svg
        assert "N/A" in svg
