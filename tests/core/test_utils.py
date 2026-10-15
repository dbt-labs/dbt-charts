"""Tests for cross-cutting value-classification and label-measurement helpers
in dbt_charts.core.utils.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.utils import (
    DEFAULT_VL_LABEL_LIMIT,
    cap_padding_to_label_limit,
    classify_date_column_align,
    is_date_like,
    is_year_shaped,
    measured_label_padding,
)


class TestIsYearShaped:
    def test_integer_years_in_range(self) -> None:
        assert is_year_shaped([2010, 2015, 2024]) is True

    def test_string_years_in_range(self) -> None:
        assert is_year_shaped(["2010", "2015", "2024"]) is True

    def test_integer_below_range_not_year(self) -> None:
        # counts / small integers must never read as years
        assert is_year_shaped([1, 2, 3, 4, 5]) is False

    def test_integer_above_range_not_year(self) -> None:
        assert is_year_shaped([100, 200, 300]) is False

    def test_year_boundaries_inclusive(self) -> None:
        assert is_year_shaped([1700, 2100]) is True
        assert is_year_shaped([1699]) is False
        assert is_year_shaped([2101]) is False

    def test_mixed_year_and_non_year_not_year(self) -> None:
        assert is_year_shaped([2010, 2011, 42]) is False
        assert is_year_shaped(["2010", "Q1"]) is False

    def test_float_years_not_year(self) -> None:
        # floats are not year buckets; measures never yield year x
        assert is_year_shaped([2014.0, 2015.0]) is False

    def test_booleans_not_year(self) -> None:
        assert is_year_shaped([True, False]) is False

    def test_empty_not_year(self) -> None:
        assert is_year_shaped([]) is False

    def test_non_4_digit_string_not_year(self) -> None:
        assert is_year_shaped(["201", "20100"]) is False

    def test_integral_decimal_years_in_range(self) -> None:
        # BigQuery NUMERIC / Snowflake NUMBER return year integers as Decimal.
        assert is_year_shaped([Decimal(2024), Decimal(2025)]) is True

    def test_non_integral_decimal_not_year(self) -> None:
        assert is_year_shaped([Decimal("2024.5")]) is False

    def test_integral_decimal_out_of_range_not_year(self) -> None:
        assert is_year_shaped([Decimal(42)]) is False


class TestIsDateLike:
    def test_date_object(self) -> None:
        assert is_date_like(datetime.date(2024, 1, 15)) is True

    def test_datetime_object(self) -> None:
        assert is_date_like(datetime.datetime(2024, 1, 15, 13, 30, 0)) is True

    def test_short_month_name(self) -> None:
        assert is_date_like("15 Jan 2024") is True

    def test_full_month_name_not_matched(self) -> None:
        # The known gap this detector doesn't close — full month names
        # ("January") are not in the pattern. See classify_date_column_align
        # for why a single non-matching cell should demote the whole column.
        assert is_date_like("15 January 2024") is False

    def test_bare_year(self) -> None:
        assert is_date_like("2024") is True

    def test_plain_number_not_date(self) -> None:
        assert is_date_like(42) is False

    def test_bool_not_date(self) -> None:
        assert is_date_like(True) is False

    def test_non_date_string(self) -> None:
        assert is_date_like("hello") is False


class TestClassifyDateColumnAlign:
    def test_uniform_short_month_names_right(self) -> None:
        assert classify_date_column_align(["15 Jan 2024", "3 Feb 2024"]) == "right"

    def test_mixed_month_name_length_no_verdict(self) -> None:
        # One cell spelled with the full month name breaks the unanimous
        # match — the column must not right-align on a per-cell majority.
        assert classify_date_column_align(["30 March 2024", "9 May 2024"]) is None

    def test_numeric_values_no_verdict(self) -> None:
        # Numeric columns already right-align unconditionally elsewhere;
        # this classifier only ever produces a temporal verdict.
        assert classify_date_column_align([1, 2, 3]) is None

    def test_null_values_ignored(self) -> None:
        assert (
            classify_date_column_align(["15 Jan 2024", None, "3 Feb 2024"]) == "right"
        )

    def test_blank_string_ignored(self) -> None:
        assert (
            classify_date_column_align(["15 Jan 2024", "  ", "3 Feb 2024"]) == "right"
        )

    def test_all_null_no_verdict(self) -> None:
        assert classify_date_column_align([None, None]) is None

    def test_empty_no_verdict(self) -> None:
        assert classify_date_column_align([]) is None

    def test_date_objects_right(self) -> None:
        assert (
            classify_date_column_align(
                [datetime.date(2024, 1, 15), datetime.date(2024, 2, 3)]
            )
            == "right"
        )

    def test_datetime_objects_no_verdict(self) -> None:
        # datetime.datetime always stringifies with a time component (even
        # at midnight) — normalize_scalar_for_json's str(value) fallback,
        # applied before render's own per-cell checks run. Classifying the
        # raw object as date-like here would right-align a column whose
        # cells render as plain (non-date-matching, non-tabular) text.
        assert (
            classify_date_column_align(
                [
                    datetime.datetime(2024, 1, 15, 0, 0, 0),
                    datetime.datetime(2024, 2, 3, 0, 0, 0),
                ]
            )
            is None
        )


class TestMeasuredLabelPadding:
    def test_empty_labels_reserve_no_gutter(self) -> None:
        assert measured_label_padding([], "Inter", 12.0) == 0.0

    def test_wider_labels_reserve_a_larger_gutter(self) -> None:
        narrow = measured_label_padding(["5"], "Inter", 12.0)
        wide = measured_label_padding(["1500000"], "Inter", 12.0)
        assert wide > narrow

    def test_gutter_grows_with_the_widest_label_only(self) -> None:
        one_label = measured_label_padding(["1500000"], "Inter", 12.0)
        many_labels = measured_label_padding(["0", "5", "1500000"], "Inter", 12.0)
        assert one_label == many_labels

    def test_does_not_add_tick_length(self) -> None:
        """Regression: Vega-Lite's own ``labelPadding`` is already measured
        from the tick's outer edge (``anchor_x = tickSize_if_visible +
        labelPadding``), so adding tick length here on top double-counts it
        whenever ticks are visible, and reserves unearned dead space when
        they aren't. This is the "labels moved further from the axis than
        they should" bug: the gutter is exactly ``max_label_width +
        breathing_room``, independent of any tick size the caller might
        otherwise have had in scope.
        """
        padding = measured_label_padding(["30,000"], "Inter", 11.0)
        measurer = get_font_measurer("Inter")
        expected = measurer.measure("30,000", 11.0) + 4.0  # breathing room
        assert padding == pytest.approx(expected)


class TestCapPaddingToLabelLimit:
    """Vega-Lite truncates any axis label wider than its labelLimit
    (``axis.label.max_width``, or VL's own 180px default when unset) with an
    ellipsis — reserving gutter space for the *untruncated* text wastes space
    the rendered label never uses. Capping at the same limit VL truncates to
    keeps the gutter tight without under-reserving (VL's truncated text is
    always <= labelLimit).
    """

    def test_caps_padding_computed_from_a_much_wider_label(self) -> None:
        wide_label = "Connection timeout - upstream service returned malformed response"
        uncapped = measured_label_padding([wide_label], "Inter", 12.0)
        capped = cap_padding_to_label_limit(uncapped, label_limit=100.0)
        assert capped < uncapped

    def test_does_not_cap_when_label_already_fits(self) -> None:
        padding = measured_label_padding(["A"], "Inter", 12.0)
        capped = cap_padding_to_label_limit(padding, label_limit=180.0)
        assert capped == padding

    def test_default_vl_label_limit_is_a_positive_constant(self) -> None:
        """Callers fall back to this when axis.label.max_width is unset —
        Vega-Lite's own default (180px).
        """
        assert DEFAULT_VL_LABEL_LIMIT > 0
