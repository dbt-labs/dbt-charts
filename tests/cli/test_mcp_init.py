"""Tests for `dct init mcp` client configuration."""

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from dbt_charts.agent_api import mcp_install
from dbt_charts.cli import main as cli_main

runner = CliRunner()


@pytest.fixture
def patch_mcp_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect Claude Desktop config into the test sandbox."""

    patched = dict(mcp_install.MCP_CLIENTS)
    patched["claude"] = mcp_install.McpClient(
        name="claude",
        config_path=tmp_path / "home/.config/claude/config.json",
        servers_key="mcpServers",
        detect_paths=(tmp_path / "home/.config/claude",),
        config_format="json",
        command_workspace_var=None,
    )
    monkeypatch.setattr(mcp_install, "MCP_CLIENTS", patched)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _seed_project() -> None:
    """Create a minimal `dbt_charts.yml` in cwd so strict project resolution succeeds.

    Call after `monkeypatch.chdir(tmp_path)` and before invoking the CLI.
    """
    Path("dbt_charts.yml").write_text("name: test\n", encoding="utf-8")


def test_mcp_init_vscode_writes_servers_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_mcp_clients: None
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_project()
    result = runner.invoke(cli_main.app, ["init", "mcp", "vscode"])

    assert result.exit_code == 0, result.output
    config = _read_json(Path(".vscode/mcp.json"))
    assert "servers" in config
    assert "mcpServers" not in config
    # cwd is the project — workspace and project roots match, no --project-dir pin.
    assert config["servers"]["dbt-charts"]["args"] == ["mcp", "serve"]
    assert "cwd" not in config["servers"]["dbt-charts"]


def test_mcp_init_corrupt_config_errors_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_mcp_clients: None
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_project()
    Path(".vscode").mkdir()
    corrupt = "{not valid json"
    Path(".vscode/mcp.json").write_text(corrupt, encoding="utf-8")

    result = runner.invoke(cli_main.app, ["init", "mcp", "vscode"])

    assert result.exit_code == 1, result.output
    assert ".vscode/mcp.json" in result.output
    assert Path(".vscode/mcp.json").read_text(encoding="utf-8") == corrupt


def test_mcp_init_claude_code_writes_project_root_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_mcp_clients: None
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_project()
    result = runner.invoke(cli_main.app, ["init", "mcp", "claude-code"])

    assert result.exit_code == 0, result.output
    config = _read_json(Path(".mcp.json"))
    assert "mcpServers" in config
    assert "servers" not in config
    assert config["mcpServers"]["dbt-charts"]["args"] == ["mcp", "serve"]


def test_mcp_init_codex_writes_toml_config_toml(
    tmp_path: Path, patch_mcp_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No project-local venv present, so this pins today's unchanged fallback
    # resolution (sys.argv[0]) rather than depending on the ambient test env's
    # PATH-installed dct.
    fake_dct = tmp_path / "outside" / "dct"
    fake_dct.parent.mkdir(parents=True)
    fake_dct.write_text("#!/bin/sh\nexit 0\n")
    fake_dct.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(fake_dct), "init", "mcp", "codex"])

    monkeypatch.chdir(tmp_path)
    _seed_project()
    result = runner.invoke(cli_main.app, ["init", "mcp", "codex"])

    assert result.exit_code == 0, result.output
    # Codex reads .codex/config.toml, NOT .codex/mcp.json
    assert not Path(".codex/mcp.json").exists()
    config = _read_toml(Path(".codex/config.toml"))
    assert "mcp_servers" in config
    # Old JSON-camelCase key must not be present
    assert "mcpServers" not in config
    entry = config["mcp_servers"]["dbt-charts"]
    assert entry["args"] == ["mcp", "serve"]
    assert entry["command"] == str(fake_dct.resolve())


def test_mcp_init_codex_preserves_existing_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_mcp_clients: None
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_project()
    codex_dir = Path(".codex")
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        'model = "gpt-5"\n'
        "\n"
        "[mcp_servers.other]\n"
        'command = "/usr/bin/echo"\n'
        'args = ["hi"]\n'
    )

    result = runner.invoke(cli_main.app, ["init", "mcp", "codex"])

    assert result.exit_code == 0, result.output
    config = _read_toml(Path(".codex/config.toml"))
    assert config["model"] == "gpt-5"
    assert config["mcp_servers"]["other"]["command"] == "/usr/bin/echo"
    assert config["mcp_servers"]["other"]["args"] == ["hi"]
    assert config["mcp_servers"]["dbt-charts"]["args"] == ["mcp", "serve"]


def test_mcp_init_codex_idempotent_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_mcp_clients: None
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_project()
    first = runner.invoke(cli_main.app, ["init", "mcp", "codex"])
    assert first.exit_code == 0, first.output
    first_text = Path(".codex/config.toml").read_text(encoding="utf-8")

    second = runner.invoke(cli_main.app, ["init", "mcp", "codex"])
    assert second.exit_code == 0, second.output
    assert "already has dbt-charts" in second.output
    assert Path(".codex/config.toml").read_text(encoding="utf-8") == first_text


def test_mcp_init_codex_force_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_mcp_clients: None
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_project()
    Path(".codex").mkdir()
    Path(".codex/config.toml").write_text(
        '[mcp_servers.dbt-charts]\ncommand = "/old/dct"\nargs = ["old"]\n',
        encoding="utf-8",
    )

    result = runner.invoke(cli_main.app, ["init", "mcp", "codex", "-f"])

    assert result.exit_code == 0, result.output
    config = _read_toml(Path(".codex/config.toml"))
    assert config["mcp_servers"]["dbt-charts"]["args"] == ["mcp", "serve"]
    assert config["mcp_servers"]["dbt-charts"]["command"] != "/old/dct"


def test_mcp_init_auto_detects_workspace_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_mcp_clients: None
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_project()
    Path(".vscode").mkdir()
    Path(".github").mkdir()
    Path("CLAUDE.md").write_text("# Claude Code\n", encoding="utf-8")
    Path("AGENTS.md").write_text("# Codex\n", encoding="utf-8")

    result = runner.invoke(cli_main.app, ["init", "mcp"])

    assert result.exit_code == 0, result.output
    assert Path(".vscode/mcp.json").exists()
    assert Path(".github/copilot/mcp.json").exists()
    assert Path(".mcp.json").exists()
    assert Path(".codex/config.toml").exists()
    assert not Path(".codex/mcp.json").exists()
    assert not Path(".cursor/mcp.json").exists()


def test_mcp_init_auto_detects_markers_at_git_root_without_dbt_charts_marker(
    tmp_path: Path, patch_mcp_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ai_config_root must resolve to the git root, not cwd, in a pure-git repo.

    Regression: when no dbt_charts.yml / dbt_project.yml exists, ai_config_root must
    fall back to the repo root via `find_dct_root(cwd) or find_repo_root(cwd) or
    cwd`, not to cwd directly.  Workspace-marker files (.cursor/, CLAUDE.md, etc.)
    that live at the git root but NOT at cwd would otherwise be invisible, and
    `dct init mcp` would report "No AI client markers detected".
    """
    # git root has .git + .cursor/ but NO dbt charts/dbt marker anywhere
    git_root = tmp_path / "repo"
    git_root.mkdir()
    (git_root / ".git").mkdir()
    (git_root / ".cursor").mkdir()

    # cwd is a subdirectory with no project marker of its own
    sub = git_root / "packages" / "scripts"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)

    # --project-dir bypasses _resolve_project_dir's marker check so the command
    # reaches the workspace-detection branch regardless of project resolution.
    # We want to isolate ai_config_root behavior (not project resolution).
    git_root_dbt = tmp_path / "dbt-project"
    git_root_dbt.mkdir()
    (git_root_dbt / "dbt_project.yml").write_text("name: x\n")

    result = runner.invoke(
        cli_main.app, ["init", "mcp", "--project-dir", str(git_root_dbt)]
    )

    assert result.exit_code == 0, result.output
    # Cursor was detected at the git root — its config must have been written there.
    assert (git_root / ".cursor" / "mcp.json").exists(), (
        "Cursor config not written: workspace-marker at git root was not detected. "
        f"Output: {result.output}"
    )


def test_mcp_init_all_writes_every_supported_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_mcp_clients: None
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_project()
    result = runner.invoke(cli_main.app, ["init", "mcp", "--all"])

    assert result.exit_code == 0, result.output
    assert _read_json(Path(".cursor/mcp.json"))["mcpServers"]["dbt-charts"]
    codex_config = _read_toml(Path(".codex/config.toml"))
    assert codex_config["mcp_servers"]["dbt-charts"]["args"] == ["mcp", "serve"]
    assert not Path(".codex/mcp.json").exists()
    assert _read_json(Path(".vscode/mcp.json"))["servers"]["dbt-charts"]
    assert _read_json(Path(".mcp.json"))["mcpServers"]["dbt-charts"]
    assert _read_json(Path(".github/copilot/mcp.json"))["servers"]["dbt-charts"]
    assert _read_json(tmp_path / "home/.config/claude/config.json")["mcpServers"][
        "dbt-charts"
    ]


def test_mcp_init_walks_up_to_find_project_from_subdir(
    tmp_path: Path, patch_mcp_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cwd in a subdir of a project: init succeeds and writes config at the project root."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "dbt_charts.yml").write_text("name: x\n")
    sub = project / "charts"
    sub.mkdir()
    monkeypatch.chdir(sub)

    result = runner.invoke(cli_main.app, ["init", "mcp", "vscode"])

    assert result.exit_code == 0, result.output
    # Config lands at the project root (broad resolver also finds dbt_charts.yml there).
    config = _read_json(project / ".vscode/mcp.json")
    # workspace and project roots match here, so no --project-dir pin.
    assert config["servers"]["dbt-charts"]["args"] == ["mcp", "serve"]


def test_mcp_init_errors_when_no_project_marker_found(
    tmp_path: Path, patch_mcp_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No marker upward from cwd: hard error, no files written."""
    parent = tmp_path / "parent_repo"
    parent.mkdir()
    monkeypatch.chdir(parent)

    result = runner.invoke(cli_main.app, ["init", "mcp", "vscode"])

    assert result.exit_code != 0
    assert "No dbt charts or dbt project" in result.output
    assert "--project-dir" in result.output
    assert not (parent / ".vscode/mcp.json").exists()
    # Skills must not have been installed either.
    assert not (parent / ".vscode/skills").exists()


def test_mcp_init_project_dir_flag_overrides_cwd(
    tmp_path: Path, patch_mcp_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit --project-dir is used as the pinned --project-dir in the server entry."""
    parent = tmp_path / "parent"
    parent.mkdir()
    project = tmp_path / "elsewhere"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: x\n")
    monkeypatch.chdir(parent)

    result = runner.invoke(
        cli_main.app,
        ["init", "mcp", "vscode", "--project-dir", str(project)],
    )

    assert result.exit_code == 0, result.output
    config = _read_json(parent / ".vscode/mcp.json")
    entry = config["servers"]["dbt-charts"]
    assert Path(entry["args"][3]).resolve() == project.resolve()


def test_mcp_init_project_dir_flag_invalid_errors(
    tmp_path: Path, patch_mcp_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--project-dir pointing at a dir with no marker errors and writes nothing."""
    parent = tmp_path / "parent"
    parent.mkdir()
    bogus = tmp_path / "bogus"
    bogus.mkdir()
    monkeypatch.chdir(parent)

    result = runner.invoke(
        cli_main.app,
        ["init", "mcp", "vscode", "--project-dir", str(bogus)],
    )

    assert result.exit_code != 0
    assert "does not contain a dbt charts or dbt project" in result.output
    assert not (parent / ".vscode/mcp.json").exists()


def test_mcp_init_project_dir_flag_rejects_subdir_of_project(
    tmp_path: Path, patch_mcp_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--project-dir must point AT the project, not a subdir whose marker is one up."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "dbt_charts.yml").write_text("name: x\n")
    sub = project / "charts"
    sub.mkdir()
    parent = tmp_path / "parent"
    parent.mkdir()
    monkeypatch.chdir(parent)

    result = runner.invoke(
        cli_main.app,
        ["init", "mcp", "vscode", "--project-dir", str(sub)],
    )

    assert result.exit_code != 0
    assert "does not contain a dbt charts or dbt project" in result.output
    assert "Tip: did you mean" in result.output
    assert str(project.resolve()) in result.output
    assert not (parent / ".vscode/mcp.json").exists()


def test_mcp_init_print_emits_simple_args_when_cwd_is_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_mcp_clients: None
) -> None:
    """`dct init mcp print` emits the simple shape when workspace == project."""
    monkeypatch.chdir(tmp_path)
    _seed_project()
    result = runner.invoke(cli_main.app, ["init", "mcp", "print"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mcpServers"]["dbt-charts"]["args"] == ["mcp", "serve"]


def test_mcp_init_print_pins_project_dir_when_diverges(
    tmp_path: Path, patch_mcp_clients: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`dct init mcp print --project-dir <other>` pins --project-dir in the emitted args."""
    parent = tmp_path / "parent"
    parent.mkdir()
    project = tmp_path / "elsewhere"
    project.mkdir()
    (project / "dbt_charts.yml").write_text("name: x\n")
    monkeypatch.chdir(parent)

    result = runner.invoke(
        cli_main.app, ["init", "mcp", "print", "--project-dir", str(project)]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    entry = payload["mcpServers"]["dbt-charts"]
    assert entry["args"][:2] == ["mcp", "serve"]
    assert entry["args"][2] == "--project-dir"
    assert Path(entry["args"][3]).resolve() == project.resolve()


# ---------------------------------------------------------------------------
# dct mcp serve — smoke depth
# ---------------------------------------------------------------------------


def test_mcp_serve_nonexistent_project_dir_exits_2(tmp_path: Path) -> None:
    """`dct mcp serve --project-dir <nonexistent>` exits 2 (Typer exists=True)."""
    result = runner.invoke(
        cli_main.app,
        ["mcp", "serve", "--project-dir", str(tmp_path / "no_such_dir")],
    )
    # Typer's exists=True validator fires before command body → exit 2
    assert result.exit_code == 2


def test_mcp_serve_without_the_extra_exits_1_with_install_hint(
    project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`dct mcp serve` gates on the `mcp` extra via the shared `_extras` helper.

    Pins the gate, not the import: per `cli/AGENTS.md`, every optional-dependency
    check routes through `dbt_charts.cli._extras` so the Rich install panel, the
    pip-vs-uv command, and `DCT_NO_AUTO_INSTALL` behave identically across
    commands. A per-command `try: import ... except ImportError` is a violation.

    Passes --project-dir explicitly: @with_project resolves the project before
    the command body's require_extras() check runs, so an unresolvable project
    would otherwise mask the extras gate this test targets.
    """

    def _mcp_missing(_extra: str) -> list[str]:
        return ["mcp"]

    monkeypatch.setattr("dbt_charts.cli._extras._missing_packages", _mcp_missing)
    monkeypatch.setenv("DCT_NO_AUTO_INSTALL", "1")
    result = runner.invoke(
        cli_main.app, ["mcp", "serve", "--project-dir", str(project_dir)]
    )
    assert result.exit_code == 1
    combined = (result.output + (result.stderr or "")).lower()
    assert "install" in combined
    assert "mcp" in combined


def _injected_mcp_cache(monkeypatch: pytest.MonkeyPatch, project_dir: Path) -> object:
    """Run `dct mcp serve` with `run_server` stubbed; return the injected cache.

    This command is the composition root for the MCP surface: it owns
    ``FilesystemProject`` construction (a tach rule bars ``dbt_charts.ai`` from it)
    and the cache lifecycle, so ``run_server`` must receive both rather than
    build either.
    """
    import importlib.util

    from dbt_charts.cli.filesystem_project import FilesystemProject

    # Ensure the real module is importable so the import inside the command
    # succeeds, then patch run_server to return immediately (no real stdio
    # handshake).
    if importlib.util.find_spec("dbt_charts.ai.mcp") is None:
        pytest.skip("dbt_charts.ai.mcp not installed; only testing ImportError branch")

    injected: list[tuple[object, object]] = []

    async def _capture(project: object, cache: object) -> None:
        injected.append((project, cache))

    monkeypatch.setattr("dbt_charts.ai.mcp.run_server", _capture)
    result = runner.invoke(
        cli_main.app, ["mcp", "serve", "--project-dir", str(project_dir)]
    )
    assert result.exit_code == 0
    assert len(injected) == 1, "run_server must be called exactly once"
    project, cache = injected[0]
    assert isinstance(project, FilesystemProject)
    return cache


def test_mcp_serve_stays_uncached_unless_opted_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP is the authoring loop: no cache unless `DCT_CACHE_PATH` asks for one.

    An agent rebuilds a dbt model and re-runs `execute_query` in the same
    process. The cache key folds in the query text and the file version, never
    warehouse table state, so a cache here serves pre-rebuild numbers for the
    life of the process — and the shipped cascade root's ttl is 24h.
    """
    (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
    monkeypatch.delenv("DCT_CACHE_PATH", raising=False)
    assert _injected_mcp_cache(monkeypatch, tmp_path) is None


def test_mcp_serve_reports_its_own_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root callback declares "cli"; the stdio server narrows it to "mcp"."""
    from dbt_charts.core.attribution import current_attribution

    (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
    monkeypatch.delenv("DCT_CACHE_PATH", raising=False)
    surfaces: list[str] = []

    async def _capture(project: object, cache: object) -> None:
        surfaces.append(current_attribution()["dbt_charts_surface"])

    monkeypatch.setattr("dbt_charts.ai.mcp.run_server", _capture)
    result = runner.invoke(
        cli_main.app, ["mcp", "serve", "--project-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert surfaces == ["mcp"]


def test_mcp_serve_opens_the_cache_dct_cache_path_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`DCT_CACHE_PATH` is the opt-in, and it opens that persistent file."""
    from dbt_charts.core.execute.trivial_local_cache import TrivialDuckDBCache

    (tmp_path / "dbt_charts.yml").write_text("# project marker\n")
    cache_file = tmp_path / "mcp-cache.duckdb"
    monkeypatch.setenv("DCT_CACHE_PATH", str(cache_file))
    cache = _injected_mcp_cache(monkeypatch, tmp_path)
    assert isinstance(cache, TrivialDuckDBCache)
    assert cache_file.exists()


# Real-codex subprocess tests (init → `codex mcp list` / `codex mcp get`,
# legacy-shape negative regression) live in `tests/e2e/test_codex_mcp.py`
# under the `e2e` + `requires_codex` markers. They run in the dedicated
# `just cli-e2e` lane, not in the per-file PR CI lane.


# ---------------------------------------------------------------------------
# project-venv detection — CLI-level coverage
#
# Regression coverage for the `spawn dct ENOENT` bug class. When `init mcp`
# resolves the binary to embed in MCP client configs via `shutil.which("dct")`
# alone, a non-activated venv invocation writes the literal string `"dct"`
# into `.cursor/mcp.json` etc., and Cursor (or any GUI MCP host) then fails
# to spawn the server with `spawn dct ENOENT`. The underlying resolution
# helper (`resolve_dct_executable`) and its unit tests live in
# `dbt_charts.agent_api.mcp_install` / `tests/agent_api/test_mcp_install.py` —
# this file covers only the end-to-end `dct init mcp <client>` behavior.
# ---------------------------------------------------------------------------


def test_mcp_init_cursor_writes_relative_command_when_project_venv_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_mcp_clients: None
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_project()
    venv_dct = Path(".venv/bin/dct")
    venv_dct.parent.mkdir(parents=True)
    venv_dct.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    venv_dct.chmod(0o755)

    result = runner.invoke(cli_main.app, ["init", "mcp", "cursor"])

    assert result.exit_code == 0, result.output
    config = _read_json(Path(".cursor/mcp.json"))
    assert (
        config["mcpServers"]["dbt-charts"]["command"]
        == "${workspaceFolder}/.venv/bin/dct"
    )


def test_mcp_init_claude_desktop_writes_absolute_command_when_project_venv_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_mcp_clients: None
) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_project()
    venv_dct = Path(".venv/bin/dct")
    venv_dct.parent.mkdir(parents=True)
    venv_dct.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    venv_dct.chmod(0o755)
    expected_abs = str(venv_dct.resolve())

    result = runner.invoke(cli_main.app, ["init", "mcp", "claude"])

    assert result.exit_code == 0, result.output
    claude_config = tmp_path / "home/.config/claude/config.json"
    config = _read_json(claude_config)
    command = config["mcpServers"]["dbt-charts"]["command"]
    # Claude Desktop config is absolute (not project-committed) — must stay absolute
    # and must resolve to THIS project's venv dct, not the invoking machine's own path.
    assert command == expected_abs


def test_configured_trailer_advertises_every_tool_and_resource_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_mcp_clients: None
) -> None:
    # Pins the post-configure trailer to the canonical tool + resource
    # registries — without this, the trailer silently drifts each time a
    # new tool or resource family is added (the bug this task was fixing).
    from dbt_charts.ai.mcp import server as mcp_server
    from dbt_charts.ai.tool_schemas import ALL_TOOLS

    monkeypatch.chdir(tmp_path)
    _seed_project()
    result = runner.invoke(cli_main.app, ["init", "mcp", "vscode"])
    assert result.exit_code == 0, result.output

    output = result.output
    tools_line = next(line for line in output.splitlines() if "MCP tools:" in line)
    trailer_tools = {
        t.strip() for t in tools_line.split("MCP tools:", 1)[1].split(",") if t.strip()
    }
    canonical_tools = {t["name"] for t in ALL_TOOLS}
    assert trailer_tools == canonical_tools, (
        f"Trailer drift vs ALL_TOOLS — missing {canonical_tools - trailer_tools}, extra {trailer_tools - canonical_tools}"
    )

    resources_block = "\n".join(
        line for line in output.splitlines() if "Resources:" in line or "dct://" in line
    )
    base_families = {uri.split("/", 3)[2] for uri, *_ in mcp_server._BASE_RESOURCES}
    # `dct://board/{path}` is registered as a ResourceTemplate, not in
    # _BASE_RESOURCES — pin both surfaces so removing it from server.py also
    # breaks this test, not just the trailer.
    template_families = {"board"}
    for family in base_families | template_families:
        assert family in resources_block, (
            f"Trailer omits resource family {family!r}: {resources_block}"
        )


def test_mcp_init_all_continues_past_a_failed_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch_mcp_clients: None
) -> None:
    """--all keeps configuring healthy clients after one config fails to read.

    Corrupts the first client in the registry (cursor) and asserts a later one
    (vscode) is still written — the assertion that separates ``continue`` from
    aborting the loop on the first failure.
    """
    monkeypatch.chdir(tmp_path)
    _seed_project()
    Path(".cursor").mkdir()
    corrupt = "{not valid json"
    Path(".cursor/mcp.json").write_text(corrupt, encoding="utf-8")

    result = runner.invoke(cli_main.app, ["init", "mcp", "--all"])

    assert result.exit_code == 1, result.output
    assert ".cursor/mcp.json" in result.output
    assert Path(".cursor/mcp.json").read_text(encoding="utf-8") == corrupt
    vscode = json.loads(Path(".vscode/mcp.json").read_text(encoding="utf-8"))
    assert "dbt-charts" in vscode["servers"]
