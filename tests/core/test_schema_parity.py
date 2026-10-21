"""Parity tests: ensure config/model registries are consistent."""

from __future__ import annotations


def test_dialect_aliases_in_config():
    """Dialect aliases in execution config are valid sqlglot dialect targets."""
    import sqlglot

    from dbt_charts.core.compile.config import get_execution_config

    aliases = get_execution_config().dialect_aliases
    for key, value in aliases.items():
        try:
            sqlglot.parse_one("SELECT 1", read=value)
        except Exception as exc:
            raise AssertionError(
                f"Dialect alias '{key}' → '{value}' is not a valid sqlglot dialect"
            ) from exc
