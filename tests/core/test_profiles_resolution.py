"""Tests for centralized profiles.yml resolution helper (resolve_profiles_path).

Covers:
- profiles_dir field on DbtProfileSourceConfig (explicit subdir)
- DBT_PROFILES_DIR env var fallback
- {project_dir}/profiles.yml fallback
- ~/.dbt/profiles.yml fallback
- Clear error when none has profiles.yml
- infer_dialect_from_dbt uses the centralized helper via DBT_PROFILES_DIR
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# resolve_profiles_path — centralized helper
# ---------------------------------------------------------------------------


class TestResolveProfilesPath:
    """resolve_profiles_path honors the documented resolution order:
    profiles_dir → DBT_PROFILES_DIR → project_dir → ~/.dbt
    """

    def test_profiles_dir_wins_when_set(self, tmp_path: Path) -> None:
        """Explicit profiles_dir is checked first, even if project_dir also has profiles.yml."""
        from dbt_charts.core.project_roots import resolve_profiles_path

        subdir = tmp_path / "services" / "dbt"
        subdir.mkdir(parents=True)
        (subdir / "profiles.yml").write_text("annote: {}\n")

        # Also put one in project_dir to verify subdir wins
        (tmp_path / "profiles.yml").write_text("other: {}\n")

        result = resolve_profiles_path(project_dir=tmp_path, profiles_dir=subdir)
        assert result == subdir / "profiles.yml"

    def test_profiles_dir_missing_profiles_raises(self, tmp_path: Path) -> None:
        """profiles_dir set but no profiles.yml in it → clear error, no fallback."""
        from dbt_charts.core.project_roots import resolve_profiles_path

        subdir = tmp_path / "services" / "dbt"
        subdir.mkdir(parents=True)
        # No profiles.yml in subdir — should raise, not silently fall back.

        with pytest.raises(FileNotFoundError, match="profiles.yml"):
            resolve_profiles_path(project_dir=tmp_path, profiles_dir=subdir)

    def test_dbt_profiles_dir_env_used_when_profiles_dir_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DBT_PROFILES_DIR env var is honored when profiles_dir field is not set."""
        from dbt_charts.core.project_roots import resolve_profiles_path

        env_dir = tmp_path / "env_dbt"
        env_dir.mkdir()
        (env_dir / "profiles.yml").write_text("myprofile: {}\n")
        monkeypatch.setenv("DBT_PROFILES_DIR", str(env_dir))

        result = resolve_profiles_path(project_dir=tmp_path, profiles_dir=None)
        assert result == env_dir / "profiles.yml"

    def test_dbt_profiles_dir_env_missing_profiles_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DBT_PROFILES_DIR points at a dir without profiles.yml → error, no fallback."""
        from dbt_charts.core.project_roots import resolve_profiles_path

        env_dir = tmp_path / "empty_env"
        env_dir.mkdir()
        monkeypatch.setenv("DBT_PROFILES_DIR", str(env_dir))

        with pytest.raises(FileNotFoundError, match="profiles.yml"):
            resolve_profiles_path(project_dir=tmp_path, profiles_dir=None)

    def test_project_dir_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Falls back to {project_dir}/profiles.yml when profiles_dir and env unset."""
        from dbt_charts.core.project_roots import resolve_profiles_path

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        (tmp_path / "profiles.yml").write_text("myprofile: {}\n")

        result = resolve_profiles_path(project_dir=tmp_path, profiles_dir=None)
        assert result == tmp_path / "profiles.yml"

    def test_home_dbt_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Falls back to ~/.dbt/profiles.yml as last resort."""
        from dbt_charts.core.project_roots import resolve_profiles_path

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        # No profiles.yml in project_dir
        fake_home = tmp_path / "fakehome"
        (fake_home / ".dbt").mkdir(parents=True)
        (fake_home / ".dbt" / "profiles.yml").write_text("myprofile: {}\n")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        result = resolve_profiles_path(project_dir=tmp_path, profiles_dir=None)
        assert result == fake_home / ".dbt" / "profiles.yml"

    def test_raises_when_none_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raises FileNotFoundError with a clear message when no profiles.yml exists."""
        from dbt_charts.core.project_roots import resolve_profiles_path

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        fake_home = tmp_path / "emptyhome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        with pytest.raises(FileNotFoundError, match="profiles.yml"):
            resolve_profiles_path(project_dir=tmp_path, profiles_dir=None)


# ---------------------------------------------------------------------------
# DbtProfileSourceConfig.profiles_dir field
# ---------------------------------------------------------------------------


class TestDbtProfileSourceConfigProfilesDir:
    """profiles_dir is a new optional field on DbtProfileSourceConfig."""

    def test_profiles_dir_accepted(self) -> None:
        """DbtProfileSourceConfig accepts profiles_dir as a string."""
        from dbt_charts.core.compile.models.source import DbtProfileSourceConfig

        cfg = DbtProfileSourceConfig(
            type="dbt_profile",
            profile="annote",
            profiles_dir="services/dbt",
        )
        assert cfg.profiles_dir == "services/dbt"

    def test_profiles_dir_defaults_none(self) -> None:
        """profiles_dir defaults to None when not provided."""
        from dbt_charts.core.compile.models.source import DbtProfileSourceConfig

        cfg = DbtProfileSourceConfig(type="dbt_profile", profile="annote")
        assert cfg.profiles_dir is None

    def test_extra_field_still_rejected(self) -> None:
        """Unknown fields are still rejected (extra=forbid)."""
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.source import DbtProfileSourceConfig

        with pytest.raises(ValidationError):
            DbtProfileSourceConfig(
                type="dbt_profile", profile="annote", not_a_field="oops"
            )


# ---------------------------------------------------------------------------
# infer_dialect_from_dbt uses centralized resolution
# ---------------------------------------------------------------------------


class TestInferDialectFromDbtSubdir:
    """infer_dialect_from_dbt can find profiles.yml in a subdir via DBT_PROFILES_DIR."""

    def test_dbt_profiles_dir_env_in_infer_dialect(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DBT_PROFILES_DIR env is honored by infer_dialect_from_dbt."""
        from dbt_charts.core.project_roots import infer_dialect_from_dbt

        # dbt project in project root, profiles.yml in subdir
        (tmp_path / "dbt_project.yml").write_text(
            yaml.dump({"name": "annote", "profile": "annote"})
        )
        subdir = tmp_path / "services" / "dbt"
        subdir.mkdir(parents=True)
        (subdir / "profiles.yml").write_text(
            yaml.dump(
                {
                    "annote": {
                        "target": "dev",
                        "outputs": {"dev": {"type": "snowflake"}},
                    }
                }
            )
        )
        monkeypatch.setenv("DBT_PROFILES_DIR", str(subdir))

        assert infer_dialect_from_dbt(tmp_path) == "snowflake"


# ---------------------------------------------------------------------------
# DefaultSourceResolver._expand_dbt_profile — relative profiles_dir wired end-to-end
# ---------------------------------------------------------------------------


class TestExpandDbtProfileSource:
    """DefaultSourceResolver._expand_dbt_profile resolves profiles_dir against dbt_project_path."""

    def _make_source_config(
        self,
        profile: str,
        profiles_dir: str | None = None,
        target: str | None = None,
    ):  # type: ignore[return]
        from dbt_charts.core.compile.models.source import DbtProfileSourceConfig

        return DbtProfileSourceConfig(
            type="dbt_profile",
            profile=profile,
            target=target,
            profiles_dir=profiles_dir,
        )

    def _make_dbt_context(self, dbt_project_path: Path):  # type: ignore[return]
        from dbt_charts.core.execute.source_resolver import DbtContext

        return DbtContext(dbt_project_path=dbt_project_path)

    def _resolver(self):  # type: ignore[return]
        from dbt_charts.core.execute.source_resolver import DefaultSourceResolver

        return DefaultSourceResolver()

    def test_relative_profiles_dir_resolved_against_dbt_project_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """profiles_dir='services/dbt' relative to dbt_project_path finds profiles.yml."""
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        subdir = tmp_path / "services" / "dbt"
        subdir.mkdir(parents=True)
        (subdir / "profiles.yml").write_text(
            yaml.dump(
                {
                    "annote": {
                        "target": "dev",
                        "outputs": {"dev": {"type": "duckdb", "path": ":memory:"}},
                    }
                }
            )
        )

        cfg = self._make_source_config("annote", profiles_dir="services/dbt")
        result = self._resolver()._expand_dbt_profile(
            cfg, self._make_dbt_context(tmp_path)
        )

        assert result.type == "duckdb"
        assert str(result.path) == ":memory:"  # type: ignore[union-attr]

    def test_missing_profile_raises_execution_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Unknown profile name → ExecutionError (caught by registry as QueryResult.error)."""
        from dbt_charts.core.diagnostics.execution import ExecutionError

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        (tmp_path / "profiles.yml").write_text(
            yaml.dump(
                {
                    "other_profile": {
                        "target": "dev",
                        "outputs": {"dev": {"type": "duckdb", "path": ":memory:"}},
                    }
                }
            )
        )
        cfg = self._make_source_config("missing_profile")
        with pytest.raises(ExecutionError, match="missing_profile"):
            self._resolver()._expand_dbt_profile(cfg, self._make_dbt_context(tmp_path))

    def test_profiles_dir_missing_profiles_yml_raises_execution_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """profiles_dir points at dir without profiles.yml → ExecutionError."""
        from dbt_charts.core.diagnostics.execution import ExecutionError

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        empty_subdir = tmp_path / "empty"
        empty_subdir.mkdir()
        cfg = self._make_source_config("annote", profiles_dir="empty")
        with pytest.raises(ExecutionError, match="profiles.yml"):
            self._resolver()._expand_dbt_profile(cfg, self._make_dbt_context(tmp_path))

    def test_duckdb_relative_path_resolved_against_dbt_project_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Relative DuckDB path: resolved against dbt_project_path (the dbt project dir).

        dbt resolves a relative path: against the directory it is invoked from —
        the dbt project root (where dbt_project.yml lives), NOT the directory that
        contains profiles.yml. When profiles_dir points at a subdir (services/dbt/),
        a relative path: ./warehouse.duckdb must resolve to dbt_project_path/warehouse.duckdb
        so dbt charts opens the same file a real `dbt run` would have written.
        """
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        subdir = tmp_path / "services" / "dbt"
        subdir.mkdir(parents=True)
        (subdir / "profiles.yml").write_text(
            yaml.dump(
                {
                    "myproj": {
                        "target": "dev",
                        "outputs": {
                            "dev": {"type": "duckdb", "path": "./warehouse.duckdb"}
                        },
                    }
                }
            )
        )
        # profiles_dir points at services/dbt/; but relative DuckDB path is resolved
        # against dbt_project_path (where dbt_project.yml lives), matching dbt behavior.
        cfg = self._make_source_config("myproj", profiles_dir="services/dbt")
        result = self._resolver()._expand_dbt_profile(
            cfg, self._make_dbt_context(tmp_path)
        )

        # Path resolved against dbt_project_path, NOT profiles_path.parent (services/dbt/)
        assert result.type == "duckdb"
        assert result.path == str((tmp_path / "warehouse.duckdb").resolve())  # type: ignore[union-attr]

    def test_profile_declared_default_target_honored(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When source target field is unset, the profile's declared default target is used.

        A profile with target: prod and no dev output must resolve prod, not dev.
        This matches the documented field contract ("defaults to the profile's default
        target") and infer_dialect_from_dbt's own resolution logic.
        """
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        (tmp_path / "profiles.yml").write_text(
            yaml.dump(
                {
                    "myproj": {
                        "target": "prod",
                        "outputs": {
                            "prod": {
                                "type": "snowflake",
                                "account": "xy12345",
                                "user": "u",
                                "database": "db",
                                "schema": "public",
                                "warehouse": "wh",
                            }
                        },
                    }
                }
            )
        )
        # No explicit target in source config — profile declares "target: prod"
        cfg = self._make_source_config("myproj")
        result = self._resolver()._expand_dbt_profile(
            cfg, self._make_dbt_context(tmp_path)
        )

        assert result.type == "snowflake"
        assert result.account == "xy12345"  # type: ignore[union-attr]


def _write_duckdb_profile(directory: Path, profile: str, db_path: str) -> None:
    (directory / "profiles.yml").write_text(
        yaml.dump(
            {
                profile: {
                    "target": "dev",
                    "outputs": {"dev": {"type": "duckdb", "path": db_path}},
                }
            }
        )
    )


def _expand(
    cfg_profile: str | None, project_path: Path | None, **cfg_fields: str
) -> dict[str, Any]:
    from dbt_charts.core.compile.models.source import DbtProfileSourceConfig
    from dbt_charts.core.execute.source_resolver import (
        DbtContext,
        DefaultSourceResolver,
    )

    cfg = DbtProfileSourceConfig(type="dbt_profile", profile=cfg_profile, **cfg_fields)
    resolved = DefaultSourceResolver()._expand_dbt_profile(
        cfg, DbtContext(dbt_project_path=project_path)
    )
    return resolved.model_dump(by_alias=True)


class TestDbtProfileNameLadder:
    """profile: is optional; the name comes from the first rung that has one."""

    def test_profile_field_is_optional(self) -> None:
        from dbt_charts.core.compile.models.source import DbtProfileSourceConfig

        assert DbtProfileSourceConfig(type="dbt_profile").profile is None

    def test_name_read_from_dbt_project_yml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DBT_PROFILE", raising=False)
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        (tmp_path / "dbt_project.yml").write_text(
            yaml.dump({"name": "p", "profile": "from_project"})
        )
        _write_duckdb_profile(tmp_path, "from_project", ":memory:")

        assert _expand(None, tmp_path)["type"] == "duckdb"

    def test_env_var_beats_dbt_project_yml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DBT_PROFILE", "from_env")
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        (tmp_path / "dbt_project.yml").write_text(
            yaml.dump({"name": "p", "profile": "from_project"})
        )
        _write_duckdb_profile(tmp_path, "from_env", "env.duckdb")

        assert _expand(None, tmp_path)["path"] == str(
            (tmp_path / "env.duckdb").resolve()
        )

    def test_explicit_profile_beats_env_and_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DBT_PROFILE", "from_env")
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        (tmp_path / "dbt_project.yml").write_text(
            yaml.dump({"name": "p", "profile": "from_project"})
        )
        _write_duckdb_profile(tmp_path, "explicit", "explicit.duckdb")

        result = _expand("explicit", tmp_path)
        assert result["path"] == str((tmp_path / "explicit.duckdb").resolve())

    def test_no_name_anywhere_names_all_three_places(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.diagnostics.execution import ExecutionError

        monkeypatch.delenv("DBT_PROFILE", raising=False)
        (tmp_path / "dbt_project.yml").write_text(yaml.dump({"name": "p"}))

        with pytest.raises(ExecutionError) as exc_info:
            _expand(None, tmp_path)
        message = str(exc_info.value)
        assert "profile:" in message
        assert "DBT_PROFILE" in message
        assert "dbt_project.yml" in message

    def test_no_name_without_project_names_all_three_places(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.diagnostics.execution import ExecutionError

        monkeypatch.delenv("DBT_PROFILE", raising=False)

        with pytest.raises(ExecutionError, match="DBT_PROFILE") as exc_info:
            _expand(None, None)
        assert "dbt_project.yml" in str(exc_info.value)


class TestDbtProfileWithoutProject:
    """A dbt_profile source expands with no linked dbt project."""

    def test_reads_global_dbt_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".dbt").mkdir()
        _write_duckdb_profile(tmp_path / ".dbt", "global_profile", ":memory:")

        assert _expand("global_profile", None)["type"] == "duckdb"

    def test_reads_dbt_profiles_dir_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_dir = tmp_path / "env_dir"
        env_dir.mkdir()
        _write_duckdb_profile(env_dir, "env_profile", ":memory:")
        monkeypatch.setenv("DBT_PROFILES_DIR", str(env_dir))

        assert _expand("env_profile", None)["type"] == "duckdb"

    def test_relative_duckdb_path_is_left_as_authored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_dir = tmp_path / "env_dir"
        env_dir.mkdir()
        _write_duckdb_profile(env_dir, "env_profile", "warehouse.duckdb")
        monkeypatch.setenv("DBT_PROFILES_DIR", str(env_dir))

        assert _expand("env_profile", None)["path"] == "warehouse.duckdb"

    def test_cwd_profiles_yml_is_not_a_project_local_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.diagnostics.execution import ExecutionError

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        _write_duckdb_profile(tmp_path, "local_only", ":memory:")
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ExecutionError, match="No profiles.yml found"):
            _expand("local_only", None)

    def test_profiles_dir_field_needs_a_project_to_be_relative_to(
        self, tmp_path: Path
    ) -> None:
        from dbt_charts.core.diagnostics.execution import ExecutionError

        with pytest.raises(ExecutionError, match="profiles_dir"):
            _expand("anything", None, profiles_dir="services/dbt")

    def test_resolve_profiles_path_without_project_skips_project_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.project_roots import resolve_profiles_path

        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".dbt").mkdir()
        (tmp_path / ".dbt" / "profiles.yml").write_text("x: 1\n")

        assert resolve_profiles_path(None, profiles_dir=None) == (
            tmp_path / ".dbt" / "profiles.yml"
        )


class TestOneProfileNameLadder:
    """Every reader of the profile name follows the same precedence."""

    def test_env_var_wins_in_every_reader(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter
        from dbt_charts.core.project_roots import infer_dialect_from_dbt

        monkeypatch.setenv("DBT_PROFILE", "profile_b")
        monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
        (tmp_path / "dbt_project.yml").write_text(
            yaml.dump({"name": "p", "profile": "profile_a"})
        )
        (tmp_path / "profiles.yml").write_text(
            yaml.dump(
                {
                    "profile_a": {
                        "target": "dev",
                        "outputs": {"dev": {"type": "snowflake"}},
                    },
                    "profile_b": {
                        "target": "dev",
                        "outputs": {"dev": {"type": "duckdb", "path": "b.db"}},
                    },
                }
            )
        )
        adapter = DbtAdapter(
            project=FilesystemProject(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
        )

        assert adapter.resolve_target_type() == "duckdb"
        assert infer_dialect_from_dbt(tmp_path) == "duckdb"
        assert _expand(None, tmp_path)["path"] == str((tmp_path / "b.db").resolve())

    def test_adapter_without_any_name_names_all_three_places(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.cli.filesystem_project import FilesystemProject
        from dbt_charts.core.execute.adapters.dbt_adapter import DbtAdapter

        monkeypatch.delenv("DBT_PROFILE", raising=False)
        adapter = DbtAdapter(
            project=FilesystemProject(tmp_path),
            dbt_project_path=tmp_path,
            target_name="dev",
        )

        with pytest.raises(ValueError, match="DBT_PROFILE"):
            adapter.resolve_target_type()


class TestReadDbtProjectProfile:
    @pytest.mark.parametrize("value", ["[a]", "123", "{a: b}"])
    def test_non_string_profile_raises(self, tmp_path: Path, value: str) -> None:
        from dbt_charts.core.project_roots import read_dbt_project_profile

        (tmp_path / "dbt_project.yml").write_text(f"name: p\nprofile: {value}\n")
        with pytest.raises(ValueError, match="profile"):
            read_dbt_project_profile(tmp_path)

    def test_invalid_yaml_raises_value_error(self, tmp_path: Path) -> None:
        from dbt_charts.core.project_roots import read_dbt_project_profile

        (tmp_path / "dbt_project.yml").write_text("name: [unclosed\n")
        with pytest.raises(ValueError, match="dbt_project.yml"):
            read_dbt_project_profile(tmp_path)

    def test_returns_profile_key(self, tmp_path: Path) -> None:
        from dbt_charts.core.project_roots import read_dbt_project_profile

        (tmp_path / "dbt_project.yml").write_text("name: p\nprofile: warehouse\n")
        assert read_dbt_project_profile(tmp_path) == "warehouse"

    def test_none_without_file_or_key(self, tmp_path: Path) -> None:
        from dbt_charts.core.project_roots import read_dbt_project_profile

        assert read_dbt_project_profile(tmp_path) is None
        (tmp_path / "dbt_project.yml").write_text("name: p\n")
        assert read_dbt_project_profile(tmp_path) is None
