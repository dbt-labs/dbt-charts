"""Install-command hints surfaced from runtime error messages.

Centralized here so every CLI/runtime hint stays in lock-step with
``dbt-charts/README.md`` and the wheel-shipped MCP-setup skill.

This module only ever runs from an install that already exists (a
missing extra, a missing optional dependency like ``uvicorn``) — so the
right question is "how did *this* install get here", not "is `uv` on
PATH". A pip/venv install with `uv` merely available on PATH must keep
recommending `pip`; recommending `uv tool install` there would create a
second, parallel installation alongside the one already running.
``cli/_extras.py`` separately emits ``uv pip install --python <path>``
when uv is the active installer for *adding an extra to the current
interpreter* (a different question); this helper is the canonical
reinstall hint.

The TypeScript mirror at
``apps/vscode-extension/src/utils/install-hint.ts`` uses a
*different* signal (is `uv` on PATH) because it runs before anything is
installed — there is no existing install to inspect there. If you
change one file, check whether the other's signal still applies before
mirroring the change.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _is_uv_tool_install() -> bool:
    """True if this process is running from a `uv tool install` environment.

    `uv tool install` creates an isolated virtualenv under uv's tool
    directory (`uv tool dir`; e.g. `~/.local/share/uv/tools` on Linux,
    the platform's per-user data dir on macOS/Windows, overridable via
    `UV_TOOL_DIR`) with the tool name as an immediate child directory.
    `sys.prefix` is that tool venv's own directory — it's the value uv
    set up when creating the venv, not a filesystem fact to be
    recomputed. Deliberately NOT `Path(sys.executable).resolve()`: a
    uv-tool venv's `bin/python` is a symlink back into uv's *shared*
    interpreter directory (`uv/python/...`), so resolving it walks
    straight out of `uv/tools/` and defeats this check.
    """
    prefix = Path(sys.prefix)
    tool_dir = os.environ.get("UV_TOOL_DIR")
    if tool_dir and prefix.parent == Path(tool_dir):
        return True
    parts = prefix.parts
    return any(
        a == "uv" and b == "tools" for a, b in zip(parts, parts[1:], strict=False)
    )


def install_hint(extra: str | None = None) -> str:
    """Return the install one-liner that matches how this dbt charts got installed.

    ``extra`` is an optional optional-dependency group (``"mcp"``, ``"bigquery"``);
    when set, the returned command installs dbt charts with that extras bracket.
    """
    spec = f"dbt-charts[{extra}]" if extra else "dbt-charts"
    if _is_uv_tool_install():
        return f'uv tool install "{spec}"'
    return f'pip install "{spec}"'
