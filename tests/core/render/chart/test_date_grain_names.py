"""``date_year`` / ``date_quarter`` / ``date_month`` / ``date_week``: engine names
for a date axis's per-grain labels, restyled by redefining the name."""

from __future__ import annotations

import json

import pytest
import vl_convert as vlc

from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.models.schema_names import FormatAlias
from dbt_charts.core.text.predefined_formats import GRAIN_FORMAT_NAMES, GRAIN_NAMES

from ..._svg_render import render_board_to_svg

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


def _painted_specs(board: str, monkeypatch: pytest.MonkeyPatch) -> str:
    specs: list[str] = []
    paint = vlc.vegalite_to_svg

    def record(spec: dict[str, object], *args: object, **kwargs: object) -> str:
        specs.append(json.dumps(spec))
        return paint(spec, *args, **kwargs)

    monkeypatch.setattr(vlc, "vegalite_to_svg", record)
    render_board_to_svg(board)
    assert specs, "no Vega-Lite spec was painted"
    return "\n".join(specs)


_REDEFINED = """style:
  formats:
    date_month: "%b-%y"
"""


def test_every_grain_has_a_name() -> None:
    assert set(GRAIN_FORMAT_NAMES.values()) == {
        "date_year",
        "date_quarter",
        "date_month",
        "date_week",
    }


def test_an_unredefined_board_paints_the_built_in_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    labels = _x_axis(_painted_specs(_board(), monkeypatch))["labelExpr"]
    assert "'%b'" in str(labels)
    assert "'%Y'" in str(labels)


def test_redefining_another_grain_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = _painted_specs(_board(), monkeypatch)
    other = _painted_specs(
        _board(style='style:\n  formats:\n    date_year: "%Y"\n'), monkeypatch
    )
    assert other == plain


def test_redefining_date_month_relabels_the_axis_and_the_tooltip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    painted = _painted_specs(_board(style=_REDEFINED), monkeypatch)
    # The axis labelExpr and the tooltip header each carry the board's spec.
    assert painted.count("%b-%y") >= 2


def test_a_redefinition_paints_as_an_authored_axis_format_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same one-row text under the same cadence gate as an authored
    axis format, so a redefined name never costs an axis its cadence."""
    redefined = _painted_specs(_board(style=_REDEFINED), monkeypatch)
    authored = _painted_specs(
        _board(
            chart_style="      axis_x:\n        labels:\n          format: '%b-%y'\n"
        ),
        monkeypatch,
    )
    axis = '"axis": {'
    assert (
        redefined.split(axis)[1].split('"title"')[0]
        == (authored.split(axis)[1].split('"title"')[0])
    )


def test_a_chart_axis_format_beats_the_board_redefinition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    painted = _painted_specs(
        _board(
            style=_REDEFINED,
            chart_style="      axis_x:\n        labels:\n          format: '%B'\n",
        ),
        monkeypatch,
    )
    assert "%B" in painted
    axis_part = painted.split('"description"')[0]
    assert "%b-%y" not in axis_part


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


def test_a_slot_format_naming_a_redefined_grain_paints_the_redefinition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    painted = _painted_specs(
        _board(
            style=_REDEFINED,
            chart_style="      axis_x:\n        labels:\n          format: date_month\n",
        ),
        monkeypatch,
    )
    assert "%b-%y" in painted


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


def _board_with_months(count: int, style: str) -> str:
    rows = "\n".join(
        f'      - ["{2023 + i // 12}-{i % 12 + 1:02d}-01", {100 + i}]'
        for i in range(count)
    )
    board = _board(style=style)
    start = board.index("    values:\n") + len("    values:\n")
    end = board.index("charts:")
    return board[:start] + rows + "\n" + board[end:]


def _x_axis(painted: str) -> dict[str, object]:
    return json.loads(painted.split("\n")[0])["encoding"]["x"]["axis"]


def test_the_redefined_text_is_what_the_overlap_gate_measures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long redefinition crowds a dense axis that the short built-in month
    labels fit on at quarterly cadence, so the gate coarsens to years."""
    plain = _x_axis(_painted_specs(_board_with_months(24, ""), monkeypatch))
    long_style = 'style:\n  formats:\n    date_month: "%B %Y"\n'
    long = _x_axis(_painted_specs(_board_with_months(24, long_style), monkeypatch))
    assert "% 3 === 0" in str(plain["labelExpr"])
    assert "% 3" not in str(long["labelExpr"])


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


@pytest.mark.parametrize("chart_type", ["line", "area", "scatter"])
def test_a_temporal_axis_paints_the_redefinition(
    chart_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The x-axis encoding carries the spec, not just the tooltip header."""
    board = _board(style=_REDEFINED).replace("type: bar", f"type: {chart_type}")
    board = board.replace("      orientation: vertical\n", "")
    assert _x_axis(_painted_specs(board, monkeypatch))["format"] == "%b-%y"
