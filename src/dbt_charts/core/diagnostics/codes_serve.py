"""ERR-* error codes for the serve domain.

Covers startup-time failures: invalid theme configuration and uvicorn launch failures.
"""

from __future__ import annotations

from dbt_charts.core.diagnostics.hints import suggest_close_theme
from dbt_charts.core.diagnostics.registry import REGISTRY, ErrorCode

ERR_INVALID_DEFAULT_THEME = REGISTRY.register(
    ErrorCode(
        code="ERR-INVALID-DEFAULT-THEME",
        domain="serve",
        title="Invalid theme name in configuration",
        message_template=(
            "{source} ({theme!r}) is not a valid theme name. "
            "Available built-in themes: {available}."
        ),
        doc=(
            "Fired when the configured default theme name is not a recognized "
            "built-in theme. Check the available theme names and correct the "
            "configuration."
        ),
        docs_topic="board",
        hint_generator=suggest_close_theme,
    )
)

# No hint_generator — the inner uvicorn exception message is opaque; the
# rendered {detail} is the actionable signal on its own.
ERR_STARTUP_FAILED = REGISTRY.register(
    ErrorCode(
        code="ERR-STARTUP-FAILED",
        domain="serve",
        title="Server failed to start",
        message_template="Server failed to start: {detail}.",
        doc=(
            "Fired when the uvicorn server process fails to start. The detail "
            "carries the inner error message from uvicorn."
        ),
        docs_topic="errors",
    )
)

ERR_DBT_PROFILES_FORBIDDEN = REGISTRY.register(
    ErrorCode(
        code="ERR-DBT-PROFILES-FORBIDDEN",
        domain="serve",
        title="dbt profiles are off limits",
        message_template="Boards and agents can't read or change dbt profiles ({path}).",
        doc=(
            "Fired when a Cloud agent file tool reads, writes, edits, moves or "
            "deletes a profiles.yml outside the board folders, or when a board "
            "imports one. A committed dbt profile can hold warehouse "
            "credentials, so Cloud never serves it to a board compile or an "
            "agent, and omits it from file searches. The refusal is by name, "
            "before any lookup, so it says nothing about whether the file "
            "exists."
        ),
        summary="Fired when a board import or agent file tool touches a dbt profiles file.",
        docs_topic="errors",
    )
)
