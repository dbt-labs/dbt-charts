"""CLI smoke tests for dct init."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app


@pytest.fixture
def dbt_dir(tmp_path: Path) -> Path:
    (tmp_path / "dbt_project.yml").write_text("name: my_project\n")
    return tmp_path


@pytest.fixture
def bare_git_subdir(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = tmp_path / "repo"
    subdir = repo_root / "nested" / "work"
    subprocess.run(["git", "init", "-q", str(repo_root)], check=True)
    subdir.mkdir(parents=True)
    return repo_root, subdir


def _init(*args: str, env: dict[str, str] | None = None) -> str:
    result = CliRunner().invoke(app, ["init", *args], env=env, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result.output


class TestInitWizard:
    def test_installs_the_stub_without_asking(self, dbt_dir: Path) -> None:
        output = _init("--project-dir", str(dbt_dir))
        assert (dbt_dir / ".agents/skills/dct/SKILL.md").exists()
        assert (dbt_dir / ".claude/skills/dct/SKILL.md").exists()
        assert "[y/N]" not in output and "[Y/n]" not in output
        assert not (dbt_dir / "AGENTS.md").exists()
        assert not (dbt_dir / "CLAUDE.md").exists()
        # inspect-eject is opt-in; not triggered by default
        assert not (dbt_dir / "charts" / "inspect").exists()

    def test_writes_no_mcp_config(self, dbt_dir: Path) -> None:
        _init("--project-dir", str(dbt_dir))
        assert not (dbt_dir / ".mcp.json").exists()
        assert not (dbt_dir / ".cursor").exists()
        assert not (dbt_dir / ".vscode").exists()

    def test_ends_with_vscode_link_and_skills_pointer(self, dbt_dir: Path) -> None:
        lines = [
            line for line in _init("--project-dir", str(dbt_dir)).splitlines() if line
        ]
        assert "dct serve" in "\n".join(lines)
        assert "marketplace.visualstudio.com" in lines[-2]
        assert "dct skills chart" in lines[-1]

    def test_removed_prompt_flags_are_rejected(self, dbt_dir: Path) -> None:
        for flag in ("--yes", "--no-skills", "--no-mcp", "--vscode", "--cursor"):
            result = CliRunner().invoke(
                app, ["init", "--project-dir", str(dbt_dir), flag]
            )
            assert result.exit_code == 2, (flag, result.output)

    def test_bare_git_repo_scaffolds_at_git_root(
        self, bare_git_subdir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root, subdir = bare_git_subdir
        monkeypatch.chdir(subdir)
        output = _init()
        assert f"Using git root: {repo_root}" in output
        assert (repo_root / "charts").is_dir()
        assert not (subdir / "charts").exists()

    def test_explicit_project_dir_beats_git_root(
        self, bare_git_subdir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root, subdir = bare_git_subdir
        monkeypatch.chdir(subdir)
        output = _init("--project-dir", str(subdir))
        assert "git root" not in output
        assert not (repo_root / "charts").exists()
        assert (subdir / "charts").is_dir()

    def test_project_dir_env_beats_git_root(
        self, bare_git_subdir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root, subdir = bare_git_subdir
        monkeypatch.chdir(subdir)
        output = _init(env={"DCT_PROJECT_DIR": str(subdir)})
        assert "git root" not in output
        assert not (repo_root / "charts").exists()
        assert (subdir / "charts").is_dir()

    @pytest.mark.parametrize("marker", ["dbt_project.yml", "dbt_charts.yml"])
    def test_existing_project_above_cwd_uses_project_root(
        self,
        marker: str,
        bare_git_subdir: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        repo_root, subdir = bare_git_subdir
        (repo_root / marker).write_text("name: existing_project\n")
        monkeypatch.chdir(subdir)
        output = _init()
        assert "git root" not in output
        assert (repo_root / "charts").is_dir()
        assert not (subdir / "charts").exists()

    def test_skills_install_at_git_root_for_nested_project_without_project_dir_flag(
        self, bare_git_subdir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No --project-dir: skills land at the git root even though the dct
        project itself lives in a nested subdirectory."""
        repo_root, subdir = bare_git_subdir
        (subdir / "dbt_project.yml").write_text("name: nested_project\n")
        monkeypatch.chdir(subdir)
        _init()
        assert (repo_root / ".agents/skills/dct/SKILL.md").exists()
        assert (repo_root / ".claude/skills/dct/SKILL.md").exists()
        assert not (subdir / ".agents/skills").exists()
        assert not (subdir / ".claude/skills").exists()

    def test_skills_install_at_explicit_project_dir_not_git_root(
        self, bare_git_subdir: tuple[Path, Path]
    ) -> None:
        """--project-dir: skills install into the named directory, not the
        enclosing git root."""
        repo_root, subdir = bare_git_subdir
        (subdir / "dbt_project.yml").write_text("name: nested_project\n")
        _init("--project-dir", str(subdir))
        assert (subdir / ".claude/skills/dct/SKILL.md").exists()
        assert not (repo_root / ".claude").exists()

    def test_existing_agent_markdown_is_not_modified(self, dbt_dir: Path) -> None:
        (dbt_dir / "AGENTS.md").write_text("# Pre-existing content\n")
        (dbt_dir / "CLAUDE.md").write_text("@AGENTS.md\n# Custom\n")
        output = _init("--project-dir", str(dbt_dir))
        assert (dbt_dir / "AGENTS.md").read_text() == "# Pre-existing content\n"
        assert (dbt_dir / "CLAUDE.md").read_text() == "@AGENTS.md\n# Custom\n"
        assert "AGENTS.md" not in output
        assert "CLAUDE.md" not in output


class TestInitProjectDirErrors:
    """Error paths for init --project-dir validation."""

    def test_nonexistent_project_dir_exits_2(self, tmp_path: Path) -> None:
        runner = CliRunner()
        # Typer's exists=True validator fires before the command body → exit 2
        result = runner.invoke(
            app,
            ["init", "--project-dir", str(tmp_path / "does_not_exist")],
        )
        assert result.exit_code == 2  # Typer exists=True validator → 2


class TestInitMcp:
    """init mcp subcommand (canonical entry point for MCP wiring)."""

    def test_init_mcp_print_emits_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dct init mcp print returns parseable JSON MCP config."""
        _runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        (Path("dbt_charts.yml")).write_text("name: test\n", encoding="utf-8")
        result = _runner.invoke(app, ["init", "mcp", "print"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "mcpServers" in payload
        assert "dbt-charts" in payload["mcpServers"]
        assert payload["mcpServers"]["dbt-charts"]["args"] == ["mcp", "serve"]

    def test_init_mcp_project_dir_nonexistent_exits_2(self, tmp_path: Path) -> None:
        runner = CliRunner()
        # Typer's exists=True on --project-dir fires before the command body → exit 2
        result = runner.invoke(
            app,
            ["init", "mcp", "--project-dir", str(tmp_path / "no_such_dir")],
        )
        assert result.exit_code == 2  # Typer exists=True validator → 2


def test_pip_extra_named_in_output() -> None:
    """The extras panel names the canonical `dbt-charts[<extra>]` install form."""
    import io

    from rich.console import Console

    from dbt_charts.cli._extras import _build_extras_panel

    buf = io.StringIO()
    Console(file=buf, no_color=True).print(_build_extras_panel("mcp", ["mcp"], "pip"))
    assert "dbt-charts[mcp]" in buf.getvalue()
