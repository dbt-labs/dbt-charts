"""Identifier quoting is the dialect's, not the dbt adapter's.

`probe_relation_readability` spells a relation with `adapter.quote()` on the dbt
arm. A driver has no adapter to ask, so the quoter lives on the dialect, and a
relation cannot be spelled two ways for one warehouse.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.dialects import DIALECTS, get_dialect


@pytest.mark.parametrize(
    ("source_type", "expected"),
    [
        ("postgres", '"orders"'),
        ("snowflake", '"orders"'),
        ("duckdb", '"orders"'),
        ("lakecompute", '"orders"'),
        ("redshift", '"orders"'),
        ("sqlite", '"orders"'),
        ("trino", '"orders"'),
        ("bigquery", "`orders`"),
        ("databricks", "`orders`"),
        ("spark", "`orders`"),
        ("mysql", "`orders`"),
        ("sqlserver", "[orders]"),
    ],
)
def test_each_dialect_quotes_in_its_own_grammar(
    source_type: str, expected: str
) -> None:
    assert get_dialect(source_type).quote("orders") == expected


@pytest.mark.parametrize(
    ("source_type", "identifier", "expected"),
    [
        ("postgres", 'we"ird', '"we""ird"'),
        ("bigquery", "we`ird", "`we\\`ird`"),
        ("sqlserver", "we]ird", "[we]]ird]"),
    ],
)
def test_an_embedded_quote_character_is_escaped_not_dropped(
    source_type: str, identifier: str, expected: str
) -> None:
    """Each grammar's own escape — doubling for ANSI and T-SQL, a backslash for
    GoogleSQL, which is the one place doubling does not work. Dropping the
    character would silently name a different relation."""
    assert get_dialect(source_type).quote(identifier) == expected


def test_every_registered_dialect_can_quote() -> None:
    """A dialect with no quoted form would fail at the probe, not at import."""
    for dialect in DIALECTS.values():
        assert dialect.quote("x") != "x"


def test_lakecompute_is_registered() -> None:
    """A missing registration would fail every lake compute query at lookup."""
    dialect = get_dialect("lakecompute")
    assert dialect.name == "lakecompute"
    assert dialect.param(1) == "$1"


def test_lakecompute_sends_no_timeout_statement() -> None:
    """The cap travels as the driver's own option, never as a SET statement."""
    assert get_dialect("lakecompute").statement_timeout_sql(30) is None
