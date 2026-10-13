"""The Arrow driver seam: which sources run on a driver, and how to open one.

Stage: EXECUTE

Most warehouses connect through dbt Core's Python adapters; a source whose
warehouse has no dbt Core adapter to use (lake compute has only an ADBC driver,
Trino only its own Python client) cannot. Each connecting call
site asks :func:`is_driver_source` *before* it builds a dbt adapter, so a
non-driver source takes the dbt path exactly as it always has and this module
never learns that path exists.

A driver opens a connection, runs a statement bounded by a
:class:`~dbt_charts.core.execute.adapters.base.RowFetchLimit` the caller already
resolved, runs session SQL, and closes. It resolves no ceiling, classifies no
error and converts no rows of its own: those live in ``execute/adapters/base.py``
and ``execute/result.py``, shared with the dbt path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from dbt_charts.core.compile.models.source import (
    LakeComputeSourceBase,
    ResolvedSourceConfig,
    TrinoSourceConfig,
)

if TYPE_CHECKING:
    import pyarrow as pa

    from dbt_charts.core.execute.adapters.base import RowFetchLimit


# The source types on the Arrow seam — one arm in `open_driver` each. Named here
# as well as matched there so an error message can list them. They cannot drift:
# test_open_source pins that every type here opens on the driver.
DRIVER_SOURCE_TYPES: frozenset[str] = frozenset({"lakecompute", "trino"})


class DriverConnection(Protocol):
    """One open warehouse session on the Arrow seam."""

    def execute(self, sql: str, limit: RowFetchLimit) -> pa.Table:
        """Run *sql* and stop fetching once ``limit.fetch_limit`` rows are in hand.

        A coarse bound, not an exact one: a driver reads whole batches, so the
        answer overshoots to the end of the batch that crossed the line. What it
        buys is not fetching a million rows to return a thousand. Slicing to the
        exact ceiling and flagging truncation is ``arrow_to_query_result``'s job.

        The limit is already resolved against the author's ``Query.limit`` and
        the ``execution.max_rows`` ceiling; the driver resolves none of its own.
        """
        ...

    def session_sql(self, sql: str) -> None:
        """Run a statement for its effect on the session, discarding any result."""
        ...

    def cancel(self) -> None:
        """Abort any statement running on this session, from another thread."""
        ...

    def close(self) -> None:
        """Release the warehouse session."""
        ...


def is_driver_source(config: ResolvedSourceConfig) -> bool:
    """Whether *config*'s warehouse is reached through a driver, not a dbt adapter."""
    return isinstance(config, (LakeComputeSourceBase, TrinoSourceConfig))


def require_driver_extra(config: ResolvedSourceConfig) -> None:
    """Raise unless the packages *config*'s driver needs are installed.

    Raises:
        AdapterNotInstalledError: The driver's extra is missing.
        ValueError: *config* is not a driver source.
    """
    if isinstance(config, LakeComputeSourceBase):
        from dbt_charts.core.execute.drivers.lakecompute import (
            require_lakecompute_extra,
        )

        return require_lakecompute_extra()
    if isinstance(config, TrinoSourceConfig):
        from dbt_charts.core.execute.drivers.trino import require_trino_extra

        return require_trino_extra()
    raise _not_a_driver_source(config)


def open_driver(
    config: ResolvedSourceConfig, timeout_seconds: int | None
) -> DriverConnection:
    """Open *config*'s warehouse on its driver.

    Args:
        config: A source for which :func:`is_driver_source` is true.
        timeout_seconds: The per-query cap the driver enforces server-side, or
            ``None`` for no cap beyond the driver's own default.

    Raises:
        ValueError: *config* is not a driver source. Every caller checks
            :func:`is_driver_source` first, so this is a caller bug.
    """
    if isinstance(config, LakeComputeSourceBase):
        from dbt_charts.core.execute.drivers.lakecompute import open_lakecompute

        return open_lakecompute(config, timeout_seconds)
    if isinstance(config, TrinoSourceConfig):
        from dbt_charts.core.execute.drivers.trino import open_trino

        return open_trino(config, timeout_seconds)
    raise _not_a_driver_source(config)


def _not_a_driver_source(config: ResolvedSourceConfig) -> ValueError:
    return ValueError(
        f"The driver seam serves sources of type {sorted(DRIVER_SOURCE_TYPES)}; "
        f"got type {config.type!r}"
    )
