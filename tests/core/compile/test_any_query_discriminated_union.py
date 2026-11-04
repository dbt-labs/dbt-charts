"""AnyQuery discriminated-union regression tests.

Covers:
- query_type is now a real Literal field on each concrete query class (not
  the old abstract property) — TypeAdapter(AnyQuery) dispatches on it via
  Discriminator("query_type") instead of Pydantic's untagged "smart" union
  matching, which could silently pick the wrong all-optional variant for a
  structurally ambiguous payload (e.g. {} matches both ValuesQuery and
  SchemaQuery — both have zero required fields).
- ValuesQuery.source_description no longer memoizes via
  object.__setattr__ onto an undeclared attribute name.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    SchemaQuery,
    SqlQuery,
    ValuesQuery,
)

_adapter = TypeAdapter(AnyQuery)


class TestQueryTypeIsARealField:
    """query_type moved from an abstract @property to a Literal field."""

    def test_sql_query_type_field(self) -> None:
        assert SqlQuery(sql="SELECT 1").query_type == "sql"

    def test_values_query_type_field(self) -> None:
        assert ValuesQuery(rows=[{"a": 1}]).query_type == "values"

    def test_schema_query_type_field(self) -> None:
        assert SchemaQuery().query_type == "schema"

    def test_query_type_is_a_declared_model_field(self) -> None:
        assert "query_type" in SqlQuery.model_fields
        assert "query_type" in ValuesQuery.model_fields
        assert "query_type" in SchemaQuery.model_fields


class TestAmbiguousPayloadRejected:
    """Without a discriminator, Pydantic's untagged union validation picks
    the first union member whose (all-optional) schema happens to validate
    the input — silently, even when a different member was intended. Both
    ValuesQuery and SchemaQuery have zero required fields, so `{}` matched
    ValuesQuery every time regardless of caller intent. The discriminator
    turns that silent mismatch into a loud, actionable error.
    """

    def test_empty_dict_raises_instead_of_silently_picking_a_type(self) -> None:
        with pytest.raises(ValidationError, match="query_type"):
            _adapter.validate_python({})

    def test_base_only_fields_raise_instead_of_silently_picking_a_type(self) -> None:
        with pytest.raises(ValidationError, match="query_type"):
            _adapter.validate_python({"limit": 5})


class TestRoundTrip:
    """dump_json -> validate_json is type-stable for every AnyQuery variant."""

    def test_sql_query_round_trips(self) -> None:
        q = SqlQuery(sql="SELECT 1", source="db")
        back = _adapter.validate_json(_adapter.dump_json(q, warnings="error"))
        assert isinstance(back, SqlQuery)
        assert back == q

    def test_values_query_round_trips(self) -> None:
        q = ValuesQuery(rows=[{"month": "Jan", "revenue": 100}])
        back = _adapter.validate_json(_adapter.dump_json(q, warnings="error"))
        assert isinstance(back, ValuesQuery)
        assert back == q

    def test_schema_query_round_trips(self) -> None:
        q = SchemaQuery(source="analytics", schema_name="public")
        back = _adapter.validate_json(_adapter.dump_json(q, warnings="error"))
        assert isinstance(back, SchemaQuery)
        assert back == q

    def test_dict_of_all_default_query_types_round_trips(self) -> None:
        """The structural-ambiguity hazard (empty ValuesQuery vs. empty
        SchemaQuery) is defused once every value carries its own tag."""
        queries: dict[str, AnyQuery] = {"v": ValuesQuery(), "s": SchemaQuery()}
        ta = TypeAdapter(dict[str, AnyQuery])
        back = ta.validate_json(ta.dump_json(queries, warnings="error"))
        assert isinstance(back["v"], ValuesQuery)
        assert isinstance(back["s"], SchemaQuery)


class TestValuesQuerySourceDescriptionMemoization:
    """Regression: source_description used to memoize via
    object.__setattr__(self, "_cached_source_description", ...) — writing an
    undeclared attribute name directly onto a Pydantic model, which leaked into
    serialization as an unexpected field. It is now a PrivateAttr filled eagerly
    by model_post_init, which Pydantic excludes from model_fields and every dump
    by construction.
    """

    def test_no_private_setattr_leak(self) -> None:
        q = ValuesQuery(rows=[{"a": 1}])
        _ = q.source_description
        assert "_cached_source_description" not in vars(q)

    def test_memoized_value_is_stable_and_content_based(self) -> None:
        q = ValuesQuery(rows=[{"a": 1}])
        assert q.source_description == q.source_description
        assert q.source_description.startswith("Values: 1 row [")

    def test_distinct_row_content_produces_distinct_descriptions(self) -> None:
        a = ValuesQuery(rows=[{"a": 1}])
        b = ValuesQuery(rows=[{"a": 2}])
        assert a.source_description != b.source_description

    def test_memoization_does_not_affect_equality(self) -> None:
        """Reading the memo must not make two equal queries compare unequal.

        The memo is instance state, so an eagerly-filled private attribute is
        load-bearing: a lazily-filled one makes equality depend on whether
        anything happened to read ``source_description`` first, which breaks
        round-trip equality for any model embedding a ValuesQuery.
        """
        a = ValuesQuery(rows=[{"a": 1}])
        b = ValuesQuery(rows=[{"a": 1}])
        _ = a.source_description
        assert a == b
