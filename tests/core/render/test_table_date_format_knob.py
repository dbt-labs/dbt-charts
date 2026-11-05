"""An authored strftime spec on a table date column reaches the rendered SVG.

The knob a migrated board needs to carry its source's date format is
``style.columns.<col>.format`` with a strftime spec -- ``columns`` is one of
``_TIME_CAPABLE_FIELDS`` in ``compile/validate/formats.py``, so a ``%``-spec is
compile-accepted there, and ``format_table_cell_value`` routes it to strftime.
There is deliberately no second ``date_format`` field: a board that wants
``2026-04-10`` instead of the ``date_short`` default writes it here.

These pin the whole path -- compile, resolve, render -- because the cell-level
half was already covered (``test_table_temporal_formatting.py``) while nothing
proved an *authored* spec survives validation and reaches the SVG.
"""

from .._svg_render import render_board_to_svg

_BOARD = """
queries:
  events:
    columns: [day, hits]
    values:
      - [2026-04-10, 12]
      - [2026-07-28, 34]
charts:
  t:
    query: events
    type: table
{columns}
"""

_ISO_COLUMN = """    style:
      columns:
        day:
          format: "%Y-%m-%d"
"""


def test_authored_strftime_spec_reaches_the_rendered_cell() -> None:
    svg = render_board_to_svg(_BOARD.format(columns=_ISO_COLUMN))
    assert ">2026-04-10<" in svg, "authored %Y-%m-%d did not reach the cell"
    assert ">2026-07-28<" in svg
    assert "10 Apr 2026" not in svg, "date_short default won over the authored spec"


def test_unformatted_date_column_keeps_the_date_short_default() -> None:
    """Omitting format: is what selects the engine default -- not a missing knob."""
    svg = render_board_to_svg(_BOARD.format(columns=""))
    assert ">10 Apr 2026<" in svg
    assert "2026-04-10" not in svg
