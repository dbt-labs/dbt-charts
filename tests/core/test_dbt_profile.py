"""Reading a dbt project's profiles.yml target without connecting."""

from pathlib import Path
from typing import Any

import pytest


class TestReadTargetDict:
    """L1: available-profile filter uses exact 'config' match, not startswith."""

    def test_profile_named_config_prefix_is_listed_as_available(
        self, tmp_path: Path
    ) -> None:
        """A real profile named 'config_dev' must appear in the error's available list.

        The old `startswith('config')` filter hid such profiles — exact != 'config'
        is the fix.
        """
        from dbt_charts.core.execute.backends.dbt_profile import _read_target_dict

        (tmp_path / "profiles.yml").write_text(
            "config_dev:\n"
            "  target: dev\n"
            "  outputs:\n"
            "    dev:\n"
            "      type: duckdb\n"
            "      path: ':memory:'\n"
        )

        with pytest.raises(ValueError, match="config_dev"):
            _read_target_dict(tmp_path, "nonexistent", "dev")


class TestReadTargetDictWithoutProject:
    """No dbt project: nothing to anchor relative paths to, no project-local step."""

    def test_relative_path_stays_relative_and_profile_comes_from_env_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.execute.backends.dbt_profile import _read_target_dict

        (tmp_path / "profiles.yml").write_text(
            "demo:\n"
            "  target: dev\n"
            "  outputs:\n"
            "    dev:\n"
            "      type: duckdb\n"
            "      path: warehouse.duckdb\n"
        )
        monkeypatch.setenv("DBT_PROFILES_DIR", str(tmp_path))

        target = _read_target_dict(None, "demo", "dev")
        assert target["path"] == "warehouse.duckdb"

    def test_missing_profile_error_names_the_profiles_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.execute.backends.dbt_profile import _read_target_dict

        (tmp_path / "profiles.yml").write_text(
            "demo:\n  outputs:\n    dev:\n      type: duckdb\n"
        )
        monkeypatch.setenv("DBT_PROFILES_DIR", str(tmp_path))

        with pytest.raises(ValueError, match="nonexistent") as exc_info:
            _read_target_dict(None, "nonexistent", "dev")
        assert str(tmp_path / "profiles.yml") in str(exc_info.value)


class TestMissingAdapterPackage:
    """A profile whose adapter isn't installed names the extra, not dbt's internals."""

    @staticmethod
    def _write_profile(path: Path, adapter_type: str) -> None:
        (path / "profiles.yml").write_text(
            "demo:\n"
            "  target: dev\n"
            "  outputs:\n"
            "    dev:\n"
            f"      type: {adapter_type}\n"
            "      account: abc\n"
            "      user: u\n"
            "      database: d\n"
            "      warehouse: w\n"
            "      schema: s\n"
        )

    @staticmethod
    def _plugin_not_found(typename: str) -> Any:
        from dbt_common.exceptions import DbtRuntimeError

        raise DbtRuntimeError(f"Could not find adapter type {typename}!")

    def test_missing_mapped_adapter_raises_coded_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys

        from dbt_charts.core.diagnostics import ERR_ADAPTER_NOT_INSTALLED
        from dbt_charts.core.execute.backends.dbt_adapter_factory import (
            AdapterNotInstalledError,
        )
        from dbt_charts.core.execute.backends.dbt_profile import _read_target_dict

        self._write_profile(tmp_path, "snowflake")
        monkeypatch.setitem(sys.modules, "dbt.adapters.snowflake", None)
        monkeypatch.setattr("dbt.adapters.factory.load_plugin", self._plugin_not_found)

        with pytest.raises(AdapterNotInstalledError) as exc_info:
            _read_target_dict(tmp_path, "demo", "dev")
        assert exc_info.value.code is ERR_ADAPTER_NOT_INSTALLED
        assert "dbt-charts[snowflake]" in str(exc_info.value)

    def test_unmapped_adapter_keeps_dbts_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A type dbt Charts has no mapping for keeps dbt's own text."""
        from dbt_charts.core.execute.backends.dbt_profile import _read_target_dict

        self._write_profile(tmp_path, "sqlserver")
        monkeypatch.setattr("dbt.adapters.factory.load_plugin", self._plugin_not_found)

        with pytest.raises(ValueError, match="Could not find adapter type sqlserver"):
            _read_target_dict(tmp_path, "demo", "dev")


class TestReadProfilesYml:
    """_read_profiles_yml must reject non-mapping content, not silently treat as {}."""

    def test_empty_profiles_yml_raises_value_error(self, tmp_path: Path) -> None:
        """An empty profiles.yml (safe_load → None) must raise ValueError, not return {}."""
        from dbt_charts.core.execute.backends.dbt_profile import _read_profiles_yml

        (tmp_path / "profiles.yml").write_text("")
        with pytest.raises(ValueError, match="did not parse to a mapping"):
            _read_profiles_yml(tmp_path)

    def test_non_mapping_profiles_yml_raises_value_error(self, tmp_path: Path) -> None:
        """A profiles.yml containing only a list raises ValueError."""
        from dbt_charts.core.execute.backends.dbt_profile import _read_profiles_yml

        (tmp_path / "profiles.yml").write_text("- just\n- a\n- list\n")
        with pytest.raises(ValueError, match="did not parse to a mapping"):
            _read_profiles_yml(tmp_path)

    def test_valid_profiles_yml_returns_dict(self, tmp_path: Path) -> None:
        """A well-formed profiles.yml returns a dict."""
        from dbt_charts.core.execute.backends.dbt_profile import _read_profiles_yml

        (tmp_path / "profiles.yml").write_text(
            "myprofile:\n  outputs:\n    dev:\n      type: duckdb\n"
        )
        path, result = _read_profiles_yml(tmp_path)
        assert path == tmp_path / "profiles.yml"
        assert "myprofile" in result
