"""Tests for read-only variable display formatting."""

import xml.etree.ElementTree as ET

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.resolve.style.board import resolve_style
from dbt_charts.core.render.variables_resolve import (
    format_date_label,
    format_daterange_label,
    format_variable_display_value,
)

from ._control_utils import render_strip_for


def _rs():
    return resolve_style(get_theme_style())


class TestFormatVariableDisplayValue:
    def test_select_unset_uses_all(self) -> None:
        var = Variable(input="select")
        assert format_variable_display_value(var, None) == "All"
        assert format_variable_display_value(var, "") == "All"

    def test_select_unset_uses_placeholder(self) -> None:
        var = Variable(input="select", placeholder="All sources")
        assert format_variable_display_value(var, None) == "All sources"

    def test_select_with_value(self) -> None:
        var = Variable(input="select")
        assert format_variable_display_value(var, "organic") == "organic"

    def test_select_rejects_a_list_value(self) -> None:
        # A JSON-array URL param (the shape multiselect/daterange legitimately
        # carry) landing on a scalar select must raise, not stringify — a
        # silent str(["a", "b"]) round-trips a Python repr through data-value
        # and back through updateVariable as one malformed value.
        var = Variable(input="select")
        with pytest.raises(ValueError, match="expects a scalar value"):
            format_variable_display_value(var, ["a", "b"])

    def test_radio_rejects_a_list_value(self) -> None:
        var = Variable(input="radio")
        with pytest.raises(ValueError, match="expects a scalar value"):
            format_variable_display_value(var, ["a", "b"])

    def test_multiselect_unset_and_set(self) -> None:
        var = Variable(input="multiselect", placeholder="All plans")
        assert format_variable_display_value(var, None) == "All plans"
        assert format_variable_display_value(var, []) == "All plans"
        assert format_variable_display_value(var, ["pro", "team"]) == "pro, team"

    def test_daterange_unset(self) -> None:
        var = Variable(input="daterange")
        assert format_variable_display_value(var, None) == "All dates"
        assert format_variable_display_value(var, ["", ""]) == "All dates"

    def test_daterange_empty_list_is_unset(self) -> None:
        # A cleared date picker publishes [], the same wire form multiselect
        # uses for unset — it must caption as unset, not raise while
        # formatting the control's label.
        var = Variable(input="daterange")
        assert format_variable_display_value(var, []) == "All dates"

    def test_daterange_blank_string_is_unset(self) -> None:
        # Pins the other half of variable_value_is_absent: a hand-written
        # `?date_range=` arrives as a bare "", not a list. Before this fix it
        # raised ValueError("expects a 2-element list, got str") while
        # captioning the control, killing the whole render. A narrowing that
        # only checks container emptiness would restore that crash.
        var = Variable(input="daterange")
        assert format_variable_display_value(var, "") == "All dates"

    def test_daterange_unset_placeholder(self) -> None:
        var = Variable(input="daterange", placeholder="Any time")
        assert format_variable_display_value(var, ["", ""]) == "Any time"

    def test_daterange_set_matches_chip_formatter(self) -> None:
        var = Variable(input="daterange")
        start, end = "2026-04-09", "2026-04-28"
        assert format_variable_display_value(
            var, [start, end]
        ) == format_daterange_label(start, end)

    def test_daterange_malformed_raises(self) -> None:
        var = Variable(input="daterange")
        with pytest.raises(ValueError, match="exactly 2 elements"):
            format_variable_display_value(var, ["2026-01-01"])

    def test_date_formats_like_daterange_endpoint(self) -> None:
        var = Variable(input="date")
        assert format_variable_display_value(var, "2025-11-19") == "19 Nov 2025"

    def test_datepicker_formats_like_daterange_endpoint(self) -> None:
        var = Variable(input="datepicker")
        assert format_variable_display_value(var, "2025-11-19") == "19 Nov 2025"

    def test_date_unset(self) -> None:
        var = Variable(input="date")
        assert format_variable_display_value(var, None) == "Any date"
        assert format_variable_display_value(var, "") == "Any date"

    def test_format_date_label_falls_back_to_raw_on_parse_error(self) -> None:
        assert format_date_label("2025-11-19") == "19 Nov 2025"
        assert format_date_label("not-a-date") == "not-a-date"

    def test_never_python_list_repr_in_readonly_svg(self) -> None:
        svg, height = render_strip_for(
            {
                "date_range": Variable(input="daterange", label="Date Range"),
                "plan": Variable(
                    input="select",
                    label="Plan",
                    placeholder="All plans",
                ),
            },
            {"date_range": ["", ""], "plan": None},
            1200.0,
            None,
            _rs(),
            variables_path="variables",
        )
        assert height > 0
        # The drawn text, not the whole document: `data-dbt-value` legitimately
        # publishes a list-valued variable as JSON for a host to commit. What
        # must never carry a Python repr is what the user reads.
        drawn = "".join(
            el.text or ""
            for el in ET.fromstring(
                f"<svg xmlns='http://www.w3.org/2000/svg'>{svg}</svg>"
            ).iter()
        )
        assert "['" not in drawn
        assert "Date Range:" in svg
        assert "All dates" in svg
        assert "Plan:" in svg
        assert "All plans" in svg


class TestCompanyOverviewStyleReadOnlyStrip:
    """Unset filters on company-overview-style variables."""

    def test_unset_filters_read_friendly(self) -> None:
        variables = {
            "date_range": Variable(input="daterange", label="Date Range"),
            "signup_source": Variable(
                input="select",
                label="Signup Source",
                placeholder="All sources",
            ),
            "plan": Variable(
                input="select",
                label="Plan",
                placeholder="All plans",
            ),
        }
        svg, _ = render_strip_for(
            variables, {}, 1600.0, None, _rs(), variables_path="variables"
        )
        assert "Signup Source:" in svg
        assert "All sources" in svg
        assert "Plan:" in svg
        assert "All plans" in svg
        assert "Date Range:" in svg
        assert "All dates" in svg
        assert "[" not in svg
