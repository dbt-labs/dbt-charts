"""One open session on a driver downloaded from dbt's CDN.

Stage: EXECUTE

Each CDN driver shares everything about *running* a statement and differs only
in the options that open the connection, so the session lives here once and a
driver module contributes its own option map.

The statement path is the low-level ``AdbcStatement`` rather than the DBAPI
cursor, and that is a measured requirement rather than a preference:
``dbapi.Cursor.execute`` prepares every statement first, and some of dbt's
drivers accept the prepare and then reject the execute ("Prepared statements are
not yet supported via `execute query`"). dbt's own Rust client never prepares
either.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import TYPE_CHECKING, Any

from dbt_charts.core.diagnostics.codes_execute import ERR_WAREHOUSE_RESULT_WARNING
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute.adapters.base import (
    WarehouseStatementError,
    redact_values,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    import pyarrow as pa
    from adbc_driver_manager import AdbcStatement, Error

    from dbt_charts.core.execute.adapters.base import RowFetchLimit


logger = logging.getLogger(__name__)

# Option keys whose *values* are credentials. Everything else a driver is given
# — endpoint, bundle, client id — is what makes a connect failure actionable, so
# scrubbing the lot would turn "cannot resolve api.example" into "cannot resolve
# [redacted]". Matched as substrings because drivers spell the same idea
# differently (`auth.token`, `auth.api_key`, `auth.fivetran.credential`).
_SECRET_OPTION_MARKERS: tuple[str, ...] = (
    "password",
    "token",
    "secret",
    "api_key",
    "credential",
    "access_key",
    "passphrase",
    "uri",
)


def _secret_option_values(options: dict[str, str]) -> list[str]:
    """The option values that are credentials, for exact-match redaction."""
    return [
        value
        for key, value in options.items()
        if any(marker in key.lower() for marker in _SECRET_OPTION_MARKERS)
    ]


class _ConnectFailed(Exception):
    """A connect that failed, carrying a message with no credential left in it.

    Raised outside the `except` that caught the driver's error, so that error —
    which holds the unredacted text — is on neither `__cause__` nor
    `__context__`.
    """


class CdnDriverConnection:
    """One warehouse session on a CDN-downloaded ADBC driver.

    No attribution rides a statement: the drivers expose no per-statement
    identity channel, and a source that accepts ``attribution:`` is refused at
    parse time rather than having it dropped here.
    """

    def __init__(
        self,
        connection: Any,  # type-state: explicit_any — adbc_driver_manager's DBAPI Connection is untyped
        warnings_option: str | None,
        secrets: list[str],
    ) -> None:
        self._connection = connection
        self._warnings_option = warnings_option
        self._secrets = secrets
        self._live: set[AdbcStatement] = set()
        self._live_lock = threading.Lock()

    def execute(self, sql: str, limit: RowFetchLimit) -> pa.Table:
        """Run *sql* and return at most ``limit.fetch_limit`` rows as Arrow.

        Raises:
            ExecutionError: ERR-WAREHOUSE-RESULT-WARNING when the backend
                attached a warning to the result, since it has said the rows
                may be incomplete.
        """
        from adbc_driver_manager import Error

        try:
            return self._execute(sql, limit)
        except Error as error:
            failure = self._redacted(error)
        raise failure

    def _execute(self, sql: str, limit: RowFetchLimit) -> pa.Table:
        import pyarrow

        from dbt_charts.core.execute.result import read_bounded_table

        with self._statement() as statement:
            statement.set_sql_query(sql)
            handle, _ = statement.execute_query()
            # `_import_from_c` is how adbc_driver_manager's own DBAPI layer turns
            # a stream handle into a reader; pyarrow has no public equivalent.
            reader = pyarrow.RecordBatchReader._import_from_c(handle.address)
            # The reader must be released before the statement that produced it:
            # releasing an unconsumed stream cancels it, into a freed statement
            # if the order is wrong. `read_bounded_table` stops early, so the
            # undrained case is the common one.
            try:
                table = read_bounded_table(reader, reader.schema, limit)
            finally:
                reader.close()
            # Read while the statement is still open: no caller ever holds one.
            if self._warnings_option is not None:
                warnings = statement.get_option(self._warnings_option)
                if warnings:
                    raise ExecutionError.from_code(
                        ERR_WAREHOUSE_RESULT_WARNING, warnings=warnings
                    )
            return table

    def session_sql(self, sql: str) -> None:
        """Run a statement for its effect on the session, discarding any result.

        ``execute_update`` rather than ``execute_query``: it returns a row count
        and no Arrow stream, so a ``SET`` leaves nothing to release.
        """
        from adbc_driver_manager import Error

        failure: WarehouseStatementError | None = None
        try:
            with self._statement() as statement:
                statement.set_sql_query(sql)
                statement.execute_update()
        except Error as error:
            failure = self._redacted(error)
        if failure is not None:
            raise failure

    def cancel(self) -> None:
        """Cancel every statement running on this session.

        Called from another thread while ``execute`` is blocked in the driver.
        A driver that cannot cancel is reported, not hidden: the caller has
        already stopped waiting, so the statement runs on until it finishes.
        """
        from adbc_driver_manager import Error

        with self._live_lock:
            live = list(self._live)
        for statement in live:
            try:
                statement.cancel()
            except Error:
                logger.warning("The driver could not cancel a running statement")

    def close(self) -> None:
        self._connection.close()

    def _redacted(self, error: Error) -> WarehouseStatementError:
        """The driver's rejection with credentials removed, carrying its SQLSTATE."""
        return WarehouseStatementError(
            error.sqlstate, redact_values(str(error), self._secrets)
        )

    @contextlib.contextmanager
    def _statement(
        self,
    ) -> Generator[Any]:  # type-state: explicit_any — AdbcStatement is untyped
        from adbc_driver_manager import AdbcStatement

        statement = AdbcStatement(self._connection.adbc_connection)
        with self._live_lock:
            self._live.add(statement)
        try:
            yield statement
        finally:
            with self._live_lock:
                self._live.discard(statement)
            statement.close()


def open_cdn_driver(driver: str, options: dict[str, str]) -> CdnDriverConnection:
    """Open *driver*'s pinned CDN build with *options*.

    The build is resolved — and downloaded once, if this machine has never run
    dbt — by ``_fetch.driver_path``. The init symbol and the warnings option are
    the driver's own row in ``_driver_index``, so no caller names either.

    ``autocommit=True`` because the DBAPI layer otherwise tries to turn it off
    and warns on every connect. dbt charts only reads, so there is no
    transaction to hold open, and a read inside one holds locks against writers
    until it closes.
    """
    from adbc_driver_manager import dbapi

    from dbt_charts.core.execute.drivers._driver_index import CDN_DRIVERS
    from dbt_charts.core.execute.drivers._fetch import driver_path

    row = CDN_DRIVERS[driver]
    path = str(driver_path(driver))
    secrets = _secret_option_values(options)
    try:
        connection = dbapi.connect(
            driver=path,
            entrypoint=row.entrypoint,
            db_kwargs=options,
            autocommit=True,
        )
    except Exception as error:  # noqa: BLE001 — a connect can fail in any of the driver's own ways, and every one of them may quote the credential back
        # Redacted by identity: a quoted token has no field name beside it for a
        # pattern to match. Raised below, outside the `except`, so the original
        # is not left on `__context__`.
        failure = _ConnectFailed(redact_values(str(error), secrets))
    else:
        return CdnDriverConnection(connection, row.warnings_option, secrets)
    raise failure
