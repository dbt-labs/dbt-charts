"""Checked-in dbt credential-contract snapshot support.

The source Pydantic models are dbt charts' authoring contract. This module reads
the installed dbt credential dataclasses only to make adapter upgrades reviewable:
tests compare its output with the small checked-in JSON snapshot.
"""

from __future__ import annotations

from dataclasses import fields
from importlib.metadata import version
from typing import Any

CredentialSnapshot = dict[str, str | list[str] | dict[str, str]]
AdapterSnapshot = dict[str, str | list[CredentialSnapshot]]
DbtCredentialContract = dict[str, dict[str, AdapterSnapshot]]


def _credential_contract(credential_cls: type[Any]) -> CredentialSnapshot:
    """Return the stable, public shape of one dbt credential dataclass."""
    return {
        "class": f"{credential_cls.__module__}.{credential_cls.__qualname__}",
        "fields": [field.name for field in fields(credential_cls)],
        "aliases": dict(credential_cls._ALIASES),
    }


def collect_dbt_credential_contract() -> DbtCredentialContract:
    """Read credential names, fields, and aliases from installed dbt adapters."""
    from dbt.adapters.bigquery import BigQueryCredentials
    from dbt.adapters.duckdb import DuckDBCredentials
    from dbt.adapters.postgres import PostgresCredentials
    from dbt.adapters.redshift import RedshiftCredentials
    from dbt.adapters.snowflake import SnowflakeCredentials

    return {
        "adapters": {
            "bigquery": {
                "package": "dbt-bigquery",
                "version": version("dbt-bigquery"),
                "credentials": [_credential_contract(BigQueryCredentials)],
            },
            "duckdb": {
                "package": "dbt-duckdb",
                "version": version("dbt-duckdb"),
                "credentials": [_credential_contract(DuckDBCredentials)],
            },
            "postgres": {
                "package": "dbt-postgres",
                "version": version("dbt-postgres"),
                "credentials": [_credential_contract(PostgresCredentials)],
            },
            "redshift": {
                "package": "dbt-redshift",
                "version": version("dbt-redshift"),
                "credentials": [_credential_contract(RedshiftCredentials)],
            },
            "snowflake": {
                "package": "dbt-snowflake",
                "version": version("dbt-snowflake"),
                "credentials": [_credential_contract(SnowflakeCredentials)],
            },
        }
    }


def render_dbt_credential_contract_markdown(
    contract: DbtCredentialContract,
) -> str:
    """Render a compact, readable projection of a credential-contract snapshot."""
    lines = [
        "# dbt credential contract snapshot",
        "",
        "<!-- Generated from the installed dbt credential dataclasses; do not edit manually. -->",
        "",
        "This snapshot is the review boundary for dbt-adapter upgrades. "
        "dbt charts' strict Pydantic source models remain the authoring contract.",
        "",
    ]
    adapters = contract["adapters"]
    for adapter, adapter_contract in adapters.items():
        package = adapter_contract["package"]
        adapter_version = adapter_contract["version"]
        credentials = adapter_contract["credentials"]
        assert isinstance(package, str)
        assert isinstance(adapter_version, str)
        assert isinstance(credentials, list)
        lines.extend([f"## {adapter}", "", f"{package} {adapter_version}", ""])
        for credential in credentials:
            class_name = credential["class"]
            credential_fields = credential["fields"]
            aliases = credential["aliases"]
            assert isinstance(class_name, str)
            assert isinstance(credential_fields, list)
            assert isinstance(aliases, dict)
            lines.extend([f"### `{class_name}`", ""])
            lines.extend([f"- `{field}`" for field in credential_fields])
            if aliases:
                lines.append("")
                lines.append("Aliases:")
                lines.extend(
                    f"- `{alias}` → `{canonical}`"
                    for alias, canonical in aliases.items()
                )
            lines.append("")
    return "\n".join(lines)
