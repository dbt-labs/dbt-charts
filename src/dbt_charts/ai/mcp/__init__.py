"""dbt charts MCP (Model Context Protocol) Server.

This module provides an MCP server that enables AI assistants (Claude, Cursor,
ChatGPT, etc.) to interact with dbt charts dashboards through a standardized
protocol.

Architecture:
    Resources (read-only context):
        - dct://boards - List of dashboards in project
        - dct://board/{path} - Dashboard content and structure
        - dct://guide/chart - Board and chart grammar, build-render-fix loop
        - dct://guide/report-design - Report design principles

    Tools (actions):
        - render_board - Validate + render; pass as_link=true for URL-only (folds view_dashboard)
        - execute_sql - Run SQL, or one named query from a board YAML, and return columns + rows
        - schema - Drill the data hierarchy: source → schema → table → column
        - search_files - Search boards by keyword, or the project's meta, model, and config files

Usage:
    # Configure MCP for your AI client
    dct init mcp cursor     # or: vscode, claude, claude-code, codex, copilot, print
    dct init mcp --all

    # Start the MCP server (stdio mode)
    dct mcp serve

    # Or programmatically
    from dbt_charts.ai.mcp import create_server
    server = create_server()
    await server.run()

For more information about MCP, see:
    https://modelcontextprotocol.io/
"""

from dbt_charts.ai.context import DbtChartsAIContext
from dbt_charts.ai.mcp.server import create_server, run_server

__all__ = [
    # Server
    "create_server",
    "run_server",
    "DbtChartsAIContext",
]
