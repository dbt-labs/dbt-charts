"""``date_year`` / ``date_quarter`` / ``date_month`` / ``date_week``: engine names
for a date axis's per-grain labels, restyled by redefining the name."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.models.schema_names import FormatAlias
from dbt_charts.core.text.predefined_formats import GRAIN_FORMAT_NAMES, GRAIN_NAMES

_MONTHLY = """
title: T
{style}
queries:
  q:
    columns: [month, revenue]
    values:
      - ["2024-01-01", 100]
      - ["2024-02-01", 150]
      - ["2024-03-01", 120]
      - ["2024-04-01", 180]
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
    style:
      orientation: vertical
{chart_style}
rows:
  - c
"""


def _board(style: str = "", chart_style: str = "") -> str:
    return _MONTHLY.format(style=style, chart_style=chart_style)


def test_every_grain_has_a_name() -> None:
    assert set(GRAIN_FORMAT_NAMES.values()) == {
        "date_year",
        "date_quarter",
        "date_month",
        "date_week",
    }


@pytest.mark.parametrize("name", sorted(GRAIN_NAMES))
@pytest.mark.parametrize(
    "slot_style",
    [
        "      axis_x:\n        labels:\n          format: NAME\n",
    ],
)
def test_a_slot_naming_an_unredefined_grain_is_an_error(
    name: str, slot_style: str
) -> None:
    result = compile_board(_board(chart_style=slot_style.replace("NAME", name)))
    assert [e.code for e in result.errors] == ["ERR-FORMAT-GRAIN-NAME-NOT-REDEFINED"]


def test_a_table_column_naming_an_unredefined_grain_is_an_error() -> None:
    result = compile_board(
        """
title: T
queries:
  q:
    columns: [month]
    values:
      - ["2024-01-01"]
charts:
  t:
    query: q
    type: table
    style:
      columns:
        month:
          format: date_year
rows:
  - t
"""
    )
    assert [e.code for e in result.errors] == ["ERR-FORMAT-GRAIN-NAME-NOT-REDEFINED"]


@pytest.mark.parametrize(
    ("name", "spec"),
    [
        ("date_year", "%Y"),
        ("date_quarter", "%b %Y"),
        ("date_month", "%b %Y"),
        ("date_week", "%-d %b %Y"),
    ],
)
def test_the_grain_name_error_leads_with_the_strftime_spec_to_write(
    name: str, spec: str
) -> None:
    result = compile_board(
        _board(
            chart_style=f"      axis_x:\n        labels:\n          format: {name}\n"
        )
    )
    (error,) = result.errors
    edit = f'Replace {name!r} with the strftime spec "{spec}".'
    assert edit in error.message
    assert error.message.index(edit) < error.message.index("style.formats")


def test_a_number_spec_under_a_grain_name_is_a_kind_error() -> None:
    result = compile_board(
        _board(style='style:\n  formats:\n    date_month: "$,.0f"\n')
    )
    assert [e.code for e in result.errors] == ["ERR-FORMAT-KIND-MISMATCH"]


def test_the_grain_names_are_not_offered_as_completions() -> None:
    assert not GRAIN_NAMES & set(FormatAlias.__args__)


def test_a_query_format_naming_an_unredefined_grain_is_an_error() -> None:
    result = compile_board(
        """
title: T
queries:
  q:
    columns: [month]
    values:
      - ["2024-01-01"]
    formats:
      month: date_month
charts:
  t:
    query: q
    type: table
rows:
  - t
"""
    )
    assert [e.code for e in result.errors] == ["ERR-FORMAT-GRAIN-NAME-NOT-REDEFINED"]


def test_an_alias_may_name_a_redefined_grain() -> None:
    result = compile_board(
        _board(
            style='style:\n  formats:\n    date_month: "%b %Y"\n    mine: date_month\n'
        )
    )
    assert result.errors == []


def test_a_table_cell_paints_a_redefined_grain_name() -> None:
    from dbt_charts.core.compile.format_value import format_table_cell_value
    from dbt_charts.core.compile.models.primitives import FormatTable

    formats = FormatTable(aliases={"date_month": "%b %Y"}, currency=None)
    assert format_table_cell_value("2024-01-15", "date_month", formats) == "Jan 2024"


@pytest.mark.parametrize(
    "aliases",
    [
        '    mine2: "$,.0f"\n    mine: mine2\n',
        "    mycur: currency\n    a: mycur\n",
    ],
)
def test_an_alias_naming_another_alias_is_a_compile_error(aliases: str) -> None:
    result = compile_board(_board(style=f"style:\n  formats:\n{aliases}"))
    assert result.errors
