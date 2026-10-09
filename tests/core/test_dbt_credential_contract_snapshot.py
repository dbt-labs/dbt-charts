"""Keep dbt charts' closed source models aligned with installed dbt credentials."""

import json

from dbt_charts.core.compile.models.dbt_credential_contract import (
    collect_dbt_credential_contract,
    render_dbt_credential_contract_markdown,
)
from dbt_charts.core.compile.models.source import (
    UNSUPPORTED_DUCKDB_FIELDS,
    BigQuerySourceConfig,
    DuckDBSourceConfig,
    PostgresSourceConfig,
    RedshiftSourceConfig,
    SnowflakeSourceConfig,
    TrinoSourceConfig,
)

from .._paths import DBT_CHARTS_DIR

_FIXTURES = DBT_CHARTS_DIR / "tests" / "fixtures"
_JSON_SNAPSHOT = _FIXTURES / "dbt_credential_contract.json"
_MARKDOWN_SNAPSHOT = _FIXTURES / "dbt_credential_contract.md"


def test_dbt_credential_contract_matches_snapshot() -> None:
    """A dbt-adapter upgrade must be reviewed before its contract changes."""
    assert collect_dbt_credential_contract() == json.loads(
        _JSON_SNAPSHOT.read_text(encoding="utf-8")
    )


def test_dbt_credential_contract_markdown_matches_snapshot() -> None:
    """The checked-in readable report is generated from the JSON contract."""
    contract = json.loads(_JSON_SNAPSHOT.read_text(encoding="utf-8"))
    assert render_dbt_credential_contract_markdown(
        contract
    ) == _MARKDOWN_SNAPSHOT.read_text(encoding="utf-8")


def test_every_snapshotted_dbt_field_has_a_closed_source_model_field() -> None:
    """dbt credential additions cannot bypass the author-facing source contract."""
    contract = json.loads(_JSON_SNAPSHOT.read_text(encoding="utf-8"))
    source_models = {
        "bigquery": BigQuerySourceConfig,
        "duckdb": DuckDBSourceConfig,
        "postgres": PostgresSourceConfig,
        "redshift": RedshiftSourceConfig,
        "snowflake": SnowflakeSourceConfig,
        "trino": TrinoSourceConfig,
    }
    field_renames = {
        "bigquery": {"database": "project", "schema": "dataset", "method": None},
        "duckdb": {"config_options": "duckdb_config", "database": "path"},
        "postgres": {"database": "dbname"},
        "redshift": {"database": "dbname", "autocommit": None},
    }

    for adapter, adapter_contract in contract["adapters"].items():
        source_fields = source_models[adapter].model_fields
        for credential in adapter_contract["credentials"]:
            for field in credential["fields"]:
                source_field = field_renames.get(adapter, {}).get(
                    field, "schema_" if field == "schema" else field
                )
                if source_field is None:
                    # build_adapter derives or forces these rather than exposing
                    # them to authors: BigQuery's credential method, Redshift's
                    # always-on autocommit.
                    continue
                if adapter == "duckdb" and field in UNSUPPORTED_DUCKDB_FIELDS:
                    # Rejected at parse with a field-naming error instead.
                    assert field not in source_fields
                    continue
                assert source_field in source_fields, (
                    f"{adapter} dbt field {field!r} has no source model field "
                    f"{source_field!r}"
                )
