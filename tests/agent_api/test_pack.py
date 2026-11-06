"""Tests for dbt_charts.agent_api.pack — propose_pack integration.

The agent_api layer reads schema from the resolver and calls the pure planner.
These tests use the same _FakeAdapter pattern from test_schema.py to avoid
requiring a real database.

Covers:
- propose_pack returns (PackProposal, Path)
- the returned Path is the actual file written on disk
- propose_pack writes proposal.yml to target/dbt_charts/proposals/<slug>/
- proposal.yml is readable by load_proposal
- propose_pack accepts a mode override
- propose_pack raises ValueError when no sources are configured
- propose_pack raises FileNotFoundError when project_dir does not exist
- multi-schema source: each schema becomes its own connector entry (no schema lost)
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from dbt_charts.agent_api.pack import propose_pack
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.source_registry import SourceRegistry
from dbt_charts.core.pack.models import PackProposal
from dbt_charts.core.pack.proposal_store import load_proposal

# ---------------------------------------------------------------------------
# Minimal fake dbt adapter — same duck-typed pattern as test_schema.py
# ---------------------------------------------------------------------------


@dataclass
class _Col:
    name: str
    dtype: str


@dataclass
class _Rel:
    identifier: str
    schema: str
    type: str = "table"
    database: str | None = None


@dataclass
class _FakeAdapter:
    schemas_to_relations: dict[str, list[_Rel]] = field(default_factory=dict)
    columns_by_relation: dict[tuple[str, str], list[_Col]] = field(default_factory=dict)

    def add(self, schema: str, table: str) -> None:
        self.schemas_to_relations.setdefault(schema, []).append(
            _Rel(identifier=table, schema=schema)
        )
        self.columns_by_relation[(schema, table)] = []

    def list_schemas(self, database: str | None) -> list[str]:
        return list(self.schemas_to_relations.keys())

    def list_relations(self, database: str | None, schema: str) -> list[_Rel]:
        return list(self.schemas_to_relations.get(schema, []))

    def get_columns_in_relation(self, relation: _Rel) -> list[_Col]:
        return list(
            self.columns_by_relation.get((relation.schema, relation.identifier), [])
        )

    @classmethod
    def type(cls) -> str:  # noqa: A003
        return "duckdb"

    connections = SimpleNamespace(
        get_thread_connection=lambda: SimpleNamespace(handle=object())
    )

    @contextmanager
    def connection_named(self, name: str) -> Any:
        yield self


def _patch_for_source(source_name: str, adapter: _FakeAdapter) -> Any:
    """Patch resolver factory + registry so source_name routes to the fake adapter."""
    p_factory = patch(
        "dbt_charts.core.inspect.resolver._schema_adapter_factory",
        return_value=adapter,
    )
    p_resolve = patch(
        "dbt_charts.core.inspect.resolver.resolve_source_config",
        return_value={"type": "duckdb", "path": ":memory:"},
    )
    return p_factory, p_resolve


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_propose_pack_returns_tuple(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """propose_pack returns (PackProposal, Path)."""
    (tmp_path / "dbt_charts.yml").write_text("")

    adapter = _FakeAdapter()
    adapter.add("zendesk", "tickets")
    adapter.add("zendesk", "users")

    p_factory, p_resolve = _patch_for_source("fivetran_zendesk", adapter)

    def _fake_list_sql_sources(sources: SourceRegistry) -> list[dict[str, Any]]:
        return [{"name": "fivetran_zendesk", "type": "duckdb"}]

    with (
        p_factory,
        p_resolve,
        patch("dbt_charts.agent_api.pack.list_sql_sources", _fake_list_sql_sources),
        patch(
            "dbt_charts.core.inspect.resolver.list_sql_sources",
            _fake_list_sql_sources,
        ),
    ):
        proposal, out_path = propose_pack(local_project(tmp_path))

    assert isinstance(proposal, PackProposal)
    assert "fivetran_zendesk" in proposal.detected_sources
    assert isinstance(out_path, Path)


def test_propose_pack_returned_path_exists_on_disk(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """The Path returned by propose_pack is the file actually written."""
    (tmp_path / "dbt_charts.yml").write_text("")

    adapter = _FakeAdapter()
    adapter.add("zendesk", "tickets")
    adapter.add("zendesk", "users")

    p_factory, p_resolve = _patch_for_source("fivetran_zendesk", adapter)

    def _fake_list_sql_sources(sources: SourceRegistry) -> list[dict[str, Any]]:
        return [{"name": "fivetran_zendesk", "type": "duckdb"}]

    with (
        p_factory,
        p_resolve,
        patch("dbt_charts.agent_api.pack.list_sql_sources", _fake_list_sql_sources),
        patch(
            "dbt_charts.core.inspect.resolver.list_sql_sources",
            _fake_list_sql_sources,
        ),
    ):
        _proposal, out_path = propose_pack(local_project(tmp_path))

    assert out_path.exists(), f"Returned path {out_path} does not exist on disk"
    assert out_path.name == "proposal.yml"


def test_propose_pack_written_yml_is_loadable(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """The written proposal.yml can be loaded by load_proposal."""
    (tmp_path / "dbt_charts.yml").write_text("")

    adapter = _FakeAdapter()
    adapter.add("zendesk", "tickets")
    adapter.add("zendesk", "users")

    p_factory, p_resolve = _patch_for_source("fivetran_zendesk", adapter)

    def _fake_list_sql_sources(sources: SourceRegistry) -> list[dict[str, Any]]:
        return [{"name": "fivetran_zendesk", "type": "duckdb"}]

    with (
        p_factory,
        p_resolve,
        patch("dbt_charts.agent_api.pack.list_sql_sources", _fake_list_sql_sources),
        patch(
            "dbt_charts.core.inspect.resolver.list_sql_sources",
            _fake_list_sql_sources,
        ),
    ):
        proposal, out_path = propose_pack(local_project(tmp_path))

    reloaded = load_proposal(out_path)
    assert reloaded == proposal


def test_propose_pack_mode_override(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """mode='domain-first' override is respected."""
    (tmp_path / "dbt_charts.yml").write_text("")

    adapter = _FakeAdapter()
    adapter.add("zendesk", "tickets")

    p_factory, p_resolve = _patch_for_source("fivetran_zendesk", adapter)

    def _fake_list_sql_sources(sources: SourceRegistry) -> list[dict[str, Any]]:
        return [{"name": "fivetran_zendesk", "type": "duckdb"}]

    with (
        p_factory,
        p_resolve,
        patch("dbt_charts.agent_api.pack.list_sql_sources", _fake_list_sql_sources),
        patch(
            "dbt_charts.core.inspect.resolver.list_sql_sources",
            _fake_list_sql_sources,
        ),
    ):
        proposal, _path = propose_pack(local_project(tmp_path), mode="domain-first")

    assert proposal.organization_mode == "domain-first"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_propose_pack_no_sources_raises(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """propose_pack raises ValueError when no sources are configured."""
    (tmp_path / "dbt_charts.yml").write_text("")

    def _fake_list_sql_sources(sources: SourceRegistry) -> list[dict[str, Any]]:
        return []

    with (
        patch("dbt_charts.agent_api.pack.list_sql_sources", _fake_list_sql_sources),
        patch(
            "dbt_charts.core.inspect.resolver.list_sql_sources",
            _fake_list_sql_sources,
        ),
        pytest.raises(ValueError, match="no sources"),
    ):
        propose_pack(local_project(tmp_path))


def test_propose_pack_missing_project_dir_raises(
    local_project: Callable[..., FilesystemProject],
) -> None:
    """propose_pack raises FileNotFoundError for a non-existent project_dir."""
    with pytest.raises(FileNotFoundError):
        propose_pack(local_project(Path("/tmp/does_not_exist_dbt_charts_project_xyz")))


# ---------------------------------------------------------------------------
# Multi-schema sources — each schema becomes its own connector entry
# ---------------------------------------------------------------------------


def test_propose_pack_multi_schema_source_all_schemas_visible(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A single source with multiple schemas expands into one entry per schema.

    dundersign has a 'db' source with zendesk, salesforce, hubspot, etc.
    propose_pack must not silently drop schemas after the first — every schema
    with recognized entity tables must produce a proposed folder.
    """
    (tmp_path / "dbt_charts.yml").write_text("")

    # Simulate a source with two schemas: zendesk + salesforce
    adapter = _FakeAdapter()
    adapter.add("zendesk", "ticket")
    adapter.add("zendesk", "user")
    adapter.add("salesforce", "account")
    adapter.add("salesforce", "opportunity")

    p_factory = patch(
        "dbt_charts.core.inspect.resolver._schema_adapter_factory",
        return_value=adapter,
    )
    p_resolve = patch(
        "dbt_charts.core.inspect.resolver.resolve_source_config",
        return_value={"type": "duckdb", "path": ":memory:"},
    )

    def _fake_list_sql_sources(sources: SourceRegistry) -> list[dict[str, Any]]:
        return [{"name": "db", "type": "duckdb"}]

    with (
        p_factory,
        p_resolve,
        patch("dbt_charts.agent_api.pack.list_sql_sources", _fake_list_sql_sources),
        patch(
            "dbt_charts.core.inspect.resolver.list_sql_sources",
            _fake_list_sql_sources,
        ),
    ):
        proposal, _out_path = propose_pack(
            local_project(tmp_path), mode="connector-first"
        )

    # Both schemas must appear as separate folders, not just the first one
    folder_paths = [f.path for f in proposal.folders]
    assert any("zendesk" in p for p in folder_paths), (
        f"Expected a zendesk folder; got: {folder_paths}"
    )
    assert any("salesforce" in p for p in folder_paths), (
        f"Expected a salesforce folder; got: {folder_paths}"
    )

    # Evidence data_source must be the configured source "db", NOT the schema name.
    # The configured source is what the SQL adapter resolves — "zendesk" doesn't exist
    # as a standalone source in the project.
    all_evidence = [
        ev
        for folder in proposal.folders
        for dash in folder.dashboards
        for ev in dash.evidence
    ]
    bad_sources = [ev.data_source for ev in all_evidence if ev.data_source != "db"]
    assert not bad_sources, (
        f"Evidence must reference configured source 'db', not schema names: {bad_sources}"
    )


def test_propose_pack_multi_schema_single_source_detected(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """detected_sources still reports the original source name, not per-schema names."""
    (tmp_path / "dbt_charts.yml").write_text("")

    adapter = _FakeAdapter()
    adapter.add("zendesk", "ticket")
    adapter.add("salesforce", "opportunity")

    p_factory = patch(
        "dbt_charts.core.inspect.resolver._schema_adapter_factory",
        return_value=adapter,
    )
    p_resolve = patch(
        "dbt_charts.core.inspect.resolver.resolve_source_config",
        return_value={"type": "duckdb", "path": ":memory:"},
    )

    def _fake_list_sql_sources(sources: SourceRegistry) -> list[dict[str, Any]]:
        return [{"name": "db", "type": "duckdb"}]

    with (
        p_factory,
        p_resolve,
        patch("dbt_charts.agent_api.pack.list_sql_sources", _fake_list_sql_sources),
        patch(
            "dbt_charts.core.inspect.resolver.list_sql_sources",
            _fake_list_sql_sources,
        ),
    ):
        proposal, _out_path = propose_pack(
            local_project(tmp_path), mode="connector-first"
        )

    # The original source name must appear in detected_sources
    assert "db" in proposal.detected_sources


def test_propose_pack_dbt_internal_schemas_excluded(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Schemas that are dbt internal layers must not produce connector folders.

    A DuckDB with schemas main, main_staging, main_serving, zendesk, salesforce
    must only produce folders for zendesk and salesforce — the dbt schemas
    (main, main_staging, main_serving) are transformation layers, not connectors.
    """
    (tmp_path / "dbt_charts.yml").write_text("")

    adapter = _FakeAdapter()
    # dbt-internal schemas — must be excluded
    adapter.add("main", "documents")
    adapter.add("main_staging", "stg_product_db__document")
    adapter.add("main_serving", "daily_metrics")
    # connector schemas — must be included
    adapter.add("zendesk", "ticket")
    adapter.add("salesforce", "opportunity")

    p_factory = patch(
        "dbt_charts.core.inspect.resolver._schema_adapter_factory",
        return_value=adapter,
    )
    p_resolve = patch(
        "dbt_charts.core.inspect.resolver.resolve_source_config",
        return_value={"type": "duckdb", "path": ":memory:"},
    )

    def _fake_list_sql_sources(sources: SourceRegistry) -> list[dict[str, Any]]:
        return [{"name": "db", "type": "duckdb"}]

    with (
        p_factory,
        p_resolve,
        patch("dbt_charts.agent_api.pack.list_sql_sources", _fake_list_sql_sources),
        patch(
            "dbt_charts.core.inspect.resolver.list_sql_sources",
            _fake_list_sql_sources,
        ),
    ):
        proposal, _out_path = propose_pack(
            local_project(tmp_path), mode="connector-first"
        )

    folder_paths = [f.path for f in proposal.folders]
    assert any("zendesk" in p for p in folder_paths), (
        f"Expected zendesk folder; got: {folder_paths}"
    )
    assert any("salesforce" in p for p in folder_paths), (
        f"Expected salesforce folder; got: {folder_paths}"
    )
    # dbt internals must not appear as folders
    dbt_internal = ["main", "staging", "serving"]
    for internal in dbt_internal:
        assert not any(internal == p.split("/")[-1] for p in folder_paths), (
            f"Expected no '{internal}' folder; got: {folder_paths}"
        )
