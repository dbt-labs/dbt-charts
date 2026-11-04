"""Backends: what executes a composed query, and the map that picks one.

Stage: EXECUTE
Purpose: A backend gets a source's credentials and a fully resolved query, and
    returns its rows: Arrow, or ``TypedRows`` for a warehouse client whose cells have
    no faithful Arrow form (JSON, nested and decimal values). Pooling and session
    lifetime are private to each backend.
    Each host builds its own routes map; a source kind with no route is an
    error, never a guess.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from dbt_charts.core.diagnostics.codes_execute import ERR_ADAPTER_UNAVAILABLE
from dbt_charts.core.diagnostics.execution import QueryError

if TYPE_CHECKING:
    import nanoarrow as na

    from dbt_charts.core.column_types import TypedRows
    from dbt_charts.core.compile.models.source import ResolvedSourceConfig
    from dbt_charts.core.dialects import SQLDialect
    from dbt_charts.core.execute.compose import ResolvedQuery

# Cheapest statement every warehouse we build answers, used to ask a connection
# whether it is still there.
LIVENESS_SQL = "SELECT 1"


def source_config_hash(
    source_config: dict[str, Any],  # type-state: explicit_any — connection kwargs
) -> str:
    """Stable hash of source_config for backend pool keys.

    Uses sorted JSON serialization so key order doesn't affect the hash.

    ``attribution`` is excluded: it is cost metadata that rides along to the
    warehouse, not connection identity. Hashing it would give every distinct set of
    labels its own pool, so a per-dashboard label would cost a fresh connection
    handshake per dashboard.
    """
    identity = {k: v for k, v in source_config.items() if k != "attribution"}
    serialized = json.dumps(identity, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


class Backend(Protocol):
    """Runs a composed query against a source and returns its rows as Arrow."""

    param_style: SQLDialect
    """The placeholder style the query's ``params`` are written in."""

    def execute(
        self, source: ResolvedSourceConfig, query: ResolvedQuery
    ) -> na.Array | TypedRows:
        """Run ``query.setup_sql`` then ``query.sql`` in one session, fetching at
        most ``query.limit.fetch_limit`` rows.

        A backend that cannot bind ``query.params`` inlines them here. Failures
        raise; the caller classifies them.
        """
        ...

    def interrupt(self) -> None:
        """Cancel running statements and refuse new ones; one-way, for shutdown."""
        ...

    def close(self) -> None:
        """Release every session the backend holds."""
        ...


class BackendRegistry:
    """Source kind to the backend that runs it."""

    def __init__(self, routes: Mapping[str, Backend]) -> None:
        self._routes = dict(routes)

    def serves(self, source: ResolvedSourceConfig) -> bool:
        """Whether *source*'s kind has a route."""
        return source.type in self._routes

    def for_source(self, source: ResolvedSourceConfig) -> Backend:
        """The backend mapped to *source*'s kind.

        Raises:
            QueryError: ERR-ADAPTER-UNAVAILABLE, no backend is mapped to the kind.
        """
        if source.type not in self._routes:
            raise QueryError.from_code(
                ERR_ADAPTER_UNAVAILABLE,
                reason=(
                    f"no backend for source kind {source.type!r}; "
                    f"this deployment routes {sorted(self._routes)}."
                ),
            )
        return self._routes[source.type]

    def interrupt(self) -> None:
        for backend in self._distinct():
            backend.interrupt()

    def close(self) -> None:
        for backend in self._distinct():
            backend.close()

    def _distinct(self) -> list[Backend]:
        """Each backend once, though several kinds may share it."""
        return list(
            {id(backend): backend for backend in self._routes.values()}.values()
        )
