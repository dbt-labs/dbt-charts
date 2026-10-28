"""Main CLI entry point."""

import contextlib
import faulthandler
import importlib.util
import io
import logging
import os
import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from typer.core import TyperGroup

from dbt_charts._render_tz import pin_vl_convert_tz_utc
from dbt_charts.agent_api import RenderFormat, set_surface
from dbt_charts.agent_api._paths import (
    defaults_file_skip_diagnostics,
    empty_stdin_diagnostic,
    find_dct_root,
    nothing_to_render_diagnostic,
    partition_defaults_files,
    resolve_dbt_project_dir,
)
from dbt_charts.cli._console import is_plain_output
from dbt_charts.cli._error_format import (
    emit_diagnostics_jsonl,
    print_diagnostics,
    print_warning,
)
from dbt_charts.cli._extras import require_extras
from dbt_charts.cli._parsing import cwd_first_all, parse_kv_pairs
from dbt_charts.cli._project import has_charts_marker, project_dir_was_typed
from dbt_charts.cli._workspace_guard import detect_workspace_mismatch
from dbt_charts.cli.commands import (
    board_artifact as board_artifact_cmd,
    cloud as cloud_cmd,
    describe as describe_cmd,
    doctor as doctor_cmd,
    examples as examples_cmd,
    exposures as exposures_cmd,
    extension as extension_cmd,
    impact as impact_cmd,
    import_source as import_source_cmd,
    init as init_cmd,
    inspect as inspect_cmd,
    mcp as mcp_cmd,
    migrate as migrate_cmd,
    query as query_cmd,
    render as render_cmd,
    search as search_cmd,
    serve as serve_cmd,
    skills as skills_cmd,
    validate as validate_cmd,
)
from dbt_charts.cli.commands.ci_init import run_init_ci as _ci_run_init
from dbt_charts.cli.commands.mcp_init import run_init as _mcp_run_init
from dbt_charts.cli.commands.skills_init import run_init_skills as _skills_run_init
from dbt_charts.telemetry.emitter import begin_invocation, command_path

# Pin static rendering (SVG/PNG/PDF export, incl. `dct render`/`dct serve`) to
# TZ=UTC before any subcommand can reach vl-convert -- see _render_tz.py for
# why this must happen once, this early, at process startup rather than at
# the render call site.
pin_vl_convert_tz_utc()

# A native fault in a loaded driver (ADBC, nanoarrow) kills the process; this makes
# it leave a traceback first.
faulthandler.enable()

# Evaluated once at process startup — mid-session env changes won't take effect.
_RICH_MARKUP_MODE: Literal["rich"] | None = None if is_plain_output() else "rich"
# Probe for private package once at startup; avoids repeated find_spec calls.
_SUPER_SCHEMA_AVAILABLE: bool = (
    importlib.util.find_spec("dbt_charts_super_schema") is not None
)

# MCP subcommand app
mcp_app = typer.Typer(
    name="mcp",
    help="MCP (Model Context Protocol) server commands for AI assistant integration.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    rich_markup_mode=_RICH_MARKUP_MODE,
)

# Init subcommand app — `dct init` (project scaffold), `dct init mcp [client]`
# (AI integration), `dct init code|cursor|vscode` (editor extension install).
init_app = typer.Typer(
    name="init",
    help="Bootstrap a dbt charts project, plus its AI / editor integrations.",
    invoke_without_command=True,
    pretty_exceptions_show_locals=False,
    rich_markup_mode=_RICH_MARKUP_MODE,
)

# Inspect subcommand app
inspect_app = typer.Typer(
    name="inspect",
    help="Inspect database tables and manage inspect templates.",
    invoke_without_command=True,
    pretty_exceptions_show_locals=False,
    rich_markup_mode=_RICH_MARKUP_MODE,
)

# Artifact subcommand app — `dct artifact emit` / `render` / `schema`
artifact_app = typer.Typer(
    name="artifact",
    help="Emit a resolved-board artifact + recording, render one back, or print its JSON Schema.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    rich_markup_mode=_RICH_MARKUP_MODE,
)

import_app = typer.Typer(
    name="import",
    help="Import a board from another BI tool into a source bundle for migration.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    rich_markup_mode=_RICH_MARKUP_MODE,
)

# Shared --project-dir option. Every user-visible command that accepts a
# project root uses this alias so help text, validation, and DCT_PROJECT_DIR
# env-var wiring stay in lock-step.
ProjectDirOption = Annotated[
    Path | None,
    typer.Option(
        "--project-dir",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        envvar="DCT_PROJECT_DIR",
        help="Project root for resolving board paths and finding project config",
    ),
]

# See resolve_dbt_project_dir for the resolution order (flag/env >
# dbt_charts.yml `dbt_project_dir:` key > sibling default). Uses dbt-core's
# own env var name, not a DCT_-prefixed one.
DbtProjectDirOption = Annotated[
    Path | None,
    typer.Option(
        "--dbt-project-dir",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        envvar="DBT_PROJECT_DIR",
        help="External dbt project directory (dbt_project.yml, profiles.yml, "
        "target/manifest.json) when it does not sit next to dbt_charts.yml",
    ),
]


# httpx logs one INFO line per request; on `dct cloud` verbs that is a
# request-log line above every answer the user asked for. The CLI owns what it
# prints, so it decides this here rather than the client silencing a logger it
# does not own.
logging.getLogger("httpx").setLevel(logging.WARNING)


def _render_init_banner() -> None:
    """Print a nudge to run ``dct init``.

    Only called from :class:`_RootHelpGroup.format_help` when cwd is
    not inside a scaffolded project. In plain mode (agent context or
    piped stdout) the banner renders as a single plain line so context
    captures do not pick up Panel chrome.
    """
    if is_plain_output():
        Console(force_terminal=False, no_color=True).print(
            "Welcome to dbt charts — run `dct init` to start a new project. "
            "New to it, or an agent? Read `dct skills chart` first."
        )
        return
    Console().print(
        Panel(
            "Run [bold cyan]dct init[/bold cyan] to start a new project!\n"
            "New to it, or an agent? Read [bold cyan]dct skills chart[/bold cyan] first.",
            title="Welcome to dbt charts",
            title_align="left",
            border_style="yellow",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


# Panel sections on `dct --help`, in the order they render. A panel absent
# from this tuple sorts last (alphabetically among its peers).
PANEL_ORDER: tuple[str, ...] = ("Dashboards", "Data & SQL", "Cloud", "AI", "Reference")


def _panel_rank(panel: str) -> int:
    return PANEL_ORDER.index(panel) if panel in PANEL_ORDER else len(PANEL_ORDER)


_COMMAND_PATH_KEY = "dct.command_path"
_ARGS_KEY = "dct.args"


class _RootHelpGroup(TyperGroup):
    """Root-only group: alphabetize the Options panel + commands and gate the init banner.

    Click otherwise displays params in declaration order with the
    auto-added `--help` last; that puts `--version` ahead of `--help`
    on the root help. TyperGroup also overrides Click's sorted
    list_commands with registration order, which buckets sub-typer
    groups after leaf commands within the same rich_help_panel (e.g.
    `init` after doctor/playground/skills under Reference). Sorting here
    interleaves them so each panel reads A-Z. Also prints the "run
    `dct init`" banner above the help body when cwd isn't a scaffolded
    dbt charts project. Intentionally attached to the root Typer only —
    sub-typer and leaf-command orders are left as authored, and
    sub-typer help never shows the banner.
    """

    def get_params(self, ctx: Any) -> list[Any]:
        def key(p: Any) -> str:
            for opt in getattr(p, "opts", ()) or ():
                if opt.startswith("--"):
                    return opt.lstrip("-").lower()
            return (p.name or "").lower()

        return sorted(super().get_params(ctx), key=key)

    def list_commands(self, ctx: Any) -> list[str]:
        # typer.rich_utils.rich_format_help iterates this list once and buckets
        # by rich_help_panel; the per-panel order is the iteration order, and
        # the panel-section order is determined by which panel gets its first
        # command first. Sort by (panel-rank, name) so PANEL_ORDER survives
        # while sub-typer groups interleave alphabetically with leaf commands
        # inside each panel.
        #
        # Rank comes from PANEL_ORDER, not registration order: Typer lists all
        # leaf commands before sub-typer groups, so a panel whose only entry is
        # a group (AI, which holds just `mcp`) would otherwise sink below every
        # panel that has a leaf command.
        panel_by_name: dict[str, str] = {}
        for n in super().list_commands(ctx):
            cmd = self.get_command(ctx, n)
            panel_by_name[n] = getattr(cmd, "rich_help_panel", None) or ""
        return sorted(
            panel_by_name,
            key=lambda n: (_panel_rank(panel_by_name[n]), n),
        )

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        # The root callback runs after click has consumed argv, so the
        # subcommand path can only be resolved here.
        ctx.meta[_COMMAND_PATH_KEY] = command_path(self, args)
        ctx.meta[_ARGS_KEY] = list(args)
        return super().parse_args(ctx, args)

    def format_help(self, ctx: Any, formatter: Any) -> None:
        if not has_charts_marker(Path.cwd()):
            _render_init_banner()
        super().format_help(ctx, formatter)


# `help_option_names` on the root Click context inherits down to every
# sub-Typer's group context and every leaf command, so a single setting
# here gives `-h` to the whole CLI tree.
app = typer.Typer(
    name="dct",
    help="Declarative, dbt-native dashboards in YAML.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    cls=_RootHelpGroup,
    rich_markup_mode=_RICH_MARKUP_MODE,
)


# =============================================================================
# init_app — `dct init …` subcommands (registered as a sub-typer at the bottom)
# =============================================================================


@init_app.callback(invoke_without_command=True)
def init_default(
    ctx: typer.Context,
    project_dir: ProjectDirOption = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite existing scaffold files"),
    ] = False,
) -> None:
    """Bootstrap a dbt charts project in an existing repo.

    Detects dbt projects, creates charts/ and charts/partials/, writes
    charts/meta.yml, and installs the `dct` agent skill. Never prompts.

    Safe to re-run — existing files are never overwritten unless --force is used.

    Subcommands:
      dct init skills [target]  # Install the dct agent skill elsewhere
      dct init mcp [client]     # Wire up MCP server
      dct init ci               # Scaffold a board-validation GitHub Action
      dct init code             # Install dbt charts extension into VS Code
      dct init cursor           # …or Cursor

    \b
    Examples:
      dct init                        # Init the enclosing project or git repo
      dct init --project-dir ./myrepo # Init in a specific directory
      dct init --force                # Re-scaffold, overwriting files
    """
    if ctx.invoked_subcommand is not None:
        return
    init_cmd.run_wizard(
        project_dir=project_dir,
        project_dir_explicit=project_dir_was_typed(ctx),
        force=force,
    )


@init_app.command("code")
def init_code() -> None:
    """Install the dbt charts extension into VS Code.

    Installs the latest release of the dbt charts extension into VS Code.
    Idempotent — re-runs upgrade to the newest version.
    """
    raise typer.Exit(extension_cmd.install_extension("code", emit=typer.echo))


@init_app.command("cursor")
def init_cursor() -> None:
    """Install the dbt charts extension into Cursor.

    Installs the latest release of the dbt charts extension into Cursor.
    Idempotent — re-runs upgrade to the newest version.
    """
    raise typer.Exit(extension_cmd.install_extension("cursor", emit=typer.echo))


@init_app.command("vscode")
def init_vscode() -> None:
    """Alias for `dct init code`."""
    raise typer.Exit(extension_cmd.install_extension("code", emit=typer.echo))


# `dct init skills [target]` — file-based workflow skill install.
@init_app.command("skills")
def init_skills(
    ctx: typer.Context,
    target: Annotated[
        str | None,
        typer.Argument(
            help="Install target: agents (Cursor/Codex/Copilot), codex, or claude"
        ),
    ] = None,
    all_targets: Annotated[
        bool,
        typer.Option("--all", help="Install to every detected skill directory"),
    ] = False,
    dir_override: Annotated[
        Path | None,
        typer.Option(
            "--dir",
            help="Explicit skills directory (e.g. .agents/skills)",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
    global_install: Annotated[
        bool,
        typer.Option(
            "--global",
            help=(
                "Install into your user-level directories "
                "(~/.claude/skills, ~/.agents/skills) instead of a repository"
            ),
        ),
    ] = False,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help="Dry run: show what would be installed without writing files",
        ),
    ] = False,
    project_dir: ProjectDirOption = None,
) -> None:
    """Install the dct agent skill for file-based auto-discovery.

    Writes one ``dct/SKILL.md`` to ``.agents/skills/`` (Cursor, Codex,
    Copilot) and/or ``.claude/skills/`` (Claude Code) inside the current
    repository. It is a pointer at ``dct skills chart``, so it carries no
    version and never goes stale: commit it. Pass ``--global`` to install into
    your user-level directories instead (``~/.claude/skills/``,
    ``~/.agents/skills/``), so any new project on this machine picks it up
    without a per-repo install. Does not configure MCP or modify AGENTS.md /
    CLAUDE.md. Removes the per-skill ``dct-*`` copies earlier releases wrote.

    \b
    Examples:
      dct init skills                 # Detect targets and install
      dct init skills agents          # .agents/skills/ only
      dct init skills claude          # .claude/skills/ only
      dct init skills --all           # Every detected target dir
      dct init skills --dir PATH      # Explicit destination
      dct init skills --global        # ~/.claude/skills and/or ~/.agents/skills
      dct init skills --check         # Dry run
    """
    _skills_run_init(
        target=target,
        all_targets=all_targets,
        dir_override=dir_override,
        global_install=global_install,
        check=check,
        project_dir=project_dir,
        project_dir_explicit=project_dir_was_typed(ctx),
    )


# `dct init mcp [client]` — MCP config only (skills: `dct init skills`).
@init_app.command("mcp")
def init_mcp(
    client: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Client to configure: cursor, vscode, claude, claude-code, "
                "codex, copilot, or print. Omit to auto-detect."
            )
        ),
    ] = None,
    all_clients: Annotated[
        bool,
        typer.Option("--all", help="Write MCP config files for every supported client"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite existing MCP client config"),
    ] = False,
    project_dir: ProjectDirOption = None,
) -> None:
    """Add dbt charts to your AI client's MCP configuration.

    Writes MCP server entries only. Install workflow skills separately with
    ``dct init skills``.

    The project root is resolved by walking up from the current directory
    looking for dbt_charts.yml or dbt_project.yml. Pass
    --project-dir to override. When the project root differs from your AI
    client's workspace, the recorded server command points at the project
    so it starts in the right place.

    \b
    Examples:
      dct init mcp                          # Auto-detect clients + project
      dct init mcp cursor                   # Configure Cursor only
      dct init mcp claude-code              # Configure Claude Code
      dct init mcp --all                    # Write every supported config file
      dct init mcp --project-dir ./analytics  # Target a specific project dir
      dct init mcp print                    # Print config JSON (for manual setup)
    """
    _mcp_run_init(
        client=client,
        all_clients=all_clients,
        force=force,
        project_dir=project_dir,
    )


# `dct init ci` — GitHub Actions workflow that runs `dct validate` on PRs.
@init_app.command("ci")
def init_ci(
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite an existing workflow file"),
    ] = False,
    project_dir: ProjectDirOption = None,
) -> None:
    """Scaffold a GitHub Actions workflow that validates your boards on every PR.

    Writes a workflow at the *repo* root — the only place GitHub reads
    workflows from — named .github/workflows/dbt-charts.yml for a project at
    the root, or dbt-charts-<project-path>.yml for a nested one. When the dbt
    root is a subdirectory, the workflow's path filters and working directory
    carry that full repo-relative path, so a monorepo with nested dbt projects
    gates the right files, one workflow per project.

    Structural tier: `dct validate` checks board YAML shape, enums and
    references without running queries, so the workflow needs no warehouse
    credentials. Add a `dbt parse` step (see the comment in the generated file)
    to also validate dbt ref()/source() calls against your models.

    Safe to re-run — an existing workflow is never overwritten unless --force.

    \b
    Examples:
      dct init ci                            # Scaffold for the detected project
      dct init ci --project-dir ./analytics  # Target a specific dbt root
      dct init ci --force                    # Overwrite the existing workflow
    """
    _ci_run_init(force=force, project_dir=project_dir)


# =============================================================================
# inspect_app — `dct inspect …` subcommands (registered hidden at the bottom)
# =============================================================================


@inspect_app.callback(invoke_without_command=True)
def inspect_default(
    ctx: typer.Context,
    connection: Annotated[
        str,
        typer.Option(help="Database connection string", hidden=True),
    ] = ":memory:",
    dialect: Annotated[
        str,
        typer.Option(
            help="SQL dialect (duckdb, postgres, bigquery, etc.)", hidden=True
        ),
    ] = "duckdb",
    schema: Annotated[
        str | None,
        typer.Option(help="Schema filter", hidden=True),
    ] = None,
    output: Annotated[
        str,
        typer.Option(help="Path to save inspection JSON", hidden=True),
    ] = "target/super_schema.json",
    approximate: Annotated[
        str,
        typer.Option(help="Approximate profiling mode: auto, on, off", hidden=True),
    ] = "auto",
    include: Annotated[
        str | None,
        typer.Option(help="Glob pattern to include tables", hidden=True),
    ] = None,
    exclude: Annotated[
        str | None,
        typer.Option(help="Glob pattern to exclude tables", hidden=True),
    ] = None,
) -> None:
    """Manage inspect templates and profile database tables (with dbt-charts-super-schema).

    Without subcommand: profiles all tables (requires dbt-charts-super-schema).

    \b
    Examples:
      dct inspect eject model          # Copy template to charts/inspect/
      dct inspect templates            # List built-in templates
      dct inspect table orders         # Profile a table (needs private pkg)
    """
    if ctx.invoked_subcommand is not None:
        return
    if not _SUPER_SCHEMA_AVAILABLE:
        typer.echo(
            "Batch profiling requires the dbt-charts-super-schema package.\n"
            "Install it from the monorepo or your private registry.\n\n"
            "Run 'dct inspect --help' for available template-management commands.",
            err=True,
        )
        raise typer.Exit(1)
    from dbt_charts_super_schema.cli.commands.inspect import (  # noqa: PLC0415
        inspect_all_command,
    )

    inspect_all_command(
        connection=connection,
        dialect=dialect,
        schema=schema,
        output=output,
        approximate=approximate,
        include=include,
        exclude=exclude,
    )


@inspect_app.command("eject")
def inspect_eject(
    templates: Annotated[
        list[str] | None,
        typer.Argument(help="Template names to eject (e.g., model quality)"),
    ] = None,
    all_templates: Annotated[
        bool,
        typer.Option("--all", help="Eject all available templates"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Overwrite existing files"),
    ] = False,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output", "-o", help="Output directory (default: charts/inspect/)"
        ),
    ] = None,
) -> None:
    """Copy inspect templates to charts/inspect/ for customization.

    Ejected templates can be modified to customize the inspect dashboards.
    The server will use your customized version instead of the built-in template.

    \b
    Examples:
      dct inspect eject model            # Eject just the model template
      dct inspect eject model quality    # Eject specific templates
      dct inspect eject --all            # Eject all templates
      dct inspect eject model --force    # Overwrite existing
      dct inspect eject --all -o custom/ # Custom output directory
    """
    inspect_cmd.eject_command(
        templates=templates or [],
        all_templates=all_templates,
        force=force,
        output_dir=output_dir,
    )


@inspect_app.command("templates")
def inspect_templates() -> None:
    """List available inspect templates.

    Shows all built-in inspect templates that can be ejected and customized.

    \b
    Examples:
      dct inspect templates
    """
    inspect_cmd.templates_command()


@inspect_app.command("validate-templates")
def inspect_validate_templates(
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Inspect template directory (default: charts/inspect/)",
        ),
    ] = None,
) -> None:
    """Validate ejected inspect templates against current built-in template versions.

    Helps teams detect when upstream template changes require rebasing custom templates.
    """
    inspect_cmd.validate_ejected_templates_command(output_dir=output_dir)


# =============================================================================
# mcp_app — `dct mcp …` subcommands (registered as a sub-typer at the bottom)
# =============================================================================


@mcp_app.command("serve")
def mcp_serve(
    project_dir: ProjectDirOption = None,
    dbt_project_dir: DbtProjectDirOption = None,
) -> None:
    """Start the MCP server for AI assistant integration.

    This command starts an MCP (Model Context Protocol) server that enables
    AI assistants like Claude, Cursor, and ChatGPT to interact with dbt charts
    dashboards.

    The server speaks the MCP protocol over standard input/output — the
    transport AI clients expect.

    Requires the ``mcp`` extra (run ``dct mcp serve`` with it missing and
    the printed install command is the canonical one for your environment).

    \b
    Examples:
      dct mcp serve
      dct mcp serve --project-dir ./my-project

    \b
    Configuration for Claude Desktop (~/.config/claude/config.json):
      {
        "mcpServers": {
          "dbt-charts": {
            "command": "dct",
            "args": ["mcp", "serve"]
          }
        }
      }
    """
    # Extras gate runs before project discovery: mcp serve cannot run at all
    # without its deps, so a missing-extra install hint must win over a
    # "no project found" error when both apply.
    require_extras("mcp")
    mcp_cmd.serve_command(project_dir=project_dir, dbt_project_dir=dbt_project_dir)


# =============================================================================
# Root commands — grouped by panel and kept alphabetical within each block for
# readability. Neither ordering is load-bearing: `_RootHelpGroup.list_commands`
# sorts by (PANEL_ORDER rank, name), so panel order comes from PANEL_ORDER above
# and within-panel order is sorted at render time. Sub-typer add_typer() calls
# are inlined at their alphabetical positions to match.
# =============================================================================


# --- Dashboards -------------------------------------------------------------


app.add_typer(artifact_app, name="artifact", rich_help_panel="Dashboards")


@app.command("describe", rich_help_panel="Dashboards")
def describe(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(
            metavar="[PATH]...",
            help=(
                "Path(s) to board YAML files or directories to describe. "
                "Omit for the project overview."
            ),
            callback=cwd_first_all,
        ),
    ] = None,
    chart: Annotated[
        str | None,
        typer.Option(
            "--chart",
            help=(
                "Describe one chart on the board: the queries it reads and the variables "
                "it depends on. Takes exactly one board file."
            ),
        ),
    ] = None,
    all_charts: Annotated[
        bool,
        typer.Option(
            "--all", help="Project overview only: also list each board's charts."
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
    project_dir: ProjectDirOption = None,
    dbt_project_dir: DbtProjectDirOption = None,
) -> None:
    """Describe the project, or a board's queries, charts, variables, and layout.

    With no path, prints the project overview: config file, sources (and the
    `source:` value for each data file), boards with their serve URLs, and
    the warehouse tables the boards read. Add --all to list every board's charts.

    With a path, compiles the board YAML and returns a structured summary without executing
    any queries. Use this for orientation when picking up an unfamiliar dashboard.
    Accepts multiple paths; each may be a file or a directory (walked recursively).
    With --chart, describes a single chart on one board.

    \b
    Examples:
      dct describe charts/sales.yml
      dct describe charts/sales.yml --json
      dct describe charts/sales.yml --project-dir ./myrepo
      dct describe charts/
      dct describe charts/*.yml --json | jq '.[] | select(.charts | length > 5)'
      dct describe charts/sales.yml --chart revenue_kpi
      dct describe
      dct describe --all --json
    """
    if paths is None:
        if chart is not None:
            raise typer.BadParameter("--chart needs a board file", param_hint="--chart")
        paths = []
    elif all_charts:
        raise typer.BadParameter(
            "--all is for the project overview; omit the path", param_hint="--all"
        )
    elif chart is not None and len(paths) != 1:
        raise typer.BadParameter(
            "--chart takes exactly one board file", param_hint="--chart"
        )
    describe_cmd.describe_command(
        paths,
        all_charts=all_charts,
        chart=chart,
        json_output=json_output,
        project_dir=project_dir,
        dbt_project_dir=dbt_project_dir,
    )


@app.command("render", rich_help_panel="Dashboards")
def render(
    boards: Annotated[
        list[Path],
        typer.Argument(
            # No exists=/file_okay=: "-" is a valid stdin sentinel and must not fail the check.
            help='Path(s) to board YAML file(s), or "-" to read YAML from stdin (single path only)',
        ),
    ],
    output: Annotated[
        str | None,
        typer.Option(
            "--output",
            "-o",
            help=(
                "Output file path. For multiple inputs, use {stem} or {dir} "
                "placeholders (e.g. renders/{stem}.svg). Default: renders/<board>.<ext>."
            ),
        ),
    ] = None,
    format: Annotated[
        RenderFormat | None,
        typer.Option(
            help=(
                "Output format: svg, html, png, pdf, thumbnail (an abstracted "
                "SVG preview with no readable text), terminal, json, text, "
                "text-data (text plus every query's rows, grouped by query, "
                "row-capped), yaml, or data"
            )
        ),
    ] = None,
    project_dir: ProjectDirOption = None,
    dbt_project_dir: DbtProjectDirOption = None,
    var: Annotated[
        list[str] | None,
        typer.Option(help="Variable value as key=value (repeatable)"),
    ] = None,
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache", help="Bypass all query caches and re-run from scratch"
        ),
    ] = False,
    cache: Annotated[
        Path | None,
        typer.Option(
            "--cache",
            envvar="DCT_CACHE_PATH",
            help=(
                "Keep the query cache in this DuckDB file (created if absent). "
                "Default: the path in dbt_charts.yml's cache: block, else a "
                "per-user file shared by every project. Mutually exclusive "
                "with --no-cache."
            ),
        ),
    ] = None,
    diagnostics_json: Annotated[
        bool,
        typer.Option(
            "--diagnostics-json",
            help=(
                "Emit all diagnostics (errors and warnings) as JSON Lines to stderr. "
                "Stdout stays the render payload on success; stderr carries "
                "one compact JSON object per diagnostic, line-delimited."
            ),
        ),
    ] = False,
    no_warnings: Annotated[
        bool,
        typer.Option(
            "--no-warnings",
            help=(
                "Suppress warning output to stderr. Warnings are still included "
                "in --format json/text output so agents and consumers always see "
                "them."
            ),
        ),
    ] = False,
    ignore_warning: Annotated[
        list[str] | None,
        typer.Option(
            "--ignore-warning",
            help=(
                "Suppress a specific warning code (repeatable). Suppressed warnings "
                "move to suppressed_warnings in --format json output. "
                "Unknown codes print a notice but do not exit non-zero."
            ),
        ),
    ] = None,
    allow_chart_errors: Annotated[
        bool,
        typer.Option(
            "--allow-chart-errors",
            help=(
                "Allow per-chart runtime errors without exiting non-zero. "
                "The default (fail on chart errors) is the CI-safe behavior: "
                "exit 1 when any chart errors are present so regressions don't "
                "ship undetected. Use --allow-chart-errors for live previews and "
                "agent iteration where partial renders are acceptable."
            ),
        ),
    ] = False,
    max_workers: Annotated[
        int | None,
        typer.Option(
            "--max-workers",
            envvar="DCT_MAX_WORKERS",
            help=(
                "Maximum parallel query workers (default: 8, from config). "
                "Effective only for external warehouse executors — DuckDB "
                "serializes access internally regardless of this setting."
            ),
        ),
    ] = None,
    print0: Annotated[
        bool,
        typer.Option(
            "--print0",
            "-0",
            help=(
                "Delimit produced output paths with NUL (\\0) instead of newline. "
                "Enables safe piping to xargs -0 for paths containing spaces."
            ),
        ),
    ] = False,
    fail_fast: Annotated[
        bool,
        typer.Option(
            "--fail-fast",
            help=(
                "Stop immediately on the first render failure. "
                "Default: continue rendering remaining boards, exit 1 at end."
            ),
        ),
    ] = False,
    chart: Annotated[
        str | None,
        typer.Option(
            "--chart",
            help=(
                "Render only this chart (by chart id). "
                "Valid only with a single board argument or stdin."
            ),
        ),
    ] = None,
) -> None:
    """Render one or more dashboards to SVG, HTML, PNG, PDF, or terminal.

    Accepts multiple board paths; output paths are printed to stdout (one per
    line) so results can be piped to other tools. Use --print0 for NUL-delimited
    output safe with xargs -0.

    Use "-" as the sole board argument to read YAML from stdin.

    By default, per-chart runtime errors (missing columns, query failures)
    cause exit 1 so CI smoke tests detect broken charts. Pass
    --allow-chart-errors to suppress this and keep the partial render
    behavior for live previews.

    \b
    Examples:
      dct render charts/sales.yml
      dct render charts/sales.yml --format html
      dct render charts/*.yml --output renders/{stem}.svg
      dct render charts/*.yml --output renders/{stem}.svg --print0 | xargs -0 ls -lh
      dct render charts/sales.yml --var region=West --var category=Electronics
      dct render charts/sales.yml --format terminal
      dct render charts/sales.yml --no-cache
      dct render charts/sales.yml --cache renders.duckdb
      dct render charts/sales.yml --diagnostics-json
      dct render charts/sales.yml --no-warnings
      dct render charts/sales.yml --ignore-warning WARN-REDUNDANT-ENCODING
      dct render charts/sales.yml --allow-chart-errors
      printf 'source: data.csv\\ncharts:\\n  c:\\n    type: table\\n    query: select * from data\\n' | dct render - --format terminal
    """
    if cache is not None and no_cache:
        raise typer.BadParameter(
            "--cache (or its DCT_CACHE_PATH env backing) and --no-cache are "
            "mutually exclusive",
            param_hint="--cache",
        )
    variables = parse_kv_pairs(var or [], "--var")
    use_cache = not no_cache
    ignore_codes = set(ignore_warning) if ignore_warning else None
    fail_on_chart_errors = not allow_chart_errors

    # Reject stdin mixed with other paths
    has_stdin = any(str(f) == "-" for f in boards)
    if has_stdin and len(boards) > 1:
        raise typer.BadParameter(
            '"-" (stdin) can only be used as the sole board argument',
            param_hint="BOARDS",
        )

    boards, defaults_files = partition_defaults_files(boards)
    if defaults_files:
        skip_diagnostics = defaults_file_skip_diagnostics(defaults_files)
        if ignore_codes:
            skip_diagnostics = [
                d for d in skip_diagnostics if d.code not in ignore_codes
            ]
        if skip_diagnostics:
            if diagnostics_json:
                emit_diagnostics_jsonl(skip_diagnostics)
            elif not no_warnings:
                print_diagnostics(skip_diagnostics)
    if not boards:
        error_diagnostic = nothing_to_render_diagnostic()
        if diagnostics_json:
            emit_diagnostics_jsonl([error_diagnostic])
        else:
            print_diagnostics([error_diagnostic])
        raise typer.Exit(1)

    # Infer format from output extension when not explicitly given
    effective_format: RenderFormat = (
        format or (output and render_cmd.infer_format_from_extension(output)) or "svg"
    )

    # Reject flat --output when rendering multiple boards (ambiguous destination)
    if len(boards) > 1 and output is not None:
        if "{stem}" not in output and "{dir}" not in output:
            raise typer.BadParameter(
                f'use a template like "{{stem}}.{effective_format}" for multiple inputs; '
                f'"{output}" is ambiguous with {len(boards)} board arguments',
                param_hint="--output",
            )

    # --chart is ambiguous with multiple boards: no mapping syntax, no silent fanout.
    if chart is not None and len(boards) > 1:
        raise typer.BadParameter(
            f"--chart is only valid with a single board argument; "
            f"got {len(boards)} board arguments",
            param_hint="--chart",
        )

    # Stdin path (single board "-")
    if has_stdin:
        yaml_content = sys.stdin.read()
        if not yaml_content.strip():
            empty = empty_stdin_diagnostic()
            if diagnostics_json:
                emit_diagnostics_jsonl([empty])
            else:
                print_diagnostics([empty])
            raise typer.Exit(1)
        render_cmd.render_command_from_yaml(
            yaml_content=yaml_content,
            output=output,
            format=effective_format,
            project_dir=project_dir,
            dbt_project_dir=dbt_project_dir,
            variables=variables or None,
            use_cache=use_cache,
            cache_path=cache,
            diagnostics_json=diagnostics_json,
            no_warnings=no_warnings,
            ignore_codes=ignore_codes,
            fail_on_chart_errors=fail_on_chart_errors,
            max_workers=max_workers,
            print0=print0,
            chart=chart,
        )
        return

    render_cmd.render_commands(
        board_paths=boards,
        output=output,
        format=effective_format,
        project_dir=project_dir,
        dbt_project_dir=dbt_project_dir,
        variables=variables or None,
        use_cache=use_cache,
        cache_path=cache,
        diagnostics_json=diagnostics_json,
        no_warnings=no_warnings,
        ignore_codes=ignore_codes,
        fail_on_chart_errors=fail_on_chart_errors,
        max_workers=max_workers,
        print0=print0,
        fail_fast=fail_fast,
        chart=chart,
    )


@artifact_app.command("emit")
def emit_board(
    board: Annotated[
        Path,
        typer.Argument(
            help="Path to the board YAML file to compile, resolve, and execute."
        ),
    ],
    project_dir: ProjectDirOption = None,
    dbt_project_dir: DbtProjectDirOption = None,
    artifact: Annotated[
        Path | None,
        typer.Option(
            help=(
                "Where to write the board artifact (the resolved board's "
                "published, data-free contract). Default: "
                "boards/<board-stem>.board.json."
            )
        ),
    ] = None,
    recording: Annotated[
        Path | None,
        typer.Option(
            help=(
                "Where to write the recording sidecar (the rows the board was "
                "resolved against, plus variable values and a recorded_at "
                "stamp). Default: boards/<board-stem>.recording.json."
            )
        ),
    ] = None,
    var: Annotated[
        list[str] | None,
        typer.Option(help="Variable value as key=value (repeatable)"),
    ] = None,
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache", help="Bypass all query caches and re-run from scratch"
        ),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Compile, resolve, and execute a board; write its board artifact + recording.

    The artifact is the resolved board's published, data-free contract; the
    recording is a separate sidecar carrying the rows it was resolved
    against. Render the pair back with `dct artifact render` — no compile step,
    no warehouse connection.

    \b
    Examples:
      dct artifact emit charts/sales.yml
      dct artifact emit charts/sales.yml --artifact out/sales.board.json --recording out/sales.recording.json
      dct artifact emit charts/sales.yml --var region=West
    """
    board_artifact_cmd.emit_board_command(
        board,
        project_dir=project_dir,
        dbt_project_dir=dbt_project_dir,
        artifact=artifact,
        recording=recording,
        var=var,
        no_cache=no_cache,
        json_output=json_output,
    )


@artifact_app.command("render")
def render_board(
    artifact: Annotated[Path, typer.Argument(help="Path to a board artifact file.")],
    recording: Annotated[
        Path, typer.Argument(help="Path to the artifact's recording sidecar.")
    ],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", "-o", help="Write the rendered SVG here. Default: stdout."
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """Render a board from its artifact + recording — no compile, no warehouse.

    Both files come from `dct artifact emit`. A recording missing a query, or
    captured under different variable values than the artifact expects,
    fails loudly rather than rendering an empty or wrong chart.

    \b
    Examples:
      dct artifact render out/sales.board.json out/sales.recording.json
      dct artifact render out/sales.board.json out/sales.recording.json --output sales.svg
    """
    board_artifact_cmd.render_board_command(
        artifact,
        recording,
        output=output,
        json_output=json_output,
    )


@artifact_app.command("schema")
def artifact_schema() -> None:
    """Print the JSON Schema (draft 2020-12) that board artifacts conform to.

    Every artifact `dct artifact emit` writes names this schema in its `$id`.
    The schema matches the installed dct version.

    \b
    Examples:
      dct artifact schema > board-resolved.schema.json
    """
    board_artifact_cmd.schema_command()


@app.command("search", rich_help_panel="Dashboards")
def search(
    text: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Boards: keywords to match against board metadata. Other kinds: "
                "a substring to find in file contents. Omit to list."
            )
        ),
    ] = None,
    kind: Annotated[
        str,
        typer.Option(
            "--kind",
            help="What to search: boards, meta, models, config, or all",
        ),
    ] = "boards",
    path: Annotated[
        str | None,
        typer.Option("--path", help="Glob over project-relative paths to narrow by"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum results to return (default 10, max 200)"),
    ] = 10,
    project_dir: ProjectDirOption = None,
    dbt_project_dir: DbtProjectDirOption = None,
) -> None:
    """Search the project's boards, or its meta, model, and config files.

    Boards come back ranked by relevance with the title, summary, match score,
    source path, and matched chart names. Other kinds return the matching
    lines, or the file paths when no text is given.

    \b
    Examples:
      dct search revenue
      dct search "monthly trends" --limit 5
      dct search orders --json
      dct search --kind meta
      dct search order_id --kind models
    """
    search_cmd.search_command(
        text=text,
        kind=kind,
        path=path,
        json_output=json_output,
        limit=limit,
        project_dir=project_dir,
        dbt_project_dir=dbt_project_dir,
    )


@app.command("impact", rich_help_panel="Dashboards")
def impact(
    column: Annotated[
        str,
        typer.Argument(help="Column name to look up (case-insensitive)"),
    ],
    table: Annotated[
        str | None,
        typer.Option("--table", help="Narrow to columns of this table/model"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
    project_dir: ProjectDirOption = None,
    dbt_project_dir: DbtProjectDirOption = None,
) -> None:
    """Which boards break if this column changes.

    Walks the compiled SQL of every board under charts/ — named queries, inline chart queries,
    layers, cross-board imports — and reports the ones that reference the
    column on a base table, resolved through CTEs, aliases and correlated
    subqueries. ref('orders') maps to `orders`; source('raw', 'orders') to
    the dotted `raw.orders`. Boards whose column use cannot be determined (a
    `SELECT *`, SQL that does not parse, a jinja expression standing where a
    column or table name would be, an unresolvable ref()/source() spelling)
    are listed separately rather than silently omitted, so an empty hit list
    is never read as "safe to rename" without checking them.

    No warehouse connection — the index is built from the YAML alone.

    \b
    Examples:
      dct impact customer_id                 # Who references this column?
      dct impact id --table users            # Narrow a common name to one table
      dct impact customer_id --json          # Agent-consumable output
    """
    impact_cmd.impact_command(
        column,
        table=table,
        json_output=json_output,
        project_dir=project_dir,
        dbt_project_dir=dbt_project_dir,
    )


@app.command("exposures", rich_help_panel="Dashboards")
def exposures(
    check: Annotated[
        bool,
        typer.Option("--check", help="Exit 1 if the file is stale; write nothing"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
    project_dir: ProjectDirOption = None,
    dbt_project_dir: DbtProjectDirOption = None,
) -> None:
    """Write one dbt exposure per board that calls ref() or source().

    The file (`exposures.path` in dbt_charts.yml, default
    models/dbt_charts/exposures.yml) puts boards into dbt's DAG:
    `dbt ls --select fct_orders+` then lists the boards reading fct_orders.
    `exposures.owner` is required. Commit the file so dbt platform jobs see
    it, and run `--check` in CI to catch a board changed without re-running.

    \b
    Examples:
      dct exposures            # Regenerate the exposures file
      dct exposures --check    # CI: fail if it is out of date
    """
    exposures_cmd.exposures_command(
        check=check,
        json_output=json_output,
        project_dir=project_dir,
        dbt_project_dir=dbt_project_dir,
    )


@app.command("serve", rich_help_panel="Dashboards")
def serve(
    port: Annotated[
        int | None,
        typer.Option(help="Port number (auto-resolved if not set)"),
    ] = None,
    host: Annotated[
        str,
        typer.Option(help="Host address"),
    ] = "localhost",
    project_dir: ProjectDirOption = None,
    dbt_project_dir: DbtProjectDirOption = None,
    dialect: Annotated[
        str | None,
        typer.Option(help="SQL dialect (auto-detected from dbt, or duckdb)"),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option(
            help="dbt target name (default: DBT_TARGET env, then profile default)"
        ),
    ] = None,
    max_workers: Annotated[
        int | None,
        typer.Option(
            "--max-workers",
            envvar="DCT_MAX_WORKERS",
            help=(
                "Maximum parallel query workers per render (default: 8, from config). "
                "Effective only for external warehouse executors — DuckDB "
                "serializes access internally regardless of this setting."
            ),
        ),
    ] = None,
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Skip the query-result cache entirely (every request re-runs queries).",
        ),
    ] = False,
    cache: Annotated[
        Path | None,
        typer.Option(
            "--cache",
            envvar="DCT_CACHE_PATH",
            help=(
                "Keep the query cache in this DuckDB file (created if absent). "
                "Default: the path in dbt_charts.yml's cache: block, else a "
                "per-user file shared by every project. Several dct processes "
                "can share one file. Mutually exclusive with --no-cache."
            ),
        ),
    ] = None,
    detach: Annotated[
        bool,
        typer.Option(
            "--detach",
            help=(
                "Serve in the background, check every board, print each board's "
                "URL and status, and return. A server already running this "
                "project on the port is reused; one for another project is "
                "replaced. Stop it with --stop."
            ),
        ),
    ] = False,
    stop: Annotated[
        bool,
        typer.Option("--stop", help="Stop the server --detach left on --port."),
    ] = False,
    describe: Annotated[
        bool,
        typer.Option("--describe", help="With --detach, print what each chart draws."),
    ] = False,
) -> None:
    """Start the dashboard server.

    Renders your dashboards in the browser. Board file paths map
    to URLs (charts/sales.yml → /sales/). Query params become variables.

    Auto-discovers the project root by walking UP from the current directory
    to find dbt_charts.yml or dbt_project.yml. Subdirectories are not searched.
    When a dbt project is found, the SQL dialect is inferred from the profile
    target.

    dbt profile location is resolved in this order:
      1. profiles_dir field in the dbt_profile source config (dbt_charts.yml)
      2. DBT_PROFILES_DIR environment variable
      3. Linked dbt project directory (next to dbt_charts.yml by default;
         --dbt-project-dir / DBT_PROJECT_DIR / dbt_project_dir: links an
         external one)
      4. ~/.dbt/profiles.yml

    Port is auto-resolved: --port flag > DCT_PORT env var > dbt_charts.yml
    server.port > deterministic hash of project directory. If the chosen port
    is occupied, the next available port is used automatically.

    Query results are cached in a per-user file by default, so they survive
    restarts and are shared with `dct render`. Pass --cache <path> to use
    another file, or --no-cache to re-run every query.

    \b
    Examples:
      dct serve
      dct serve --port 3000
      dct serve --dialect duckdb
      dct serve --target prod
      dct serve --max-workers 16
      dct serve --cache cache.duckdb
      dct serve --no-cache
      dct serve --detach
      dct serve --stop --port 9123
    """
    if cache is not None and no_cache:
        raise typer.BadParameter(
            "--cache (or its DCT_CACHE_PATH env backing) and --no-cache are "
            "mutually exclusive",
            param_hint="--cache",
        )
    if detach and stop:
        raise typer.BadParameter("--detach and --stop are mutually exclusive")
    serve_cmd.serve_command(
        port=port,
        host=host,
        project_dir=project_dir,
        dbt_project_dir=dbt_project_dir,
        dialect=dialect,
        target=target,
        max_workers=max_workers,
        no_cache=no_cache,
        cache_path=cache,
        detach=detach,
        stop=stop,
        describe=describe,
    )


@app.command("validate", rich_help_panel="Dashboards")
def validate(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(
            metavar="[PATH]...",
            help="Board YAML files or directories (default: charts/)",
            callback=cwd_first_all,
        ),
    ] = None,
    project_dir: ProjectDirOption = None,
    dbt_project_dir: DbtProjectDirOption = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
    strict: Annotated[
        bool,
        typer.Option(help="Errors always fail; with --strict, warnings also fail."),
    ] = False,
    warehouse: Annotated[
        bool,
        typer.Option(
            "--warehouse",
            help=(
                "Validate queries against the warehouse using the per-adapter mechanism. "
                "DuckDB: DESCRIBE — schema only, no billing. "
                "csv/json/parquet: DESCRIBE — schema only, but must materialize "
                "the source's files first. "
                "BigQuery: native dry-run — validity + schema, unbilled. "
                "Postgres/Redshift/Snowflake: EXPLAIN — validity only, "
                "no result schema. "
                "Every other adapter reports 'unchecked' — queries are never run to "
                "check them."
            ),
        ),
    ] = False,
) -> None:
    """Fast YAML schema + cross-reference validation. No DB, no execute.

    By default, validation is stateless — no warehouse connection, no query
    execution. Add --warehouse to validate queries against your warehouse using
    the cheapest per-adapter mechanism (DuckDB and csv/json/parquet: DESCRIBE;
    BigQuery: dry-run; Postgres/Redshift/Snowflake: EXPLAIN, validity only;
    'unchecked' everywhere else). No query is ever run at full cost to check
    it.

    \b
    Examples:
      dct validate                              # Validate all boards in charts/
      dct validate charts/                       # Validate all boards in a directory
      dct validate charts/sales.yml              # Validate one file
      dct validate charts/*.yml                  # Shell-expanded glob
      dct validate charts/*.yml --json | jq '.[] | select(.success == false)'
      dct validate charts/sales.yml charts/orders.yml --strict
      dct validate charts/sales.yml --warehouse  # Warehouse-level validation
    """
    validate_cmd.validate_command(
        paths=paths,
        project_dir=project_dir,
        dbt_project_dir=dbt_project_dir,
        json_output=json_output,
        strict=strict,
        warehouse=warehouse,
    )


app.add_typer(import_app, name="import", rich_help_panel="Dashboards")


@import_app.command("looker")
def import_looker(
    dashboard: Annotated[
        str, typer.Argument(help="Looker dashboard URL or numeric dashboard id.")
    ],
    out: Annotated[
        Path | None,
        typer.Option(
            "--out", "-o", help="Bundle directory. Default: ./looker-<dashboard id>."
        ),
    ] = None,
    base_url: Annotated[
        str | None,
        typer.Option(
            "--base-url",
            envvar="LOOKER_BASE_URL",
            help=(
                "Looker API origin, e.g. https://acme.looker.com:19999. Required "
                "for a bare id; for a URL it overrides the URL's origin (the API "
                "port differs by instance)."
            ),
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Replace a non-empty bundle directory.")
    ] = False,
) -> None:
    """Fetch a Looker dashboard into a source bundle: outline, SQL, values, render.

    Reads LOOKER_CLIENT_ID and LOOKER_CLIENT_SECRET from the environment;
    nothing is cached or written to disk besides the bundle.

    \b
    Examples:
      dct import looker https://acme.looker.com/dashboards/42
      dct import looker 42 --base-url https://acme.looker.com:19999
    """
    import_source_cmd.import_looker_command(
        dashboard, out=out, base_url=base_url, force=force
    )


@import_app.command("omni")
def import_omni(
    dashboard: Annotated[
        str | None, typer.Argument(help="Omni dashboard URL or identifier.")
    ] = None,
    list_only: Annotated[
        bool, typer.Option("--list", help="List dashboards instead of importing one.")
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out", "-o", help="Bundle directory. Default: ./omni-<dashboard id>."
        ),
    ] = None,
    base_url: Annotated[
        str | None,
        typer.Option(
            "--base-url",
            envvar="OMNI_BASE_URL",
            help=(
                "Omni origin, e.g. https://acme.omniapp.co. Required for a bare "
                "id and for --list; for a URL it overrides the URL's origin."
            ),
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Replace a non-empty bundle directory.")
    ] = False,
) -> None:
    """Fetch an Omni dashboard into a source bundle: outline, SQL, values, renders.

    Reads OMNI_API_KEY from the environment.

    \b
    Examples:
      dct import omni --list
      dct import omni https://acme.omniapp.co/dashboards/abc12345
      dct import omni abc12345 --base-url https://acme.omniapp.co
    """
    import_source_cmd.import_omni_command(
        dashboard, list_only=list_only, out=out, base_url=base_url, force=force
    )


@import_app.command("sigma")
def import_sigma(
    workbook: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Sigma workbook URL, url id or workbook id. With --list, a "
                "case-insensitive filter on workbook name and path instead."
            )
        ),
    ] = None,
    list_only: Annotated[
        bool,
        typer.Option("--list", help="List visible workbooks instead of importing."),
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out", "-o", help="Bundle directory. Default: ./sigma-<workbook url id>."
        ),
    ] = None,
    base_url: Annotated[
        str | None,
        typer.Option(
            "--base-url",
            envvar="SIGMA_API_BASE",
            help=(
                "Sigma API origin for your cloud region, e.g. "
                "https://aws-api.sigmacomputing.com."
            ),
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Replace a non-empty bundle directory.")
    ] = False,
) -> None:
    """Fetch a Sigma workbook into a source bundle: outline, SQL, values, renders.

    Reads SIGMA_CLIENT_ID and SIGMA_CLIENT_SECRET from the environment;
    nothing is cached or written to disk besides the bundle.

    \b
    Examples:
      dct import sigma https://app.sigmacomputing.com/acme/workbook/AbC123
      dct import sigma AbC123 --base-url https://aws-api.sigmacomputing.com
      dct import sigma --list revenue
    """
    import_source_cmd.import_sigma_command(
        workbook, list_only=list_only, out=out, base_url=base_url, force=force
    )


@import_app.command("hex")
def import_hex(
    project: Annotated[
        str | None, typer.Argument(help="Hex project URL or project id (a UUID).")
    ] = None,
    list_projects: Annotated[
        bool,
        typer.Option("--list", help="List the projects the token can view and exit."),
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out", "-o", help="Bundle directory. Default: ./hex-<project id>."
        ),
    ] = None,
    base_url: Annotated[
        str | None,
        typer.Option(
            "--base-url",
            envvar="HEX_BASE_URL",
            help=(
                "Hex API origin. Default: the project URL's origin, else the "
                "public https://app.hex.tech/api/v1."
            ),
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Replace a non-empty bundle directory.")
    ] = False,
) -> None:
    """Fetch a published Hex app into a source bundle: outline, SQL, chart renders.

    Reads HEX_API_TOKEN from the environment (one token per workspace). Hex has
    no whole-app screenshot, so png/ holds one render per chart cell of the
    latest run, and an app without chart cells is refused.

    \b
    Examples:
      dct import hex --list
      dct import hex https://app.hex.tech/acme/hex/<project id>
    """
    import_source_cmd.import_hex_command(
        project, list_projects=list_projects, out=out, base_url=base_url, force=force
    )


@app.command("migrate", rich_help_panel="Dashboards")
def migrate(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(
            metavar="[PATH]...",
            help="Board YAML files or directories (default: project root).",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report changes without writing files."),
    ] = False,
    project_dir: ProjectDirOption = None,
    dbt_project_dir: DbtProjectDirOption = None,
) -> None:
    """Rewrite supported older board YAML syntax for the latest released dbt charts version.

    dbt charts recognizes a board's newest compatible retained grammar, then
    applies only declared lossless moves -- never further than the latest
    *released* version, even when a further change is already in flight for
    the next release. Also stamps an informational _schema_version field with
    the version it migrated to; authors never write this field by hand.

    \b
    Examples:
      dct migrate --dry-run
      dct migrate
      dct migrate charts/revenue.yml
      dct migrate charts/
    """
    migrate_cmd.migrate_command(
        paths,
        project_dir=project_dir,
        dbt_project_dir=dbt_project_dir,
        dry_run=dry_run,
    )


# --- Data & SQL -------------------------------------------------------------


@app.command(
    "query",
    rich_help_panel="Data & SQL",
    help="""\b
SQL and named board queries — common forms:
  dct query
  dct query SOURCE
  dct query SOURCE 'SQL'
  dct query data/orders.csv 'SELECT * FROM orders'
  dct query BOARD.yml REFERENCE
  dct query SOURCE 'SQL' --validate

With no operands, lists the configured sources and the data files in the project
root and data/, each with the `source:` value to write (an unknown source name
lists them too, and suggests a data file of the same name).

With no SQL, a source (duckdb, sqlite, csv, parquet, json; a file path such as
./shop.csv works too) prints a data profile: per table the row count; per column
type, nulls, values or range; 3 sample rows. --table NAME (repeatable) profiles those tables in full.
In `sh -c`, wrap SQL that has string literals in double quotes, as in
"SELECT n FROM t WHERE kind = 'new'" (or use --file).

The first operand is the query context: a data source name, a CSV/JSON/Parquet
file path (table name = file stem, as a board's `source:`), or a board file
(.yml, .yaml, .md, .markdown).
--file reads SQL from a file for source contexts.
Results print as CSV under the same byte budget as the MCP execute_sql tool;
use SQL LIMIT to bound rows. --json returns up to 1,000 rows.""",
)
def query(
    context: Annotated[
        str | None,
        typer.Argument(
            metavar="CONTEXT",
            help="Data source name, data file path, or board path",
        ),
    ] = None,
    query_text: Annotated[
        str | None,
        typer.Argument(
            metavar="QUERY",
            help="SQL string for source contexts, or query reference for boards",
        ),
    ] = None,
    validate: Annotated[
        bool,
        typer.Option(
            "--validate",
            help="Static SQL lint only (works for named queries and raw SQL)",
        ),
    ] = False,
    file: Annotated[
        Path | None,
        typer.Option("--file", "-f", metavar="PATH", help="Read SQL from file"),
    ] = None,
    table: Annotated[
        list[str] | None,
        typer.Option("--table", help="Profile this table in full (repeatable; no SQL)"),
    ] = None,
    dialect: Annotated[
        str | None,
        typer.Option(
            "--dialect",
            help=(
                "SQL dialect for lint (duckdb, bigquery, …); "
                "defaults to the source's dialect"
            ),
        ),
    ] = None,
    var: Annotated[
        list[str] | None,
        typer.Option("--var", help="Variable override as key=value (repeatable)"),
    ] = None,
    show_suppressed: Annotated[
        bool,
        typer.Option("--show-suppressed", help="Include suppressed lint diagnostics"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="JSON output"),
    ] = False,
    project_dir: ProjectDirOption = None,
    dbt_project_dir: DbtProjectDirOption = None,
) -> None:
    variables = parse_kv_pairs(var or [], "--var")

    query_cmd.query_command(
        context=context,
        query_text=query_text,
        validate=validate,
        file=file,
        table=table,
        dialect=dialect,
        vars=variables or None,
        show_suppressed=show_suppressed,
        json_output=json_output,
        project_dir=project_dir,
        dbt_project_dir=dbt_project_dir,
    )


# --- Cloud ------------------------------------------------------------------


app.add_typer(cloud_cmd.cloud_app, name="cloud", rich_help_panel="Cloud")


# --- AI ---------------------------------------------------------------------


app.add_typer(mcp_app, name="mcp", rich_help_panel="AI")


# --- Reference --------------------------------------------------------------


@app.command("doctor", rich_help_panel="Reference")
def doctor(
    project_dir: ProjectDirOption = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
    with_warehouse: Annotated[
        bool,
        typer.Option(
            "--with-warehouse",
            help="Also connect to each warehouse source with SELECT 1.",
        ),
    ] = False,
) -> None:
    """Diagnose why dct can't reach your data: install, project, adapters.

    Reports how dbt Charts was installed, whether the project and profiles.yml
    were found, and whether each source's warehouse adapter is installed. Paste
    the output into a support ticket. Exits 1 when any check fails.

    \b
    Examples:
      dct doctor                   # Offline checks only
      dct doctor --with-warehouse  # Also test each warehouse connection
      dct doctor --json            # Machine-readable report
    """
    doctor_cmd.doctor_command(
        project_dir=project_dir,
        with_warehouse=with_warehouse,
        json_output=json_output,
    )


app.add_typer(init_app, name="init", rich_help_panel="Reference")


PLAYGROUND_URL = "https://play.dbtcharts.com"


@app.command("playground", rich_help_panel="Reference")
def playground() -> None:
    """Open the hosted dbt Charts playground in your browser."""
    typer.echo(PLAYGROUND_URL)
    with contextlib.suppress(OSError):
        webbrowser.open(PLAYGROUND_URL)


@app.command("examples", rich_help_panel="Reference")
def examples(
    slug: Annotated[
        str | None,
        typer.Argument(help="Example slug to print (default: list all)"),
    ] = None,
    search: Annotated[
        str | None,
        typer.Option("--search", "-s", help="Search slugs, titles, and board YAML"),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=25, help="Max search hits (default 10)"),
    ] = 10,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
) -> None:
    """List bundled board specimens, search them, or print one by slug.

    \b
    Modes:
      dct examples                       # List all specimens, grouped by category
      dct examples <slug>                # Print that specimen's board YAML
      dct examples -s "<query>"          # Search slugs, titles, and board YAML
      dct examples --json                # Stable JSON for any of the above

    Specimens are complete boards that render standalone: inline data, no
    `source:`. Point their queries at your own source after copying. Workflow
    prose is `dct skills`; the YAML field reference is `dct skills chart`.
    """
    examples_cmd.examples_command(
        slug=slug,
        search=search,
        limit=limit,
        as_json=as_json,
    )


@app.command("skills", rich_help_panel="Reference")
def skills(
    skill_name: Annotated[
        str | None,
        typer.Argument(
            metavar="SKILL_NAME", help="Skill name to show (default: list all)"
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON"),
    ] = False,
) -> None:
    """List packaged agent skills, or show one by name.

    \b
    Modes:
      dct skills                  # List all skills (grouped: workflows + patterns)
      dct skills <name>           # Show the named skill body
      dct skills --json           # Stable JSON for any of the above

    Skills are agent workflows and layout patterns; `dct skills chart` is the
    YAML field reference.
    """
    skills_cmd.skills_command(
        name=skill_name,
        as_json=as_json,
    )


# Hidden — not shown in root help
app.add_typer(inspect_app, name="inspect", hidden=True)

# Load CLI plugins registered via [project.entry-points."dbt_charts.cli_plugins"].
# Each plugin's ``register()`` callable mounts its subcommands onto Typer apps
# (e.g. ``inspect_app``) before the app executes. Runs at import time so that
# ``dct --help`` shows profiler commands when the private package is installed.
from importlib.metadata import entry_points as _entry_points

for _ep in _entry_points(group="dbt_charts.cli_plugins"):
    _plugin = _ep.load()
    _plugin()


def _reconfigure_console_encoding() -> None:
    """Force UTF-8 on stdout/stderr so non-ASCII diagnostics never crash.

    Windows consoles default to the legacy cp1252 codepage; printing
    non-ASCII text (e.g. an arrow in an error message) would otherwise raise
    UnicodeEncodeError while reporting the original error. No-op on
    Linux/macOS, where the streams are already UTF-8. Streams without
    reconfigure() — e.g. one swapped in under test capture — are left alone.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        from dbt_charts.agent_api.version_info import collect

        typer.echo(collect().render())
        raise typer.Exit()


def _never_fail(fn: Callable[..., None], *args: Any) -> None:
    with contextlib.suppress(Exception):  # noqa: BLE001 — telemetry must never fail the command it measures
        fn(*args)


def _option_path(args: list[str], option: str, envvar: str) -> Path | None:
    """Read a subcommand's path option from raw argv or its env var.

    The root callback runs before subcommand options are parsed, so telemetry
    reads the two project-dir options itself. Flag wins over env and the last
    occurrence wins, as in click.
    """
    value = os.environ.get(envvar)
    for i, token in enumerate(args):
        if token == "--":
            break
        if token == option and i + 1 < len(args):
            value = args[i + 1]
        elif token.startswith(f"{option}="):
            value = token.removeprefix(f"{option}=")
    return Path(value).resolve() if value else None


def _targeted_dbt_project_dir(args: list[str]) -> Path:
    """The directory whose dbt_project.yml carries consent and project identity."""
    project_dir = _option_path(args, "--project-dir", "DCT_PROJECT_DIR")
    if project_dir is None:
        project_dir = find_dct_root() or Path.cwd()
    return resolve_dbt_project_dir(
        project_dir, _option_path(args, "--dbt-project-dir", "DBT_PROJECT_DIR")
    )


def _track_invocation(ctx: typer.Context, no_send: bool) -> None:
    try:
        invocation = begin_invocation(
            ctx.meta[_COMMAND_PATH_KEY],
            no_send,
            _targeted_dbt_project_dir(ctx.meta[_ARGS_KEY]),
        )
    except ValueError as exc:
        print_warning(f"usage stats disabled: {exc}")
        return
    if invocation is not None:
        ctx.call_on_close(lambda: _never_fail(invocation.end, sys.exc_info()[1]))


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            help="Print version and exit.",
            callback=version_callback,
            is_eager=True,
        ),
    ] = None,
    no_workspace_guard: Annotated[
        bool,
        typer.Option(
            "--no-workspace-guard",
            envvar="DCT_NO_WORKSPACE_GUARD",
            help=(
                "Suppress the workspace-mismatch advisory on stderr. For machine "
                "stderr consumers (--diagnostics-json readers, CI smoke runs) "
                "where the advisory's non-JSON first line corrupts parsing."
            ),
        ),
    ] = False,
    no_send_anonymous_usage_stats: Annotated[
        bool,
        typer.Option(
            "--no-send-anonymous-usage-stats",
            help="Do not send anonymous usage statistics.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Print internal log lines to stderr, for debugging dct itself.",
        ),
    ] = False,
) -> None:
    """dct - Declarative, dbt-native dashboards in YAML."""
    # Diagnostics are the user-facing channel; library log records repeat them,
    # so they print only on request.
    if verbose:
        logging.basicConfig(
            level=logging.INFO, format="%(name)s - %(levelname)s - %(message)s"
        )
        logging.getLogger("dbt_charts").setLevel(logging.DEBUG)
    _reconfigure_console_encoding()
    _never_fail(_track_invocation, ctx, no_send_anonymous_usage_stats)
    # Runs before any subcommand, so every warehouse query this process sends is
    # attributed to the CLI. `dct serve` narrows it to "serve" in its own command.
    set_surface("cli")
    if not no_workspace_guard and (mismatch := detect_workspace_mismatch(Path.cwd())):
        print_warning(mismatch)


if __name__ == "__main__":
    app()
