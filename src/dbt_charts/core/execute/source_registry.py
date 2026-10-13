"""Project-source state for the execute boundary.

Owns the project-source allowlist that the resolver consults: the
``ProjectSourcesConfig`` loaded once at construction time, plus any synthetic
sources registered at runtime (the ``dct inspect`` HTML server is the canonical
registrar — it synthesizes a ``warehouse`` source when no sources are configured).
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.compile.config import ProjectSourcesConfig


class SourceRegistry:
    """Merged view of project-level sources plus synthetic registrations.

    Constructed with an already-loaded ProjectSourcesConfig — does no I/O.
    Project sources by a given name win over synthetic registrations.
    """

    def __init__(self, project_sources: ProjectSourcesConfig) -> None:
        self._project = project_sources
        self._synthetic: dict[str, dict[str, Any]] = {}

    def get(self, name: str) -> dict[str, Any] | None:
        """Return the resolved config for ``name``, or ``None`` if unknown."""
        return self.all().get(name)

    def all(self) -> dict[str, dict[str, Any]]:
        """Return the merged source map (project sources win over synthetics)."""
        return {**self._synthetic, **self._project.sources}

    def register(self, name: str, config: dict[str, Any]) -> None:
        """Register a synthetic source. Idempotent: first registration wins."""
        self._synthetic.setdefault(name, dict(config))

    def runtime_config(self) -> ProjectSourcesConfig:
        """Project source config with any synthetic registrations layered on top.

        This is what the resolver consumes at query time — project sources win
        over synthetic on name collision (matches the ordering in `all()`).
        """
        return ProjectSourcesConfig(
            sources=self.all(), implicit_source=self._project.implicit_source
        )
