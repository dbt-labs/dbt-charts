"""Tests for one-command serve bootstrap: project root discovery, dialect inference,
DBT_TARGET support, and dct init dbt_charts.yml creation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dbt_charts.agent_api.init import init_project as init_command
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.project_roots import find_dct_root

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dbt_repo(tmp_path: Path) -> Path:
    """Minimal dbt project with profiles.yml."""
    (tmp_path / "dbt_project.yml").write_text(
        yaml.dump({"name": "test_project", "profile": "test_profile"})
    )
    (tmp_path / "profiles.yml").write_text(
        yaml.dump(
            {
                "test_profile": {
                    "target": "dev",
                    "outputs": {
                        "dev": {
                            "type": "bigquery",
                            "method": "oauth",
                            "project": "my-gcp",
                        },
                        "prod": {
                            "type": "bigquery",
                            "method": "oauth",
                            "project": "my-gcp",
                        },
                    },
                }
            }
        )
    )
    return tmp_path


@pytest.fixture
def dbt_repo_duckdb(tmp_path: Path) -> Path:
    """dbt project using duckdb adapter."""
    (tmp_path / "dbt_project.yml").write_text(
        yaml.dump({"name": "duck_project", "profile": "duck_profile"})
    )
    (tmp_path / "profiles.yml").write_text(
        yaml.dump(
            {
                "duck_profile": {
                    "target": "dev",
                    "outputs": {
                        "dev": {"type": "duckdb", "path": "data/dev.duckdb"},
                    },
                }
            }
        )
    )
    return tmp_path


# ---------------------------------------------------------------------------
# find_dct_root
# ---------------------------------------------------------------------------


class TestFindDctRoot:
    def test_finds_dbt_project_from_cwd(self, dbt_repo: Path) -> None:
        """From the dbt repo root, find_dct_root returns that directory."""
        assert find_dct_root(dbt_repo) == dbt_repo

    def test_finds_dbt_project_from_subdir(self, dbt_repo: Path) -> None:
        """From a subdirectory, walks up to find dbt_project.yml."""
        subdir = dbt_repo / "models" / "staging"
        subdir.mkdir(parents=True)
        assert find_dct_root(subdir) == dbt_repo

    def test_finds_dbt_charts_yml(self, tmp_path: Path) -> None:
        """Finds project root via dbt_charts.yml (no dbt)."""
        (tmp_path / "dbt_charts.yml").write_text("# config\n")
        subdir = tmp_path / "charts"
        subdir.mkdir()
        assert find_dct_root(subdir) == tmp_path

    def test_finds_dir_with_both_markers(self, dbt_repo: Path) -> None:
        """When both markers exist in the same directory, returns that directory."""
        (dbt_repo / "dbt_charts.yml").write_text("# config\n")
        assert find_dct_root(dbt_repo) == dbt_repo

    def test_returns_none_when_nothing_found(self, tmp_path: Path) -> None:
        """When no marker files exist, returns None (caller supplies the fallback)."""
        empty = tmp_path / "empty"
        empty.mkdir()
        assert find_dct_root(empty) is None


# ---------------------------------------------------------------------------
# dct init — dbt_charts.yml creation
# ---------------------------------------------------------------------------


class TestInitDbtChartsYml:
    def test_creates_dbt_charts_yml(self, dbt_repo: Path) -> None:
        result = init_command(project_dir=dbt_repo)
        config_path = dbt_repo / "dbt_charts.yml"
        assert config_path.exists()
        assert Path("dbt_charts.yml") in result.created_files

    def test_does_not_overwrite_existing(self, dbt_repo: Path) -> None:
        (dbt_repo / "dbt_charts.yml").write_text("custom: true\n")
        result = init_command(project_dir=dbt_repo)
        assert (dbt_repo / "dbt_charts.yml").read_text() == "custom: true\n"
        assert Path("dbt_charts.yml") in result.skipped_files

    def test_force_overwrites_dbt_charts_yml(self, dbt_repo: Path) -> None:
        (dbt_repo / "dbt_charts.yml").write_text("custom: true\n")
        result = init_command(project_dir=dbt_repo, force=True)
        assert (dbt_repo / "dbt_charts.yml").read_text() != "custom: true\n"
        # Force-overwritten files report as refreshed (matches AGENTS.md branch).
        assert Path("dbt_charts.yml") in result.refreshed_files
        assert Path("dbt_charts.yml") not in result.created_files

    def test_dbt_charts_yml_is_valid_yaml(self, dbt_repo: Path) -> None:
        init_command(project_dir=dbt_repo)
        content = (dbt_repo / "dbt_charts.yml").read_text()
        # Should parse as valid YAML (comments-only parses to None, that's fine)
        parsed = yaml.safe_load(content)
        assert parsed is None or isinstance(parsed, dict)

    def test_non_dbt_repo_also_gets_dbt_charts_yml(self, tmp_path: Path) -> None:
        result = init_command(project_dir=tmp_path)
        assert (tmp_path / "dbt_charts.yml").exists()
        assert Path("dbt_charts.yml") in result.created_files


# ---------------------------------------------------------------------------
# Dialect inference from dbt profile
# ---------------------------------------------------------------------------


class TestInferDialectFromDbt:
    def test_infers_bigquery(self, dbt_repo: Path) -> None:
        from dbt_charts.core.project_roots import infer_dialect_from_dbt

        assert infer_dialect_from_dbt(dbt_repo) == "bigquery"

    def test_infers_duckdb(self, dbt_repo_duckdb: Path) -> None:
        from dbt_charts.core.project_roots import infer_dialect_from_dbt

        assert infer_dialect_from_dbt(dbt_repo_duckdb) == "duckdb"

    def test_infers_from_specific_target(self, dbt_repo: Path) -> None:
        from dbt_charts.core.project_roots import infer_dialect_from_dbt

        assert infer_dialect_from_dbt(dbt_repo, target_name="prod") == "bigquery"

    def test_returns_none_no_dbt_project(self, tmp_path: Path) -> None:
        from dbt_charts.core.project_roots import infer_dialect_from_dbt

        assert infer_dialect_from_dbt(tmp_path) is None

    def test_returns_none_no_profiles(self, tmp_path: Path) -> None:
        from dbt_charts.core.project_roots import infer_dialect_from_dbt

        (tmp_path / "dbt_project.yml").write_text(
            yaml.dump({"name": "test", "profile": "missing"})
        )
        assert infer_dialect_from_dbt(tmp_path) is None

    def test_falls_back_to_home_dbt_profiles(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.project_roots import infer_dialect_from_dbt

        # dbt project with no local profiles.yml
        (tmp_path / "dbt_project.yml").write_text(
            yaml.dump({"name": "test", "profile": "home_profile"})
        )

        # Create fake ~/.dbt/profiles.yml
        fake_home = tmp_path / "fakehome"
        fake_dbt = fake_home / ".dbt"
        fake_dbt.mkdir(parents=True)
        (fake_dbt / "profiles.yml").write_text(
            yaml.dump(
                {
                    "home_profile": {
                        "target": "dev",
                        "outputs": {"dev": {"type": "snowflake"}},
                    }
                }
            )
        )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        assert infer_dialect_from_dbt(tmp_path) == "snowflake"


# ---------------------------------------------------------------------------
# Serve integration: project root discovery + dialect inference
# ---------------------------------------------------------------------------


class TestServeProjectRootDiscovery:
    """Test that create_server serves from the project it is handed.

    Root discovery/construction now happens at the CLI edge; create_server takes
    an already-built project and threads its root into app state.
    """

    def test_server_uses_discovered_dbt_root(self, dbt_repo: Path) -> None:
        """create_server threads the given project's root into app state."""
        from fastapi.testclient import TestClient

        from dbt_charts.core.serve.server import create_server

        (dbt_repo / "charts").mkdir(exist_ok=True)
        (dbt_repo / "charts" / "hello.yml").write_text(
            'title: "Test"\nrows:\n  - text: "hi"\n'
        )

        # When project_dir is explicitly passed, it should use it
        app = create_server(FilesystemProject(dbt_repo))
        with TestClient(app):
            assert app.state.project.root == dbt_repo.resolve()

    def test_server_threads_target_to_dbt_context(self, dbt_repo: Path) -> None:
        """When target is provided, the built registry's dbt context carries it."""
        from fastapi.testclient import TestClient

        from dbt_charts.core.serve.server import create_server

        app = create_server(FilesystemProject(dbt_repo), target="prod")
        with TestClient(app):
            dbt_context = app.state.adapter_registry.dbt_context
        assert dbt_context is not None, "expected a linked dbt project"
        assert dbt_context.target == "prod"
