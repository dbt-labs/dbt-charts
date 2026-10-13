"""A dbt project that declares no ``sources:`` gets one: its dbt profile.

Sourceless SQL runs against it; declaring any source switches it off, and a
project with no dbt_project.yml never gets one.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
import yaml

from dbt_charts.agent_api.doctor import run_doctor
from dbt_charts.agent_api.project_session import ProjectSession
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import load_project_sources
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.diagnostics import ERR_SOURCE_NOT_FOUND, ERR_SOURCE_NOT_NAMED
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_CONFIG_INVALID
from dbt_charts.core.execute.adapters import build_adapter_registry


@pytest.fixture(autouse=True)
def isolated_profile_search(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    monkeypatch.delenv("DBT_PROFILE", raising=False)


def _dbt_project(root: Path, *, charts_yml: str | None = None) -> Path:
    root.mkdir()
    db_path = root / "warehouse.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute("CREATE TABLE numbers (n INTEGER)")
    conn.execute("INSERT INTO numbers VALUES (42)")
    conn.close()
    (root / "dbt_project.yml").write_text("name: shop\nprofile: shop_profile\n")
    (root / "profiles.yml").write_text(
        yaml.safe_dump(
            {
                "shop_profile": {
                    "target": "dev",
                    "outputs": {
                        "dev": {"type": "duckdb", "path": str(db_path), "threads": 1}
                    },
                }
            }
        )
    )
    if charts_yml is not None:
        (root / "dbt_charts.yml").write_text(charts_yml)
    return root


_SOURCELESS = SqlQuery(sql="SELECT n FROM numbers")


@pytest.mark.parametrize("charts_yml", [None, "name: shop\n", "sources: {}\n"])
def test_sourceless_sql_runs_against_the_profile_named_source(
    tmp_path: Path, charts_yml: str | None
) -> None:
    root = _dbt_project(tmp_path / "shop", charts_yml=charts_yml)

    registry = build_adapter_registry(FilesystemProject(root), read_only=True)

    assert registry.list_sql_sources() == [
        {"name": "shop_profile", "type": "dbt_profile"}
    ]
    result = registry.execute(_SOURCELESS)
    assert result.error is None, result.error
    assert result.data == [{"n": 42}]


def test_declaring_a_source_disables_the_implicit_one(tmp_path: Path) -> None:
    root = _dbt_project(
        tmp_path / "shop",
        charts_yml="sources:\n  mine:\n    type: duckdb\n    path: ':memory:'\n",
    )

    registry = build_adapter_registry(FilesystemProject(root), read_only=True)

    assert [s["name"] for s in registry.list_sql_sources()] == ["mine"]
    with pytest.raises(DbtChartsError) as exc_info:
        registry.resolve_query_source(_SOURCELESS)
    assert exc_info.value.code is ERR_SOURCE_NOT_NAMED


def test_a_project_without_dbt_project_yml_still_needs_a_named_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plain"
    root.mkdir()

    registry = build_adapter_registry(FilesystemProject(root), read_only=True)

    assert registry.list_sql_sources() == []
    with pytest.raises(DbtChartsError) as exc_info:
        registry.resolve_query_source(_SOURCELESS)
    assert exc_info.value.code is ERR_SOURCE_NOT_NAMED


def test_a_dbt_project_with_no_profile_gets_no_implicit_source(tmp_path: Path) -> None:
    root = tmp_path / "package"
    root.mkdir()
    (root / "dbt_project.yml").write_text("name: some_package\n")

    assert load_project_sources(FilesystemProject(root)).sources == {}


def test_a_malformed_dbt_project_yml_is_a_coded_error(tmp_path: Path) -> None:
    root = tmp_path / "broken"
    root.mkdir()
    (root / "dbt_project.yml").write_text("profile: [not, a, string]\n")

    with pytest.raises(CompilationError) as exc_info:
        load_project_sources(FilesystemProject(root))
    assert exc_info.value.code is ERR_SOURCE_CONFIG_INVALID


def test_implicit_source_does_not_swallow_a_named_unknown_source(
    tmp_path: Path,
) -> None:
    root = _dbt_project(tmp_path / "shop")
    registry = build_adapter_registry(FilesystemProject(root), read_only=True)

    with pytest.raises(DbtChartsError) as exc_info:
        registry.resolve_query_source(SqlQuery(sql="SELECT 1", source="typo"))
    assert exc_info.value.code is ERR_SOURCE_NOT_FOUND


def test_session_runs_sourceless_ad_hoc_sql_in_a_bare_dbt_project(
    tmp_path: Path,
) -> None:
    root = _dbt_project(tmp_path / "shop")

    with ProjectSession.open(root) as session:
        result = session.execute_query("SELECT n FROM numbers")

    assert result.success, result.errors
    assert result.data == [{"n": 42}]


def test_doctor_lists_the_implicit_source(tmp_path: Path) -> None:
    root = _dbt_project(tmp_path / "shop")

    report = run_doctor(root, with_warehouse=False)

    adapter_checks = [c for c in report.checks if c.code == "adapter"]
    assert [c.source for c in adapter_checks] == ["shop_profile"]


def test_board_with_sourceless_sql_renders_in_a_bare_dbt_project(
    tmp_path: Path,
) -> None:
    from dbt_charts.agent_api._paths import resolve_board_or_error
    from dbt_charts.core.diagnostics import Diagnostic

    root = _dbt_project(tmp_path / "shop")
    (root / "charts").mkdir()
    (root / "charts" / "board.yml").write_text(
        "title: Numbers\n"
        "queries:\n"
        "  numbers: SELECT n FROM numbers\n"
        "charts:\n"
        "  big:\n"
        "    type: kpi\n"
        "    query: queries.numbers\n"
        "    value: n\n"
    )

    with ProjectSession.open(root) as session:
        board = resolve_board_or_error(Path("board.yml"), session.project)
        assert not isinstance(board, Diagnostic), board
        rendered = session.render_board(board=board)

    assert rendered.status == "ok", (rendered.validation_errors, rendered.chart_errors)


def test_resolve_source_config_follows_the_same_sourceless_rule(
    tmp_path: Path,
) -> None:
    dbt = build_adapter_registry(FilesystemProject(_dbt_project(tmp_path / "shop")))
    plain_root = tmp_path / "plain"
    plain_root.mkdir()
    plain = build_adapter_registry(FilesystemProject(plain_root))

    assert dbt.resolve_source_config(None) == {"type": "dbt_profile"}
    with pytest.raises(DbtChartsError) as exc_info:
        plain.resolve_source_config(None)
    assert exc_info.value.code is ERR_SOURCE_NOT_NAMED
