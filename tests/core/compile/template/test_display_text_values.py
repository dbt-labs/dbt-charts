"""How a variable's value prints inside board and chart display text."""

from typing import Any

import pytest

from dbt_charts.core.compile.template.display_text import resolve_display_text
from dbt_charts.core.compile.template.variables import (
    DaterangeValue,
    MultiselectValue,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError


def _text(template: str, **variables: Any) -> str:
    return resolve_display_text(template, variables, {}, None)


@pytest.mark.parametrize(
    ("selected", "printed"),
    [
        (["Referral"], "Referral"),
        (["Referral", "Webinar"], "Referral and Webinar"),
        (["Referral", "Webinar", "Paid"], "Referral, Webinar, and Paid"),
    ],
)
def test_multiselect_prints_as_a_natural_list(
    selected: list[str], printed: str
) -> None:
    text = _text("Won, {{ lead_source }}", lead_source=MultiselectValue(selected))
    assert text == f"Won, {printed}"


def test_scalar_variables_print_unchanged() -> None:
    assert _text("{{ a }} {{ b }}", a="x", b=3) == "x 3"


def test_mapping_variable_in_text_is_refused_by_name() -> None:
    with pytest.raises(ChartDataError, match="mapping"):
        _text("{{ lead_source }}", lead_source={"a": 1})


def test_nested_multiselect_in_text_is_refused() -> None:
    with pytest.raises(ChartDataError, match="nested list"):
        _text("{{ v }}", v=MultiselectValue([["a"], ["b"]]))


def test_daterange_in_text_is_refused_and_its_ends_still_print() -> None:
    value = DaterangeValue(["2026-01-01", "2026-03-31"])
    with pytest.raises(ChartDataError, match="date range"):
        _text("{{ r }}", r=value)
    assert _text("{{ r[0] }} to {{ r[1] }}", r=value) == "2026-01-01 to 2026-03-31"
