"""tick_min_step_for_format: the min tick step a fixed-decimal d3 spec implies."""

from __future__ import annotations

from dbt_charts.core.compile.format import tick_min_step_for_format


def test_integer_format_implies_step_one():
    assert tick_min_step_for_format(",.0f") == 1.0


def test_one_decimal_format_implies_step_tenth():
    assert tick_min_step_for_format(".1f") == 0.1


def test_two_decimal_format_implies_step_hundredth():
    assert tick_min_step_for_format(",.2f") == 0.01


def test_delta_format_implies_step_one():
    assert tick_min_step_for_format("+,d") == 1.0


def test_precision_less_f_defaults_to_d3s_own_six_decimals():
    # d3 itself paints 6 decimals when none is authored (",f" -> "0.500000"),
    # not 0 -- a naive 0 default would derive a step 6 orders of magnitude
    # too coarse and collapse a genuinely distinguishable axis.
    assert tick_min_step_for_format(",f") == 10.0**-6


def test_precision_less_percent_defaults_to_d3s_own_six_decimals():
    assert tick_min_step_for_format(",%") == 10.0 ** -(6 + 2)


def test_percent_whole_accounts_for_x100():
    # ".0%" paints value*100 with 0 decimals -- 0.005 and 0.006 both round to
    # "1%", so the underlying value needs a step of 0.01, not 1.
    assert tick_min_step_for_format(".0%") == 0.01


def test_percent_one_decimal_accounts_for_x100():
    assert tick_min_step_for_format(".1%") == 0.001


def test_si_compact_format_derives_nothing():
    assert tick_min_step_for_format(".3~s") is None


def test_native_predefined_name_derives_nothing():
    # Bypasses d3 entirely (PREDEFINED_NATIVE) -- not parseable d3 grammar.
    assert tick_min_step_for_format("percent_number") is None


def test_empty_format_derives_nothing():
    assert tick_min_step_for_format("") is None
