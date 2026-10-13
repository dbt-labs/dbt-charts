"""Trino on the `trino` Python client.

Stage: EXECUTE

Trino has no ADBC driver and no dbt v2 backend, so its arm of the seam is the
DB-API client: rows arrive as Python values and are built into Arrow here. Each
column's Arrow type is read from the Trino type on ``cursor.description``, never
inferred from the values, so an empty result and a column of NULLs are typed the
same as a full one. A type with no entry in the map raises.

The client connects lazily: `trino.dbapi.connect` opens no socket. `open_trino`
therefore sends one statement before it returns, so a wrong host or credential
fails the open, where callers report a connection failure, and not the first
query.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import warnings
from collections.abc import Callable, Iterator, Mapping
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any, assert_never

import pyarrow as pa

from dbt_charts._install_hint import install_hint
from dbt_charts.core.attribution import attribution_payload, connection_identity
from dbt_charts.core.compile.models.source import TrinoSourceConfig
from dbt_charts.core.diagnostics.codes_execute import ERR_ADAPTER_NOT_INSTALLED
from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    AdapterNotInstalledError,
)
from dbt_charts.core.execute.result import read_bounded_table

if TYPE_CHECKING:
    from dbt_charts.core.execute.adapters.base import RowFetchLimit

logger = logging.getLogger(__name__)

# Rows per `fetchmany`, and so the grain of the row-limit bound.
_BATCH_ROWS = 10_000

_Convert = Callable[
    [Any],  # type-state: explicit_any — one warehouse value
    Any,  # type-state: explicit_any — the value Arrow stores for it
]

_SCALAR_TYPES: dict[str, pa.DataType] = {
    "boolean": pa.bool_(),
    "tinyint": pa.int8(),
    "smallint": pa.int16(),
    "integer": pa.int32(),
    "bigint": pa.int64(),
    # The client parses REAL into a Python float; float32 would re-round it and
    # turn 0.1 into 0.10000000149.
    "real": pa.float64(),
    "double": pa.float64(),
    "varchar": pa.string(),
    "char": pa.string(),
    "json": pa.string(),
    "ipaddress": pa.string(),
    "varbinary": pa.binary(),
    "date": pa.date32(),
    # Python's datetime and time hold microseconds, so the client has already
    # rounded any finer precision away.
    "timestamp": pa.timestamp("us"),
    "timestamp with time zone": pa.timestamp("us", tz="UTC"),
    "time": pa.time64("us"),
    # The type of a bare NULL literal.
    "unknown": pa.null(),
}

# Arrow has no type that round-trips these into a chart, so they travel as JSON
# text, which is what a board can read.
_JSON_TYPES = frozenset({"array", "map", "row"})

_DECIMAL = re.compile(r"decimal\((\d+),\s*(\d+)\)")
_PARAMETERS = re.compile(r"\(.*\)", re.DOTALL)


def _jsonable(
    value: Any,  # type-state: explicit_any — nested warehouse value
) -> Any:  # type-state: explicit_any — the same value in JSON's types
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _to_json(value: Any) -> str:  # type-state: explicit_any — nested warehouse value
    return json.dumps(_jsonable(value))


def arrow_column_type(
    name: str, trino_type: str
) -> tuple[pa.DataType, _Convert | None]:
    """The Arrow type for a Trino column type, and how to convert its values.

    Raises:
        ValueError: The Trino type has no Arrow mapping here.
    """
    lowered = trino_type.lower()
    decimal = _DECIMAL.fullmatch(lowered)
    if decimal is not None:
        return pa.decimal128(int(decimal[1]), int(decimal[2])), None
    family = lowered.split("(", 1)[0].strip()
    if family in _JSON_TYPES:
        return pa.string(), _to_json
    if family == "uuid":
        return pa.string(), str
    # `timestamp(3) with time zone` → `timestamp with time zone`.
    scalar = " ".join(_PARAMETERS.sub("", lowered).split())
    if scalar in _SCALAR_TYPES:
        return _SCALAR_TYPES[scalar], None
    raise ValueError(
        f"Column {name!r} has Trino type {trino_type!r}, which dbt Charts does "
        "not read. CAST it in the query, for example to varchar."
    )


def _auth(
    config: TrinoSourceConfig,
) -> Any:  # type-state: explicit_any — trino.auth is untyped
    import trino.auth

    if config.method is None:
        return None
    if config.method == "ldap":
        return trino.auth.BasicAuthentication(_set(config.user), _set(config.password))
    if config.method == "jwt":
        return trino.auth.JWTAuthentication(_set(config.jwt_token))
    if config.method == "certificate":
        return trino.auth.CertificateAuthentication(
            _set(config.client_certificate), _set(config.client_private_key)
        )
    if config.method == "oauth":
        return trino.auth.OAuth2Authentication(
            trino.auth.CompositeRedirectHandler(
                [trino.auth.WebBrowserRedirectHandler()]
            )
        )
    if config.method == "oauth_console":
        return trino.auth.OAuth2Authentication(
            trino.auth.CompositeRedirectHandler([trino.auth.ConsoleRedirectHandler()])
        )
    if config.method == "kerberos":
        return _kerberos_auth(trino.auth.KerberosAuthentication, config)
    if config.method == "gssapi":
        return _kerberos_auth(trino.auth.GSSAPIAuthentication, config)
    assert_never(config.method)


def _set(value: str | None) -> str:
    """Narrow a field the source model's constraints already require."""
    if value is None:
        raise ValueError("a field the Trino auth method requires is unset")
    return value


def _kerberos_auth(
    auth_cls: Any,  # type-state: explicit_any — trino.auth is untyped
    config: TrinoSourceConfig,
) -> Any:  # type-state: explicit_any — trino.auth is untyped
    # A dbt profile writes true/false for kerberos and a mode name for gssapi;
    # both mean the same three client constants.
    mode = config.mutual_authentication
    if isinstance(mode, str):
        mutual = getattr(auth_cls, f"MUTUAL_{mode}")
    else:
        mutual = auth_cls.MUTUAL_REQUIRED if mode else auth_cls.MUTUAL_DISABLED
    return auth_cls(
        config=config.krb5_config,
        service_name=config.service_name,
        principal=config.principal,
        mutual_authentication=mutual,
        force_preemptive=bool(config.force_preemptive),
        hostname_override=config.hostname_override,
        sanitize_mutual_error_response=config.sanitize_mutual_error_response
        is not False,
        delegate=bool(config.delegate),
        ca_bundle=config.cert if isinstance(config.cert, str) else None,
    )


def trino_connect_kwargs(
    config: TrinoSourceConfig, timeout_seconds: int | None
) -> dict[str, Any]:  # type-state: explicit_any — trino.dbapi.connect kwargs
    """The arguments `trino.dbapi.connect` takes for *config*.

    *timeout_seconds* is the cap already resolved against the source's own
    ``query_max_run_time`` (``TrinoDialect.resolve_timeout_seconds``), so it
    replaces that property rather than being compared to it again.

    The source model has already refused a method this driver does not
    implement and a method missing its fields, so an authenticated source
    cannot reach here and connect without its credential.
    """
    session_properties = dict(config.session_properties)
    if timeout_seconds is not None:
        session_properties["query_max_run_time"] = f"{timeout_seconds}s"
    identity = connection_identity()
    return {
        "host": config.host,
        "port": config.port,
        "user": config.impersonation_user or config.user,
        "catalog": config.database,
        "schema": config.schema_,
        # Every authenticated method sends a credential, so none runs in clear.
        "http_scheme": "https" if config.method else config.http_scheme,
        "http_headers": config.http_headers,
        "auth": _auth(config),
        "verify": True if config.cert is None else config.cert,
        "roles": config.roles,
        "timezone": config.timezone,
        "max_attempts": config.retries,
        "session_properties": session_properties,
        "source": identity["app"],
        # An authored tag may drive the cluster's resource-group routing, so the
        # engine's identity fills the field only when the author left it unset.
        "client_tags": config.client_tags
        or [f"{key}={value}" for key, value in sorted(identity.items())],
    }


class TrinoDriverConnection:
    """One Trino session on the `trino` Python client."""

    def __init__(
        self,
        connection: Any,  # type-state: explicit_any — trino.dbapi.Connection is untyped
    ) -> None:
        self._connection = connection
        self._live: set[Any] = set()  # type-state: explicit_any — untyped Cursor
        self._live_lock = threading.Lock()

    def execute(self, sql: str, limit: RowFetchLimit) -> pa.Table:
        """Run *sql* and return about ``limit.fetch_limit`` rows as Arrow.

        Closing the cursor cancels the query server-side, so a fetch that stops
        at the limit does not leave the rest of the result running.
        """
        cursor = self._open_cursor()
        try:
            cursor.execute(f"/* {attribution_payload()} */\n{sql}")
            columns = [
                (column[0], *arrow_column_type(column[0], column[1]))
                for column in cursor.description
            ]
            schema = pa.schema([(name, type_) for name, type_, _ in columns])
            return read_bounded_table(_batches(cursor, columns, schema), schema, limit)
        finally:
            self._release(cursor)

    def session_sql(self, sql: str) -> None:
        """Run a statement for its effect on the session, discarding any result.

        Trino finishes a statement only as its result is read, so the result is
        drained rather than dropped.
        """
        cursor = self._open_cursor()
        try:
            cursor.execute(sql)
            cursor.fetchall()
        finally:
            self._release(cursor)

    def cancel(self) -> None:
        """Cancel every statement running on this session, from another thread."""
        with self._live_lock:
            live = list(self._live)
        for cursor in live:
            try:
                cursor.cancel()
            except Exception:  # noqa: BLE001 — one failed cancel must not skip the rest
                logger.warning(
                    "Trino could not cancel a running statement", exc_info=True
                )

    def close(self) -> None:
        self._connection.close()

    def _open_cursor(
        self,
    ) -> Any:  # type-state: explicit_any — trino.dbapi.Cursor is untyped
        cursor = self._connection.cursor()
        with self._live_lock:
            self._live.add(cursor)
        return cursor

    def _release(
        self,
        cursor: Any,  # type-state: explicit_any — trino.dbapi.Cursor is untyped
    ) -> None:
        with self._live_lock:
            self._live.discard(cursor)
        try:
            cursor.close()
        except Exception:  # noqa: BLE001 — a failed release must not replace the result
            logger.debug("Error closing a Trino cursor", exc_info=True)


def _batches(
    cursor: Any,  # type-state: explicit_any — trino.dbapi.Cursor is untyped
    columns: list[tuple[str, pa.DataType, _Convert | None]],
    schema: pa.Schema,
) -> Iterator[pa.RecordBatch]:
    while rows := cursor.fetchmany(_BATCH_ROWS):
        arrays = [
            pa.array(
                [
                    row[index]
                    if convert is None or row[index] is None
                    else convert(row[index])
                    for row in rows
                ],
                type=type_,
            )
            for index, (_, type_, convert) in enumerate(columns)
        ]
        yield pa.RecordBatch.from_arrays(arrays, schema=schema)


def require_trino_extra() -> None:
    """Raise unless the ``trino`` extra's client is importable.

    Raises:
        AdapterNotInstalledError: The `trino` package is missing.
    """
    if find_spec("trino") is None:
        raise AdapterNotInstalledError.from_code(
            ERR_ADAPTER_NOT_INSTALLED,
            adapter_type="trino",
            package="trino",
            install=install_hint("trino"),
        )


def open_trino(
    config: TrinoSourceConfig, timeout_seconds: int | None
) -> TrinoDriverConnection:
    """Open a Trino session and prove the coordinator answers on it.

    Raises:
        AdapterNotInstalledError: The ``trino`` extra is not installed.
    """
    require_trino_extra()
    import trino.dbapi

    if config.suppress_cert_warning:
        # urllib3's InsecureRequestWarning, matched by its text so the driver
        # imports nothing the `trino` extra does not itself declare.
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    connection = TrinoDriverConnection(
        trino.dbapi.connect(**trino_connect_kwargs(config, timeout_seconds))
    )
    try:
        connection.session_sql("SELECT 1")
    except BaseException:
        connection.close()
        raise
    return connection
