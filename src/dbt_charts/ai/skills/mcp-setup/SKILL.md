---
name: mcp-setup
kind: workflow
surfaces: [cli]
description: >
  Set up the dbt charts MCP server for AI assistant integration. Use when
  running 'dct init mcp', installing dbt charts, configuring MCP for Cursor,
  Claude Desktop, VS Code, Codex, Copilot, or any MCP-compatible client,
  or troubleshooting MCP connection issues like 'server not starting',
  'tools not appearing', 'MCP requires additional dependencies'. Do NOT
  use for building dashboards (use {{ s_skill_name_build }}). Do NOT
  use for dashboard errors (use {{ s_skill_name_troubleshooting }}).
metadata:
  author: fivetran
---

# Configuring dbt charts MCP Server

Set up the dbt charts MCP server to give AI assistants access to dashboard tools and resources.

> This skill is **CLI-only** (`surfaces: [cli]`) — it walks a human operator
> through installing the MCP server from a shell. An already-connected MCP
> agent does not need to read it.

## Quick Setup

The fastest way — auto-detects your AI clients and configures them:

```bash
pip install "dbt-charts[mcp]"
dct init mcp
```

This detects Cursor, VS Code, Claude Desktop, Claude Code, Copilot, and Codex, then writes the appropriate config files.

### Target a Specific Client

```bash
dct init mcp cursor       # Configure Cursor only
dct init mcp vscode       # Configure VS Code only
dct init mcp claude       # Configure Claude Desktop only
dct init mcp claude-code  # Configure Claude Code only
dct init mcp codex        # Configure Codex CLI only
dct init mcp copilot      # Configure GitHub Copilot Coding Agent only
dct init mcp --all        # Write every supported config file
dct init mcp print        # Print JSON config to stdout (manual setup)
```

### Force Update

```bash
dct init mcp cursor -f    # Overwrite existing config
```

After running `dct init mcp`, restart your AI client for changes to take effect.

## Manual Configuration

If you prefer manual setup, add this to your client's MCP config.

For JSON-based clients (Cursor, VS Code, Claude Desktop, Claude Code, Copilot):

```json
{
  "mcpServers": {
    "dbt-charts": {
      "command": "/path/to/dct",
      "args": ["mcp", "serve"]
    }
  }
}
```

For Codex (TOML — `.codex/config.toml`):

```toml
[mcp_servers.dbt-charts]
command = "/path/to/dct"
args = ["mcp", "serve"]
```

If your dbt charts or dbt project lives in a subdirectory of the workspace your
AI client opens (or the server otherwise won't be launched with cwd at the
project root), append `"--project-dir", "/abs/path/to/your/project"` to `args`.
`dct init mcp` will add this for you when its `--project-dir` flag is used or
when it detects that the workspace and project roots diverge.

Config file locations:
- **Cursor**: `.cursor/mcp.json`
- **VS Code / Copilot agent mode**: `.vscode/mcp.json` with a `servers` root key
- **Claude Desktop**: `~/.config/claude/config.json`
- **Claude Code**: `.mcp.json` at the project root
- **Codex**: `.codex/config.toml` (project must be trusted by Codex) — globally, `~/.codex/config.toml`
- **GitHub Copilot Coding Agent**: `.github/copilot/mcp.json` with a `servers` root key

Use the absolute path to `dct` in the `command` field. Find it with `which dct`.

## Available Tools

After setup, your AI assistant has access to these tools:

| Tool | Purpose |
|------|---------|
| `validate_board` | Fast YAML schema and cross-reference validation — use after every file edit |
| `render_board` | Compile, execute queries, and generate visual HTML/text output |
| `query_board` | Run one named query from a saved board and inspect columns/sample rows |
| `execute_query` | Run ad-hoc SQL queries while exploring data or testing SQL |
| `describe_query` | Return column schema for a SQL string without fetching rows |
| `docs` | Browse the packaged YAML reference corpus offline |
| `list_skills` / `get_skill` | Discover and read packaged dbt charts authoring skills — `get_skill` returns the full body including example YAML |

## Available Resources

| Resource | Content |
|----------|---------|
| `dct://docs/all` | Syntax guide plus the generated field reference, unsliced |
| `dct://docs/{topic}` | One H2 section by slug (`cheatsheet`, `board`, `charts`, `queries`, `variables`, `layout`, `errors`) |
| `dct://guide/board-design` | Dashboard design principles and patterns |
| `dct://guide/report-design` | Report design principles and narrative structure |
| `dct://guide/board-build` | Build-test-iterate workflow best practices |
| `dct://guide/board-review` | Self-review checklist for a finished board |
| `dct://boards` | List of all dashboards in the project |
| `dct://board/{path}` | YAML content and compiled structure of a specific dashboard |

## Troubleshooting

### "MCP server requires additional dependencies"

Install the MCP extras:

```bash
pip install "dbt-charts[mcp]"
```

### MCP server not starting

1. Verify `dct` is accessible: `which dct`
2. Ensure the path in MCP config is absolute, not relative
3. Check that your Python environment has `dbt-charts[mcp]` installed
4. Try running `dct mcp serve` manually to see error output

### "No AI client directories detected"

`dct init mcp` looks for `.cursor/`, `.codex/`, `.vscode/`, `.github/`, `CLAUDE.md`, `AGENTS.md`, or `~/.config/claude/` to auto-detect clients. If none exist yet, specify the client explicitly:

```bash
dct init mcp cursor
```

### Tools not appearing in AI client

1. Restart the AI client after running `dct init mcp`
2. Verify the config file was written: check `.cursor/mcp.json` or equivalent
3. Ensure the `command` path in the config points to the correct `dct` binary

## Verifying the Setup

After configuration, test by asking your AI assistant:

- "List available data sources" → should invoke `schema` (no args)
- "Show me the database schema" → should invoke `schema(source=...)`
- "Validate this dashboard YAML file" → should invoke `validate_board`

If the assistant doesn't recognize these tools, the MCP server isn't connected — check the Troubleshooting steps above.
