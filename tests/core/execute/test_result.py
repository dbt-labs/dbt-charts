"""Arrow → QueryResult: the one conversion every driver's results pass through.

The decimal policy is the reason this module exists. A driver's own downcast can
wrap silently on overflow (``12345678901234567890123`` came back as
``4807115922877859019`` in a spike), so the cast happens here with ``safe=True``
and an integer that does not fit is an error rather than a plausible wrong
number.

The policy reads the Arrow type and nothing else. A board's number format is a
chart-layer concern and must not reach the data layer.
"""

from __future__ import annotations

import datetime as dt
import decimal
from typing import Any

import nanoarrow as na
import pyarrow as pa
import pytest

from dbt_charts.core.diagnostics.codes_execute import ERR_RESULT_NUMERIC_OVERFLOW
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters.base import RowFetchLimit
from dbt_charts.core.execute.result import (
    arrow_to_query_result,
    read_bounded_stream,
)


def _unbounded(rows: int) -> RowFetchLimit:
    """A limit no result in these tests can reach."""
    return RowFetchLimit(
        fetch_limit=rows + 1, effective_limit=rows, ceiling_binding=False
    )


class TestDecimalPolicy:
    def test_scale_zero_that_fits_becomes_int(self) -> None:
        table = pa.table({"n": pa.array([decimal.Decimal("42")], pa.decimal128(38, 0))})
        result = arrow_to_query_result(na.Array(table), _unbounded(10))
        assert result.data == [{"n": 42}]
        assert isinstance(result.data[0]["n"], int)

    def test_scale_zero_overflow_raises(self) -> None:
        table = pa.table(
            {
                "n": pa.array(
                    [decimal.Decimal("12345678901234567890123")], pa.decimal128(38, 0)
                )
            }
        )
        with pytest.raises(ExecutionError) as excinfo:
            arrow_to_query_result(na.Array(table), _unbounded(10))
        assert excinfo.value.code is ERR_RESULT_NUMERIC_OVERFLOW
        assert "n" in str(excinfo.value)

    def test_scaled_decimal_becomes_float(self) -> None:
        table = pa.table(
            {"n": pa.array([decimal.Decimal("1.50")], pa.decimal128(10, 2))}
        )
        result = arrow_to_query_result(na.Array(table), _unbounded(10))
        assert result.data == [{"n": 1.5}]
        assert isinstance(result.data[0]["n"], float)

    def test_decimal256_follows_the_same_policy(self) -> None:
        table = pa.table({"n": pa.array([decimal.Decimal("7")], pa.decimal256(40, 0))})
        result = arrow_to_query_result(na.Array(table), _unbounded(10))
        assert result.data == [{"n": 7}]


class TestOtherTypes:
    def test_timestamp_without_timezone_stays_naive(self) -> None:
        table = pa.table(
            {"t": pa.array([dt.datetime(2024, 1, 1, 12, 0)], pa.timestamp("ns"))}
        )
        result = arrow_to_query_result(na.Array(table), _unbounded(10))
        assert result.data[0]["t"] == dt.datetime(2024, 1, 1, 12, 0)
        assert result.data[0]["t"].tzinfo is None

    def test_timestamp_with_timezone_keeps_its_offset(self) -> None:
        table = pa.table(
            {
                "t": pa.array(
                    [dt.datetime(2024, 1, 1, 12, 0, tzinfo=dt.UTC)],
                    pa.timestamp("us", tz="UTC"),
                )
            }
        )
        result = arrow_to_query_result(na.Array(table), _unbounded(10))
        assert result.data[0]["t"].utcoffset() == dt.timedelta(0)

    def test_variant_arrives_as_its_json_string(self) -> None:
        table = pa.table({"v": pa.array(['{"k": 1}'], pa.string())})
        result = arrow_to_query_result(na.Array(table), _unbounded(10))
        assert result.data == [{"v": '{"k": 1}'}]

    @pytest.mark.parametrize("width", [pa.decimal128, pa.decimal256])
    def test_negative_decimals_keep_their_sign(self, width: Any) -> None:
        table = pa.table(
            {
                "whole": pa.array([decimal.Decimal("-7")], width(38, 0)),
                "scaled": pa.array([decimal.Decimal("-2.25")], width(10, 2)),
                "nested": pa.array(
                    [{"d": [decimal.Decimal("-1.5")]}],
                    pa.struct([("d", pa.list_(width(10, 1)))]),
                ),
            }
        )
        result = arrow_to_query_result(na.Array(table), _unbounded(10))
        assert result.data == [
            {"whole": -7, "scaled": -2.25, "nested": {"d": [decimal.Decimal("-1.5")]}}
        ]

    def test_dictionary_encoded_decimals_decode_and_narrow(self) -> None:
        values = pa.array([decimal.Decimal("-1.5"), None], pa.decimal128(10, 1))
        table = pa.table({"d": values.dictionary_encode()})
        result = arrow_to_query_result(na.Array(table), _unbounded(10))
        assert result.data == [{"d": -1.5}, {"d": None}]

    def test_an_empty_result_with_a_map_keeps_its_columns(self) -> None:
        table = pa.table({"m": pa.array([], pa.map_(pa.string(), pa.int64()))})
        result = arrow_to_query_result(na.Array(table), _unbounded(10))
        assert result.data == []
        assert result.columns == ["m"]

    def test_view_arrays_are_read_as_plain_values(self) -> None:
        """nanoarrow crashes the process on string_view/binary_view arrays, so a
        stream holding them is read another way."""
        table = pa.table(
            {
                "s": pa.array(["x", None], pa.string_view()),
                "b": pa.array([b"y", None], pa.binary_view()),
            }
        )
        result = arrow_to_query_result(
            read_bounded_stream(table, _unbounded(10)), _unbounded(10)
        )
        assert result.data == [{"s": "x", "b": b"y"}, {"s": None, "b": None}]

    def test_a_map_arrives_as_key_value_dicts_at_any_depth(self) -> None:
        entries = pa.map_(pa.string(), pa.int64())
        table = pa.table(
            {
                "m": pa.array([[("k", 1)], None], entries),
                "s": pa.array([{"m": [("j", 2)]}, None], pa.struct([("m", entries)])),
            }
        )
        result = arrow_to_query_result(na.Array(table), _unbounded(10))
        assert result.data == [
            {"m": [{"key": "k", "value": 1}], "s": {"m": [{"key": "j", "value": 2}]}},
            {"m": None, "s": None},
        ]

    def test_column_names_keep_the_case_the_query_gave_them(self) -> None:
        """DuckDB and lake compute preserve an alias's case, as the in-process
        DuckDB adapter does; lower-casing would break a chart that names `MRR`."""
        table = pa.table({"MRR": pa.array([1], pa.int64())})
        result = arrow_to_query_result(na.Array(table), _unbounded(10))
        assert result.columns == ["MRR"]
        assert result.data == [{"MRR": 1}]

    def test_column_descriptions_are_left_unset(self) -> None:
        """Arrow carries a schema, not PEP 249 cursor.description tuples, and
        nothing reads them off a warehouse result. Fabricating display_size /
        precision / null_ok from an Arrow field would invent values."""
        table = pa.table({"a": pa.array([1], pa.int64())})
        assert (
            arrow_to_query_result(na.Array(table), _unbounded(10)).column_descriptions
            is None
        )


class TestTruncation:
    def test_ceiling_binding_over_fetch_is_flagged(self) -> None:
        table = pa.table({"a": pa.array(range(11), pa.int64())})
        limit = RowFetchLimit(fetch_limit=11, effective_limit=10, ceiling_binding=True)
        result = arrow_to_query_result(na.Array(table), limit)
        assert len(result.data) == 10
        assert result.truncated_reason == "max_rows"

    def test_authors_own_limit_is_never_flagged(self) -> None:
        """When the author's own Query.limit bound the fetch there is nothing to
        report: the result is exactly what they asked for."""
        table = pa.table({"a": pa.array(range(11), pa.int64())})
        limit = RowFetchLimit(fetch_limit=10, effective_limit=10, ceiling_binding=False)
        result = arrow_to_query_result(na.Array(table), limit)
        assert len(result.data) == 10
        assert result.truncated_reason is None

    def test_result_within_the_ceiling_is_not_flagged(self) -> None:
        table = pa.table({"a": pa.array(range(3), pa.int64())})
        limit = RowFetchLimit(fetch_limit=11, effective_limit=10, ceiling_binding=True)
        result = arrow_to_query_result(na.Array(table), limit)
        assert len(result.data) == 3
        assert result.truncated_reason is None

    def test_empty_result_keeps_its_columns(self) -> None:
        table = pa.table({"a": pa.array([], pa.int64())})
        result = arrow_to_query_result(na.Array(table), _unbounded(10))
        assert result.data == []
        assert result.columns == ["a"]
