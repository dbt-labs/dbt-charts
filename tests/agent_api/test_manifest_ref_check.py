"""Integration tests for manifest-backed ref/source validation via validate_content."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject

_MANIFEST_CONTENT = json.dumps(
    {
        "metadata": {
            "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"
        },
        "nodes": {
            "model.analytics.fct_orders": {
                "resource_type": "model",
                "name": "fct_orders",
                "schema": "analytics",
                "relation_name": "analytics.fct_orders",
            }
        },
        "sources": {
            "source.analytics.raw.customers": {
                "source_name": "raw",
                "name": "customers",
                "schema": "raw",
                "relation_name": "raw.customers",
            }
        },
    }
)

_BOARD_WITH_VALID_REF = """
queries:
  orders:
    sql: "SELECT * FROM {{ ref('fct_orders') }}"
    source: dbt_duckdb
rows: []
"""

_BOARD_WITH_UNKNOWN_REF = """
queries:
  orders:
    sql: "SELECT * FROM {{ ref('typo_model') }}"
    source: dbt_duckdb
rows: []
"""

_BOARD_WITH_UNKNOWN_SOURCE = """
queries:
  orders:
    sql: "SELECT * FROM {{ source('raw', 'nonexistent') }}"
    source: dbt_duckdb
rows: []
"""

_BOARD_NO_REFS = """
queries:
  plain:
    sql: SELECT 1
    source: analytics
rows: []
"""


def _project_with_manifest(tmp_path: Path) -> FilesystemProject:
    (tmp_path / "charts").mkdir()
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "manifest.json").write_text(_MANIFEST_CONTENT)
    return FilesystemProject(tmp_path)


def _project_without_manifest(tmp_path: Path) -> FilesystemProject:
    (tmp_path / "charts").mkdir()
    return FilesystemProject(tmp_path)


def test_valid_ref_passes(tmp_path: Path) -> None:
    from dbt_charts.agent_api.validate import validate_content

    project = _project_with_manifest(tmp_path)
    result = validate_content(_BOARD_WITH_VALID_REF, project=project)
    manifest_errors = [e for e in result.errors if "ERR-DBT-REF" in e.code]
    manifest_warns = [
        w for w in result.warnings if w.code == "WARN-DBT-MANIFEST-MISSING"
    ]
    assert manifest_errors == []
    assert manifest_warns == []


def test_unknown_ref_produces_error(tmp_path: Path) -> None:
    from dbt_charts.agent_api.validate import validate_content

    project = _project_with_manifest(tmp_path)
    result = validate_content(_BOARD_WITH_UNKNOWN_REF, project=project)
    error_codes = [e.code for e in result.errors]
    assert "ERR-DBT-REF-UNKNOWN-NODE" in error_codes


def test_unknown_source_produces_error(tmp_path: Path) -> None:
    from dbt_charts.agent_api.validate import validate_content

    project = _project_with_manifest(tmp_path)
    result = validate_content(_BOARD_WITH_UNKNOWN_SOURCE, project=project)
    error_codes = [e.code for e in result.errors]
    assert "ERR-DBT-SOURCE-UNKNOWN-TABLE" in error_codes


def test_missing_manifest_warns_once(tmp_path: Path) -> None:
    from dbt_charts.agent_api.validate import validate_content

    project = _project_without_manifest(tmp_path)
    result = validate_content(_BOARD_WITH_VALID_REF, project=project)
    manifest_warns = [
        w for w in result.warnings if w.code == "WARN-DBT-MANIFEST-MISSING"
    ]
    assert len(manifest_warns) == 1
    assert "looked for" in manifest_warns[0].message


def test_no_refs_no_manifest_no_warning(tmp_path: Path) -> None:
    from dbt_charts.agent_api.validate import validate_content

    project = _project_without_manifest(tmp_path)
    result = validate_content(_BOARD_NO_REFS, project=project)
    manifest_warns = [
        w for w in result.warnings if w.code == "WARN-DBT-MANIFEST-MISSING"
    ]
    assert manifest_warns == []


def test_loader_not_called_when_no_refs(tmp_path: Path) -> None:
    from dbt_charts.agent_api.validate import validate_content

    project = _project_without_manifest(tmp_path)
    with patch("dbt_charts.core.dbt_ref_check.load_manifest") as mock_load:
        validate_content(_BOARD_NO_REFS, project=project)
    mock_load.assert_not_called()


def test_file_based_validate_unknown_ref(tmp_path: Path) -> None:
    from dbt_charts.agent_api.validate import validate

    (tmp_path / "charts").mkdir()
    board = tmp_path / "charts" / "board.yml"
    board.write_text(_BOARD_WITH_UNKNOWN_REF)
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "manifest.json").write_text(_MANIFEST_CONTENT)
    project = FilesystemProject(tmp_path)

    result = validate(board, project=project)

    assert not result.success
    assert any(e.code == "ERR-DBT-REF-UNKNOWN-NODE" for e in result.errors)


def test_corrupt_manifest_surfaces_as_diagnostic(tmp_path: Path) -> None:
    from dbt_charts.agent_api.validate import validate_content

    (tmp_path / "charts").mkdir()
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "manifest.json").write_text("{bad json")
    project = FilesystemProject(tmp_path)

    result = validate_content(_BOARD_WITH_VALID_REF, project=project)

    # Must not raise — error must be in result.errors as a diagnostic
    assert any("ERR-DBT-MANIFEST-UNREADABLE" in e.code for e in result.errors)


def _prod_manifest(model: str) -> str:
    return json.dumps(
        {
            "nodes": {
                f"model.analytics.{model}": {
                    "resource_type": "model",
                    "name": model,
                    "schema": "prod",
                    "relation_name": f"prod.{model}",
                }
            },
            "sources": {},
        }
    )


def _board_on(source: str, model: str) -> str:
    return (
        "queries:\n"
        "  orders:\n"
        f"    sql: \"SELECT * FROM {{{{ ref('{model}') }}}}\"\n"
        f"    source: {source}\n"
        "rows: []\n"
    )


def _project_with_prod_source(
    tmp_path: Path, *, prod_manifest: str | None
) -> FilesystemProject:
    """Default manifest knows fct_orders; the prod_wh source's own knows what
    ``prod_manifest`` says (or has no manifest at all)."""
    (tmp_path / "charts").mkdir()
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "manifest.json").write_text(_MANIFEST_CONTENT)
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n"
        "  prod_wh:\n"
        "    type: dbt_profile\n"
        "    profile: analytics\n"
        "    target: prod\n"
        "    target_path: target/prod\n"
    )
    if prod_manifest is not None:
        (tmp_path / "target" / "prod").mkdir()
        (tmp_path / "target" / "prod" / "manifest.json").write_text(prod_manifest)
    return FilesystemProject(tmp_path)


def test_ref_validates_against_the_sources_target_path(tmp_path: Path) -> None:
    from dbt_charts.agent_api.validate import validate_content

    project = _project_with_prod_source(
        tmp_path, prod_manifest=_prod_manifest("fct_prod_only")
    )

    ok = validate_content(_board_on("prod_wh", "fct_prod_only"), project=project)
    stale = validate_content(_board_on("prod_wh", "fct_orders"), project=project)

    assert [e for e in ok.errors if "ERR-DBT" in e.code] == []
    assert [e.code for e in stale.errors if "ERR-DBT" in e.code] == [
        "ERR-DBT-REF-UNKNOWN-NODE"
    ]


def test_missing_target_path_manifest_warns_naming_the_path(tmp_path: Path) -> None:
    from dbt_charts.agent_api.validate import validate_content

    project = _project_with_prod_source(tmp_path, prod_manifest=None)

    result = validate_content(_board_on("prod_wh", "fct_orders"), project=project)

    warns = [w for w in result.warnings if w.code == "WARN-DBT-MANIFEST-MISSING"]
    assert len(warns) == 1
    assert "target/prod/manifest.json" in warns[0].message


def test_board_sources_resolve_target_path_ahead_of_the_project(
    tmp_path: Path,
) -> None:
    """A board-level `sources:` entry shadows the project's, as at execution."""
    from dbt_charts.agent_api.validate import validate_content

    project = _project_with_prod_source(
        tmp_path, prod_manifest=_prod_manifest("fct_prod_only")
    )
    board = (
        "sources:\n"
        "  prod_wh:\n"
        "    type: dbt_profile\n"
        "    profile: analytics\n"
        "    target_path: target\n" + _board_on("prod_wh", "fct_orders")
    )

    result = validate_content(board, project=project)

    assert [e for e in result.errors if "ERR-DBT" in e.code] == []


def test_target_path_env_var_renders_as_execution_renders_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dbt_charts.agent_api.validate import validate_content

    project = _project_with_prod_source(
        tmp_path, prod_manifest=_prod_manifest("fct_prod_only")
    )
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n"
        "  prod_wh:\n"
        "    type: dbt_profile\n"
        "    profile: analytics\n"
        "    target_path: \"target/{{ env_var('DCT_TEST_TARGET') }}\"\n"
    )
    monkeypatch.setenv("DCT_TEST_TARGET", "prod")

    result = validate_content(_board_on("prod_wh", "fct_prod_only"), project=project)

    assert [e for e in result.errors if "ERR-DBT" in e.code] == []
    assert [w for w in result.warnings if w.code == "WARN-DBT-MANIFEST-MISSING"] == []
