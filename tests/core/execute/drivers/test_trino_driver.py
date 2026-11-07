"""Trino on the `trino` Python client: what reaches the client's connection, and
how a DB-API cursor becomes an Arrow table.

Every Arrow type comes from the Trino type on `cursor.description`, never from
the values, so a column of NULLs and an empty result carry the same schema as a
full one.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import uuid
import warnings
from collections.abc import Callable, Iterator
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import patch

import nanoarrow as na
import pytest
import trino.auth
import trino.exceptions
from pydantic import ValidationError

from dbt_charts.core.attribution import connection_identity
from dbt_charts.core.compile.models.source import TrinoSourceConfig
from dbt_charts.core.execute.backends.dbt_adapter_factory import (
    AdapterNotInstalledError,
)
from dbt_charts.core.execute.drivers._trino_client import SourceHostConnection
from dbt_charts.core.execute.drivers.trino import (
    TrinoDriverConnection,
    arrow_column_type,
    open_trino,
    trino_connect_kwargs,
)
from dbt_charts.core.execute.query_result import resolve_effective_row_limit

_CONNECT = "trino.dbapi.connect"


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
    kwargs = trino_connect_kwargs(_source(), None, "UTC")
    assert kwargs["host"] == "trino.example.com"
    assert kwargs["port"] == 8080
    assert kwargs["user"] == "ana"
    assert kwargs["catalog"] == "lake"
    assert kwargs["schema"] == "public"
    assert kwargs["http_scheme"] == "http"
    assert kwargs["auth"] is None


@pytest.mark.parametrize("zone", ["America/New_York", "UTC"])
def test_the_session_zone_is_the_querys_not_the_sources_or_the_machines(
    zone: str,
) -> None:
    """The client sends its own zone with every request, so the session zone can
    only be set at connect."""
    kwargs = trino_connect_kwargs(_source(timezone="Asia/Tokyo"), None, zone)
    assert kwargs["timezone"] == zone


def test_ldap_sends_basic_auth_over_https() -> None:
    kwargs = trino_connect_kwargs(_source(method="ldap", password="pw"), None, "UTC")
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
        _source(method="ldap", password="pw", impersonation_user="svc"), None, "UTC"
    )
    assert kwargs["user"] == "svc"


def test_jwt_and_certificate_map_to_the_clients_auth_classes() -> None:
    jwt = trino_connect_kwargs(_source(method="jwt", jwt_token="tok"), None, "UTC")
    assert isinstance(jwt["auth"], trino.auth.JWTAuthentication)
    certificate = trino_connect_kwargs(
        _source(
            method="certificate",
            client_certificate="/c.pem",
            client_private_key="/k.pem",
        ),
        None,
        "UTC",
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
        trino_connect_kwargs(_source(method=method), None, "UTC")
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
            "UTC",
        )
    kwargs = auth.call_args.kwargs
    assert kwargs["mutual_authentication"] == getattr(real, constant)
    assert kwargs["principal"] == "ana@EXAMPLE"
    assert kwargs["ca_bundle"] == "/ca.pem"


def test_retries_reach_the_client() -> None:
    assert trino_connect_kwargs(_source(retries=7), None, "UTC")["max_attempts"] == 7


def test_suppress_cert_warning_silences_only_the_insecure_request_warning() -> None:
    with (
        warnings.catch_warnings(record=True) as caught,
        patch(_CONNECT),
    ):
        warnings.simplefilter("always")
        open_trino(_source(suppress_cert_warning=True), None, "UTC")
        warnings.warn("Unverified HTTPS request is being made", stacklevel=1)
        warnings.warn("something else", stacklevel=1)
    assert [str(w.message) for w in caught] == ["something else"]


def test_opening_proves_the_coordinator_answers() -> None:
    """The client opens no socket at connect, so the open sends a statement."""
    cursor = _Cursor([("_col0", "integer")], [(1,)])
    with patch(_CONNECT, return_value=_Connection(cursor)):
        open_trino(_source(), None, "UTC")
    assert cursor.executed == ["SELECT 1"]


def test_a_coordinator_that_does_not_answer_fails_the_open() -> None:
    def refuse() -> None:
        raise ConnectionError("connection refused")

    connection = _Connection(_Cursor([], [], refuse))
    with (
        patch(_CONNECT, return_value=connection),
        pytest.raises(ConnectionError, match="connection refused"),
    ):
        open_trino(_source(), None, "UTC")
    assert connection.closed


def test_tls_is_verified_unless_the_author_says_otherwise() -> None:
    def verify(source: TrinoSourceConfig) -> bool | str:
        kwargs = trino_connect_kwargs(source, None, "UTC")
        return SourceHostConnection(**kwargs)._http_session.verify

    assert verify(_source()) is True
    assert verify(_source(cert=False)) is False
    assert verify(_source(cert="/ca.pem")) == "/ca.pem"


def test_the_engine_identity_rides_client_tags_unless_authored() -> None:
    identity = connection_identity()
    assert trino_connect_kwargs(_source(), None, "UTC")["client_tags"] == [
        f"{key}={value}" for key, value in sorted(identity.items())
    ]
    authored = trino_connect_kwargs(_source(client_tags=["team-a"]), None, "UTC")
    assert authored["client_tags"] == ["team-a"]


def test_the_query_cap_is_a_session_property_beside_the_authored_ones() -> None:
    kwargs = trino_connect_kwargs(_source(session_properties={"x": 1}), 45, "UTC")
    assert kwargs["session_properties"] == {"x": 1, "query_max_run_time": "45s"}
    assert trino_connect_kwargs(_source(), None, "UTC")["session_properties"] == {}


def test_a_missing_client_names_the_extra_to_install() -> None:
    with (
        patch("dbt_charts.core.execute.drivers.trino.find_spec", return_value=None),
        pytest.raises(AdapterNotInstalledError, match="trino"),
    ):
        open_trino(_source(), None, "UTC")


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


# --- URLs the server hands back ----------------------------------------------


class _Server:
    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, dict[str, str], bytes]] = {}
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                server.requests.append((self.command, self.path, dict(self.headers)))
                status, headers, body = server.routes.get(
                    self.path, (404, {}, b"no route")
                )
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_DELETE = do_HEAD = _answer

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def url(self, path: str, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{path}"

    def json(self, path: str, payload: dict[str, Any]) -> None:
        self.routes[path] = (
            200,
            {"Content-Type": "application/json"},
            json.dumps(payload).encode(),
        )

    def paths(self) -> list[str]:
        return [path for _, path, _ in self.requests]

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def coordinator() -> Iterator[_Server]:
    server = _Server()
    yield server
    server.close()


@pytest.fixture
def elsewhere() -> Iterator[_Server]:
    server = _Server()
    yield server
    server.close()


_COLUMNS = [
    {
        "name": "n",
        "type": "integer",
        "typeSignature": {"rawType": "integer", "arguments": []},
    }
]


def _page(server: _Server, next_uri: str | None, data: Any = None) -> dict[str, Any]:
    page: dict[str, Any] = {
        "id": "q1",
        "infoUri": server.url("/ui/q1"),
        "stats": {"state": "RUNNING"},
        "columns": _COLUMNS,
    }
    if next_uri is not None:
        page["nextUri"] = next_uri
    if data is not None:
        page["data"] = data
    return page


def _open(coordinator: _Server) -> TrinoDriverConnection:
    return open_trino(
        _source(host="127.0.0.1", port=coordinator.port, restrict_to_host=True),
        None,
        "UTC",
    )


def test_a_paged_query_reads_every_page_from_the_source_host(
    coordinator: _Server,
) -> None:
    coordinator.json("/v1/statement", _page(coordinator, coordinator.url("/v1/q1/1")))
    coordinator.json("/v1/q1/1", _page(coordinator, None, [[7]]))
    connection = _open(coordinator)
    assert connection.execute(
        "SELECT 7", resolve_effective_row_limit(10)
    ).to_pylist() == [{"n": 7}]
    assert coordinator.paths().count("/v1/q1/1") == 2


def test_a_host_written_as_a_url_is_the_place_the_client_dials(
    coordinator: _Server,
) -> None:
    coordinator.json("/v1/statement", _page(coordinator, coordinator.url("/v1/q1/1")))
    coordinator.json("/v1/q1/1", _page(coordinator, None, [[7]]))
    source = _source(host=f"http://127.0.0.1:{coordinator.port}", restrict_to_host=True)
    connection = open_trino(source, None, "UTC")
    assert connection.execute(
        "SELECT 7", resolve_effective_row_limit(10)
    ).to_pylist() == [{"n": 7}]


def test_an_ipv6_host_is_not_refused_by_the_guard() -> None:
    with pytest.raises(trino.exceptions.TrinoConnectionError):
        open_trino(
            _source(host="[::1]", port=1, retries=1, restrict_to_host=True), None, "UTC"
        )


def test_the_driver_never_asks_for_spooled_results(coordinator: _Server) -> None:
    coordinator.json("/v1/statement", _page(coordinator, None, [[1]]))
    _open(coordinator)
    _, _, headers = coordinator.requests[0]
    assert "X-Trino-Query-Data-Encoding" not in headers


@pytest.mark.parametrize("other", ["other host", "other port"])
def test_a_next_page_url_off_the_source_host_fails_the_query(
    coordinator: _Server, elsewhere: _Server, other: str
) -> None:
    stolen = (
        coordinator.url("/stolen", host="localhost")
        if other == "other host"
        else elsewhere.url("/stolen")
    )
    coordinator.json("/v1/statement", _page(coordinator, stolen))
    with pytest.raises(ValueError, match="only follows URLs on http://127.0.0.1"):
        _open(coordinator)
    assert "/stolen" not in coordinator.paths() + elsewhere.paths()


def test_a_redirect_off_the_source_host_fails_the_query(
    coordinator: _Server, elsewhere: _Server
) -> None:
    coordinator.routes["/v1/statement"] = (
        307,
        {"Location": elsewhere.url("/stolen")},
        b"",
    )
    with pytest.raises(ValueError, match="only follows URLs on http://127.0.0.1"):
        _open(coordinator)
    assert elsewhere.requests == []


def test_spooled_results_fail_the_query_before_any_segment_is_fetched(
    coordinator: _Server, elsewhere: _Server
) -> None:
    segment = {
        "type": "spooled",
        "uri": elsewhere.url("/segment"),
        "ackUri": elsewhere.url("/ack"),
        "headers": {"Metadata-Flavor": ["Google"]},
        "metadata": {"segmentSize": 10, "rowOffset": 0, "rowsCount": 1},
    }
    coordinator.json("/v1/statement", _page(coordinator, coordinator.url("/v1/q1/1")))
    coordinator.json(
        "/v1/q1/1",
        _page(coordinator, None, {"encoding": "json", "segments": [segment]}),
    )
    with pytest.raises(ValueError, match="spooled"):
        _open(coordinator)
    assert elsewhere.requests == []


def test_without_the_restriction_a_next_page_url_on_another_host_is_followed(
    coordinator: _Server, elsewhere: _Server
) -> None:
    coordinator.json("/v1/statement", _page(coordinator, elsewhere.url("/v1/q1/1")))
    elsewhere.json("/v1/q1/1", _page(elsewhere, None, [[1]]))
    open_trino(_source(host="127.0.0.1", port=coordinator.port), None, "UTC")
    assert elsewhere.paths() == ["/v1/q1/1"]


@pytest.mark.parametrize(("restricted", "asks"), [(False, True), (True, False)])
def test_only_the_restriction_stops_the_client_asking_for_spooled_results(
    coordinator: _Server, restricted: bool, asks: bool
) -> None:
    coordinator.json("/v1/statement", _page(coordinator, None, [[1]]))
    open_trino(
        _source(host="127.0.0.1", port=coordinator.port, restrict_to_host=restricted),
        None,
        "UTC",
    )
    _, _, headers = coordinator.requests[0]
    assert ("X-Trino-Query-Data-Encoding" in headers) is asks
