"""QueryResult/QueryRow: the result type board text and registered views read."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from dbt_charts.core.compile.template.query_result import QueryResult
from dbt_charts.core.diagnostics.chart_data import ChartDataError


def _result(*rows: dict[str, object]) -> QueryResult:
    return QueryResult("summary", list(rows))


def test_rows_expose_cells_by_attribute_and_subscript() -> None:
    row = _result({"region": "EMEA", "revenue": 3}).rows[0]
    assert row.region == "EMEA"
    assert row["revenue"] == 3


def test_a_column_named_like_a_dict_method_is_that_column() -> None:
    [row] = _result({"items": 7, "values": 8, "get": 9}).rows
    assert (row.items, row.values, row.get) == (7, 8, 9)


def test_membership_tells_whether_a_row_has_a_column() -> None:
    row = _result({"a": 1}).one
    assert "a" in row
    assert "b" not in row


def test_columns_follow_first_row_order_and_are_empty_without_rows() -> None:
    assert _result({"b": 1, "a": 2}).columns == ["b", "a"]
    assert _result().columns == []


def test_one_returns_the_single_row() -> None:
    assert _result({"x": 1}).one.x == 1


@pytest.mark.parametrize("rows", [[], [{"x": 1}, {"x": 2}]])
def test_one_raises_on_wrong_row_count(rows: list[dict[str, object]]) -> None:
    with pytest.raises(ChartDataError) as exc:
        _ = QueryResult("summary", rows).one
    assert exc.value.code is not None
    assert exc.value.code.code == "ERR-TEXT-QUERY-ROW-COUNT"
    assert f"'summary' returned {len(rows)} rows" in str(exc.value)


def test_unknown_column_names_query_and_available_columns() -> None:
    row = _result({"net_revenue": 1, "vs_plan": 2}).one
    with pytest.raises(ChartDataError) as exc:
        _ = row.net_revnue
    assert exc.value.code is not None
    assert exc.value.code.code == "ERR-TEXT-QUERY-UNKNOWN-COLUMN"
    assert "Query 'summary' has no column 'net_revnue'" in str(exc.value)
    assert "Available: net_revenue, vs_plan" in str(exc.value)
    with pytest.raises(ChartDataError):
        _ = row["net_revnue"]


def test_result_attribute_other_than_rows_one_columns_raises() -> None:
    with pytest.raises(ChartDataError) as exc:
        _ = _result({"net_revenue": 1}).net_revenue
    assert exc.value.code is not None
    assert exc.value.code.code == "ERR-TEXT-QUERY-ACCESS"
    assert "no attribute 'net_revenue'" in str(exc.value)
    assert "Use .one.net_revenue, .rows or .columns" in str(exc.value)


def test_printing_a_result_raises() -> None:
    with pytest.raises(ChartDataError) as exc:
        str(_result({"x": 1}))
    assert exc.value.code is not None
    assert exc.value.code.code == "ERR-TEXT-QUERY-ACCESS"
    assert "cannot be printed directly" in str(exc.value)


def test_there_is_no_first_accessor() -> None:
    with pytest.raises(ChartDataError):
        _ = _result({"x": 1}, {"x": 2}).first


def test_underscore_column_needs_subscript() -> None:
    from dbt_charts.core.compile.template.jinja import resolve_jinja_template

    results = {"q": _result({"_fivetran_synced": 5})}

    def text(template: str) -> str:
        return resolve_jinja_template(
            template,
            strict=False,
            query_results=results,
            format_cell=lambda value, spec: str(value),
        )

    assert text("{{ queries.q.one['_fivetran_synced'] }}") == "5"
    with pytest.raises(ChartDataError) as exc:
        text("{{ queries.q.one._fivetran_synced }}")
    assert exc.value.code is not None
    assert exc.value.code.code == "ERR-TEXT-QUERY-ACCESS"
    assert "row['_fivetran_synced']" in str(exc.value)


def test_printing_a_row_raises() -> None:
    with pytest.raises(ChartDataError) as exc:
        str(_result({"x": 1}).one)
    assert exc.value.code is not None
    assert exc.value.code.code == "ERR-TEXT-QUERY-ACCESS"


def test_repr_of_a_row_raises() -> None:
    with pytest.raises(ChartDataError):
        repr(_result({"x": 1}).one)


def test_query_results_raises_on_an_unreferenced_name() -> None:
    from dbt_charts.core.compile.template.query_result import QueryResults

    results = QueryResults({"summary": QueryResult("summary", [{"x": 1}])})
    with pytest.raises(ChartDataError) as err:
        results["other"]
    assert err.value.code is not None
    assert err.value.code.code == "ERR-TEXT-QUERY-ACCESS"


@pytest.mark.parametrize(
    ("template", "hint"),
    [
        ("{{ queries.q._rows }}", ".one['_rows']"),
        ("{{ queries.q._foo }}", ".one['_foo']"),
        ("{{ queries._x }}", "queries['_x']"),
    ],
)
def test_underscore_hints_name_a_form_that_works(template: str, hint: str) -> None:
    from dbt_charts.core.compile.template.jinja import resolve_jinja_template

    with pytest.raises(ChartDataError) as exc:
        resolve_jinja_template(
            template,
            strict=False,
            query_results={"q": _result({"x": 1}), "_x": _result({"x": 1})},
            format_cell=lambda value, spec: str(value),
        )
    assert hint in str(exc.value)


@pytest.mark.parametrize(
    ("access", "message"),
    [
        (lambda r: r.rows[0], "Query 'summary' has no row 0 (returned 0 rows)."),
        (lambda r: r.rows[-1], "Query 'summary' has no row -1 (returned 0 rows)."),
        (lambda r: r.columns[2], "Query 'summary' has no column 2 (0 columns)."),
    ],
)
def test_indexing_past_the_end_raises(
    access: Callable[[QueryResult], object], message: str
) -> None:
    with pytest.raises(ChartDataError) as exc:
        access(_result())
    assert exc.value.code is not None
    assert exc.value.code.code == "ERR-TEXT-QUERY-INDEX"
    assert message in str(exc.value)


def test_slices_and_in_range_indexes_still_work() -> None:
    result = _result({"a": 1}, {"a": 2})
    assert result.rows[-1].a == 2
    assert [r.a for r in result.rows[:1]] == [1]
    assert result.columns[0] == "a"
