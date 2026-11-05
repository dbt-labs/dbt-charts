from datetime import UTC, datetime
from decimal import Decimal

from dbt_charts.core.column_types import ColumnType
from dbt_charts.core.execute.cache_composition import compose_over_named_rows

_FLOAT = ColumnType(kind="float")


def test_list_column_composes_as_a_list_with_its_declared_type() -> None:
    rows, schema = compose_over_named_rows(
        "SELECT id, vals FROM up",
        {"up": [{"id": 1, "vals": [1.5, 2.5]}, {"id": 2, "vals": []}]},
        [],
        schemas={
            "up": {
                "id": ColumnType(kind="integer"),
                "vals": ColumnType(kind="list", element=_FLOAT),
            }
        },
    )

    assert rows == [{"id": 1, "vals": [1.5, 2.5]}, {"id": 2, "vals": []}]
    assert schema["vals"] == ColumnType(kind="list", element=_FLOAT)


def test_all_null_column_keeps_its_declared_type() -> None:
    _, schema = compose_over_named_rows(
        "SELECT d, n FROM up",
        {"up": [{"d": None, "n": None}]},
        [],
        schemas={"up": {"d": ColumnType(kind="date"), "n": ColumnType(kind="decimal")}},
    )

    assert [schema["d"].kind, schema["n"].kind] == ["date", "decimal"]


def test_struct_json_and_timestamps_round_trip() -> None:
    aware = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    naive = datetime(2026, 1, 2, 3, 4, 5)
    cells = {
        "s": {"a": 1, "b": "x"},
        "j": {"k": [1, 2]},
        "tz": aware,
        "ts": naive,
    }
    struct = ColumnType(
        kind="struct",
        fields=(("a", ColumnType(kind="integer")), ("b", ColumnType(kind="string"))),
    )
    rows, schema = compose_over_named_rows(
        "SELECT s, j, tz, ts FROM up",
        {"up": [cells]},
        [],
        schemas={
            "up": {
                "s": struct,
                "j": ColumnType(kind="json"),
                "tz": ColumnType(kind="datetime_tz"),
                "ts": ColumnType(kind="datetime"),
            }
        },
    )

    assert rows == [cells]
    assert schema["s"] == struct
    assert schema["tz"].kind == "datetime_tz"


def _round_trip(column: ColumnType, cell: object) -> object:
    rows, _ = compose_over_named_rows(
        "SELECT c FROM up",
        {"up": [{"c": cell}]},
        [],
        schemas={"up": {"c": column}},
    )
    return rows[0]["c"]


def test_decimal_keeps_scale_beyond_nine() -> None:
    cell = Decimal("1.123456789012")
    assert _round_trip(ColumnType(kind="decimal"), cell) == cell


def test_decimal_nested_in_list_and_struct_is_exact() -> None:
    dec = ColumnType(kind="decimal")
    listed = [Decimal("1.123456789012"), Decimal("2")]
    struct = ColumnType(kind="struct", fields=(("d", dec),))
    assert _round_trip(ColumnType(kind="list", element=dec), listed) == listed
    assert _round_trip(struct, {"d": Decimal("0.000000000001")}) == {
        "d": Decimal("0.000000000001")
    }


def test_list_of_json_keeps_each_document_unchanged() -> None:
    column = ColumnType(kind="list", element=ColumnType(kind="json"))
    cell = [{"k": 1}, {"z": "q"}]
    assert _round_trip(column, cell) == cell


def test_json_field_inside_struct_round_trips() -> None:
    column = ColumnType(kind="struct", fields=(("j", ColumnType(kind="json")),))
    cell = {"j": [1, {"a": 2}]}
    assert _round_trip(column, cell) == cell


def test_scalar_and_nested_columns_stay_aligned_in_schema_order() -> None:
    up = [
        {"tags": [f"t{i}"], "id": i, "label": f"n{i}", "pt": {"x": i * 10}}
        for i in range(5)
    ]
    rows, _ = compose_over_named_rows(
        "SELECT tags, id, label, pt FROM up ORDER BY id",
        {"up": up},
        [],
        schemas={
            "up": {
                "tags": ColumnType(kind="list", element=ColumnType(kind="string")),
                "id": ColumnType(kind="integer"),
                "label": ColumnType(kind="string"),
                "pt": ColumnType(
                    kind="struct", fields=(("x", ColumnType(kind="integer")),)
                ),
            }
        },
    )

    assert rows == up
