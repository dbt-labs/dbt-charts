"""Default numeric formatting for table cells lacking an explicit ``format:``.

When a numeric cell has no explicit column format, ``default_number_format()``
returns the predefined name ``"number"``, which the engine resolves to
``.3~s``: big numbers read as SI (``452 M``), fractions as plain digits
(``0.67``), instead of full float or full-precision-string output.

``"number"`` is an engine-owned predefined name. It does not need to
live in the theme's ``formats`` dict; a board that redefines the name replaces
the default. An explicit column
format always wins.
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.format_value import (
    format_kpi_parts,
    format_table_cell_value,
)

# Any non-empty formats dict.
_FORMATS = {"mine": ",.2f"}


class TestUnformattedNumericDefault:
    def test_float_no_format_uses_si_default(self) -> None:
        # Was "452,342,060.87" (hardcoded ,.2f); now the predefined SI default.
        assert format_table_cell_value(452342060.8737367, None, _FORMATS) == "452M"

    def test_numeric_string_no_format_uses_si_default(self) -> None:
        # The str(value) passthrough bug: a numeric string with no column format
        # previously rendered at full precision. Now coerced + SI-formatted.
        assert format_table_cell_value("452342060.8737367", None, _FORMATS) == "452M"

    def test_fraction_default(self) -> None:
        assert format_table_cell_value(0.67109416775821, None, _FORMATS) == "0.67"
        assert format_table_cell_value("0.67109416775821", None, _FORMATS) == "0.67"

    def test_kpi_parts_splits_si_suffix(self) -> None:
        # Table numeric-lane cells go through format_kpi_parts with
        # default_number=True; the SI suffix splits into its own lane.
        assert format_kpi_parts(
            452342060.8737367, None, _FORMATS, default_number=True
        ) == (
            "",
            "452",
            "M",
        )

    def test_kpi_parts_integer_default(self) -> None:
        assert format_kpi_parts(849, None, _FORMATS, default_number=True) == (
            "",
            "849",
            "",
        )

    def test_kpi_parts_without_default_keeps_exact_digits(self) -> None:
        # KPI callers pass no default: below-threshold values render exactly
        # (their digit-integrity contract), not SI-compacted.
        assert format_kpi_parts(131.5, None, _FORMATS) == ("", "131.5", "")

    def test_explicit_column_format_wins(self) -> None:
        # A set column format overrides the default, unchanged from before.
        assert format_table_cell_value(0.67109, "percent", _FORMATS) == "67.1%"
        assert format_kpi_parts(0.67109, "percent", _FORMATS) == ("", "67.1", "%")

    def test_number_works_without_formats_dict(self) -> None:
        # "number" is predefined — no formats dict needed.
        assert format_table_cell_value(452342060.0, None, None) == "452M"
        assert format_table_cell_value(452342060.0, None, {}) == "452M"

    def test_non_numeric_string_untouched(self) -> None:
        assert format_table_cell_value("Marketing", None, _FORMATS) == "Marketing"

    def test_temporal_string_untouched(self) -> None:
        # ISO date still formats as a date via the predefined date_short.
        assert format_table_cell_value("2026-06-01", None, _FORMATS) == "1 Jun 2026"


class TestThemeCascadeSuppliesDefault:
    def test_predefined_number_independent_of_theme_formats(self) -> None:
        # "number" is predefined; themes need not include it in formats.
        # Stark has no formats dict but numeric defaults still work.
        assert get_theme_style("stark").formats is None
        assert format_table_cell_value(452342060.0, None, None) == "452M"
