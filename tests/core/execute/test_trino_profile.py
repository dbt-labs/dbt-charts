"""A `type: trino` profiles.yml target, read without dbt Core.

Trino connects through its own driver, so no dbt adapter validates the target:
the Trino source model does, and a `dbt_profile` source resolves to the same
`TrinoSourceConfig` an authored `type: trino` source parses to.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.source import (
    ResolvedSourceConfig,
    TrinoSourceConfig,
)
from dbt_charts.core.diagnostics.codes_execute import ERR_DBT_TARGET_NEEDS_SOURCE
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter
from dbt_charts.core.execute.drivers import is_driver_source
from dbt_charts.core.execute.source_resolver import DbtContext, DefaultSourceResolver

_TARGET = {
    "type": "trino",
    "host": "trino.example.com",
    "port": 8443,
    "database": "lake",
    "schema": "public",
    "user": "ana",
    "threads": 4,
}


def _write_profile(project_dir: Path, target: dict[str, Any]) -> None:
    (project_dir / "profiles.yml").write_text(
        yaml.dump({"analytics": {"target": "dev", "outputs": {"dev": target}}})
    )


def _resolve(
    project_dir: Path,
    target: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    **source_fields: Any,
) -> ResolvedSourceConfig | None:
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    _write_profile(project_dir, target)
    return DefaultSourceResolver().resolve(
        authored={"type": "dbt_profile", "profile": "analytics", **source_fields},
        board_sources={},
        project_sources=ProjectSourcesConfig(),
        dbt_context=DbtContext(dbt_project_path=project_dir),
    )


def test_a_trino_target_becomes_a_trino_source_on_the_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _resolve(tmp_path, _TARGET, monkeypatch)
    assert isinstance(result, TrinoSourceConfig)
    assert is_driver_source(result)
    assert (result.host, result.port, result.database, result.schema_) == (
        "trino.example.com",
        8443,
        "lake",
        "public",
    )


def test_dbts_none_method_is_the_unauthenticated_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _resolve(tmp_path, {**_TARGET, "method": "none"}, monkeypatch)
    assert isinstance(result, TrinoSourceConfig)
    assert result.method is None


def test_a_templated_credential_is_rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRINO_PW", "s3cret")
    target = {**_TARGET, "method": "ldap", "password": "{{ env_var('TRINO_PW') }}"}
    result = _resolve(tmp_path, target, monkeypatch)
    assert isinstance(result, TrinoSourceConfig)
    assert result.password == "s3cret"


def test_attribution_rides_the_resolved_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _resolve(tmp_path, _TARGET, monkeypatch, attribution={"team": "ops"})
    assert isinstance(result, TrinoSourceConfig)
    assert result.attribution == {"team": "ops"}


def test_metricflow_rides_the_resolved_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _resolve(tmp_path, _TARGET, monkeypatch, metricflow="semantics")
    assert isinstance(result, TrinoSourceConfig)
    assert result.metricflow == "semantics"


def test_a_key_the_trino_source_does_not_know_is_refused_without_its_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = {**_TARGET, "password": "s3cret", "warehouse": "nope"}
    with pytest.raises(ExecutionError, match="warehouse") as excinfo:
        _resolve(tmp_path, target, monkeypatch)
    assert "not a valid trino connection" in str(excinfo.value)
    assert "s3cret" not in str(excinfo.value)


def test_target_path_is_refused_rather_than_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ExecutionError, match="target_path"):
        _resolve(tmp_path, _TARGET, monkeypatch, target_path="target-prod")


def test_a_project_whose_own_target_is_trino_needs_a_declared_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    _write_profile(tmp_path, _TARGET)
    (tmp_path / "dbt_project.yml").write_text("name: analytics\nprofile: analytics\n")
    adapter = DbtAdapter(
        project=FilesystemProject(tmp_path),
        dbt_project_path=tmp_path,
        target_name="dev",
    )
    assert adapter.resolve_target_type() == "trino"
    with pytest.raises(ExecutionError) as excinfo:
        adapter._get_dbt_adapter()
    assert excinfo.value.code is ERR_DBT_TARGET_NEEDS_SOURCE
