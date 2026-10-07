"""Regression: a cell whose text is deeper than its column's format precision.

`measure_column_demands` applied a column's `decimal_pad_table` to every cell's
rendered text unconditionally, including text `format_table_cell_value` produced
by falling through to `str(value)` because the value was not float-parseable.
That text owes nothing to the column's format, so its fractional depth can
exceed what the pad table was built for, and `decimal_pad_for` raises
`IndexError` -- surfacing as `ERR-INTERNAL` and taking down every chart on the
board, not just the offending table.

Both paint sites gate the pad on the full shape -- numeric, not bool, and not
date-like -- and pad the number lane alone rather than the whole rendered cell.
Measure did none of that. Measure must mirror paint or the column is
mis-measured, so the fix aligns it to that same predicate and that same string.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.style.authored import TableColumnConfig
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.render.chart.table_support import measure_column_demands


def _demands(
    data: list[dict[str, Any]],
    col_configs: dict[str, TableColumnConfig],
    columns: list[str] | None = None,
) -> dict[str, float]:
    cell_demands, _ = measure_column_demands(
        columns or list(col_configs),
        col_configs,
        data,
        get_font_measurer(),
        header_measurer=get_font_measurer(),
        font_size=13.0,
        header_font_size=12.0,
        cell_pad=8,
        formats=None,
        column_when_rules={},
    )
    return cell_demands


def _with_pad_config(fmt: Any, rows: list[dict[str, Any]]) -> TableColumnConfig:
    """A column config carrying a baked decimal_pad_table, as the real bake does."""
    from dbt_charts.core.compile.resolve.chart._table import (
        _with_resolved_scale_stops,
    )
    from dbt_charts.core.fonts import DBT_SANS_TABULAR_FONT_FAMILY

    resolved = _with_resolved_scale_stops(
        {"amt": TableColumnConfig(format=fmt)},
        text_color=None,
        formats=None,
        font_family=DBT_SANS_TABULAR_FONT_FAMILY,
        rows=rows,
        allow_shared_scale=True,
        own_formats={
            n: c.format for n, c in {"amt": TableColumnConfig(format=fmt)}.items()
        },
        defaults_format=None,
        declared=None,
    )
    assert resolved is not None
    return resolved["amt"]


def test_unparseable_cell_in_a_padded_column_does_not_raise() -> None:
    """The reported crash: one non-numeric cell killed the whole board.

    ``"12.3456789012345678 kg"`` is not float-parseable, so
    ``format_table_cell_value`` returns it verbatim at depth 16, while the
    column's ``,.2~f`` pad table is built for depth 2.
    """
    rows: list[dict[str, Any]] = [
        {"name": "a", "amt": 1.5},
        {"name": "b", "amt": 2.25},
        {"name": "note", "amt": "12.3456789012345678 kg"},
    ]
    col = _with_pad_config(",.2~f", rows)
    assert col.decimal_pad_table, "precondition: the column must carry a pad table"

    demands = _demands(rows, {"amt": col, "name": TableColumnConfig()})

    assert demands["amt"] > 0.0


def test_deep_unparseable_text_is_not_padded() -> None:
    """Not merely non-crashing — the fallback text must not borrow the pad.

    A guard that clamped the lookup instead would still pad this cell, so this
    pins the divergence being closed rather than just its symptom. Compared
    against the same data in a column with no pad table at all: if the fallback
    text is unpadded, the two columns must demand exactly the same width.
    """
    # ONE sub-precision row: the demand is a p95 over rows, so a dataset whose
    # widest row is already at full depth selects the empty pad and cannot tell
    # padded from unpadded.
    rows: list[dict[str, Any]] = [{"amt": "1.5 kg"}]
    padded_col = _with_pad_config(",.2~f", [{"amt": 1.5}, {"amt": 2.25}])
    assert padded_col.decimal_pad_table, "precondition: column carries a pad table"

    with_pad_table = _demands(rows, {"amt": padded_col})["amt"]
    without_pad_table = _demands(rows, {"amt": TableColumnConfig(format=",.2~f")})[
        "amt"
    ]

    assert with_pad_table == without_pad_table, (
        "fallback text picked up the column's decimal pad — it is not on the "
        "column's decimal lane and must measure identically either way"
    )


def test_decimal_cells_lose_the_pad_to_match_paint() -> None:
    """Intended narrowing, not a regression.

    The bake reaches ``Decimal`` through ``coerce_numeric_cell`` (warehouse
    drivers return it for DECIMAL/NUMERIC), so a Decimal column bakes a pad
    table. Both paint sites exclude ``Decimal`` via their ``isinstance``
    predicate, so measure must too — a disagreement is a mis-measured column.

    A single sub-precision row is used deliberately: at depth == precision the
    selected pad is the empty string, so a column whose widest row is already at
    full depth cannot distinguish padded from unpadded.
    """
    rows: list[dict[str, Any]] = [{"amt": Decimal("1.5")}]
    # The bake needs depth variance to emit a pad table at all; the measured
    # dataset is a single shallow row so the selected pad is non-empty.
    padded_col = _with_pad_config(",.2~f", [{"amt": 1.5}, {"amt": 2.25}])
    assert padded_col.decimal_pad_table

    with_pad_table = _demands(rows, {"amt": padded_col})["amt"]
    without_pad_table = _demands(rows, {"amt": TableColumnConfig(format=",.2~f")})[
        "amt"
    ]

    assert with_pad_table == without_pad_table, (
        "Decimal cells were padded — paint does not pad them, so measure must not"
    )


def test_float_cells_below_precision_still_get_their_pad() -> None:
    """The narrowing must not overshoot: real numeric cells keep the pad.

    Guards the fix against being 'stop padding everything', which would also
    make the crash go away while silently under-measuring every padded column.
    """
    rows: list[dict[str, Any]] = [{"amt": 1.5}]
    padded_col = _with_pad_config(",.2~f", [{"amt": 1.5}, {"amt": 2.25}])
    assert padded_col.decimal_pad_table

    with_pad_table = _demands(rows, {"amt": padded_col})["amt"]
    without_pad_table = _demands(rows, {"amt": TableColumnConfig(format=",.2~f")})[
        "amt"
    ]

    assert with_pad_table > without_pad_table, (
        "a sub-precision float cell lost its decimal pad — the guard is too wide"
    )


def test_bool_cells_are_not_padded() -> None:
    """`bool` is an `int` subclass; both paint sites exclude it explicitly."""
    rows: list[dict[str, Any]] = [{"amt": True}]
    padded_col = _with_pad_config(",.2~f", [{"amt": 1.5}, {"amt": 2.25}])

    with_pad_table = _demands(rows, {"amt": padded_col})["amt"]
    without_pad_table = _demands(rows, {"amt": TableColumnConfig(format=",.2~f")})[
        "amt"
    ]

    assert with_pad_table == without_pad_table, (
        "bool cell was padded — bool is an int subclass, and both paint sites "
        "exclude it explicitly"
    )


def test_digit_bearing_suffix_does_not_crash() -> None:
    """The same crash, reached by a genuine float — the predicate is not enough.

    ``decimal_pad_for`` counts every digit after the first ``.``, and
    ``format_table_cell_value`` returns prefix + number + **suffix**. So an
    authored suffix carrying a digit (`` CO2``, ``m2``, ``ft3`` — ordinary
    ``FormatConfig.suffix`` input) inflates the observed depth past the table's
    precision, on a value that is a real ``float`` and sails through the
    numeric guard.

    Both paint sites pad the number lane alone (``format_kpi_parts`` splits the
    suffix out), so measure must too. Aligning the *predicate* without aligning
    the *string* leaves the crash reachable.
    """
    fmt = FormatConfig(spec=",.1~f", suffix=" CO2")
    rows: list[dict[str, Any]] = [{"amt": 1.25}, {"amt": 2.0}]
    resolved = _with_pad_config(fmt, rows)
    assert resolved.decimal_pad_table

    assert _demands(rows, {"amt": resolved})["amt"] > 0.0


def test_suffix_digits_do_not_change_the_selected_pad() -> None:
    """Not merely non-crashing: the suffix must not shift which pad is chosen.

    A column identical but for a digit-free suffix must select the same pad, so
    the two demands differ by exactly the suffix's own width. Pins that depth is
    read from the number lane rather than the whole cell.
    """
    digit_rows: list[dict[str, Any]] = [{"amt": 1.5}, {"amt": 2.25}]
    with_digit = _with_pad_config(FormatConfig(spec=",.2~f", suffix=" m2"), digit_rows)
    no_digit = _with_pad_config(FormatConfig(spec=",.2~f", suffix=" mm"), digit_rows)
    assert with_digit.decimal_pad_table == no_digit.decimal_pad_table

    one_row: list[dict[str, Any]] = [{"amt": 1.5}]
    measurer = get_font_measurer()
    delta = (
        _demands(one_row, {"amt": with_digit})["amt"]
        - _demands(one_row, {"amt": no_digit})["amt"]
    )
    expected = measurer.measure(" m2", 13.0) - measurer.measure(" mm", 13.0)

    assert abs(delta - expected) < 0.01, (
        "the digit in the suffix changed which pad was selected — depth must be "
        "read from the number lane, not the whole rendered cell"
    )


def test_shared_scale_columns_keep_their_pad() -> None:
    """The guard must not swallow the shared-scale branch.

    That branch renders through ``format_kpi_parts`` and is exactly what the
    bake's ``pad_fmt = shared_scale.digit_spec`` exists for, so it must stay
    padded. Moving the numeric guard into the ``else`` arm — the one plausible
    mis-implementation of this fix — makes every shared-scale column measure
    narrower while paint still pads it, misaligning the decimal lane. The
    nearest existing test compares two demands that would both lose the pad
    symmetrically, so it cannot see that.
    """
    rows: list[dict[str, Any]] = [{"amt": 3_500_000.0}, {"amt": 12_000_000.0}]
    padded_col = _with_pad_config(".3~s", rows)
    assert padded_col.shared_scale is not None, "precondition: a shared-scale column"
    assert padded_col.decimal_pad_table, "precondition: it carries a pad table"

    # Same column with only the pad table stripped — an unbaked config would
    # carry no shared_scale either and take the other branch entirely, which
    # renders different text and makes the comparison meaningless.
    # 12M scales to "12" (zero fractional digits) so it selects a NON-empty
    # pad; 3.5M scales to "3.5", already at full precision, whose pad is the
    # empty string and cannot distinguish padded from unpadded.
    one_row: list[dict[str, Any]] = [{"amt": 12_000_000.0}]
    with_pad_table = _demands(one_row, {"amt": padded_col})["amt"]
    without_pad_table = _demands(
        one_row, {"amt": padded_col.model_copy(update={"decimal_pad_table": ()})}
    )["amt"]

    assert with_pad_table > without_pad_table, (
        "a shared-scale column lost its decimal pad — the guard leaked into the "
        "shared-scale branch, which paint still pads"
    )


def test_date_formatted_column_with_numeric_cells_does_not_crash() -> None:
    """A strftime-formatted column holding a bare year must still render.

    ``columns`` is a time-capable field, so ``format: date_short`` is accepted by
    design, and a numeric-parseable cell in one is the documented mixed
    date/number outlier. ``float("2024")`` succeeds, so any numeric-only guard
    passes it to ``format_kpi_parts``, whose d3 number parser rejects
    ``'%-d %b %Y'`` — one cell, every chart on the board lost, exactly the
    failure under repair.

    Both paint sites gate on ``not is_date_like(value)`` as well; measure must.
    Such a column also bakes an empty pad table, so the work is pure collateral.
    """
    rows: list[dict[str, Any]] = [{"yr": "2024"}, {"yr": "2025"}]

    demands = _demands(rows, {"yr": TableColumnConfig(format="date_short")})

    assert demands["yr"] > 0.0


def test_bare_strftime_spec_column_with_numeric_cells_does_not_crash() -> None:
    """Same hazard reached through a raw strftime spec rather than a preset."""
    rows: list[dict[str, Any]] = [{"yr": "2024"}, {"yr": "2025"}]

    assert _demands(rows, {"yr": TableColumnConfig(format="%b %Y")})["yr"] > 0.0


def test_date_like_cell_in_a_padded_column_is_not_padded() -> None:
    """The mirror must include the date guard, not just the numeric one.

    Both paint sites gate on ``not is_date_like(value)`` as well, and render a
    date-like cell verbatim. A measure that pads it over-reserves the column by
    the pad's width permanently. Pinned because "measure mirrors paint" is the
    stated justification for the whole guard, and a half-mirror is the thing a
    future editor would otherwise reintroduce.
    """
    rows: list[dict[str, Any]] = [{"amt": "2024"}]
    padded_col = _with_pad_config(",.2~f", [{"amt": 1.5}, {"amt": 2.25}])
    assert padded_col.decimal_pad_table

    with_pad_table = _demands(rows, {"amt": padded_col})["amt"]
    without_pad_table = _demands(
        rows, {"amt": padded_col.model_copy(update={"decimal_pad_table": ()})}
    )["amt"]

    assert with_pad_table == without_pad_table, (
        "a date-like cell was padded — paint renders it verbatim, so measure "
        "over-reserves the column"
    )
