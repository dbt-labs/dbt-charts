"""category_label_text: the CATEGORY-axis twin of cadence_label_text.

Unit coverage for the leaf helper itself — the per-site regression tests
(overlap resolver, horizontal-bar gutter, facet extra-axis width) live in
their own files and prove the helper is actually wired in at each site.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.text.category_label import category_label_text


def test_numeric_string_gets_authored_number_format() -> None:
    assert category_label_text("1200", "$,.0f", None) == "$1,200"


def test_non_numeric_value_keeps_raw_text_under_a_number_format() -> None:
    # Mirrors gate_label_format's own permissiveness: a nominal category
    # column that reads as numbers stays formattable even if one row
    # doesn't — but a value that doesn't read as a number is left as-is
    # rather than raising or guessing.
    assert category_label_text("acme", "$,.0f", None) == "acme"


def test_no_number_format_leaves_value_untouched() -> None:
    assert category_label_text("1200", None, None) == "1200"


def test_upper_case_applies() -> None:
    assert category_label_text("acme corp", None, "upper") == "ACME CORP"


def test_lower_case_applies() -> None:
    assert category_label_text("ACME", None, "lower") == "acme"


def test_other_case_values_are_no_ops() -> None:
    # "title"/"sentence"/"slug"/"camel" never reach an axis label as a
    # Vega label expression (inject_axis_label_case only wires up/lower) —
    # applying them here would measure text Vega never paints.
    assert category_label_text("acme corp", None, "title") == "acme corp"
    assert category_label_text("acme corp", None, "sentence") == "acme corp"


def test_format_and_case_compose() -> None:
    assert category_label_text("1200", "$,.0f", "upper") == "$1,200"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_value_formats_as_zero_like_js(blank: str) -> None:
    # JS reads +"" as 0, so d3 paints the formatted zero.
    assert category_label_text(blank, "$,.0f", None) == "$0"


def test_separator_padded_number_formats_like_reads_as_number() -> None:
    # str.strip() removes U+001C-U+001F; float() alone rejects them.
    assert category_label_text("\x1c1", "$,.0f", None) == "$1"
