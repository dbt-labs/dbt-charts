"""Tests for dct init command."""

from pathlib import Path

import pytest

from dbt_charts.agent_api.init import init_project as init_command


@pytest.fixture
def dbt_repo(tmp_path: Path) -> Path:
    """Create a minimal dbt project directory."""
    (tmp_path / "dbt_project.yml").write_text("name: test_project\n")
    (tmp_path / "models").mkdir()
    return tmp_path


@pytest.fixture
def empty_dir(tmp_path: Path) -> Path:
    """A directory with no dbt markers."""
    return tmp_path


class TestDbtDetection:
    def test_detects_dbt_project(self, dbt_repo: Path) -> None:
        result = init_command(project_dir=dbt_repo)
        assert result.dbt_detected
        assert (dbt_repo / "charts").is_dir()

    def test_non_dbt_directory_still_works(self, empty_dir: Path) -> None:
        result = init_command(project_dir=empty_dir)
        assert not result.dbt_detected
        assert (empty_dir / "charts").is_dir()


class TestFilesCreated:
    def test_creates_boards_dir(self, dbt_repo: Path) -> None:
        init_command(project_dir=dbt_repo)
        assert (dbt_repo / "charts").is_dir()

    def test_creates_partials_dir(self, dbt_repo: Path) -> None:
        init_command(project_dir=dbt_repo)
        assert (dbt_repo / "charts" / "partials").is_dir()

    def test_creates_no_starter_board(self, dbt_repo: Path) -> None:
        init_command(project_dir=dbt_repo)
        assert not (dbt_repo / "charts" / "guide.yml").exists()

    def test_does_not_create_index_md(self, dbt_repo: Path) -> None:
        """charts/index.md must not be created — it would hijack the root URL."""
        init_command(project_dir=dbt_repo)
        assert not (dbt_repo / "charts" / "index.md").exists()

    def test_creates_gitkeep_in_partials(self, dbt_repo: Path) -> None:
        init_command(project_dir=dbt_repo)
        assert (dbt_repo / "charts" / "partials" / ".gitkeep").exists()

    def test_gitignores_default_render_output_dir(self, dbt_repo: Path) -> None:
        result = init_command(project_dir=dbt_repo)
        gitignore = dbt_repo / ".gitignore"
        assert gitignore.exists()
        lines = gitignore.read_text().splitlines()
        assert "renders/" in lines
        assert ".venv/" in lines
        assert "__pycache__/" in lines
        assert "*.duckdb" in lines
        assert Path(".gitignore") in result.created_files

    def test_gitignore_does_not_duplicate_existing_entries(
        self, dbt_repo: Path
    ) -> None:
        gitignore = dbt_repo / ".gitignore"
        gitignore.write_text(".venv/\nrenders/\n")

        init_command(project_dir=dbt_repo)

        lines = gitignore.read_text().splitlines()
        # Each scaffold entry appears exactly once.
        assert lines.count(".venv/") == 1
        assert lines.count("renders/") == 1
        # New entries got appended.
        assert "__pycache__/" in lines
        assert "*.duckdb" in lines


class TestIdempotency:
    def test_rerun_does_not_clobber_existing_files(self, dbt_repo: Path) -> None:
        init_command(project_dir=dbt_repo)
        guide = dbt_repo / "charts" / "meta.yml"
        guide.write_text("custom content\n")

        result = init_command(project_dir=dbt_repo)
        assert guide.read_text() == "custom content\n"
        assert Path("charts/meta.yml") in result.skipped_files

    def test_rerun_creates_missing_files(self, dbt_repo: Path) -> None:
        """If charts/ exists but meta.yml was deleted, re-create it."""
        (dbt_repo / "charts").mkdir()
        result = init_command(project_dir=dbt_repo)
        assert (dbt_repo / "charts" / "meta.yml").exists()
        assert Path("charts/meta.yml") in result.created_files

    def test_existing_gitignore_is_appended_not_clobbered(self, dbt_repo: Path) -> None:
        gitignore = dbt_repo / ".gitignore"
        gitignore.write_text("target/\n")

        result = init_command(project_dir=dbt_repo)

        lines = gitignore.read_text().splitlines()
        # User's existing entry preserved as first line; scaffold entries appended.
        assert lines[0] == "target/"
        assert {"renders/", ".venv/", "__pycache__/", "*.duckdb"} <= set(lines)
        assert Path(".gitignore") in result.refreshed_files

    def test_force_overwrites_existing(self, dbt_repo: Path) -> None:
        init_command(project_dir=dbt_repo)
        guide = dbt_repo / "charts" / "meta.yml"
        guide.write_text("custom content\n")

        result = init_command(project_dir=dbt_repo, force=True)
        assert guide.read_text() != "custom content\n"
        # Force-overwritten files report as refreshed (matches AGENTS.md branch).
        assert Path("charts/meta.yml") in result.refreshed_files
        assert Path("charts/meta.yml") not in result.created_files


class TestProjectDirOverride:
    def test_explicit_project_dir(self, dbt_repo: Path) -> None:
        result = init_command(project_dir=dbt_repo)
        assert (dbt_repo / "charts").is_dir()
        assert result.project_dir == dbt_repo

    def test_defaults_to_cwd(
        self, dbt_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(dbt_repo)
        result = init_command()
        assert result.project_dir == dbt_repo
        assert (dbt_repo / "charts").is_dir()


class TestInitHints:
    """InitResult.hints must not surface experimental inspect commands."""

    def test_no_inspect_hints(self, dbt_repo: Path) -> None:
        result = init_command(project_dir=dbt_repo)
        assert result.hints == [], f"unexpected hints: {result.hints}"
