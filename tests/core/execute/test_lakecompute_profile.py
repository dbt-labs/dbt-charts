"""A `type: lakecompute` profiles.yml target, read without dbt Core.

dbt v2's profile layer owns the schema, and there is no dbt-core 1.x adapter for
it, so the target maps field by field onto the lakecompute source. Every key dbt
v2 declares is either mapped, dropped for a stated reason, or rejected; none is
ignored by accident (the table lives in `_read_target_dict`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.source import (
    LakeComputeApiKeySource,
    LakeComputeFivetranSource,
    LakeComputeOktaSource,
    LakeComputeTokenSource,
    ResolvedSourceConfig,
)
from dbt_charts.core.diagnostics.codes_execute import ERR_DBT_TARGET_NEEDS_SOURCE
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter, _read_target_dict
from dbt_charts.core.execute.source_resolver import DbtContext, DefaultSourceResolver

_MULTI_CONNECTION = (
    "uses dbt v2's multi-connection shape, which dbt Charts does not read yet. "
    "Add a target whose type is lakecompute, or declare the source directly with "
    "type: lakecompute."
)


def _write_profile(project_dir: Path, target: Any, name: str = "lake") -> None:
    (project_dir / "profiles.yml").write_text(
        yaml.dump({"analytics": {"target": name, "outputs": {name: target}}})
    )


def _resolve(
    project_dir: Path,
    target: Any,
    monkeypatch: pytest.MonkeyPatch,
    **source_fields: Any,
) -> ResolvedSourceConfig | None:
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    _write_profile(project_dir, target)
    return DefaultSourceResolver().resolve(
        authored={
            "type": "dbt_profile",
            "profile": "analytics",
            "target": "lake",
            **source_fields,
        },
        board_sources={},
        project_sources=ProjectSourcesConfig(),
        dbt_context=DbtContext(dbt_project_path=project_dir),
    )


def test_a_token_target_becomes_a_lakecompute_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _resolve(
        tmp_path,
        {
            "type": "lakecompute",
            "token": "t",
            "base_url": "https://staging/",
            "catalog_bundle": "analytics",
        },
        monkeypatch,
    )
    assert isinstance(result, LakeComputeTokenSource)
    assert (result.token, result.base_url, result.catalog_bundle) == (
        "t",
        "https://staging/",
        "analytics",
    )


@pytest.mark.parametrize(
    ("fields", "variant"),
    [
        ({"method": "api_key", "api_key": "k"}, LakeComputeApiKeySource),
        (
            {"method": "okta_browser", "okta_client_id": "c"},
            LakeComputeOktaSource,
        ),
        (
            {"method": "fivetran", "fivetran_credential": "p"},
            LakeComputeFivetranSource,
        ),
    ],
)
def test_each_auth_method_maps_to_its_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fields: dict[str, Any],
    variant: type,
) -> None:
    result = _resolve(tmp_path, {"type": "lakecompute", **fields}, monkeypatch)
    assert type(result) is variant


def test_the_type_is_case_folded_as_dbt_folds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _resolve(tmp_path, {"type": "LakeCompute", "token": "t"}, monkeypatch)
    assert isinstance(result, LakeComputeTokenSource)


@pytest.mark.parametrize(
    "dropped",
    [
        {"database": "analytics"},
        {"schema": "dbt_dave"},
        {"threads": 4},
        {"snowflake_pat_duration_days": 30},
        {"organization": "fivetran"},
    ],
)
def test_a_profile_only_key_is_dropped_on_purpose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dropped: dict[str, Any]
) -> None:
    """These govern where `dbt run` writes or how dbt mints its own credentials;
    dbt never sends them to the driver, so sending them would connect differently
    from `dbt run`. Each is pinned so a drop is a decision, not an accident."""
    result = _resolve(
        tmp_path, {"type": "lakecompute", "token": "t", **dropped}, monkeypatch
    )
    assert isinstance(result, LakeComputeTokenSource)
    assert not set(dropped) & set(result.model_dump(by_alias=True))


def test_path_selects_dbts_local_engine_and_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(
        ExecutionError, match="'path' selects dbt's local DuckDB engine"
    ):
        _resolve(
            tmp_path, {"type": "lakecompute", "token": "t", "path": "x.db"}, monkeypatch
        )


def test_an_unknown_key_is_rejected_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ExecutionError, match="warehouse"):
        _resolve(
            tmp_path,
            {"type": "lakecompute", "token": "t", "warehouse": "w"},
            monkeypatch,
        )


@pytest.mark.parametrize("retired", ["alt", "lake_compute"])
def test_a_retired_type_spelling_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retired: str
) -> None:
    with pytest.raises(
        ExecutionError, match="dbt v2 spells lake compute 'lakecompute'"
    ):
        _resolve(tmp_path, {"type": retired, "token": "t"}, monkeypatch)


@pytest.mark.parametrize(
    "target",
    [
        [{"type": "lakecompute", "token": "t"}],
        {"type": "snowflake", "lakecompute": {"type": "lakecompute", "token": "t"}},
    ],
    ids=["list", "sibling_key"],
)
def test_dbt_v2s_multi_connection_shape_is_named_not_a_bare_type_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: Any
) -> None:
    with pytest.raises(ExecutionError) as excinfo:
        _resolve(tmp_path, target, monkeypatch)
    assert f"Profile 'analytics' target 'lake' {_MULTI_CONNECTION}" in str(
        excinfo.value
    )


def test_reading_a_lakecompute_target_never_imports_dbt_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with patch(
        "dbt.adapters.factory.load_plugin",
        side_effect=AssertionError("lake compute has no dbt-core adapter"),
    ):
        result = _resolve(tmp_path, {"type": "lakecompute", "token": "t"}, monkeypatch)
    assert result is not None


def test_env_var_in_a_field_renders_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAKE_TOKEN_FOR_TEST", "from-env")
    result = _resolve(
        tmp_path,
        {"type": "lakecompute", "token": "{{ env_var('LAKE_TOKEN_FOR_TEST') }}"},
        monkeypatch,
    )
    assert isinstance(result, LakeComputeTokenSource)
    assert result.token == "from-env"


def test_a_validation_error_never_quotes_a_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`method: token` with no `token:` fails, and the message must not carry the
    API key that sits beside it in the same target."""
    secret = "fake-api-key-value-for-redaction"
    with pytest.raises(ExecutionError) as excinfo:
        _resolve(tmp_path, {"type": "lakecompute", "api_key": secret}, monkeypatch)
    assert secret not in str(excinfo.value)
    assert "token" in str(excinfo.value)


def test_attribution_on_a_lakecompute_dbt_profile_source_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The driver has no query-header channel yet; accepting it only to drop it
    is the silent no-op these sources are built to avoid."""
    with pytest.raises(
        ExecutionError, match="attribution is not supported for lakecompute sources yet"
    ):
        _resolve(
            tmp_path,
            {"type": "lakecompute", "token": "t"},
            monkeypatch,
            attribution={"team": "analytics"},
        )


def test_target_path_on_a_lakecompute_dbt_profile_source_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(
        ExecutionError,
        match="target_path is not supported for lakecompute sources yet",
    ):
        _resolve(
            tmp_path,
            {"type": "lakecompute", "token": "t"},
            monkeypatch,
            target_path="target",
        )


class TestReadTargetDictKeepsItsContract:
    """`_read_target_dict` returns a dict for every type, lakecompute included, so
    its callers and the `render=` semantics are unchanged."""

    def test_unrendered_by_default_and_rendered_on_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LAKE_TOKEN_FOR_TEST", "from-env")
        _write_profile(
            tmp_path,
            {
                "type": "lakecompute",
                "threads": 4,
                "token": "{{ env_var('LAKE_TOKEN_FOR_TEST') }}",
            },
        )
        raw = _read_target_dict(tmp_path, "analytics", "lake")
        rendered = _read_target_dict(tmp_path, "analytics", "lake", render=True)
        assert raw == {
            "type": "lakecompute",
            "token": "{{ env_var('LAKE_TOKEN_FOR_TEST') }}",
        }
        assert rendered == {"type": "lakecompute", "token": "from-env"}


class TestSourceLessQueriesOnALakeComputeTarget:
    """A source-less `{{ ref() }}` query runs through dbt Core against the
    project's own target, and dbt Core cannot reach lake compute."""

    def _adapter(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DbtAdapter:
        from dbt_charts.cli.filesystem_project import FilesystemProject

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        _write_profile(tmp_path, {"type": "lakecompute", "token": "t"})
        (tmp_path / "dbt_project.yml").write_text(
            "name: analytics\nprofile: analytics\n"
        )
        return DbtAdapter(
            project=FilesystemProject(tmp_path),
            dbt_project_path=tmp_path,
            target_name="lake",
        )

    def test_the_type_is_still_readable_without_a_connection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`dct doctor` names the type from this."""
        assert (
            self._adapter(tmp_path, monkeypatch).resolve_target_type() == "lakecompute"
        )

    def test_building_the_dbt_adapter_raises_the_coded_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = self._adapter(tmp_path, monkeypatch)
        with pytest.raises(ExecutionError) as excinfo:
            adapter._get_dbt_adapter()
        assert excinfo.value.code is ERR_DBT_TARGET_NEEDS_SOURCE
        message = str(excinfo.value)
        assert "'lake'" in message
        assert "dbt_profile: analytics" in message


def test_a_templated_method_is_rendered_before_the_variant_is_chosen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAKE_METHOD_FOR_TEST", "api_key")
    result = _resolve(
        tmp_path,
        {
            "type": "lakecompute",
            "method": "{{ env_var('LAKE_METHOD_FOR_TEST') }}",
            "api_key": "k",
        },
        monkeypatch,
    )
    assert isinstance(result, LakeComputeApiKeySource)


def test_a_rendered_value_that_turns_invalid_is_an_execution_error_without_the_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The profile reader validates once; expansion validates the result again,
    and a failure there surfaces like every other profile error."""
    secret = "fake-credential-value-for-redaction"
    with (
        patch(
            "dbt_charts.core.execute.adapters.dbt_adapter._read_target_dict",
            return_value={"type": "lakecompute", "token": secret, "warehouse": "w"},
        ),
        pytest.raises(ExecutionError) as excinfo,
    ):
        _resolve(tmp_path, {"type": "lakecompute", "token": "t"}, monkeypatch)
    assert secret not in str(excinfo.value)
