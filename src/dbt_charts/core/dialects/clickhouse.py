"""ClickHouse dialect implementation.

ClickHouse uses:
- %(name)s named parameters (the client-side binding style of both
  clickhouse-connect and clickhouse-driver, the two drivers dbt-clickhouse wraps)
"""

import math
import re
from typing import Any

from dbt_charts.core.dialects.base import SQLDialect

# Error 159 is what the server raises when a statement runs past
# max_execution_time. The HTTP interface spells it with the symbolic name
# ("... (TIMEOUT_EXCEEDED)"); the native protocol carries the numeric code
# ("Code: 159."). Both are matched so the classification does not depend on
# which of dbt-clickhouse's two drivers a profile chose. The symbolic name is
# anchored to its parentheses: ClickHouse echoes the offending query back in
# several messages, so a bare substring would read a query that merely
# mentions the word as a timeout.
_TIMEOUT_EXCEEDED_RE = re.compile(r"\(TIMEOUT_EXCEEDED\)|\bCode: 159\b")


def _profile_cap_seconds(
    value: Any,  # type-state: explicit_any — raw profile value
) -> int:
    """A profile's ``max_execution_time`` as whole seconds, or raise.

    ClickHouse types the setting ``SettingSeconds``, an alias of
    ``SettingUInt64``, and its writer casts with ``int()``. So a fractional
    value is not a short cap but a truncated one, and ``max_execution_time = 0``
    means *no limit* — 0.5s would disable the cap entirely on the native driver
    and be rejected outright on HTTP. Anything below a whole second therefore
    cannot be honored as a cap and is refused rather than silently widened.

    Accepts the string spelling (``"30"``) because dbt-clickhouse types
    ``custom_settings`` as ``Dict[str, Any]`` and seeds string-valued settings
    itself. Rejects ``bool``, which is an ``int`` in Python and would read
    ``true`` as a one-second cap.
    """
    if isinstance(value, bool):
        raise ValueError(
            f"custom_settings.max_execution_time must be whole seconds, got {value!r}"
        )
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"custom_settings.max_execution_time must be whole seconds, got {value!r}"
        ) from exc
    if not math.isfinite(seconds) or seconds != int(seconds) or seconds < 1:
        raise ValueError(
            "custom_settings.max_execution_time must be a whole number of "
            f"seconds >= 1, got {value!r}"
        )
    return int(seconds)


class ClickHouseDialect(SQLDialect):
    """ClickHouse dialect with %(pN)s parameter style.

    statement_timeout_sql() is not overridden: the base no-op is correct
    because the cap is a connection setting, not SQL. See
    :meth:`connect_credentials` for why.

    Example:
        >>> dialect = ClickHouseDialect()
        >>> dialect.param(1)
        '%(p1)s'
    """

    name = "clickhouse"
    # String literals take backslash escape sequences (\\', \\\\, \\n, ...); an
    # embedded quote may be spelled either \\' or ''.
    # https://clickhouse.com/docs/sql-reference/syntax#string
    escapes_backslashes = True
    uses_named_params = True
    # dbt-clickhouse's ConnectionManager.execute accepts the limit kwarg and
    # ignores it: its client fetches the whole result set regardless. Declaring
    # False routes the row cap to the post-fetch slice instead of trusting a
    # bound that was never applied.
    cursor_supports_driver_limit = False

    def param(self, index: int) -> str:
        """Generate ClickHouse parameter placeholder.

        Args:
            index: 1-based parameter index

        Returns:
            Parameter placeholder in %(pN)s format
        """
        return f"%(p{index})s"

    def resolve_timeout_seconds(
        self,
        creds: dict[str, Any],  # type-state: explicit_any — raw source_config
        seconds: int,
    ) -> int:
        """Narrow the ceiling to a profile that caps itself harder.

        ``custom_settings`` is a dbt profile key, so a ``max_execution_time``
        there is the author's own setting for dbt's runs. Loosening it to a
        ceiling nobody chose (the shipped default is 120s) would be the wrong
        half of the bargain, so the stricter of the two wins.
        """
        authored = self._profile_settings(creds).get("max_execution_time")
        if authored is None:
            return seconds
        return min(seconds, _profile_cap_seconds(authored))

    def connect_credentials(
        self,
        creds: dict[str, Any],  # type-state: explicit_any — raw source_config
        seconds: int,
    ) -> dict[str, Any]:  # type-state: explicit_any — raw source_config
        """Put the resolved cap on the connection's own settings.

        ClickHouse's per-statement cap is a *setting*, and dbt-clickhouse sends
        ``custom_settings`` with every request (URL parameters over HTTP, the
        query packet over the native protocol). That is the only carrier that
        survives the pool holding a connection across renders: a ``SET`` would
        bind the cap to the session, and an HTTP session expires after a minute
        idle, so the next render would run uncapped with nothing to say so.

        A ``readonly = 1`` user cannot carry any setting, so the connection
        fails at setup with ClickHouse's own "Cannot modify ... in readonly
        mode" rather than running uncapped; ``readonly = 2`` is the read-only
        mode ClickHouse gives BI clients for exactly this reason.
        """
        settings = self._profile_settings(creds)
        settings["max_execution_time"] = seconds
        return {**creds, "custom_settings": settings}

    @staticmethod
    def _profile_settings(
        creds: dict[str, Any],  # type-state: explicit_any — raw source_config
    ) -> dict[str, Any]:  # type-state: explicit_any — raw profile settings
        """A mutable copy of the profile's ``custom_settings``."""
        return dict(
            creds.get(
                "custom_settings"
            )  # type-state: silent_fallback — an unauthored custom_settings is an empty settings map, the documented dbt-clickhouse default
            or {}
        )

    def is_statement_timeout_error(self, exc: Exception) -> bool:
        """Return True when ClickHouse canceled the statement for exceeding max_execution_time.

        dbt-clickhouse wraps every driver error in a DbtRuntimeError that keeps
        the server's message, so the text is what there is to read.
        """
        return _TIMEOUT_EXCEEDED_RE.search(str(exc)) is not None

    def bulk_schema_sql(self, scope: str = "") -> str:
        """Whole-server columns from ``system.columns``.

        ClickHouse has one level of namespace — its *database* is what dbt (and
        so dbt charts) calls the schema — and ``system.columns`` spans every
        database on the server in one query. The server's own catalogs are
        excluded so the result is user tables only.
        """
        return (
            "SELECT database AS table_schema, table AS table_name, "
            "name AS column_name, type AS data_type "
            "FROM system.columns "
            "WHERE database NOT IN "
            "('system', 'INFORMATION_SCHEMA', 'information_schema') "
            "ORDER BY database, table, position"
        )
