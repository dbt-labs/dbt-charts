"""Trino on the `trino` Python client: what reaches `trino.dbapi.connect`, and
how a DB-API cursor becomes an Arrow table.

Every Arrow type comes from the Trino type on `cursor.description`, never from
the values, so a column of NULLs and an empty result carry the same schema as a
full one.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
import warnings
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import nanoarrow as na
import pytest
import trino.auth
from pydantic import ValidationError

from dbt_charts.core.attribution import connection_identity
from dbt_charts.core.compile.models.source import TrinoSourceConfig
from dbt_charts.core.execute.adapters.base import resolve_effective_row_limit
from dbt_charts.core.execute.adapters.dbt_adapter_factory import (
    AdapterNotInstalledError,
)
from dbt_charts.core.execute.drivers.trino import (
    TrinoDriverConnection,
    arrow_column_type,
    open_trino,
    trino_connect_kwargs,
)


def _source(**fields: Any) -> TrinoSourceConfig:
    return TrinoSourceConfig.model_validate(
        {
            "type": "trino",
            "host": "trino.example.com",
            "catalog": "lake",
            "schema": "public",
            "user": "ana",
            **fields,
        }
    )


class _Cursor:
    """A DB-API cursor over fixed rows that records how it was driven."""

    def __init__(
        self,
        columns: list[tuple[str, str]],
        rows: list[tuple[Any, ...]],
        on_execute: Callable[[], None] | None = None,
    ) -> None:
        self.description = [
            (name, type_, None, None, None, None, None) for name, type_ in columns
        ]
        self._rows = rows
        self._on_execute = on_execute
        self.executed: list[str] = []
        self.fetched = 0
        self.cancelled = False
        self.closed = False

    def execute(self, sql: str) -> None:
        self.executed.append(sql)
        if self._on_execute is not None:
            self._on_execute()

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        batch, self._rows = self._rows[:size], self._rows[size:]
        self.fetched += len(batch)
        return batch

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.fetchmany(len(self._rows))

    def cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.closed = True


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> _Cursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def _run(columns: list[tuple[str, str]], rows: list[tuple[Any, ...]]) -> na.Array:
    connection = TrinoDriverConnection(_Connection(_Cursor(columns, rows)))
    return connection.execute("SELECT 1", resolve_effective_row_limit(100))


# --- connection options ------------------------------------------------------


def test_an_unauthenticated_source_connects_as_its_user() -> None:
    kwargs = trino_connect_kwargs(_source(), None)
    assert kwargs["host"] == "trino.example.com"
    assert kwargs["port"] == 8080
    assert kwargs["user"] == "ana"
    assert kwargs["catalog"] == "lake"
    assert kwargs["schema"] == "public"
    assert kwargs["http_scheme"] == "http"
    assert kwargs["auth"] is None


def test_ldap_sends_basic_auth_over_https() -> None:
    kwargs = trino_connect_kwargs(_source(method="ldap", password="pw"), None)
    assert isinstance(kwargs["auth"], trino.auth.BasicAuthentication)
    assert kwargs["http_scheme"] == "https"


@pytest.mark.parametrize(
    ("fields", "missing"),
    [
        ({"method": "ldap"}, "password"),
        ({"method": "ldap", "password": ""}, "password"),
        ({"method": "jwt"}, "jwt_token"),
        ({"method": "jwt", "jwt_token": ""}, "jwt_token"),
        ({"method": "certificate", "client_certificate": "/c.pem"}, "certificate"),
        ({"method": "kerberos", "keytab": "/k.keytab"}, "KRB5_CLIENT_KTNAME"),
        ({"method": "gssapi", "keytab": "/k.keytab"}, "KRB5_CLIENT_KTNAME"),
        ({"method": "gssapi", "mutual_authentication": "sometimes"}, "REQUIRED"),
        ({"method": "saml"}, "method"),
    ],
)
def test_a_method_the_source_cannot_honor_is_refused_when_it_is_parsed(
    fields: dict[str, Any], missing: str
) -> None:
    """Before any connection: `dct validate` sees it, and no path connects
    without the credential the author asked for."""
    with pytest.raises(ValidationError, match=missing):
        _source(**fields)


def test_ldap_can_impersonate_another_user() -> None:
    kwargs = trino_connect_kwargs(
        _source(method="ldap", password="pw", impersonation_user="svc"), None
    )
    assert kwargs["user"] == "svc"


def test_jwt_and_certificate_map_to_the_clients_auth_classes() -> None:
    jwt = trino_connect_kwargs(_source(method="jwt", jwt_token="tok"), None)
    assert isinstance(jwt["auth"], trino.auth.JWTAuthentication)
    certificate = trino_connect_kwargs(
        _source(
            method="certificate",
            client_certificate="/c.pem",
            client_private_key="/k.pem",
        ),
        None,
    )
    assert isinstance(certificate["auth"], trino.auth.CertificateAuthentication)


@pytest.mark.parametrize(
    ("method", "handler"),
    [
        ("oauth", trino.auth.WebBrowserRedirectHandler),
        ("oauth_console", trino.auth.ConsoleRedirectHandler),
    ],
)
def test_each_oauth_method_signs_in_its_own_way(method: str, handler: type) -> None:
    with patch("trino.auth.OAuth2Authentication") as oauth:
        trino_connect_kwargs(_source(method=method), None)
    (composite,) = oauth.call_args.args
    assert [type(h) for h in composite.handlers] == [handler]


@pytest.mark.parametrize(
    ("method", "auth_cls", "authored", "constant"),
    [
        ("kerberos", "KerberosAuthentication", None, "MUTUAL_DISABLED"),
        ("kerberos", "KerberosAuthentication", False, "MUTUAL_DISABLED"),
        ("kerberos", "KerberosAuthentication", True, "MUTUAL_REQUIRED"),
        ("kerberos", "KerberosAuthentication", "DISABLED", "MUTUAL_DISABLED"),
        ("gssapi", "GSSAPIAuthentication", None, "MUTUAL_DISABLED"),
        ("gssapi", "GSSAPIAuthentication", "disabled", "MUTUAL_DISABLED"),
        ("gssapi", "GSSAPIAuthentication", "OPTIONAL", "MUTUAL_OPTIONAL"),
        ("gssapi", "GSSAPIAuthentication", "required", "MUTUAL_REQUIRED"),
    ],
)
def test_mutual_authentication_reaches_the_client_as_the_mode_authored(
    method: str, auth_cls: str, authored: bool | str | None, constant: str
) -> None:
    real = getattr(trino.auth, auth_cls)
    fields = {} if authored is None else {"mutual_authentication": authored}
    with patch(f"trino.auth.{auth_cls}") as auth:
        auth.MUTUAL_REQUIRED = real.MUTUAL_REQUIRED
        auth.MUTUAL_OPTIONAL = real.MUTUAL_OPTIONAL
        auth.MUTUAL_DISABLED = real.MUTUAL_DISABLED
        trino_connect_kwargs(
            _source(method=method, principal="ana@EXAMPLE", cert="/ca.pem", **fields),
            None,
        )
    kwargs = auth.call_args.kwargs
    assert kwargs["mutual_authentication"] == getattr(real, constant)
    assert kwargs["principal"] == "ana@EXAMPLE"
    assert kwargs["ca_bundle"] == "/ca.pem"


def test_retries_reach_the_client() -> None:
    assert trino_connect_kwargs(_source(retries=7), None)["max_attempts"] == 7


def test_suppress_cert_warning_silences_only_the_insecure_request_warning() -> None:
    with (
        warnings.catch_warnings(record=True) as caught,
        patch("trino.dbapi.connect"),
    ):
        warnings.simplefilter("always")
        open_trino(_source(suppress_cert_warning=True), None)
        warnings.warn("Unverified HTTPS request is being made", stacklevel=1)
        warnings.warn("something else", stacklevel=1)
    assert [str(w.message) for w in caught] == ["something else"]


def test_opening_proves_the_coordinator_answers() -> None:
    """The client opens no socket at connect, so the open sends a statement."""
    cursor = _Cursor([("_col0", "integer")], [(1,)])
    with patch("trino.dbapi.connect", return_value=_Connection(cursor)):
        open_trino(_source(), None)
    assert cursor.executed == ["SELECT 1"]


def test_a_coordinator_that_does_not_answer_fails_the_open() -> None:
    def refuse() -> None:
        raise ConnectionError("connection refused")

    connection = _Connection(_Cursor([], [], refuse))
    with (
        patch("trino.dbapi.connect", return_value=connection),
        pytest.raises(ConnectionError, match="connection refused"),
    ):
        open_trino(_source(), None)
    assert connection.closed


def test_tls_is_verified_unless_the_author_says_otherwise() -> None:
    assert trino_connect_kwargs(_source(), None)["verify"] is True
    assert trino_connect_kwargs(_source(cert=False), None)["verify"] is False
    assert trino_connect_kwargs(_source(cert="/ca.pem"), None)["verify"] == "/ca.pem"


def test_the_engine_identity_rides_client_tags_unless_authored() -> None:
    identity = connection_identity()
    assert trino_connect_kwargs(_source(), None)["client_tags"] == [
        f"{key}={value}" for key, value in sorted(identity.items())
    ]
    authored = trino_connect_kwargs(_source(client_tags=["team-a"]), None)
    assert authored["client_tags"] == ["team-a"]


def test_the_query_cap_is_a_session_property_beside_the_authored_ones() -> None:
    kwargs = trino_connect_kwargs(_source(session_properties={"x": 1}), 45)
    assert kwargs["session_properties"] == {"x": 1, "query_max_run_time": "45s"}
    assert trino_connect_kwargs(_source(), None)["session_properties"] == {}


def test_a_missing_client_names_the_extra_to_install() -> None:
    with (
        patch("dbt_charts.core.execute.drivers.trino.find_spec", return_value=None),
        pytest.raises(AdapterNotInstalledError, match="trino"),
    ):
        open_trino(_source(), None)


# --- Trino type → Arrow type -------------------------------------------------


@pytest.mark.parametrize(
    ("trino_type", "value", "arrow_type", "expected"),
    [
        ("boolean", True, na.bool_(), True),
        ("tinyint", 1, na.int8(), 1),
        ("smallint", 1, na.int16(), 1),
        ("integer", 1, na.int32(), 1),
        ("bigint", 2**40, na.int64(), 2**40),
        ("real", 0.1, na.float64(), 0.1),
        ("double", 0.1, na.float64(), 0.1),
        ("decimal(10,2)", Decimal("1.50"), na.decimal128(10, 2), Decimal("1.50")),
        ("varchar", "a", na.string(), "a"),
        ("varchar(5)", "a", na.string(), "a"),
        ("char(3)", "a  ", na.string(), "a  "),
        ("json", '{"a":1}', na.string(), '{"a":1}'),
        ("varbinary", b"\x00", na.binary(), b"\x00"),
        ("date", dt.date(2026, 1, 2), na.date32(), dt.date(2026, 1, 2)),
        (
            "timestamp(3)",
            dt.datetime(2026, 1, 2, 3, 4, 5),
            na.timestamp("us"),
            dt.datetime(2026, 1, 2, 3, 4, 5),
        ),
        (
            "timestamp(3) with time zone",
            dt.datetime(2026, 1, 2, 3, tzinfo=dt.timezone(dt.timedelta(hours=1))),
            na.timestamp("us", "UTC"),
            dt.datetime(2026, 1, 2, 2, tzinfo=dt.UTC),
        ),
        ("time(3)", dt.time(1, 2, 3), na.time64("us"), dt.time(1, 2, 3)),
        (
            "uuid",
            uuid.UUID("12345678-1234-5678-1234-567812345678"),
            na.string(),
            "12345678-1234-5678-1234-567812345678",
        ),
        ("array(integer)", [1, 2], na.string(), "[1, 2]"),
        ("map(varchar, integer)", {"a": 1}, na.string(), '{"a": 1}'),
        ("row(x integer, y varchar)", (1, "a"), na.string(), '[1, "a"]'),
    ],
)
def test_each_trino_type_family_maps_to_one_arrow_type(
    trino_type: str, value: object, arrow_type: na.Schema, expected: object
) -> None:
    table = _run([("c", trino_type)], [(value,), (None,)])
    assert na.c_schema(table.schema.field(0)).format == na.c_schema(arrow_type).format
    assert table.child(0).to_pylist() == [expected, None]


def test_a_nested_value_is_json_all_the_way_down() -> None:
    value = {1: [Decimal("1.5"), dt.date(2026, 1, 2)]}
    table = _run([("c", "map(integer, array(decimal(3,1)))")], [(value,)])
    assert json.loads(table.child(0).to_pylist()[0]) == {"1": ["1.5", "2026-01-02"]}


def test_an_empty_result_still_carries_its_typed_columns() -> None:
    table = _run([("n", "bigint"), ("s", "varchar")], [])
    fields = [table.schema.field(i) for i in range(table.schema.n_fields)]
    assert [(f.name, na.c_schema(f).format) for f in fields] == [("n", "l"), ("s", "u")]
    assert len(table) == 0


@pytest.mark.parametrize(
    "trino_type", ["interval day to second", "time(3) with time zone", "HyperLogLog"]
)
def test_an_unmapped_type_raises_instead_of_guessing(trino_type: str) -> None:
    with pytest.raises(ValueError, match="CAST"):
        arrow_column_type("c", trino_type)


# --- running statements ------------------------------------------------------


def test_a_fetch_stops_at_the_row_limit_and_releases_the_query() -> None:
    cursor = _Cursor([("n", "bigint")], [(n,) for n in range(50_000)])
    limit = resolve_effective_row_limit(10)
    table = TrinoDriverConnection(_Connection(cursor)).execute("SELECT n", limit)
    assert len(table) >= limit.fetch_limit
    assert cursor.fetched < 50_000
    assert cursor.closed


def test_a_statement_carries_the_attribution_comment() -> None:
    cursor = _Cursor([("n", "bigint")], [(1,)])
    TrinoDriverConnection(_Connection(cursor)).execute(
        "SELECT 1", resolve_effective_row_limit(1)
    )
    comment, sql = cursor.executed[0].split("\n", 1)
    assert sql == "SELECT 1"
    assert json.loads(comment.removeprefix("/* ").removesuffix(" */"))["app"] == (
        "dbt-charts"
    )


def test_session_sql_runs_the_statement_to_completion() -> None:
    cursor = _Cursor([("result", "boolean")], [(True,)])
    TrinoDriverConnection(_Connection(cursor)).session_sql("SET SESSION x = 1")
    assert cursor.executed == ["SET SESSION x = 1"]
    assert cursor.fetched == 1
    assert cursor.closed


def test_cancel_reaches_the_statement_that_is_running() -> None:
    cursor = _Cursor([("n", "bigint")], [(1,)], lambda: connection.cancel())
    connection = TrinoDriverConnection(_Connection(cursor))
    connection.execute("SELECT 1", resolve_effective_row_limit(1))
    assert cursor.cancelled


class _UnreachableCursor(_Cursor):
    """A cursor whose coordinator stops answering once the rows are read."""

    def cancel(self) -> None:
        raise RuntimeError("coordinator gone")

    def close(self) -> None:
        raise RuntimeError("coordinator gone")


def test_a_failed_cancel_or_release_does_not_replace_the_result() -> None:
    cursor = _UnreachableCursor([("n", "bigint")], [(1,)], lambda: connection.cancel())
    connection = TrinoDriverConnection(_Connection(cursor))
    assert len(connection.execute("SELECT 1", resolve_effective_row_limit(1))) == 1


def test_cancel_with_nothing_running_is_a_no_op() -> None:
    cursor = _Cursor([("n", "bigint")], [])
    TrinoDriverConnection(_Connection(cursor)).cancel()
    assert not cursor.cancelled
