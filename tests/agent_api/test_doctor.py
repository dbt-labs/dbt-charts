"""`run_doctor` reports install, project, profiles, and per-source adapter health."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
import yaml

from dbt_charts.agent_api import doctor
from dbt_charts.agent_api.doctor import DoctorCheck, DoctorReport, run_doctor
from dbt_charts.core.compile.models.source import ResolvedSourceConfig
from dbt_charts.core.connections import WarehouseAuthError, WarehouseProbeError
from dbt_charts.core.diagnostics import ERR_ADAPTER_NOT_INSTALLED
from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    AdapterNotInstalledError,
)

_DUCKDB_PROFILE = {
    "shop": {
        "target": "dev",
        "outputs": {"dev": {"type": "duckdb", "path": ":memory:"}},
    }
}
_SNOWFLAKE_SOURCE = {
    "type": "snowflake",
    "account": "abc",
    "user": "u",
    "database": "d",
    "schema": "s",
}
_SNOWFLAKE_INSTALL = 'uv tool install "dbt-charts[snowflake]"'
_DUCKDB_SOURCE = {"type": "duckdb", "path": ":memory:"}
_DBT_PROFILE_SOURCE = {"type": "dbt_profile", "profile": "shop"}


@pytest.fixture(autouse=True)
def isolated_profiles_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No developer ~/.dbt/profiles.yml or DBT_PROFILES_DIR may leak in."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)


def _project(
    tmp_path: Path,
    sources: dict[str, dict[str, Any]],
    *,
    dbt: bool = True,
    profiles: dict[str, Any] | None = None,
) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "dbt_charts.yml").write_text(yaml.safe_dump({"sources": sources}))
    if dbt:
        (root / "dbt_project.yml").write_text(
            yaml.safe_dump({"name": "shop", "version": "1.0", "profile": "shop"})
        )
    if profiles is not None:
        (root / "profiles.yml").write_text(yaml.safe_dump(profiles))
    return root


def _checks(report: DoctorReport, code: str) -> list[DoctorCheck]:
    return [c for c in report.checks if c.code == code]


def _never_called(*args: object, **kwargs: object) -> None:
    raise AssertionError("test_connection must not be called")


def test_missing_adapter_fails_that_source_and_names_the_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(
        tmp_path,
        {"warehouse": _SNOWFLAKE_SOURCE, "local": _DUCKDB_SOURCE},
        dbt=False,
    )
    real_import = doctor.import_adapter_module

    def fake_import(adapter_type: str) -> object:
        if adapter_type == "snowflake":
            raise AdapterNotInstalledError.from_code(
                ERR_ADAPTER_NOT_INSTALLED,
                adapter_type="snowflake",
                package="dbt-snowflake",
                install=_SNOWFLAKE_INSTALL,
            )
        return real_import(adapter_type)

    monkeypatch.setattr(doctor, "import_adapter_module", fake_import)

    report = run_doctor(root, False)

    by_source = {c.source: c for c in _checks(report, "adapter")}
    assert by_source["warehouse"].status == "fail"
    assert "dbt-snowflake" in by_source["warehouse"].message
    assert _SNOWFLAKE_INSTALL not in by_source["warehouse"].message
    assert by_source["warehouse"].hint == _SNOWFLAKE_INSTALL
    assert by_source["local"].status == "pass"
    assert report.success is False


def test_duckdb_only_project_passes_and_skips_connection_without_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor, "test_connection", _never_called)
    root = _project(tmp_path, {"local": _DUCKDB_SOURCE}, profiles=_DUCKDB_PROFILE)

    report = run_doctor(root, False)

    assert report.success is True
    assert [c.status for c in report.checks if c.code != "connection"] == ["pass"] * 4
    (connection,) = _checks(report, "connection")
    assert connection.status == "skip"
    assert connection.source is None
    assert connection.hint == "run `dct doctor --with-warehouse`"


def test_install_check_reports_version_method_and_python(tmp_path: Path) -> None:
    root = _project(tmp_path, {"local": _DUCKDB_SOURCE}, dbt=False)

    (install,) = _checks(run_doctor(root, False), "install")

    assert install.status == "pass"
    assert any(m in install.message for m in ("(uv tool)", "(pip)", "(editable)"))


def test_missing_profiles_yml_fails_profiles_and_dbt_profile_source(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        {"warehouse": _DBT_PROFILE_SOURCE, "local": _DUCKDB_SOURCE},
        profiles=None,
    )

    report = run_doctor(root, True)

    (profiles,) = _checks(report, "profiles")
    assert profiles.status == "fail"
    assert "profiles.yml" in profiles.message
    adapters = {c.source: c for c in _checks(report, "adapter")}
    assert adapters["warehouse"].status == "fail"
    assert adapters["local"].status == "pass"
    connections = {c.source: c for c in _checks(report, "connection")}
    assert connections["warehouse"].status == "skip"
    assert connections["local"].status == "pass"
    assert report.success is False


def test_dbt_profile_source_reports_the_concrete_adapter_type(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path, {"warehouse": _DBT_PROFILE_SOURCE}, profiles=_DUCKDB_PROFILE
    )

    (adapter,) = _checks(run_doctor(root, False), "adapter")

    assert adapter.source == "warehouse"
    assert adapter.status == "pass"
    assert "duckdb" in adapter.message


def test_unexpandable_dbt_profile_fails_adapter_with_the_error_text(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        {"warehouse": {"type": "dbt_profile", "profile": "nope"}},
        profiles=_DUCKDB_PROFILE,
    )

    report = run_doctor(root, True)

    (profiles,) = _checks(report, "profiles")
    assert profiles.status == "pass"
    (adapter,) = _checks(report, "adapter")
    assert adapter.status == "fail"
    assert "nope" in adapter.message
    (connection,) = _checks(report, "connection")
    assert (connection.source, connection.status) == ("warehouse", "skip")


def test_no_project_warns_and_skips_source_checks(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    report = run_doctor(empty, True)

    (project,) = _checks(report, "project")
    assert project.status == "warn"
    (profiles,) = _checks(report, "profiles")
    assert profiles.status == "skip"
    for code in ("adapter", "connection"):
        (check,) = _checks(report, code)
        assert (check.status, check.source) == ("skip", None)
        assert check.message == "no project found"
    assert report.success is True


def test_charts_config_without_dbt_project_skips_profiles(tmp_path: Path) -> None:
    root = _project(tmp_path, {"local": _DUCKDB_SOURCE}, dbt=False)

    report = run_doctor(root, False)

    (project,) = _checks(report, "project")
    assert project.status == "pass"
    (profiles,) = _checks(report, "profiles")
    assert (profiles.status, profiles.message) == ("skip", "no dbt project")


def test_unknown_source_type_fails_instead_of_raising(tmp_path: Path) -> None:
    root = _project(tmp_path, {"legacy": {"type": "oracle"}}, dbt=False)

    report = run_doctor(root, True)

    (adapter,) = _checks(report, "adapter")
    assert (adapter.source, adapter.status) == (None, "fail")
    assert "oracle" in adapter.message
    (connection,) = _checks(report, "connection")
    assert connection.status == "skip"
    assert report.success is False


@pytest.mark.parametrize(
    ("source", "bad_field"),
    [
        pytest.param({"type": "dbt_profile"}, "profile", id="missing-profile"),
        pytest.param(
            {"type": "duckdb", "path": ":memory:", "stray": 1},
            "stray",
            id="stray-key",
        ),
    ],
)
def test_schema_invalid_source_fails_instead_of_raising(
    tmp_path: Path, source: dict[str, Any], bad_field: str
) -> None:
    """The registry validates sources: on load, so a schema error surfaces
    there as one sourceless adapter fail naming the field, never a raise."""
    root = _project(tmp_path, {"broken": source}, dbt=False)

    report = run_doctor(root, False)

    (adapter,) = _checks(report, "adapter")
    assert (adapter.source, adapter.status) == (None, "fail")
    assert "broken" in adapter.message
    assert bad_field in adapter.message
    assert report.success is False


def test_database_type_without_a_dbt_adapter_runs_natively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor, "test_connection", _never_called)
    root = _project(tmp_path, {"legacy": {"type": "sqlite", "path": "x.db"}}, dbt=False)

    report = run_doctor(root, True)

    (adapter,) = _checks(report, "adapter")
    assert (adapter.source, adapter.status) == ("legacy", "skip")
    assert "runs natively" in adapter.message
    (connection,) = _checks(report, "connection")
    assert connection.status == "skip"
    assert report.success is True


def test_http_source_needs_no_adapter(tmp_path: Path) -> None:
    root = _project(
        tmp_path, {"api": {"type": "http", "url": "https://example.com"}}, dbt=False
    )

    (adapter,) = _checks(run_doctor(root, False), "adapter")

    assert (adapter.source, adapter.status) == ("api", "skip")


def test_file_source_needs_no_adapter_or_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor, "test_connection", _never_called)
    root = _project(
        tmp_path,
        {"files": {"type": "csv", "files": {"orders": "orders.csv"}}},
        dbt=False,
    )

    report = run_doctor(root, True)

    (adapter,) = _checks(report, "adapter")
    (connection,) = _checks(report, "connection")
    assert (adapter.source, adapter.status) == ("files", "skip")
    assert (connection.source, connection.status) == ("files", "skip")
    assert report.success is True


def test_with_warehouse_connects_each_source(tmp_path: Path) -> None:
    root = _project(tmp_path, {"local": _DUCKDB_SOURCE}, dbt=False)

    (connection,) = _checks(run_doctor(root, True), "connection")

    assert (connection.source, connection.status) == ("local", "pass")


def test_with_warehouse_failure_carries_display_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def rejected(config: ResolvedSourceConfig) -> tuple[bool, WarehouseProbeError]:
        return False, WarehouseAuthError("password rejected")

    monkeypatch.setattr(doctor, "test_connection", rejected)
    root = _project(tmp_path, {"local": _DUCKDB_SOURCE}, dbt=False)

    report = run_doctor(root, True)

    (connection,) = _checks(report, "connection")
    assert (connection.source, connection.status) == ("local", "fail")
    assert connection.message == "password rejected"
    assert report.success is False


def test_connection_skips_source_whose_adapter_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(adapter_type: str) -> object:
        raise AdapterNotInstalledError.from_code(
            ERR_ADAPTER_NOT_INSTALLED,
            adapter_type=adapter_type,
            package="dbt-duckdb",
            install="pip install dbt-charts",
        )

    monkeypatch.setattr(doctor, "import_adapter_module", missing)
    monkeypatch.setattr(doctor, "test_connection", _never_called)
    root = _project(tmp_path, {"local": _DUCKDB_SOURCE}, dbt=False)

    report = run_doctor(root, True)

    (connection,) = _checks(report, "connection")
    assert (connection.source, connection.status) == ("local", "skip")
    assert "adapter" in connection.message


def _install_message(
    monkeypatch: pytest.MonkeyPatch, *, editable: bool, uv_tool: bool, tmp_path: Path
) -> str:
    real = doctor.version_info.collect()
    monkeypatch.setattr(
        doctor.version_info,
        "collect",
        lambda: dataclasses.replace(real, editable=editable),
    )
    monkeypatch.setattr(doctor, "is_uv_tool_install", lambda: uv_tool)
    root = _project(tmp_path, {"local": _DUCKDB_SOURCE}, dbt=False)
    (install,) = _checks(run_doctor(root, False), "install")
    return install.message


@pytest.mark.parametrize(
    ("editable", "uv_tool", "method"),
    [
        (True, True, "editable"),
        (True, False, "editable"),
        (False, True, "uv tool"),
        (False, False, "pip"),
    ],
)
def test_install_method(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    editable: bool,
    uv_tool: bool,
    method: str,
) -> None:
    message = _install_message(
        monkeypatch, editable=editable, uv_tool=uv_tool, tmp_path=tmp_path
    )

    assert f"({method})," in message
    assert sum(m in message for m in ("(editable)", "(uv tool)", "(pip)")) == 1


def test_dbt_profile_source_with_profiles_dir_is_checked_against_that_dir(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        {"warehouse": {**_DBT_PROFILE_SOURCE, "profiles_dir": "conf"}},
    )
    (root / "conf").mkdir()
    (root / "conf" / "profiles.yml").write_text(yaml.safe_dump(_DUCKDB_PROFILE))

    report = run_doctor(root, False)

    (profiles,) = _checks(report, "profiles")
    assert (profiles.source, profiles.status) == ("warehouse", "pass")
    assert profiles.message.endswith("conf/profiles.yml")
    assert report.success is True


def test_profiles_dir_without_profiles_yml_fails_that_source(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        {"warehouse": {**_DBT_PROFILE_SOURCE, "profiles_dir": "conf"}},
        profiles=_DUCKDB_PROFILE,
    )
    (root / "conf").mkdir()

    profiles = _checks(run_doctor(root, False), "profiles")

    by_source = {c.source: c.status for c in profiles}
    assert by_source == {"warehouse": "fail"}


def test_global_profiles_check_stays_when_some_source_has_no_profiles_dir(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        {
            "a": {**_DBT_PROFILE_SOURCE, "profiles_dir": "conf"},
            "b": _DBT_PROFILE_SOURCE,
        },
        profiles=_DUCKDB_PROFILE,
    )
    (root / "conf").mkdir()
    (root / "conf" / "profiles.yml").write_text(yaml.safe_dump(_DUCKDB_PROFILE))

    profiles = _checks(run_doctor(root, False), "profiles")

    assert {(c.source, c.status) for c in profiles} == {(None, "pass"), ("a", "pass")}


def test_adapter_with_a_broken_dependency_fails_naming_the_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(adapter_type: str) -> object:
        raise ModuleNotFoundError("No module named 'snowflake'", name="snowflake")

    monkeypatch.setattr(doctor, "import_adapter_module", broken)
    root = _project(tmp_path, {"local": _DUCKDB_SOURCE}, dbt=False)

    report = run_doctor(root, True)

    (adapter,) = _checks(report, "adapter")
    assert (adapter.source, adapter.status) == ("local", "fail")
    assert "'snowflake'" in adapter.message
    assert report.success is False


def test_non_mapping_sources_section_fails_instead_of_raising(
    tmp_path: Path,
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "dbt_charts.yml").write_text("sources: oops\n")

    report = run_doctor(root, False)

    (adapter,) = _checks(report, "adapter")
    assert (adapter.source, adapter.status) == (None, "fail")
    assert "mapping" in adapter.message
    assert report.success is False
