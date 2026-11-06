"""Shared scoping context for dbt charts AI tool surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dbt_charts.core.project import assert_relpath

if TYPE_CHECKING:
    from dbt_charts.agent_api import ProjectSession


@dataclass(frozen=True)
class DbtChartsAIContext:
    """Scope shared AI/MCP helpers to a specific project + source boundary."""

    project_session: ProjectSession
    dashboards_directory: Path | None = None
    default_source: str | None = None
    # Port of the embedded HTTP preview server. Set by `dct mcp serve` and
    # the host after binding; consumed by `_view_url` to build clickable
    # localhost URLs in `render_board` responses (including as_link=True).
    server_port: int | None = None
    prompt_context: dict[str, str] | None = None

    def resolve_dashboard_path(self, path: Path) -> Path:
        """Resolve a dashboard path inside the scoped dashboards directory.

        When no explicit dashboards directory is configured, paths default to the
        project's ``charts/`` directory under the project root.
        """
        # path.is_absolute() is host-specific (misses Windows-absolute paths
        # on a POSIX host); assert_relpath classifies host-independently.
        # The resolve()+relative_to check below still matters: it's
        # symlink-aware, which this lexical guard isn't.
        assert_relpath(path.as_posix())
        if self.dashboards_directory is None:
            base_dir = self.project_session.charts_dir.resolve()
        else:
            base_dir = self.dashboards_directory.resolve()
        resolved = (base_dir / path).resolve()
        try:
            resolved.relative_to(base_dir)
        except ValueError as exc:
            raise ValueError(
                f"Dashboard path escapes scoped directory: {path}"
            ) from exc
        return resolved
