"""`dbt_utils.py` against a real dbt v2-emitted manifest.json.

`dbt-charts/tests/fixtures/fusion_manifest/manifest.json` was produced by
`dbt parse` with `dbt-fusion 2.0.0-preview.193` against the `jaffle-shop`
jaffle project (Snowflake target), with the `macros` key stripped (unused
by any dbt charts reader — it was 828KB of the 934KB raw file). Every other
key is byte-for-byte what dbt v2 wrote.

The highest-risk field per that inventory is `relation_name` — a
dbt v1-computed convenience string. These tests confirm dbt v2 emits it
(`DATABASE.schema.alias`, same shape as dbt v1) so ref resolution
resolves `ref()`/`source()` calls without falling back to composing
`schema.alias` itself.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from dbt_charts.core.dbt_manifest import LoadedManifest, load_manifest, ref_index
from dbt_charts.core.execute.dbt_utils import resolve_dbt_refs_with_provenance
from dbt_charts.core.project import Project

from ..._paths import DBT_CHARTS_DIR

_FUSION_FIXTURES = DBT_CHARTS_DIR / "tests" / "fixtures" / "fusion_manifest"
_FUSION_MANIFEST_JSON = (_FUSION_FIXTURES / "manifest.json").read_text()

# A second real dbt v2 fixture, from the same jaffle_shop project built with a
# DuckDB target instead of Snowflake, diffed key-for-key against a dbt v1
# 1.12.0 manifest from the identical project (see the task worksheet's
# divergence table). `relation_name` differs in shape by *adapter*, not by
# engine: dbt v1 emits the 3-part `"memory"."main"."customers"` (DuckDB's
# in-memory default database name baked in); dbt v2 emits the 2-part
# `"main"."customers"` — no database segment. Both are still present (the
# highest-risk field from the inventory), just quoted/segmented differently.
# dbt charts' own DuckDB adapter always connects directly to one resolved
# file/`:memory:` context (`_resolved_path` in backends/duckdb_local.py) rather than
# ATTACHing multiple databases by name, so a schema-qualified 2-part
# reference is unambiguous and correct here — not a bug to fix.
_FUSION_MANIFEST_DUCKDB_JSON = (_FUSION_FIXTURES / "manifest_duckdb.json").read_text()


def test_load_manifest_reads_target_manifest_json(
    in_memory_project: Callable[..., Project], tmp_path: Path
) -> None:
    project = in_memory_project(
        tmp_path, {"target/manifest.json": _FUSION_MANIFEST_JSON}
    )
    loaded = load_manifest(project)
    assert loaded is not None
    assert "customers" in {n["name"] for n in loaded.raw["nodes"].values()}


def test_load_manifest_returns_none_without_target_manifest(
    in_memory_project: Callable[..., Project], tmp_path: Path
) -> None:
    """No target/manifest.json — a stray manifest.snapshot.json is not a
    candidate (the convention was removed, not aliased)."""
    project = in_memory_project(
        tmp_path, {"manifest.snapshot.json": _FUSION_MANIFEST_JSON}
    )
    assert load_manifest(project) is None


def test_resolve_dbt_refs_uses_fusion_relation_name_for_model() -> None:
    loaded = LoadedManifest(
        raw=json.loads(_FUSION_MANIFEST_JSON),
        relpath="target/manifest.json",
        version="v1",
    )
    sql, _ = resolve_dbt_refs_with_provenance(
        "select * from {{ ref('orders') }}", ref_index(loaded)
    )
    assert sql == "select * from DBT_TEST.webbeard.orders"


def test_resolve_dbt_refs_uses_fusion_relation_name_for_source() -> None:
    loaded = LoadedManifest(
        raw=json.loads(_FUSION_MANIFEST_JSON),
        relpath="target/manifest.json",
        version="v1",
    )
    sql, _ = resolve_dbt_refs_with_provenance(
        "select * from {{ source('ecom', 'raw_customers') }}", ref_index(loaded)
    )
    assert sql == "select * from DBT_TEST.raw.raw_customers"


def test_resolve_dbt_refs_uses_fusion_duckdb_relation_name_without_database_segment() -> (
    None
):
    """DuckDB-target dbt v2 output: relation_name is present but 2-part
    (`"schema"."table"`, no database segment) — still used verbatim, no
    fallback to composing schema.alias."""
    loaded = LoadedManifest(
        raw=json.loads(_FUSION_MANIFEST_DUCKDB_JSON),
        relpath="target/manifest.json",
        version="v1",
    )
    sql, _ = resolve_dbt_refs_with_provenance(
        "select * from {{ ref('customers') }}", ref_index(loaded)
    )
    assert sql == 'select * from "main"."customers"'
