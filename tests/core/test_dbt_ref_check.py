"""Tests for core/dbt_ref_check.py — extract_ref_calls extraction primitive."""

from __future__ import annotations

from pathlib import Path

from dbt_charts.core.compile.host_facts import HostQuery

_REF_SQL = "SELECT * FROM {{ ref('orders') }}"
_SOURCE_SQL = "SELECT * FROM {{ source('jaffle', 'customers') }}"
_PLAIN_SQL = "SELECT 1"
_BOTH_SQL = "SELECT * FROM {{ ref('orders') }} JOIN {{ source('jaffle', 'customers') }}"


def _sql_query(sql: str) -> HostQuery:
    return {
        "authored": True,
        "type": "sql",
        "source": None,
        "sql": sql,
        "setup_sql": None,
        "dialect": None,
    }


def test_extract_finds_refs() -> None:
    from dbt_charts.core.dbt_ref_check import QueryRefCalls, extract_ref_calls

    found = extract_ref_calls({"my_query": _sql_query(_REF_SQL)})
    assert found == {"my_query": QueryRefCalls(refs=["orders"], sources=[])}


def test_extract_finds_sources() -> None:
    from dbt_charts.core.dbt_ref_check import QueryRefCalls, extract_ref_calls

    found = extract_ref_calls({"src_query": _sql_query(_SOURCE_SQL)})
    assert found == {
        "src_query": QueryRefCalls(refs=[], sources=[("jaffle", "customers")])
    }


def test_extract_skips_queries_with_no_dbt_calls() -> None:
    from dbt_charts.core.dbt_ref_check import extract_ref_calls

    found = extract_ref_calls({"plain": _sql_query(_PLAIN_SQL)})
    assert found == {}


def test_extract_mixed() -> None:
    """Only queries with dbt calls appear in the result."""
    from dbt_charts.core.dbt_ref_check import QueryRefCalls, extract_ref_calls

    found = extract_ref_calls(
        {"with_ref": _sql_query(_REF_SQL), "plain": _sql_query(_PLAIN_SQL)}
    )
    assert set(found.keys()) == {"with_ref"}
    assert found["with_ref"] == QueryRefCalls(refs=["orders"], sources=[])


def test_check_manifest_refs_returns_diagnostics_for_unknown_refs(
    tmp_path: Path,
) -> None:
    import json

    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.dbt_ref_check import check_manifest_refs

    (tmp_path / "dbt_charts.yml").write_text("name: p\n")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "manifest.json").write_text(
        json.dumps({"metadata": {"adapter_type": "duckdb"}, "nodes": {}})
    )
    found = check_manifest_refs(
        {"q": _sql_query(_REF_SQL), "plain": _sql_query(_PLAIN_SQL)},
        FilesystemProject(tmp_path),
    )
    assert [d.code for d in found] == ["ERR-DBT-REF-UNKNOWN-NODE"]


def test_check_manifest_refs_warns_once_when_no_manifest(tmp_path: Path) -> None:
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.dbt_ref_check import check_manifest_refs

    (tmp_path / "dbt_charts.yml").write_text("name: p\n")
    found = check_manifest_refs(
        {"a": _sql_query(_REF_SQL), "b": _sql_query(_REF_SQL)},
        FilesystemProject(tmp_path),
    )
    assert [d.code for d in found] == ["WARN-DBT-MANIFEST-MISSING"]
