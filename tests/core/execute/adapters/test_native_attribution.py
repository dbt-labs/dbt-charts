"""Tests for warehouse-native attribution credentials.

Each warehouse exposes a different field for "who is connecting", with a different
shape. Pin the mapping, and pin the rule that keeps it safe: an authored value is
filled in, never overwritten.

What may travel on a credential is decided by `connection_identity()` — see
`tests/core/test_attribution.py`. A pooled connection is shared by every source with
the same connection identity, so per-query and authored pairs must stay off it.
"""

from __future__ import annotations

import json

import pytest

import dbt_charts.core.attribution as attribution_module
from dbt_charts.core.execute.adapters.native_attribution import (
    native_attribution_credential,
)


@pytest.fixture(autouse=True)
def _fixed_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the process surface so shape assertions do not depend on test order."""
    monkeypatch.setattr(
        attribution_module, "_process_surface", {"dbt_charts_surface": "serve"}
    )


class TestPerWarehouseShape:
    def test_snowflake_takes_a_json_query_tag(self) -> None:
        value = native_attribution_credential("snowflake")["query_tag"]
        assert json.loads(str(value))["app"] == "dbt-charts"

    def test_databricks_takes_a_json_query_tags_string(self) -> None:
        """dbt-databricks parses this with QueryTagsUtils.parse_query_tags."""
        value = native_attribution_credential("databricks")["query_tags"]
        assert json.loads(str(value))["app"] == "dbt-charts"

    def test_trino_takes_a_list_of_client_tags(self) -> None:
        value = native_attribution_credential("trino")["client_tags"]
        assert "app=dbt-charts" in value
        assert "dbt_charts_surface=serve" in value

    def test_postgres_takes_a_bare_name(self) -> None:
        """application_name caps near 63 bytes — JSON does not fit."""
        assert native_attribution_credential("postgres") == {
            "application_name": "dbt-charts"
        }

    def test_postgresql_alias_matches_postgres(self) -> None:
        assert native_attribution_credential(
            "postgresql"
        ) == native_attribution_credential("postgres")

    @pytest.mark.parametrize(
        "adapter_type", ["redshift", "athena", "duckdb", "bigquery"]
    )
    def test_warehouses_without_an_inert_field_get_nothing(
        self, adapter_type: str
    ) -> None:
        """Redshift's query_group routes WLM queues and Athena's work_group routes
        data limits/engine settings, so writing either could move a customer's
        queries; BigQuery carries the whole payload as job labels; DuckDB is
        in-process."""
        assert native_attribution_credential(adapter_type) == {}
