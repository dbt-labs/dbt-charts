"""Coverage table for dbt-charts' [project.optional-dependencies] extras.

Shared by tests/integration/test_oss_install_smoke.py (which runs the probes
against a fresh install) and tests/packaging/test_packaging_metadata.py at the
repo root (which only checks every declared extra is covered here) — a plain
module with no relative imports of its own (deliberately not importing from
._paths), so it loads the same way whether imported as part of the
dbt_charts.tests package or standalone via importlib file path.
"""

from __future__ import annotations

from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "oss-smoke"
_FIXTURE_DIR_REPR = repr(str(FIXTURE_DIR))

# extra -> a probe script that exercises what the extra exists for.
EXTRA_PROBES: dict[str, str] = {
    # mcp 2.0 removed the decorator API from mcp.server.Server; the import
    # still succeeds under 2.x, and only construction inside create_server()
    # fails — which is exactly why installing the extra alone wasn't enough
    # to catch the original break.
    "mcp": (
        "from dbt_charts.agent_api import ProjectSession\n"
        "from dbt_charts.ai.mcp import create_server, DbtChartsAIContext\n"
        f"session = ProjectSession.open({_FIXTURE_DIR_REPR})\n"
        "create_server(DbtChartsAIContext(project_session=session))\n"
        "print('ok')\n"
    ),
}

# Declared extras deliberately not probed here, and why. Every entry needs a
# reason — this table exists so a future extra can't silently go unchecked.
EXTRA_PROBES_EXCUSED: dict[str, str] = {
    "bigquery": "full dbt adapter + driver stack; install weight",
    "snowflake": "full dbt adapter + driver stack; install weight",
    "redshift": "full dbt adapter + driver stack; install weight",
    "databricks": "full dbt adapter + driver stack; install weight",
    "postgresql": "full dbt adapter + driver stack; install weight",
    "spark": "full dbt adapter + driver stack; install weight",
    "trino": "full dbt adapter + driver stack; install weight",
    "athena": "full dbt adapter + driver stack; install weight",
    "clickhouse": "full dbt adapter + driver stack; install weight",
    # fastapi/uvicorn are unconditional base dependencies (identical
    # versions declared both places) — the extra changes nothing an
    # `import fastapi, uvicorn` probe could observe with or without it.
    "server": "byte-identical to base dependencies; nothing observable differs",
    # dbt_charts never imports mkdocs itself — consumers wire its Pygments
    # lexer into their own mkdocs.yml externally, so there is no in-package
    # coupling for an import probe to exercise.
    "mkdocs-plugin": "no coupling in dbt_charts to probe; lexer is consumed externally",
}
