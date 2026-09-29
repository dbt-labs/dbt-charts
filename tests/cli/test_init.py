"""CLI smoke tests for dct init."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
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
    (repo_root / ".git").mkdir(parents=True)
    subdir.mkdir(parents=True)
    return repo_root, subdir


@pytest.fixture
def mock_dispatches() -> Generator[MagicMock, None, None]:
    """Mock wizard opt-in dispatches to prevent network/subprocess side effects."""
    with (
        patch("dbt_charts.cli.commands.mcp_init.run_init") as mock_mcp,
        patch("dbt_charts.cli._extras.install_extras") as mock_extras,
        patch(
            "dbt_charts.cli.commands.extension.install_extension", return_value=0
        ) as mock_ext,
        patch(
            "dbt_charts.cli.commands.init.shutil.which", return_value=None
        ) as mock_which,
    ):
        mocks = MagicMock()
        mocks.mcp = mock_mcp
        mocks.extras = mock_extras
        mocks.ext = mock_ext
        mocks.which = mock_which
        yield mocks


class TestInitDoesNotWriteAgentMarkdown:
    @pytest.fixture(autouse=True)
    def _setup(self, mock_dispatches: MagicMock) -> None:
        self.mocks = mock_dispatches

    def test_init_does_not_create_agent_markdown(self, dbt_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app, ["init", "--project-dir", str(dbt_dir)], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        assert not (dbt_dir / "AGENTS.md").exists()
        assert not (dbt_dir / "CLAUDE.md").exists()
        assert "AGENTS.md" not in result.output
        assert "CLAUDE.md" not in result.output

    def test_wizard_resolves_skills_not_agent_markdown(self, dbt_dir: Path) -> None:
        prompts: list[str] = []

        def resolve_spy(
            flag: bool | None,
            yes: bool,
            prompt: str,
            default: bool,
        ) -> bool:
            if flag is not None:
                return flag
            prompts.append(prompt)
            return False

        runner = CliRunner()
        with patch("dbt_charts.cli.commands.init._resolve", side_effect=resolve_spy):
            result = runner.invoke(
                app,
                [
                    "init",
                    "--project-dir",
                    str(dbt_dir),
                    "--no-mcp",
                    "--no-vscode",
                    "--no-cursor",
                ],
                catch_exceptions=False,
            )
        assert result.exit_code == 0, result.output
        assert prompts == ["Install dbt charts workflow skills for AI assistants?"]
        assert not (dbt_dir / "AGENTS.md").exists()
        assert not (dbt_dir / "CLAUDE.md").exists()

    def test_yes_installs_workflow_skills_directly(self, dbt_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "init",
                "--project-dir",
                str(dbt_dir),
                "--yes",
                "--no-mcp",
                "--no-vscode",
                "--no-cursor",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (dbt_dir / ".agents/skills/dct-board-build/SKILL.md").exists()
        assert (dbt_dir / ".claude/skills/dct-board-build/SKILL.md").exists()
        assert not (dbt_dir / "AGENTS.md").exists()
        assert not (dbt_dir / "CLAUDE.md").exists()

    def test_ends_with_pointer_to_skills_intro(self, dbt_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app, ["init", "--project-dir", str(dbt_dir)], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        lines = [line for line in result.output.splitlines() if line.strip()]
        assert "dct skills intro" in lines[-1]


class TestInitWizardFlags:
    @pytest.fixture(autouse=True)
    def _setup(self, mock_dispatches: MagicMock) -> None:
        self.mocks = mock_dispatches

    def test_yes_flag_skips_prompts(self, dbt_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["init", "--project-dir", str(dbt_dir), "--yes"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert not (dbt_dir / "AGENTS.md").exists()
        assert not (dbt_dir / "CLAUDE.md").exists()

    def test_no_skills_skips_agent_artifacts(self, dbt_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "init",
                "--project-dir",
                str(dbt_dir),
                "--no-skills",
                "--no-mcp",
                "--no-vscode",
                "--no-cursor",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert not (dbt_dir / "AGENTS.md").exists()
        assert not (dbt_dir / "CLAUDE.md").exists()
        assert not (dbt_dir / ".agents/skills").exists()
        assert not (dbt_dir / ".claude/skills").exists()
        # inspect-eject is opt-in; not triggered by default
        assert not (dbt_dir / "charts" / "inspect").exists()

    def test_mcp_extra_installed_when_mcp_wiring_is_written(
        self, dbt_dir: Path
    ) -> None:
        """`dct init --mcp` must install `mcp`, not just write client config.

        Config pointing at a `dct mcp serve` that cannot start surfaces to the
        user as a bare "server failed to start" — the install hint goes to
        stderr, which stdio MCP clients discard.
        """

        runner = CliRunner()
        runner.invoke(
            app,
            [
                "init",
                "--project-dir",
                str(dbt_dir),
                "--mcp",
                "--no-vscode",
                "--no-cursor",
                "--yes",
            ],
            catch_exceptions=False,
        )
        self.mocks.extras.assert_any_call("mcp", interactive=False)

    def test_mcp_install_failure_warns_and_still_writes_config(
        self, dbt_dir: Path
    ) -> None:
        """A failed mcp install must not abort the wizard or skip the config.

        Every other opt-in in run_wizard degrades to a warning. Aborting here
        would also skip the IDE-extension steps the user already accepted, and
        leave no MCP config at all — strictly worse than config whose server
        prints an install hint.
        """

        with patch.object(self.mocks.extras, "side_effect", typer.Exit(1)):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "init",
                    "--project-dir",
                    str(dbt_dir),
                    "--mcp",
                    "--no-vscode",
                    "--no-cursor",
                    "--yes",
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.stdout
        self.mocks.mcp.assert_called_once()
        # The warning is the whole point of the branch — without it the user
        # gets config for a server that cannot start and no indication why.
        assert "mcp extra install failed" in result.stdout, result.stdout

    def test_does_not_create_claude_md_by_default(self, dbt_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app, ["init", "--project-dir", str(dbt_dir)], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        assert not (dbt_dir / "CLAUDE.md").exists()

    def test_success_block_shown(self, dbt_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["init", "--project-dir", str(dbt_dir), "--yes"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "dct serve" in result.output

    def test_mcp_called_when_yes(self, dbt_dir: Path) -> None:
        runner = CliRunner()
        runner.invoke(
            app,
            ["init", "--project-dir", str(dbt_dir), "--yes", "--mcp"],
            catch_exceptions=False,
        )
        self.mocks.mcp.assert_called_once()

    def test_mcp_not_called_when_no_mcp(self, dbt_dir: Path) -> None:
        runner = CliRunner()
        runner.invoke(
            app,
            ["init", "--project-dir", str(dbt_dir), "--no-mcp"],
            catch_exceptions=False,
        )
        self.mocks.mcp.assert_not_called()

    def test_extension_called_when_vscode_flag(self, dbt_dir: Path) -> None:
        runner = CliRunner()
        runner.invoke(
            app,
            ["init", "--project-dir", str(dbt_dir), "--vscode"],
            catch_exceptions=False,
        )
        self.mocks.ext.assert_called_once()

    def test_extension_not_called_without_ide_on_path(self, dbt_dir: Path) -> None:
        # which returns None (default mock), no --vscode or --cursor flag
        runner = CliRunner()
        result = runner.invoke(
            app, ["init", "--project-dir", str(dbt_dir)], catch_exceptions=False
        )
        assert result.exit_code == 0
        self.mocks.ext.assert_not_called()
        assert "skipping extension install" in result.output

    def test_bare_git_repo_prompts_for_git_root_by_default(
        self, bare_git_subdir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root, subdir = bare_git_subdir
        monkeypatch.chdir(subdir)
        runner = CliRunner()
        with patch("dbt_charts.cli.commands.init.sys.stdin.isatty", return_value=True):
            result = runner.invoke(
                app,
                [
                    "init",
                    "--no-skills",
                    "--no-mcp",
                    "--no-vscode",
                    "--no-cursor",
                ],
                input="\n\n\n",
                catch_exceptions=False,
            )
        assert result.exit_code == 0, result.output
        assert "No dbt charts or dbt project found" in result.output
        assert str(repo_root) in result.output
        assert str(subdir) in result.output
        assert (repo_root / "charts").is_dir()
        assert not (subdir / "charts").exists()

    def test_bare_git_repo_prompt_can_keep_current_directory(
        self, bare_git_subdir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root, subdir = bare_git_subdir
        monkeypatch.chdir(subdir)
        runner = CliRunner()
        with (
            patch(
                "dbt_charts.cli.commands.init._resolve",
                side_effect=[False, True, True, False],
            ),
            patch("dbt_charts.cli._extras._missing_packages", return_value=[]),
        ):
            result = runner.invoke(
                app,
                [
                    "init",
                    "--no-skills",
                    "--no-mcp",
                    "--no-vscode",
                    "--no-cursor",
                ],
                catch_exceptions=False,
            )
        assert result.exit_code == 0, result.output
        assert not (repo_root / "charts").exists()
        assert (subdir / "charts").is_dir()

    def test_bare_git_repo_yes_defaults_to_git_root(
        self, bare_git_subdir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root, subdir = bare_git_subdir
        monkeypatch.chdir(subdir)
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "init",
                "--yes",
                "--no-skills",
                "--no-mcp",
                "--no-vscode",
                "--no-cursor",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert f"Using git root: {repo_root}" in result.output
        assert (repo_root / "charts").is_dir()
        assert not (subdir / "charts").exists()

    def test_explicit_project_dir_skips_git_root_prompt(
        self, bare_git_subdir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root, subdir = bare_git_subdir
        monkeypatch.chdir(subdir)
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "init",
                "--project-dir",
                str(subdir),
                "--no-skills",
                "--no-mcp",
                "--no-vscode",
                "--no-cursor",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "No dbt charts or dbt project found" not in result.output
        assert not (repo_root / "charts").exists()
        assert (subdir / "charts").is_dir()

    def test_project_dir_env_skips_git_root_prompt(
        self, bare_git_subdir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root, subdir = bare_git_subdir
        monkeypatch.chdir(subdir)
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "init",
                "--no-skills",
                "--no-mcp",
                "--no-vscode",
                "--no-cursor",
            ],
            env={"DCT_PROJECT_DIR": str(subdir)},
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "No dbt charts or dbt project found" not in result.output
        assert not (repo_root / "charts").exists()
        assert (subdir / "charts").is_dir()

    def test_existing_dbt_project_above_cwd_uses_project_root(
        self, bare_git_subdir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root, subdir = bare_git_subdir
        (repo_root / "dbt_project.yml").write_text("name: test_project\n")
        monkeypatch.chdir(subdir)
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "init",
                "--no-skills",
                "--no-mcp",
                "--no-vscode",
                "--no-cursor",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "No dbt charts or dbt project found" not in result.output
        assert (repo_root / "charts").is_dir()
        assert not (subdir / "charts").exists()

    def test_skills_install_at_git_root_for_nested_project_without_project_dir_flag(
        self, bare_git_subdir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No --project-dir given: skills must land at the git root even though
        the dct project itself lives in a nested subdirectory.

        Regression: the wizard forwarded its already-resolved scaffold root
        (the nested project) to run_init_skills unconditionally, which made
        skills_init treat every wizard run as if --project-dir had been passed
        explicitly, and install skills next to the nested project instead of
        the git root.
        """
        repo_root, subdir = bare_git_subdir
        (subdir / "dbt_project.yml").write_text("name: nested_project\n")
        monkeypatch.chdir(subdir)
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "init",
                "--yes",
                "--no-mcp",
                "--no-vscode",
                "--no-cursor",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (repo_root / ".agents/skills/dct-board-build/SKILL.md").exists()
        assert (repo_root / ".claude/skills/dct-board-build/SKILL.md").exists()
        assert not (subdir / ".agents/skills").exists()
        assert not (subdir / ".claude/skills").exists()

    def test_skills_install_at_explicit_project_dir_not_git_root(
        self, bare_git_subdir: tuple[Path, Path]
    ) -> None:
        """--project-dir given: skills install into the named directory, not
        the enclosing git root — the wizard must forward the user's raw
        --project-dir, not its own already-resolved scaffold root."""
        repo_root, subdir = bare_git_subdir
        (subdir / "dbt_project.yml").write_text("name: nested_project\n")
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "init",
                "--project-dir",
                str(subdir),
                "--yes",
                "--no-mcp",
                "--no-vscode",
                "--no-cursor",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (subdir / ".claude/skills/dct-board-build/SKILL.md").exists()
        assert not (repo_root / ".claude").exists()

    def test_existing_dbt_charts_project_above_cwd_uses_project_root(
        self, bare_git_subdir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root, subdir = bare_git_subdir
        (repo_root / "dbt_charts.yml").write_text("name: existing_project\n")
        monkeypatch.chdir(subdir)
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "init",
                "--no-skills",
                "--no-mcp",
                "--no-vscode",
                "--no-cursor",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "No dbt charts or dbt project found" not in result.output
        assert (repo_root / "charts").is_dir()
        assert not (subdir / "charts").exists()


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

    def test_init_mcp_print_emits_json(self, tmp_path: Path) -> None:
        """dct init mcp print returns parseable JSON MCP config."""
        _runner = CliRunner()
        with _runner.isolated_filesystem(temp_dir=tmp_path):
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


class TestInitPreservesExistingAgentMarkdown:
    @pytest.fixture(autouse=True)
    def _setup(self, mock_dispatches: MagicMock) -> None:
        self.mocks = mock_dispatches

    def test_existing_agents_md_is_not_modified(self, dbt_dir: Path) -> None:
        (dbt_dir / "AGENTS.md").write_text("# Pre-existing content\n")
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "init",
                "--project-dir",
                str(dbt_dir),
                "--yes",
                "--no-mcp",
                "--no-vscode",
                "--no-cursor",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (dbt_dir / "AGENTS.md").read_text() == "# Pre-existing content\n"
        assert "AGENTS.md" not in result.output
        assert (dbt_dir / ".agents/skills/dct-board-build/SKILL.md").exists()

    def test_existing_claude_md_is_not_modified(self, dbt_dir: Path) -> None:
        (dbt_dir / "CLAUDE.md").write_text("@AGENTS.md\n# Custom\n")
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "init",
                "--project-dir",
                str(dbt_dir),
                "--yes",
                "--no-mcp",
                "--no-vscode",
                "--no-cursor",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (dbt_dir / "CLAUDE.md").read_text() == "@AGENTS.md\n# Custom\n"
        assert "CLAUDE.md" not in result.output
        assert (dbt_dir / ".claude/skills/dct-board-build/SKILL.md").exists()

    def test_ide_detection_both_found(self, dbt_dir: Path) -> None:
        def _which(cmd: str) -> str | None:
            return f"/usr/bin/{cmd}" if cmd in ("code", "cursor") else None

        with patch(
            "dbt_charts.cli.commands.init.shutil.which",
            side_effect=_which,
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                ["init", "--project-dir", str(dbt_dir), "--yes"],
                catch_exceptions=False,
            )
        assert result.exit_code == 0, result.output
        # Both extensions should have been installed
        assert self.mocks.ext.call_count == 2

    def test_pip_extra_named_in_output(self, dbt_dir: Path) -> None:
        """The extras panel names the canonical `dbt-charts[<extra>]` install form."""
        from dbt_charts.cli._extras import _build_extras_panel

        panel = _build_extras_panel("mcp", ["mcp"], "pip")
        import io

        from rich.console import Console

        buf = io.StringIO()
        con = Console(file=buf, no_color=True)
        con.print(panel)
        output = buf.getvalue()
        assert "dbt-charts[mcp]" in output
