"""`lookup_board_query_sql` previews the SQL execution would send: the same
variable coercion, and the filter helpers spelled for the source's dialect."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from dbt_charts.agent_api.query import lookup_board_query_sql
from dbt_charts.cli.filesystem_project import FilesystemProject

BOARD = """
variables:
  year:
    input: select
    data_type: number
    options:
      query: years
  span:
    input: daterange
    default: ['2024-01-01', '2024-01-31']
queries:
  years:
    sql: SELECT 2024 AS year
    source: db
  main:
    sql: |
      SELECT * FROM t WHERE {{ filter('year', year) }}
      AND {{ filter_date_range('day', span) }}
    source: db
charts:
  c:
    type: kpi
    query: main
    value: year
"""


def _project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject], source_type: str
) -> FilesystemProject:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "b.yml").write_text(BOARD)
    detail = "profile: p" if source_type == "dbt_profile" else "path: x.db"
    (tmp_path / "dbt_charts.yml").write_text(
        f"sources:\n  db:\n    type: {source_type}\n    {detail}\n"
    )
    return local_project(tmp_path)


def test_preview_spells_helpers_for_the_sources_dialect(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _project(tmp_path, local_project, "sqlite")
    result = lookup_board_query_sql("main", Path("charts/b.yml"), project=project)
    assert result.success, result.errors
    assert result.sql is not None
    assert "DATE(day) BETWEEN" in result.sql


def test_preview_coerces_values_like_execution(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _project(tmp_path, local_project, "sqlite")
    result = lookup_board_query_sql(
        "main", Path("charts/b.yml"), project=project, vars={"year": "twenty"}
    )
    assert not result.success
    assert any("must be numeric" in e for e in result.errors), result.errors


def test_preview_of_a_source_without_a_knowable_dialect_uses_the_default(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A `dbt_profile` source's warehouse is only known at execute; the
    preview still renders, in the default dialect's spelling."""
    project = _project(tmp_path, local_project, "dbt_profile")
    result = lookup_board_query_sql("main", Path("charts/b.yml"), project=project)
    assert result.success, result.errors
    assert result.sql is not None
    assert "CAST(day AS DATE) BETWEEN" in result.sql


REF_BOARD = """
queries:
  sub:
    sql: "SELECT * FROM {{ ref('stg_orders') }}"
    source: db
  main:
    sql: "SELECT label, amount FROM {{ ref('fct_orders') }}"
    source: db
  composed:
    sql: "SELECT * FROM {{ queries.sub }}"
    source: db
  composed_nospace:
    sql: "SELECT * FROM {{queries.sub}}"
    source: db
charts:
  c:
    type: table
    query: main
"""

_MANIFEST_FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "dbt_core_manifest" / "manifest.json"
)


def _ref_project(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    with_manifest: bool,
) -> FilesystemProject:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "b.yml").write_text(REF_BOARD)
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
    )
    if with_manifest:
        (tmp_path / "target").mkdir()
        (tmp_path / "target" / "manifest.json").write_text(
            _MANIFEST_FIXTURE.read_text()
        )
    return local_project(tmp_path)


def test_preview_resolves_dbt_refs(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    project = _ref_project(tmp_path, local_project, with_manifest=True)
    result = lookup_board_query_sql("main", Path("charts/b.yml"), project=project)
    assert result.success, result.errors
    # The fully-qualified relation, not the bare model name the ref() call
    # already spelled: only this distinguishes a resolve from a no-op.
    assert '"sample"."main"."fct_orders"' in result.sql


def test_preview_resolves_refs_inlined_by_a_query_reference(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """`{{ queries.X }}` is expanded to X's raw SQL by `resolve_query_references`
    before the first `dbt_refs.resolve` pass, so a `ref()` inside X resolves in
    that single pass."""
    project = _ref_project(tmp_path, local_project, with_manifest=True)
    result = lookup_board_query_sql("composed", Path("charts/b.yml"), project=project)
    assert result.success, result.errors
    assert '"sample"."main"."stg_orders"' in result.sql


def test_preview_resolves_refs_inlined_by_a_nospace_query_reference(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """`{{queries.X}}` (no space) isn't recognized by `resolve_query_references`'s
    dependency-graph short-circuit, so X's raw SQL — and any `ref()` inside it —
    only gets inlined by `render_parameterized_with_queries`'s own `{{ queries.* }}`
    handling. The second `dbt_refs.resolve` pass after that render is what
    resolves the ref() in this case; removing it regresses this exact query."""
    project = _ref_project(tmp_path, local_project, with_manifest=True)
    result = lookup_board_query_sql(
        "composed_nospace", Path("charts/b.yml"), project=project
    )
    assert result.success, result.errors
    assert '"sample"."main"."stg_orders"' in result.sql


def test_preview_without_a_manifest_names_the_manifest(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """No manifest is a manifest diagnostic, not `'ref' is undefined`."""
    project = _ref_project(tmp_path, local_project, with_manifest=False)
    result = lookup_board_query_sql("main", Path("charts/b.yml"), project=project)
    assert not result.success
    assert any("no dbt manifest was found" in e for e in result.errors), result.errors


def test_preview_of_a_ref_opens_no_warehouse_connection(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """Resolving a ref() reads the manifest; it must not reach for an adapter.

    Patching the constructor catches every build path, however
    `build_adapter_registry` is imported at the call site.
    """
    project = _ref_project(tmp_path, local_project, with_manifest=True)
    with patch(
        "dbt_charts.core.execute.adapters.adapter_registry.AdapterRegistry.__init__",
        side_effect=AssertionError(
            "adapter_registry must not be built during lookup_board_query_sql"
        ),
    ):
        result = lookup_board_query_sql("main", Path("charts/b.yml"), project=project)
    assert result.success, result.errors
