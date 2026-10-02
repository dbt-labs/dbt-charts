"""init_project warns when the dbt profile's warehouse adapter is not installed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dbt_charts.agent_api.init import init_project


def _dbt_project(root: Path, adapter_type: str | None) -> None:
    (root / "dbt_project.yml").write_text("name: demo\nprofile: demo\n")
    if adapter_type is not None:
        (root / "profiles.yml").write_text(
            f"demo:\n  target: dev\n  outputs:\n    dev:\n      type: {adapter_type}\n"
        )


@pytest.fixture(autouse=True)
def profiles_in_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    monkeypatch.setenv("HOME", "/nonexistent")


def test_missing_profile_adapter_adds_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dbt_project(tmp_path, "snowflake")
    monkeypatch.setitem(sys.modules, "dbt.adapters.snowflake", None)

    result = init_project(tmp_path)

    assert any("dbt-charts[snowflake]" in hint for hint in result.hints)


@pytest.mark.parametrize(
    "adapter_type",
    [
        pytest.param("duckdb", id="installed"),
        pytest.param(None, id="no-profiles-yml"),
        pytest.param("sqlserver", id="unmapped-type"),
    ],
)
def test_no_adapter_hint(tmp_path: Path, adapter_type: str | None) -> None:
    _dbt_project(tmp_path, adapter_type)

    result = init_project(tmp_path)

    assert not any("dbt-charts" in hint for hint in result.hints)


@pytest.mark.parametrize(
    "profiles_yml",
    [
        pytest.param("demo:\n  target: dev\n  outputs:\n", id="empty-outputs"),
        pytest.param("- demo\n", id="top-level-list"),
        pytest.param(
            "demo:\n  target: dev\n  outputs:\n    dev:\n      type: 123\n",
            id="non-string-type",
        ),
    ],
)
def test_malformed_profile_adds_no_hint_and_does_not_crash(
    tmp_path: Path, profiles_yml: str
) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: demo\nprofile: demo\n")
    (tmp_path / "profiles.yml").write_text(profiles_yml)

    result = init_project(tmp_path)

    assert not any("dbt-charts" in hint for hint in result.hints)


def test_non_mapping_dbt_project_adds_no_hint_and_does_not_crash(
    tmp_path: Path,
) -> None:
    (tmp_path / "dbt_project.yml").write_text("- demo\n")

    result = init_project(tmp_path)

    assert not any("dbt-charts" in hint for hint in result.hints)
