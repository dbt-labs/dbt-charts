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
from dbt_charts.core.board import BoardRenderResult
from dbt_charts.core.compile.config import load_project_sources
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.source import DbtProfileSourceConfig
from dbt_charts.core.diagnostics import (
    ERR_DBT_PROFILE_UNRESOLVED,
    ERR_SOURCE_NOT_FOUND,
    ERR_SOURCE_NOT_NAMED,
)
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_CONFIG_INVALID
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.execute.plan_sources import (
    list_sql_sources,
    resolve_query_source,
    resolve_source_config,
)
from dbt_charts.core.execute.run import run_query
from dbt_charts.core.execute.source_resolver import DbtContext, DefaultSourceResolver


@pytest.fixture(autouse=True)
def isolated_profile_search(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    monkeypatch.delenv("DBT_PROFILE", raising=False)
    monkeypatch.delenv("DBT_TARGET", raising=False)


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

    registry = build_exec_env(FilesystemProject(root), read_only=True)

    assert list_sql_sources(registry.sources) == [
        {"name": "shop_profile", "type": "dbt_profile"}
    ]
    result = run_query(registry, _SOURCELESS)
    assert result.error is None, result.error
    assert result.data == [{"n": 42}]


def test_declaring_a_source_disables_the_implicit_one(tmp_path: Path) -> None:
    root = _dbt_project(
        tmp_path / "shop",
        charts_yml="sources:\n  mine:\n    type: duckdb\n    path: ':memory:'\n",
    )

    registry = build_exec_env(FilesystemProject(root), read_only=True)

    assert [s["name"] for s in list_sql_sources(registry.sources)] == ["mine"]
    with pytest.raises(DbtChartsError) as exc_info:
        resolve_query_source(
            _SOURCELESS,
            sources=registry.sources,
            resolver=registry.resolver,
            dbt_context=registry.dbt_context,
        )
    assert exc_info.value.code is ERR_SOURCE_NOT_NAMED


def test_a_project_without_dbt_project_yml_still_needs_a_named_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plain"
    root.mkdir()

    registry = build_exec_env(FilesystemProject(root), read_only=True)

    assert list_sql_sources(registry.sources) == []
    with pytest.raises(DbtChartsError) as exc_info:
        resolve_query_source(
            _SOURCELESS,
            sources=registry.sources,
            resolver=registry.resolver,
            dbt_context=registry.dbt_context,
        )
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
    registry = build_exec_env(FilesystemProject(root), read_only=True)

    with pytest.raises(DbtChartsError) as exc_info:
        resolve_query_source(
            SqlQuery(sql="SELECT 1", source="typo"),
            sources=registry.sources,
            resolver=registry.resolver,
            dbt_context=registry.dbt_context,
        )
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


_SOURCELESS_BOARD = (
    "title: Numbers\n"
    "queries:\n"
    "  numbers: SELECT n FROM numbers\n"
    "charts:\n"
    "  big:\n"
    "    type: kpi\n"
    "    query: queries.numbers\n"
    "    value: n\n"
)


def test_board_with_sourceless_sql_renders_in_a_bare_dbt_project(
    tmp_path: Path,
) -> None:
    from dbt_charts.agent_api._paths import resolve_board_or_error
    from dbt_charts.core.diagnostics import Diagnostic

    root = _dbt_project(tmp_path / "shop")
    (root / "charts").mkdir()
    (root / "charts" / "board.yml").write_text(_SOURCELESS_BOARD)

    with ProjectSession.open(root) as session:
        board = resolve_board_or_error(Path("board.yml"), session.project)
        assert not isinstance(board, Diagnostic), board
        rendered = session.render_board(board=board)

    assert rendered.status == "ok", (rendered.validation_errors, rendered.chart_errors)


def _render_package_board(root: Path) -> BoardRenderResult:
    from dbt_charts.agent_api._paths import resolve_board_or_error
    from dbt_charts.core.diagnostics import Diagnostic

    pkg_charts = root / "dbt_packages" / "pkg_a" / "charts"
    pkg_charts.mkdir(parents=True)
    (pkg_charts / "overview.yml").write_text(_SOURCELESS_BOARD)

    with ProjectSession.open(root) as session:
        board = resolve_board_or_error(
            Path("dbt_packages/pkg_a/charts/overview.yml"), session.project
        )
        assert not isinstance(board, Diagnostic), board
        return session.render_board(board=board)


def test_sourceless_package_board_renders_in_a_bare_dbt_project(
    tmp_path: Path,
) -> None:
    rendered = _render_package_board(_dbt_project(tmp_path / "shop"))

    assert rendered.status == "ok", (rendered.validation_errors, rendered.chart_errors)


def test_sourceless_package_board_follows_the_host_meta_source(
    tmp_path: Path,
) -> None:
    root = _dbt_project(
        tmp_path / "shop",
        charts_yml="sources:\n  mine:\n    type: dbt_profile\n",
    )
    (root / "charts").mkdir()
    (root / "charts" / "meta.yml").write_text("source: mine\n")

    rendered = _render_package_board(root)

    assert rendered.status == "ok", (rendered.validation_errors, rendered.chart_errors)


def test_sourceless_package_board_fails_when_the_host_names_no_default(
    tmp_path: Path,
) -> None:
    root = _dbt_project(
        tmp_path / "shop",
        charts_yml="sources:\n  mine:\n    type: dbt_profile\n",
    )

    rendered = _render_package_board(root)

    assert [d.code for d in rendered.validation_errors] == ["ERR-SOURCE-REQUIRED"]


def test_resolve_source_config_follows_the_same_sourceless_rule(
    tmp_path: Path,
) -> None:
    dbt = build_exec_env(FilesystemProject(_dbt_project(tmp_path / "shop")))
    plain_root = tmp_path / "plain"
    plain_root.mkdir()
    plain = build_exec_env(FilesystemProject(plain_root))

    assert resolve_source_config(dbt.sources, dbt.project, None) == {
        "type": "dbt_profile"
    }
    with pytest.raises(DbtChartsError) as exc_info:
        resolve_source_config(plain.sources, plain.project, None)
    assert exc_info.value.code is ERR_SOURCE_NOT_NAMED


def _two_target_project(root: Path, *, charts_yml: str | None = None) -> Path:
    """A dbt project whose default target ``dev`` holds 42 and ``alt`` holds 7."""
    _dbt_project(root, charts_yml=charts_yml)
    alt_path = root / "alt.duckdb"
    conn = duckdb.connect(str(alt_path))
    conn.execute("CREATE TABLE numbers (n INTEGER)")
    conn.execute("INSERT INTO numbers VALUES (7)")
    conn.close()
    profiles = yaml.safe_load((root / "profiles.yml").read_text())
    profiles["shop_profile"]["outputs"]["alt"] = {
        "type": "duckdb",
        "path": str(alt_path),
        "threads": 1,
    }
    (root / "profiles.yml").write_text(yaml.safe_dump(profiles))
    return root


def test_dbt_target_env_selects_the_implicit_source_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _two_target_project(tmp_path / "shop")
    monkeypatch.setenv("DBT_TARGET", "alt")

    with ProjectSession.open(root) as session:
        result = session.execute_query("SELECT n FROM numbers")

    assert result.success, result.errors
    assert result.data == [{"n": 7}]


def test_registry_target_selects_the_implicit_source_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _two_target_project(tmp_path / "shop")
    monkeypatch.setenv("DBT_TARGET", "dev")

    registry = build_exec_env(FilesystemProject(root), target="alt")
    result = run_query(registry, _SOURCELESS)

    assert result.error is None, result.error
    assert result.data == [{"n": 7}]


def test_a_source_that_names_its_target_keeps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _two_target_project(
        tmp_path / "shop",
        charts_yml="sources:\n  pinned:\n    type: dbt_profile\n    target: dev\n",
    )
    monkeypatch.setenv("DBT_TARGET", "alt")

    registry = build_exec_env(FilesystemProject(root), target="alt")
    result = run_query(registry, SqlQuery(sql="SELECT n FROM numbers", source="pinned"))

    assert result.error is None, result.error
    assert result.data == [{"n": 42}]


def test_package_board_renders_from_its_integration_tests_dbt_project(
    tmp_path: Path,
) -> None:
    from dbt_charts.agent_api._paths import resolve_board_or_error
    from dbt_charts.core.diagnostics import Diagnostic

    package = tmp_path / "package"
    package.mkdir()
    (package / "dbt_project.yml").write_text("name: shop_package\n")
    (package / "charts").mkdir()
    (package / "charts" / "board.yml").write_text(_SOURCELESS_BOARD)
    linked = _dbt_project(package / "integration_tests")

    project = FilesystemProject(package, dbt_root=linked)
    with ProjectSession.from_project(project) as session:
        board = resolve_board_or_error(Path("board.yml"), session.project)
        assert not isinstance(board, Diagnostic), board
        rendered = session.render_board(board=board)

    assert rendered.status == "ok", (rendered.validation_errors, rendered.chart_errors)


def _expand_profile(
    project: Path | None,
    outputs: dict[str, dict[str, str]] | None = None,
    **cfg: str,
) -> None:
    if project is not None and outputs is not None:
        project.mkdir()
        (project / "dbt_project.yml").write_text("name: shop\nprofile: shop_profile\n")
        (project / "profiles.yml").write_text(
            yaml.safe_dump({"shop_profile": {"target": "dev", "outputs": outputs}})
        )
    elif project is not None:
        project.mkdir()
    DefaultSourceResolver()._expand_dbt_profile(
        DbtProfileSourceConfig(type="dbt_profile", **cfg),
        DbtContext(dbt_project_path=project) if project is not None else None,
    )


_DEV_DUCKDB = {"dev": {"type": "duckdb", "path": ":memory:"}}


@pytest.mark.parametrize(
    ("outputs", "cfg", "message"),
    [
        pytest.param(None, {"profile": "shop_profile"}, "profiles.yml", id="no-file"),
        pytest.param(_DEV_DUCKDB, {"profile": "other"}, "other", id="unknown-profile"),
        pytest.param(
            _DEV_DUCKDB,
            {"profile": "shop_profile", "target": "prod"},
            "prod",
            id="unknown-target",
        ),
        pytest.param(
            {"dev": {"type": "snowflake", "account": "xy"}},
            {"profile": "shop_profile"},
            "",
            id="invalid-snowflake-target",
        ),
        pytest.param(
            {"dev": {"type": "trino", "host": "h"}},
            {"profile": "shop_profile"},
            "",
            id="invalid-trino-target",
        ),
        pytest.param(
            {"dev": {"type": "lakecompute", "password": "hunter2"}},
            {"profile": "shop_profile"},
            "",
            id="invalid-lakecompute-target",
        ),
    ],
)
def test_an_unreadable_dbt_profile_is_a_coded_error(
    tmp_path: Path,
    outputs: dict[str, dict[str, str]] | None,
    cfg: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(DbtChartsError) as exc_info:
        _expand_profile(tmp_path / "shop", outputs=outputs, **cfg)

    assert exc_info.value.code is ERR_DBT_PROFILE_UNRESOLVED
    assert message in str(exc_info.value)
    assert "hunter2" not in str(exc_info.value)


def test_a_profiles_dir_without_a_linked_project_is_a_coded_error() -> None:
    with pytest.raises(DbtChartsError) as exc_info:
        _expand_profile(None, profile="shop_profile", profiles_dir="dbt")

    assert exc_info.value.code is ERR_DBT_PROFILE_UNRESOLVED
    assert "no dbt project is linked" in str(exc_info.value)


def test_dbt_profiles_dir_env_without_profiles_yml_is_a_coded_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("DBT_PROFILES_DIR", str(empty))

    with pytest.raises(DbtChartsError) as exc_info:
        _expand_profile(tmp_path / "shop", profile="shop_profile")

    assert exc_info.value.code is ERR_DBT_PROFILE_UNRESOLVED
    assert "does not contain profiles.yml" in str(exc_info.value)
