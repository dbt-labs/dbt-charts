from __future__ import annotations

import jsonschema
import pytest
from jsonschema.validators import validator_for
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.query.authored import (
    AuthoredCompactValuesQuery,
    AuthoredHttpQuery,
    AuthoredQuery,
    AuthoredValuesQuery,
)
from dbt_charts.core.compile.models.refs import normalize_query_value
from dbt_charts.core.compile.schema.introspection import introspect
from dbt_charts.core.compile.schema.renderers.json_schema import render_yaml_schema

_ADAPTER: TypeAdapter[AuthoredQuery] = TypeAdapter(AuthoredQuery)


def _validate_board(schema: dict, board: dict) -> None:
    validator_for(schema)(schema).validate(board)


def _validate(query: dict[str, object]) -> AuthoredQuery:
    return _ADAPTER.validate_python(normalize_query_value(query))


def test_http_query_requires_url() -> None:
    with pytest.raises(ValidationError, match="url"):
        _validate({"type": "http"})


def test_values_query_requires_rows_or_columns_and_values() -> None:
    with pytest.raises(ValidationError, match="columns"):
        _validate({"type": "values"})


@pytest.mark.parametrize(
    ("incomplete", "missing_field"),
    [
        ({"columns": ["value"]}, "values"),
        ({"values": [[1]]}, "columns"),
    ],
)
def test_values_query_rejects_incomplete_columns_and_values(
    incomplete: dict[str, object],
    missing_field: str,
) -> None:
    with pytest.raises(ValidationError, match=missing_field):
        _validate({"type": "values", **incomplete})


def test_columns_key_infers_values_before_reporting_missing_values() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _validate({"columns": ["value"]})

    assert exc_info.value.errors()[0]["loc"][0] == "values_compact"
    assert exc_info.value.errors()[0]["loc"][1] == "values"


def test_values_query_rejects_rows_with_columns_and_values() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _validate(
            {
                "type": "values",
                "rows": [{"value": 1}],
                "columns": ["value"],
                "values": [[1]],
            }
        )


@pytest.mark.parametrize(
    "partial_compact",
    [
        {"columns": ["value"]},
        {"values": [[1]]},
    ],
)
def test_values_query_rejects_rows_with_partial_compact_form(
    partial_compact: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _validate(
            {
                "type": "values",
                "rows": [{"value": 1}],
                **partial_compact,
            }
        )


def test_values_query_accepts_rows() -> None:
    query = _validate({"type": "values", "rows": [{"value": 1}]})

    assert isinstance(query, AuthoredValuesQuery)
    assert query.rows == [{"value": 1}]


def test_values_query_accepts_columns_and_values() -> None:
    query = _validate({"type": "values", "columns": ["value"], "values": [[1]]})

    assert isinstance(query, AuthoredCompactValuesQuery)
    assert query.columns == ["value"]
    assert query.values == [[1]]


def test_http_query_accepts_url() -> None:
    query = _validate({"type": "http", "url": "https://api.example.com/data"})

    assert isinstance(query, AuthoredHttpQuery)
    assert query.url == "https://api.example.com/data"


_REPRESENTATIVE_VALID_QUERIES = [
    {"sql": "select 1 as value"},
    {"columns": ["value"], "values": [[1]]},
    {"rows": [{"value": 1}]},
    {"url": "https://api.example.com/data"},
]


@pytest.mark.parametrize("query", _REPRESENTATIVE_VALID_QUERIES)
def test_strict_schema_and_runtime_accept_the_same_query_variants(
    query: dict[str, object],
) -> None:
    board = {"title": "Query schema parity", "queries": {"sample": query}}

    AuthoredBoard.model_validate(board)
    _validate_board(render_yaml_schema(introspect()), board)


@pytest.mark.parametrize(
    "query",
    [
        {"type": "values"},
        {"type": "values", "columns": ["value"]},
        {"type": "values", "values": [[1]]},
        {
            "type": "values",
            "rows": [{"value": 1}],
            "columns": ["value"],
            "values": [[1]],
        },
    ],
)
def test_strict_schema_and_runtime_reject_the_same_invalid_query_shapes(
    query: dict[str, object],
) -> None:
    board = {"title": "Query schema parity", "queries": {"sample": query}}

    with pytest.raises(ValidationError):
        AuthoredBoard.model_validate(board)
    with pytest.raises(jsonschema.ValidationError):
        _validate_board(render_yaml_schema(introspect()), board)
