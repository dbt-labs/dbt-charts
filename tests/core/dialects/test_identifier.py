"""The shared SQL identifier quoter: validation and per-dialect quoting."""

from __future__ import annotations

import pytest

from dbt_charts.core.dialects.identifier import sql_identifier


class TestSqlIdentifierDialect:
    def test_bigquery_dialect_uses_backticks(self) -> None:
        assert sql_identifier("dundersign", "bigquery") == "`dundersign`"

    def test_postgres_dialect_keeps_double_quotes(self) -> None:
        assert sql_identifier("dundersign", "postgres") == '"dundersign"'

    def test_duckdb_dialect_keeps_double_quotes(self) -> None:
        assert sql_identifier("dundersign", "duckdb") == '"dundersign"'

    def test_none_dialect_defaults_to_ansi_double_quotes(self) -> None:
        assert sql_identifier("dundersign", None) == '"dundersign"'

    def test_dotted_identifier_is_accepted(self) -> None:
        assert sql_identifier("orders.v2", "postgres") == '"orders.v2"'

    def test_non_ascii_identifier_is_accepted(self) -> None:
        assert sql_identifier("ventas_señor", "postgres") == '"ventas_señor"'

    def test_space_in_identifier_is_accepted(self) -> None:
        assert sql_identifier("my col", "postgres") == '"my col"'

    def test_hash_and_percent_in_identifier_are_accepted(self) -> None:
        assert sql_identifier("col#1", "postgres") == '"col#1"'
        assert sql_identifier("col%done", "postgres") == '"col%done"'

    def test_embedded_double_quote_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            sql_identifier('bad"name', "postgres")

    def test_embedded_backtick_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            sql_identifier("bad`name", "bigquery")

    def test_backslash_payload_is_rejected_not_escaped(self) -> None:
        """sqlglot leaves a backslash unescaped on BigQuery, so reject it."""
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            sql_identifier("a\\`,other.secrets#", "bigquery")

    def test_embedded_single_quote_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            sql_identifier("bad'name", "postgres")

    def test_embedded_bracket_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            sql_identifier("bad[name]", "postgres")

    def test_control_character_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            sql_identifier("bad\tname", "postgres")

    def test_c1_control_character_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            sql_identifier("bad\x82name", "postgres")

    def test_jinja_delimiters_are_rejected(self) -> None:
        """Board SQL is re-rendered as Jinja downstream, so braces are rejected here."""
        for payload in ("{{ 7*7 }}", "{% raw %}", "a{b"):
            with pytest.raises(ValueError, match="not a valid SQL identifier"):
                sql_identifier(payload, "postgres")

    def test_trailing_newline_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            sql_identifier("orders\n", "bigquery")

    def test_empty_identifier_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid SQL identifier"):
            sql_identifier("", "postgres")
