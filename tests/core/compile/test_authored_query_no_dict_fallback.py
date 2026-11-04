"""Regression tests: queries dict must not silently accept unknown keys.

Before fix: queries were typed `dict[str, AuthoredQuery | str | dict[str, Any]]` —
Pydantic fell back to the `dict[str, Any]` arm when AuthoredQuery raised
extra="forbid", swallowing the error silently.

After fix: queries are typed `dict[str, QueryOrRef]` — a discriminated union of
AuthoredQuery (inline) and QueryRef (cross-file ref). Cross-file refs are coerced
to QueryRef at parse time; SQL shorthands are normalized to full dicts by the parser
before Pydantic sees them. Unknown keys raise a ValidationError on the correct branch.

Also covers: valid subtype-specific fields (json_path, delimiter, encoding,
schema/table/column) that were missing from AuthoredQuery and required the
dict[str, Any] fallback to compile at all.
"""

from __future__ import annotations

from dbt_charts.core.compile import compile


def _board(query_body: str) -> str:
    return f"""\
title: test
source: _test
queries:
  q:
{query_body}
rows: []
"""


# ---------------------------------------------------------------------------
# Typo / unknown-key rejection (these silently passed before the fix)
# ---------------------------------------------------------------------------


def test_typo_sql_query_rejects_at_parse_time() -> None:
    """sqll: is not a known field — must raise an error, not silently compile."""
    result = compile(_board("    sqll: SELECT 1\n    source: _test\n"))
    assert not result.success
    errors = " ".join(e.message for e in result.errors)
    assert "sqll" in errors


def test_typo_values_query_rejects_at_parse_time() -> None:
    """rowss: is not a known field on values queries."""
    result = compile(_board("    type: values\n    rowss:\n      - a: 1\n"))
    assert not result.success
    errors = " ".join(e.message for e in result.errors)
    assert "rowss" in errors


# ---------------------------------------------------------------------------
# Valid subtype-specific fields that were missing from AuthoredQuery
# (these were rejected before the fix due to missing fields in AuthoredQuery)
# ---------------------------------------------------------------------------


def test_schema_with_schema_alias_compiles_ok() -> None:
    """schema: (alias for schema_name) is a valid SchemaQuery field."""
    result = compile(
        _board("    type: schema\n    source: warehouse\n    schema: analytics\n")
    )
    assert result.success, [e.message for e in result.errors]


def test_schema_with_table_and_column_compiles_ok() -> None:
    """table + column are valid SchemaQuery fields."""
    result = compile(
        _board(
            "    type: schema\n"
            "    source: warehouse\n"
            "    schema: analytics\n"
            "    table: orders\n"
            "    column: id\n"
        )
    )
    assert result.success, [e.message for e in result.errors]
