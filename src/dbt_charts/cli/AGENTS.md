# dbt_charts/cli

`dct` command-line interface. The job is exactly three things: parse argv into structured arguments, call one function from `dbt_charts.agent_api`, and format the result for the terminal. Owns exit codes, help-text composition, error-panel rendering, JSON output, and TTY/agent-context detection. Does NOT own query compilation, dashboard rendering, dbt adapter construction, schema inspection, port resolution, theme application, or any AI/LLM dispatch — that all lives in `dbt_charts.agent_api` or `dbt_charts.core`.

## Implementation philosophy

### Imports

- ALLOWED: `dbt_charts.agent_api.*`, `dbt_charts.cli._*` (shared CLI infrastructure), `typer`, `rich.*`, `dbt_charts._install_hint`.
- ALLOWED: `dbt_charts.cloud_client.*` — the Cloud HTTP client, token store, wire contract, and per-invocation org/project resolution behind `dct cloud`. It is `agent_api`'s networked sibling, not a replacement: `agent_api` is local-by-contract (no network, no Django) and `cloud_client` is the only module that talks to Cloud. Same thin-wrapper rule applies — `cli/commands/cloud.py` parses arguments, calls one client method, prints the result. Tach-enforced in both directions: `dbt_charts.cli` declares the edge, and `dbt_charts.cloud_client` may import nothing first-party at all.
- FORBIDDEN: `dbt_charts.core.*` — route via `agent_api`. (The convention is `surface → agent_api → core`. Do not add direct `core` imports in new code; existing ones are debt.)
- EXCEPTION: `dbt_charts.cli.filesystem_project` — the composition-root module that owns the concrete `FilesystemProject`. It is the one sanctioned `cli → core` importer (it builds the local-filesystem `Project` from `core.project`/`core.compile` primitives), declared as its own tach module. `core`/`agent_api`/`ai` receive a `Project` and cannot construct `FilesystemProject` (tach-enforced: `core` via `depends_on`, `agent_api`/`ai` via `cannot_depend_on`). Construction lives at composition roots outside those layers — `dbt_charts.cli`, `dbt_charts.integrations.markdown`, `dbt_charts.integrations.streamlit`, and sibling distributions; those edges are not tach-covered.
- FORBIDDEN: `dbt_charts.ai.*` — enforced by tach.

### ProjectSession-open boundary

Command bodies wrap work in a `with ProjectSession.open(project_dir) as project_session:` block and call `project_session.<verb>(...)`. Never construct an `AdapterRegistry` directly in a command file — that is `ProjectSession`'s responsibility.

Commands that render dashboards (the only verbs that thread cache through to the executor) additionally compose `project_cache_ctx(project)` from `dbt_charts.agent_api.cache`. Because that needs the `FilesystemProject`, pair it with `ProjectSession.from_project(project, ...)` — `ProjectSession.open(project_dir, ...)` would build a second project from the same path:

```python
project = FilesystemProject(ctx.project_root)
with (
    project_cache_ctx(project, no_cache=not use_cache, cache_path=cache_path) as cache,
    ProjectSession.from_project(project, cache=cache) as project_session,
):
    project_session.render_board(...)
```

Precedence for the store: `no_cache=True` (`--no-cache`) skips it entirely; `cache_path` (`--cache <path>`, or its `DCT_CACHE_PATH` env backing) opens that persistent DuckDB file, created if absent; otherwise the project's `dbt_charts.yml` `cache:` block picks the location, defaulting to in-memory and ephemeral. A project-level `cache: false` only defaults the *cascade* to off — a nearer scope can still opt in — so the store still opens; the resolved per-query policy decides what gets written. Compile-only / no-cache verbs (currently `dct query`, `dct validate`, `dct describe`) do not open a cache at all — their `project_session.<verb>(...)` methods never consume it, and opening one is wasted I/O.

### One `agent_api` call per command

Each command module calls one (or a small, clearly named set of) functions from `dbt_charts.agent_api` and renders the result. If the function you need doesn't exist there yet, add it to `agent_api/` first, then call it.

### Use the shared helpers; never inline equivalents

- `dbt_charts.cli._json_output.print_json_result(result)` — every `--json` path that emits a Pydantic model
- `dbt_charts.cli._error_format.print_diagnostics(errs, ...)` — every `Diagnostic` list, both TTY and pipe modes
- `dbt_charts.cli._console.is_plain_output()` — every TTY / agent-context check
- `dbt_charts.cli._parsing.parse_kv_pairs(items, flag)` — every `--var key=value` repeatable option
- `dbt_charts.cli._extras` — every optional-dependency gate (`mcp`, etc.) and its install-hint message

An inline `json.dumps(obj.model_dump(...))`, a hand-rolled `for e in errors: console.print(...)`, a bare `os.environ.get("CLAUDECODE")`, or a per-command `try: import openai except ImportError:` is a violation — extend the shared helper instead.

### Typer is the framework

- Arguments / options: declare via `typer.Annotated` + `typer.Option` / `typer.Argument`.
- Env-var backing: `envvar=` on `typer.Option` so `dct <cmd> --help` auto-documents the variable. Never `os.environ.get()` in command bodies.
- Invalid argument combinations: raise `typer.BadParameter`.
- Exit codes: raise `typer.Exit(n)`. Never `sys.exit()`.
- Multi-line `--help` blocks (Examples, Modes, usage forms): prefix the block with `\b` on its own line — both Typer's rich renderer and Click's `format_help_text` collapse single newlines into spaces without it.

```python
help="""\b
SQL and named board queries — common forms:
  dct query SOURCE 'SQL'
  dct query BOARD.yml REFERENCE
"""
```

## Tests

Every new `dct <verb>` gets a subprocess test at `tests/cli/test_<verb>.py`. Use the `project_dir` fixture from `tests/cli/conftest.py`.

In-process tests of CLI internals (help rendering, error panels, console / TTY detection, optional-dependency gating) live in `tests/cli/test_<thing>.py`. Import from `dbt_charts.cli._*` directly. PTY-driven tests (proving plain-vs-rich rendering branches) go here too — see `_subprocess_help.py` for the helper.
