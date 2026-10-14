"""The public dbt_charts.core connection API: discovery helpers, probes,
bulk schema, and test_connection."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dbt_charts._install_hint import install_hint
from dbt_charts.core.compile.config import get_execution_config
from dbt_charts.core.compile.models.source import (
    BigQuerySourceConfig,
    DatabricksSourceConfig,
    DbtTargetSourceConfig,
    DuckDBSourceConfig,
    PostgresSourceConfig,
    RedshiftSourceConfig,
    SnowflakeSourceConfig,
)

# Aliased: pytest's default python_functions collector treats any top-level
# callable named test_* as a test item, imported or not — a bare
# `test_connection` here would get collected and fail at setup (no fixture
# named source_config).
from dbt_charts.core.connections import (
    WarehouseAuthError,
    WarehouseDatabaseNotFoundError,
    WarehousePermissionError,
    WarehouseProbeError,
    WarehouseUnreachableError,
    build_bigquery_client,
    bulk_schema_for_config,
    bulk_tables_for_config,
    classify_connection_error,
    dataset_location,
    list_catalogs,
    list_datasets,
    probe_relation_readability,
    test_connection as probe_connection,
)
from dbt_charts.core.dialects import DuckDBDialect
from dbt_charts.core.inspect.relations import Relation


@dataclass
class _StubDataset:
    dataset_id: str
    location: str


class _StubClient:
    """Stands in for google.cloud.bigquery.Client."""

    def __init__(self, datasets: list[_StubDataset]) -> None:
        self._datasets = datasets
        self.got: list[str] = []

    def list_datasets(self) -> list[_StubDataset]:
        return self._datasets

    def get_dataset(self, dataset_id: str) -> _StubDataset:
        self.got.append(dataset_id)
        for ds in self._datasets:
            if ds.dataset_id == dataset_id:
                return ds
        raise KeyError(dataset_id)


def _bq_config(dataset: str = "") -> BigQuerySourceConfig:
    return BigQuerySourceConfig(
        type="bigquery",
        project="my-project",
        dataset=dataset,
        keyfile_json={"type": "service_account", "project_id": "my-project"},
    )


@pytest.fixture
def stub_client(monkeypatch: pytest.MonkeyPatch) -> _StubClient:
    client = _StubClient(
        [_StubDataset("analytics", "us-east4"), _StubDataset("raw", "EU")]
    )

    def _build(*_args: Any, **_kwargs: Any) -> _StubClient:
        return client

    monkeypatch.setattr("dbt_charts.core.connections.build_bigquery_client", _build)
    return client


def test_list_datasets_returns_names(stub_client: _StubClient) -> None:
    """Discovery works with no dataset set — that is the whole point of it."""
    assert list_datasets(_bq_config()) == ["analytics", "raw"]


def test_dataset_location_reads_the_chosen_dataset(stub_client: _StubClient) -> None:
    assert dataset_location(_bq_config("raw")) == "EU"
    # Exactly one lookup: resolving every listed dataset would be an N+1.
    assert stub_client.got == ["raw"]


def test_dataset_location_requires_a_dataset() -> None:
    """No dataset is a caller bug, not a None-returning soft path."""
    with pytest.raises(ValueError, match="dataset"):
        dataset_location(_bq_config())


def test_list_datasets_names_its_own_permission_on_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google.api_core.exceptions import Forbidden

    class _ForbiddenClient:
        def list_datasets(self) -> None:
            raise Forbidden("Access Denied: svc@my-proj.iam.gserviceaccount.com")

    monkeypatch.setattr(
        "dbt_charts.core.connections.build_bigquery_client",
        lambda *a, **k: _ForbiddenClient(),
    )

    with pytest.raises(WarehousePermissionError) as exc_info:
        list_datasets(_bq_config())

    exc = exc_info.value
    assert "bigquery.datasets.list" in str(exc)
    assert "svc@my-proj.iam.gserviceaccount.com" not in str(exc)
    assert "svc@my-proj.iam.gserviceaccount.com" in exc.detail


def test_dataset_location_names_its_own_permission_on_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dataset_location fails on a different grant (.get, not .list) than
    list_datasets — the shared classifier must not hard-code either one's
    permission name into the other's display."""
    from google.api_core.exceptions import Forbidden

    class _ForbiddenClient:
        def get_dataset(self, dataset_id: str) -> None:
            raise Forbidden("Access Denied")

    monkeypatch.setattr(
        "dbt_charts.core.connections.build_bigquery_client",
        lambda *a, **k: _ForbiddenClient(),
    )

    with pytest.raises(WarehousePermissionError) as exc_info:
        dataset_location(_bq_config("analytics"))

    assert "bigquery.datasets.get" in str(exc_info.value)
    assert "bigquery.datasets.list" not in str(exc_info.value)


def test_list_datasets_classifies_a_transport_error_as_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google.auth.exceptions import TransportError

    class _UnreachableClient:
        def list_datasets(self) -> None:
            raise TransportError("connection reset by peer at 10.9.8.7")

    monkeypatch.setattr(
        "dbt_charts.core.connections.build_bigquery_client",
        lambda *a, **k: _UnreachableClient(),
    )

    with pytest.raises(WarehouseUnreachableError) as exc_info:
        list_datasets(_bq_config())

    exc = exc_info.value
    assert "10.9.8.7" not in str(exc)
    assert "10.9.8.7" in exc.detail


@pytest.mark.parametrize("exc_name", ["MalformedError", "RefreshError"])
def test_list_datasets_classifies_a_bad_credential_as_auth(
    monkeypatch: pytest.MonkeyPatch, exc_name: str
) -> None:
    """A malformed keyfile and a revoked/expired key are different google
    types on the same bucket."""
    import google.auth.exceptions

    raised = getattr(google.auth.exceptions, exc_name)(
        "invalid_grant: Invalid JWT Signature for svc@my-proj.iam.gserviceaccount.com"
    )

    class _BadCredentialClient:
        def list_datasets(self) -> None:
            raise raised

    monkeypatch.setattr(
        "dbt_charts.core.connections.build_bigquery_client",
        lambda *a, **k: _BadCredentialClient(),
    )

    with pytest.raises(WarehouseAuthError) as exc_info:
        list_datasets(_bq_config())

    exc = exc_info.value
    assert "service account credentials" in str(exc)
    assert "svc@my-proj.iam.gserviceaccount.com" not in str(exc)
    assert "svc@my-proj.iam.gserviceaccount.com" in exc.detail


def test_list_datasets_classifies_an_unparseable_pem_as_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The discovery path sees a bare ValueError for this, not a google type
    — the one rule in this classifier keyed on a substring rather than
    isinstance."""

    class _BadKeyClient:
        def list_datasets(self) -> None:
            raise ValueError("Unable to load PEM file. See /etc/keys/svc.json")

    monkeypatch.setattr(
        "dbt_charts.core.connections.build_bigquery_client",
        lambda *a, **k: _BadKeyClient(),
    )

    with pytest.raises(WarehouseAuthError) as exc_info:
        list_datasets(_bq_config())

    exc = exc_info.value
    assert "service account credentials" in str(exc)
    assert "/etc/keys/svc.json" not in str(exc)
    assert "Unable to load PEM file" in exc.detail


def test_list_datasets_pem_rule_is_scoped_to_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The PEM rule is the one substring match in a classifier that otherwise
    discriminates by type, so it is deliberately conjoined with ValueError —
    the same text on any other exception is not a credential diagnosis."""

    class _OddlyWordedClient:
        def list_datasets(self) -> None:
            raise RuntimeError("Unable to load PEM file")

    monkeypatch.setattr(
        "dbt_charts.core.connections.build_bigquery_client",
        lambda *a, **k: _OddlyWordedClient(),
    )

    with pytest.raises(WarehouseProbeError) as exc_info:
        list_datasets(_bq_config())

    assert type(exc_info.value) is WarehouseProbeError


def test_list_datasets_splits_quota_from_permission_on_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google raises the same Forbidden type for a missing permission and for
    quota exhaustion — only the substring 'quota exceeded' tells them apart,
    and getting the split wrong hands a quota problem a permission fix."""
    from google.api_core.exceptions import Forbidden

    class _QuotaExhaustedClient:
        def list_datasets(self) -> None:
            raise Forbidden("Quota exceeded: too many requests for project my-proj")

    monkeypatch.setattr(
        "dbt_charts.core.connections.build_bigquery_client",
        lambda *a, **k: _QuotaExhaustedClient(),
    )

    with pytest.raises(WarehouseProbeError) as exc_info:
        list_datasets(_bq_config())

    exc = exc_info.value
    assert type(exc) is not WarehousePermissionError
    assert "quota" in str(exc).lower()
    assert "my-proj" not in str(exc)


def test_list_datasets_classifies_a_not_found_as_database_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google.api_core.exceptions import NotFound

    class _MissingProjectClient:
        def list_datasets(self) -> None:
            raise NotFound("404 GET https://bigquery.googleapis.com/…: not found")

    monkeypatch.setattr(
        "dbt_charts.core.connections.build_bigquery_client",
        lambda *a, **k: _MissingProjectClient(),
    )

    with pytest.raises(WarehouseDatabaseNotFoundError) as exc_info:
        list_datasets(_bq_config())

    exc = exc_info.value
    assert "bigquery.googleapis.com" not in str(exc)


def test_list_datasets_details_a_message_less_driver_exception_by_type_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A driver exception with no message must still leave a non-empty
    .detail for logs."""

    class _SilentlyFailingClient:
        def list_datasets(self) -> None:
            raise RuntimeError()

    monkeypatch.setattr(
        "dbt_charts.core.connections.build_bigquery_client",
        lambda *a, **k: _SilentlyFailingClient(),
    )

    with pytest.raises(WarehouseProbeError) as exc_info:
        list_datasets(_bq_config())

    assert exc_info.value.detail == "RuntimeError"


def test_dataset_location_rejects_non_bigquery() -> None:
    """No other warehouse has a dataset location — asking for one is a caller bug."""
    cfg = DuckDBSourceConfig(type="duckdb", path=":memory:")
    with pytest.raises(ValueError, match="BigQuery"):
        dataset_location(cfg)


def test_list_datasets_rejects_non_bigquery() -> None:
    cfg = DuckDBSourceConfig(type="duckdb", path=":memory:")
    with pytest.raises(ValueError, match="BigQuery"):
        list_datasets(cfg)


def test_missing_bigquery_extra_names_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing optional dep must say how to fix it, not raise a bare ImportError."""

    def _no_module(_name: str) -> Any:
        raise ImportError("No module named 'google.cloud.bigquery'")

    monkeypatch.setattr("dbt_charts.core.connections.import_bigquery", _no_module)
    with pytest.raises(ImportError, match="bigquery"):
        list_datasets(_bq_config())


# ── build_bigquery_client: the one home for BigQuery client construction ─────


def test_missing_google_cloud_raises_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_import(name: str) -> Any:
        if name == "google.cloud.bigquery":
            raise ModuleNotFoundError(
                "No module named 'google.cloud'", name="google.cloud"
            )
        raise AssertionError(f"unexpected module import: {name}")

    monkeypatch.setattr("importlib.import_module", _fake_import)
    with pytest.raises(ImportError) as exc_info:
        build_bigquery_client("my-project")
    # The installer-aware command: a uv-tool install must not be told to pip.
    assert install_hint("bigquery") in str(exc_info.value)


def test_missing_google_oauth_raises_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_import(name: str) -> Any:
        if name == "google.cloud.bigquery":
            return SimpleNamespace(Client=lambda **_kwargs: object())
        if name == "google.oauth2.service_account":
            raise ModuleNotFoundError(
                "No module named 'google.oauth2'", name="google.oauth2"
            )
        raise AssertionError(f"unexpected module import: {name}")

    monkeypatch.setattr("importlib.import_module", _fake_import)
    with pytest.raises(ImportError, match=r"dbt-charts\[bigquery\]"):
        build_bigquery_client("my-project", {"type": "service_account"})


# ── probe_relation_readability: the D-03 per-relation LIMIT 0 probe ──────────


def _duckdb_with_table(tmp_path: Path, schema: str, table: str) -> DuckDBSourceConfig:
    import duckdb

    db_path = tmp_path / "probe.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(f"CREATE SCHEMA {schema}")
    con.execute(f"CREATE TABLE {schema}.{table} (id INTEGER)")
    con.close()
    return DuckDBSourceConfig(type="duckdb", path=str(db_path))


def test_a_readable_relation_probes_successfully(tmp_path: Path) -> None:
    source_config = _duckdb_with_table(tmp_path, "analytics", "orders")
    success, message = probe_relation_readability(
        source_config, Relation(name="orders", schema="analytics")
    )
    assert success is True
    assert message == ""


def test_an_unreadable_relation_reports_the_failure(tmp_path: Path) -> None:
    source_config = _duckdb_with_table(tmp_path, "analytics", "orders")
    success, message = probe_relation_readability(
        source_config, Relation(name="missing_table", schema="analytics")
    )
    assert success is False
    assert message


def test_probe_never_raises_on_a_driver_error(tmp_path: Path) -> None:
    """The probe reports failure through the return value, unlike
    test_connection — never an unhandled exception, since a caller runs this
    per relation in a loop."""
    source_config = _duckdb_with_table(tmp_path, "analytics", "orders")
    success, _message = probe_relation_readability(
        source_config, Relation(name="nope", schema="does_not_exist")
    )
    assert success is False


# ── bulk_schema_for_config: the connection-scoped bulk-schema seam ───────────


def _duckdb_with_two_schemas(tmp_path: Path) -> DuckDBSourceConfig:
    import duckdb

    db_path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA sales")
        con.execute("CREATE SCHEMA marketing")
        con.execute("CREATE TABLE sales.orders (id INTEGER, amount DECIMAL(10,2))")
        con.execute("CREATE TABLE marketing.campaigns (id INTEGER, channel VARCHAR)")
    finally:
        con.close()
    return DuckDBSourceConfig(type="duckdb", path=str(db_path))


def test_bulk_schema_for_config_returns_the_schema_tree(tmp_path: Path) -> None:
    source_config = _duckdb_with_two_schemas(tmp_path)
    tree = bulk_schema_for_config(source_config)

    assert set(tree) >= {"sales", "marketing"}
    assert set(tree["sales"]) == {"orders"}
    assert list(tree["sales"]["orders"]) == ["id", "amount"]
    assert set(tree["marketing"]["campaigns"]) == {"id", "channel"}


def test_bulk_schema_for_config_propagates_not_implemented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dialect with no bulk-introspection form must not be swallowed into a
    RuntimeError — the caller needs to distinguish 'unsupported' from 'failed'."""
    source_config = _duckdb_with_two_schemas(tmp_path)

    class _NoBulkFormDialect(DuckDBDialect):
        def bulk_schema_sql(self, scope: str = "") -> str:
            raise NotImplementedError("no bulk-introspection form")

    monkeypatch.setattr(
        "dbt_charts.core.dialects.get_dialect", lambda _type: _NoBulkFormDialect()
    )
    with pytest.raises(NotImplementedError):
        bulk_schema_for_config(source_config)


def test_bulk_schema_for_config_normalizes_driver_errors_to_runtime_error(
    tmp_path: Path,
) -> None:
    source_config = DuckDBSourceConfig(
        type="duckdb", path=str(tmp_path / "missing_dir" / "wh.duckdb")
    )
    # match pins the normalized wrapper: a bare RuntimeError would also accept
    # NotImplementedError (its subclass) — the very case the contract separates.
    with pytest.raises(RuntimeError, match="bulk_schema_for_config query failed"):
        bulk_schema_for_config(source_config)


def test_bulk_schema_for_config_scopes_bigquery_to_the_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scope must survive the SourceConfig→model_dump→bulk_schema_scope
    hop: if the dump ever stops carrying `location`, BigQuery's dialect raises
    NotImplementedError and every BigQuery connection profiles as an error."""
    from types import SimpleNamespace

    from dbt_charts.core.compile.models.source import BigQuerySourceConfig

    captured: dict[str, str] = {}

    class _CapturingAdapter:
        connections = SimpleNamespace(
            get_thread_connection=lambda: SimpleNamespace(handle=object())
        )

        def connection_named(self, name: str):
            from contextlib import nullcontext

            return nullcontext()

        def execute(self, sql: str, **kwargs: object):
            captured["sql"] = sql
            table = SimpleNamespace(
                column_names=["table_schema", "table_name", "column_name", "data_type"],
                rows=[],
            )
            return None, table

    monkeypatch.setattr(
        "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
        lambda creds, **kwargs: _CapturingAdapter(),
    )
    tree = bulk_schema_for_config(
        BigQuerySourceConfig(type="bigquery", project="p", dataset="d", location="US")
    )
    assert tree == {}
    assert "`region-us`" in captured["sql"]


def test_bulk_schema_for_config_raises_on_max_rows_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same guard as bulk_schema: a truncated catalog must raise loudly, and
    the fetch itself is bounded — never the whole warehouse into memory."""
    monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "1")
    source_config = _duckdb_with_two_schemas(tmp_path)

    with pytest.raises(RuntimeError, match="truncated"):
        bulk_schema_for_config(source_config)


# --- test_connection: the cleanup crash must not replace the real cause ------
#
# dbt-bigquery's BigQueryConnectionManager.close is a bare
# `connection.handle.close()`. A credential that fails to parse leaves
# `handle` None, so the release the `connection_named` context manager runs on
# every exit raises AttributeError -- and that AttributeError is what a caller
# sees unless test_connection holds on to the open failure itself.

_PEM_FAILURE = (
    "Database Error\n  Unable to load PEM file. See "
    "https://cryptography.io/en/latest/faq/ for more details. "
    "InvalidData(InvalidPadding)"
)

# A private key that fails PEM parsing before any network call is attempted.
_UNPARSEABLE_KEYFILE_JSON = {
    "type": "service_account",
    "project_id": "my-project",
    "private_key_id": "abc123",
    "private_key": "-----BEGIN PRIVATE KEY-----\nNOTAREALKEY\n-----END PRIVATE KEY-----\n",
    "client_email": "svc@my-project.iam.gserviceaccount.com",
    "client_id": "1",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}


class _UnopenableConnection:
    """dbt's Connection after a failed open: no handle, and asking for one raises."""

    def __init__(self, open_error: Exception) -> None:
        self._open_error = open_error

    @property
    def handle(self) -> Any:
        raise self._open_error


class _CleanupCrashingAdapter:
    """dbt-bigquery's shape on a credential that will not parse."""

    def __init__(self, open_error: Exception) -> None:
        self._open_error = open_error
        self.connections = SimpleNamespace(
            get_thread_connection=lambda: _UnopenableConnection(open_error)
        )

    @contextmanager
    def connection_named(self, name: str) -> Generator[None, None, None]:
        try:
            yield
        finally:
            # BigQueryConnectionManager.close, verbatim in effect.
            crash = AttributeError("'NoneType' object has no attribute 'close'")
            crash.__context__ = self._open_error
            raise crash

    def execute(self, sql: str, **kwargs: Any) -> None:
        raise self._open_error


def test_test_connection_reports_the_credential_error_not_the_cleanup_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_error = RuntimeError(_PEM_FAILURE)
    monkeypatch.setattr(
        "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
        lambda creds, **kwargs: _CleanupCrashingAdapter(open_error),
    )

    success, exc = probe_connection(_bq_config("analytics"))

    assert success is False
    assert type(exc) is WarehouseAuthError
    assert "'NoneType' object has no attribute 'close'" not in str(exc)
    assert "Unable to load PEM file" not in str(exc)
    assert "Unable to load PEM file" in exc.detail


def test_test_connection_classifies_a_malformed_bigquery_keyfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The keyfile-shape failure shares the PEM rule's bucket and copy, but
    is a separate server text — it has to reach the bucket on its own."""
    driver_text = (
        "Database Error\n  Service account info was not in the expected"
        " format, missing fields client_email, token_uri."
    )
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(_bq_config("analytics"))

    assert success is False
    assert type(exc) is WarehouseAuthError
    assert "client_email" not in str(exc)
    assert driver_text in exc.detail


def test_test_connection_names_an_unparseable_bigquery_key() -> None:
    """The whole path, against the real dbt-bigquery adapter.

    Offline: PEM parsing fails while the credential is being built, long before
    anything would reach Google. The raw PEM text lands in .detail, for logs
    only — str(exc) is authored display copy.
    """

    success, exc = probe_connection(
        BigQuerySourceConfig(
            type="bigquery",
            project="my-project",
            dataset="analytics",
            keyfile_json=_UNPARSEABLE_KEYFILE_JSON,
        )
    )

    assert success is False
    assert type(exc) is WarehouseAuthError
    assert "'NoneType' object has no attribute 'close'" not in str(exc)
    assert "PEM" not in str(exc)
    assert "PEM" in exc.detail


class _FailingAdapter:
    """A dbt adapter whose connection fails to open with ordinary (non-crashing)
    cleanup — the shape every family but BigQuery takes."""

    def __init__(self, open_error: Exception) -> None:
        self.connections = SimpleNamespace(
            get_thread_connection=lambda: _UnopenableConnection(open_error)
        )

    @contextmanager
    def connection_named(self, name: str) -> Generator[None, None, None]:
        yield


def _failing(monkeypatch: pytest.MonkeyPatch, open_error: Exception) -> None:
    monkeypatch.setattr(
        "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
        lambda creds, **kwargs: _FailingAdapter(open_error),
    )


class _RecordingAdapter:
    def __init__(self, fail_on: str | None = None, fail_with: Exception | None = None):
        self.statements: list[str] = []
        self._fail_on = fail_on
        self._fail_with = fail_with
        self.connections = SimpleNamespace(
            get_thread_connection=lambda: SimpleNamespace(handle=object())
        )

    @contextmanager
    def connection_named(self, name: str) -> Generator[None, None, None]:
        yield

    def execute(self, sql: str, **kwargs: Any) -> None:
        self.statements.append(sql)
        if self._fail_on is not None and sql.startswith(self._fail_on):
            assert self._fail_with is not None
            raise self._fail_with


def _recording(monkeypatch: pytest.MonkeyPatch, adapter: _RecordingAdapter) -> None:
    monkeypatch.setattr(
        "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
        lambda creds, **kwargs: adapter,
    )


def _databricks_config(catalog: str) -> DatabricksSourceConfig:
    return DatabricksSourceConfig(
        type="databricks",
        host="dbc-1.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/abc",
        token="t",
        catalog=catalog,
    )


_NO_SUCH_CATALOG = (
    "Database Error\n  [NO_SUCH_CATALOG_EXCEPTION] Catalog 'does_not_exist_xyz' "
    "was not found. Please verify the catalog name and then retry the query or "
    "command again. SQLSTATE: 42704"
)


class _CatalogsAdapter(_RecordingAdapter):
    def execute(self, sql: str, **kwargs: Any) -> tuple[None, SimpleNamespace]:
        super().execute(sql, **kwargs)
        return None, SimpleNamespace(rows=[("workspace",), ("samples",)])


def _catalogs_adapter(
    monkeypatch: pytest.MonkeyPatch, adapter: _RecordingAdapter
) -> list[dict[str, Any]]:
    built: list[dict[str, Any]] = []

    def _build(creds: dict[str, Any], **_kwargs: Any) -> _RecordingAdapter:
        built.append(creds)
        return adapter

    monkeypatch.setattr(
        "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter", _build
    )
    return built


def test_list_catalogs_runs_show_catalogs_through_the_adapter_without_a_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _CatalogsAdapter()
    built = _catalogs_adapter(monkeypatch, adapter)

    names = list_catalogs(
        _databricks_config("").model_copy(update={"socket_timeout": 7})
    )

    assert names == ["workspace", "samples"]
    assert adapter.statements == ["SHOW CATALOGS"]
    assert "catalog" not in built[0]
    assert (built[0]["token"], built[0]["socket_timeout"]) == ("t", 7)


def test_list_catalogs_bounds_the_session_open_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dbt_charts.core.execute.adapters import dbt_adapter_factory

    real_build_adapter = dbt_adapter_factory.build_adapter
    retry_budgets: list[int] = []

    def _build(creds: dict[str, Any], **_kwargs: Any) -> _CatalogsAdapter:
        built = real_build_adapter(creds, register_macros=False)
        params = built.config.credentials.connection_parameters
        retry_budgets.append(params["_retry_stop_after_attempts_duration"])
        return _CatalogsAdapter()

    monkeypatch.setattr(dbt_adapter_factory, "build_adapter", _build)

    list_catalogs(_databricks_config(""))

    assert retry_budgets == [15]


def test_probe_relation_readability_hands_build_adapter_the_schema_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _catalogs_adapter(monkeypatch, _RecordingAdapter())

    probe_relation_readability(
        _databricks_config("main"), Relation(name="orders", schema="analytics")
    )

    assert built[0]["connect_timeout"] == 15


def test_probe_never_raises_on_a_backend_that_ignores_the_cap() -> None:
    """connect_credentials refuses the mssql-python backend; the probe reports
    that through its return value like every other failure."""
    source_config = DbtTargetSourceConfig(
        type="sqlserver", backend="mssql-python", host="h"
    )

    success, message = probe_relation_readability(
        source_config, Relation(name="orders", schema="analytics")
    )

    assert success is False
    assert "mssql-python" in message


def test_bulk_schema_for_config_hands_build_adapter_the_schema_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _EmptyTableAdapter(_RecordingAdapter):
        def execute(self, sql: str, **kwargs: Any) -> tuple[None, SimpleNamespace]:
            return None, SimpleNamespace(
                column_names=["table_schema", "table_name", "column_name", "data_type"],
                rows=[],
            )

    built = _catalogs_adapter(monkeypatch, _EmptyTableAdapter())

    bulk_schema_for_config(_databricks_config("main"))

    assert built[0]["connect_timeout"] == 15


def test_list_catalogs_hands_the_adapter_service_principal_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _catalogs_adapter(monkeypatch, _CatalogsAdapter())
    m2m = DatabricksSourceConfig(
        type="databricks",
        host="dbc-1.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/abc",
        client_id="sp-app-id",
        client_secret="sp-secret",
        catalog="",
    )

    list_catalogs(m2m)

    assert built[0]["token"] is None
    assert (built[0]["client_id"], built[0]["client_secret"]) == (
        "sp-app-id",
        "sp-secret",
    )


def test_list_catalogs_classifies_a_driver_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalogs_adapter(
        monkeypatch,
        _RecordingAdapter(
            fail_on="SHOW CATALOGS",
            fail_with=RuntimeError("401 Unauthorized for token dapi-secret-1234"),
        ),
    )

    with pytest.raises(WarehouseProbeError) as exc_info:
        list_catalogs(_databricks_config(""))

    exc = exc_info.value
    assert "dapi-secret-1234" not in str(exc)
    assert "dapi-secret-1234" in exc.detail


def test_list_catalogs_is_databricks_only() -> None:
    with pytest.raises(ValueError, match="Databricks-only"):
        list_catalogs(_bq_config())


def test_test_connection_probes_the_databricks_catalog_after_select_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter()
    _recording(monkeypatch, adapter)

    success, exc = probe_connection(_databricks_config("workspace"))

    assert (success, exc) == (True, None)
    assert adapter.statements == ["SELECT 1", "USE CATALOG `workspace`"]


def test_test_connection_classifies_a_missing_databricks_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter(
        fail_on="USE CATALOG", fail_with=RuntimeError(_NO_SUCH_CATALOG)
    )
    _recording(monkeypatch, adapter)

    success, exc = probe_connection(_databricks_config("does_not_exist_xyz"))

    assert success is False
    assert type(exc) is WarehouseDatabaseNotFoundError
    assert str(exc) == "The catalog does not exist, or this token cannot see it."
    assert "does_not_exist_xyz" not in str(exc)
    assert _NO_SUCH_CATALOG in exc.detail


def test_test_connection_rejects_a_databricks_catalog_with_a_backtick_before_any_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter()
    _recording(monkeypatch, adapter)

    success, exc = probe_connection(_databricks_config("a`; DROP CATALOG main; --"))

    assert success is False
    assert type(exc) is WarehouseProbeError
    assert "DROP" not in str(exc)
    assert adapter.statements == []


def test_test_connection_skips_the_probe_for_a_dbt_target_with_no_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter()
    _recording(monkeypatch, adapter)

    success, exc = probe_connection(
        DbtTargetSourceConfig(
            type="databricks",
            host="dbc-1.cloud.databricks.com",
            http_path="/sql/1.0/warehouses/abc",
            token="t",
            schema="s",
        )
    )

    assert (success, exc) == (True, None)
    assert adapter.statements == ["SELECT 1"]


def test_test_connection_skips_the_probe_for_a_spark_target_with_a_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter()
    _recording(monkeypatch, adapter)

    success, exc = probe_connection(
        DbtTargetSourceConfig(
            type="spark",
            host="spark.example.com",
            schema="s",
            catalog="x",
        )
    )

    assert (success, exc) == (True, None)
    assert adapter.statements == ["SELECT 1"]


def test_test_connection_sends_only_select_1_for_a_non_databricks_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _RecordingAdapter()
    _recording(monkeypatch, adapter)

    success, exc = probe_connection(
        PostgresSourceConfig(
            type="postgres", host="h", dbname="d", user="u", password="p"
        )
    )

    assert (success, exc) == (True, None)
    assert adapter.statements == ["SELECT 1"]


def test_test_connection_propagates_a_missing_adapter_package_unclassified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_adapter's own ImportError (missing dbt-<warehouse> package) is a
    caller/environment bug, not a warehouse outcome — genericizing it would
    tell someone with a perfectly good credential to check their connection
    details."""
    install_hint = (
        "dbt-snowflake is not installed. Install it with: pip install dbt-snowflake"
    )
    monkeypatch.setattr(
        "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
        lambda creds, **kwargs: (_ for _ in ()).throw(ImportError(install_hint)),
    )

    with pytest.raises(ImportError, match="dbt-snowflake"):
        probe_connection(
            SnowflakeSourceConfig(
                type="snowflake",
                account="a",
                warehouse="wh",
                database="db",
                user="u",
                password="p",
            )
        )


def test_test_connection_propagates_an_unsupported_adapter_type_unclassified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_adapter's own ValueError (unsupported adapter type) is a caller
    bug, not a warehouse outcome."""
    monkeypatch.setattr(
        "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
        lambda creds, **kwargs: (_ for _ in ()).throw(
            ValueError("Unsupported adapter type 'oracle'. Supported: postgres, ...")
        ),
    )

    with pytest.raises(ValueError, match="Unsupported adapter type"):
        probe_connection(
            PostgresSourceConfig(
                type="postgres", host="h", dbname="d", user="u", password="p"
            )
        )


def test_test_connection_classifies_a_value_error_raised_below_build_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ValueError carve-out is scoped to build_adapter. One raised deeper
    in the probe — mashumaro's InvalidFieldValue is a ValueError that reprs
    the offending value — must classify like any other escape, or it carries
    driver internals straight out to the caller."""
    driver_text = "InvalidFieldValue: password='hunter2' is not a valid str"
    _failing(monkeypatch, ValueError(driver_text))

    success, exc = probe_connection(
        PostgresSourceConfig(
            type="postgres", host="h", dbname="d", user="u", password="hunter2"
        )
    )

    assert success is False
    assert exc is not None
    assert "hunter2" not in str(exc)
    assert "hunter2" in exc.detail


def test_test_connection_classifies_postgres_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver_text = 'FATAL:  password authentication failed for user "testuser"'
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(
        PostgresSourceConfig(
            type="postgres", host="h", dbname="d", user="testuser", password="p"
        )
    )

    assert success is False
    assert type(exc) is WarehouseAuthError
    assert driver_text not in str(exc)
    assert str(exc) != ""
    assert driver_text in exc.detail


def test_classify_connection_error_fabric_database_not_found() -> None:
    driver_text = (
        "Database Error\n  Driver Error: Invalid authorization specification;"
        " DDBC Error: [Microsoft][SQL Server]Login failed for user"
        " '<token-identified principal>'.Reason: Authentication was successful,"
        " but the database was not found or you have insufficient permissions"
        " to connect to it."
    )
    exc = classify_connection_error("fabric", RuntimeError(driver_text))

    assert type(exc) is WarehouseDatabaseNotFoundError
    assert driver_text not in str(exc)
    assert driver_text in exc.detail


def test_test_connection_classifies_postgres_database_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver_text = 'FATAL:  database "nosuchdb" does not exist'
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(
        PostgresSourceConfig(
            type="postgres", host="h", dbname="nosuchdb", user="u", password="p"
        )
    )

    assert success is False
    assert type(exc) is WarehouseDatabaseNotFoundError
    assert "nosuchdb" not in str(exc)
    assert driver_text in exc.detail


def test_test_connection_classifies_postgres_permission_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver_text = 'FATAL:  permission denied for database "realdb"'
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(
        PostgresSourceConfig(
            type="postgres", host="h", dbname="realdb", user="u", password="p"
        )
    )

    assert success is False
    assert type(exc) is WarehousePermissionError
    assert "realdb" not in str(exc)
    assert driver_text in exc.detail


@pytest.mark.parametrize(
    "driver_text",
    [
        'connection to server at "10.0.0.9", port 5 failed: Connection refused',
        'could not translate host name "db.internal" to address: nodename nor'
        " servname provided",
        'connection to server at "10.0.0.9", port 5 failed: timeout expired',
    ],
)
def test_test_connection_classifies_postgres_unreachable(
    monkeypatch: pytest.MonkeyPatch, driver_text: str
) -> None:
    """Don't collapse these to one representative text: each has to reach
    the bucket on its own."""
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(
        PostgresSourceConfig(
            type="postgres", host="10.0.0.9", dbname="d", user="u", password="p"
        )
    )

    assert success is False
    assert type(exc) is WarehouseUnreachableError
    assert "10.0.0.9" not in str(exc)
    assert "db.internal" not in str(exc)
    assert driver_text in exc.detail


def test_test_connection_classifies_duckdb_missing_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver_text = (
        'IO Error: Cannot open file "/no/such/dir/db.duckdb": No such file or directory'
    )
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(
        DuckDBSourceConfig(type="duckdb", path="/no/such/dir/db.duckdb")
    )

    assert success is False
    assert type(exc) is WarehouseDatabaseNotFoundError
    assert "/no/such/dir/db.duckdb" not in str(exc)
    assert driver_text in exc.detail


def test_test_connection_classifies_duckdb_lock_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock message embeds an OS username and PID — a dedicated bucket
    strips both, leaving only the authored "locked by another process"
    sentence."""
    driver_text = (
        'Could not set lock on file "db.duckdb": Conflicting lock is held in'
        " /usr/bin/python3.13 (PID 4242) by user alice"
    )
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(DuckDBSourceConfig(type="duckdb", path="db.duckdb"))

    assert success is False
    assert type(exc) is WarehouseProbeError
    assert "locked by another process" in str(exc)
    assert "alice" not in str(exc)
    assert "4242" not in str(exc)
    assert driver_text in exc.detail


def test_test_connection_classifies_duckdb_permission_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable file is a filesystem problem, and DuckDB has no
    credentials — falling through to the generic copy would send the user to
    re-check credentials that do not exist."""
    driver_text = 'IO Error: Cannot open file "db.duckdb": Permission denied'
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(DuckDBSourceConfig(type="duckdb", path="db.duckdb"))

    assert success is False
    assert type(exc) is WarehousePermissionError
    assert "permission denied" in str(exc)
    assert driver_text in exc.detail


def test_test_connection_classifies_duckdb_path_is_a_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver_text = 'IO Error: Could not read from file "/tmp": Is a directory'
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(DuckDBSourceConfig(type="duckdb", path="/tmp"))

    assert success is False
    assert type(exc) is WarehouseProbeError
    assert "points at a directory" in str(exc)
    assert "/tmp" not in str(exc)
    assert driver_text in exc.detail


@pytest.mark.parametrize(
    "driver_text",
    [
        'IO Error: "db.duckdb" is not a valid DuckDB database file',
        'IO Error: Could not read enough bytes from file "db.duckdb"',
        'IO Error: Corrupt database file "db.duckdb"',
    ],
)
def test_test_connection_classifies_duckdb_not_a_valid_database_file(
    monkeypatch: pytest.MonkeyPatch, driver_text: str
) -> None:
    """Don't collapse these to one representative text: each has to reach
    the bucket on its own."""
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(DuckDBSourceConfig(type="duckdb", path="db.duckdb"))

    assert success is False
    assert type(exc) is WarehouseProbeError
    assert "not a valid DuckDB database file" in str(exc)
    assert driver_text in exc.detail


def test_test_connection_classifies_duckdb_file_name_too_long(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver_text = "IO Error: File name too long"
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(DuckDBSourceConfig(type="duckdb", path="x" * 300))

    assert success is False
    assert type(exc) is WarehouseProbeError
    assert "database path is too long" in str(exc)
    assert driver_text in exc.detail


def test_test_connection_classifies_duckdb_empty_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The empty/trailing-slash path case bypasses ConnectionSetupFailed
    entirely — build_adapter itself raises DbtRuntimeError before
    open_connection runs, arriving via test_connection's broad except."""
    driver_text = (
        "Unable to determine target database name from 'path' field in profile"
    )
    monkeypatch.setattr(
        "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
        lambda creds, **kwargs: (_ for _ in ()).throw(RuntimeError(driver_text)),
    )

    success, exc = probe_connection(DuckDBSourceConfig(type="duckdb", path=""))

    assert success is False
    assert type(exc) is WarehouseProbeError
    assert "database path is empty or invalid" in str(exc)
    assert driver_text in exc.detail


def test_test_connection_classifies_bigquery_invalid_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In production the invalid_grant shape arrives query-phase, through
    test_connection's broad except rather than ConnectionSetupFailed. The
    text is what the rule keys on, and _failing reproduces it through the
    connect branch, so this pins the classification and not the arrival."""
    driver_text = (
        "Unable to generate access token: ('invalid_grant: Invalid grant:"
        " account not found')"
    )
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(_bq_config("analytics"))

    assert success is False
    assert type(exc) is WarehouseAuthError
    assert driver_text in exc.detail


def test_bigquery_invalid_grant_message_mentions_key_propagation() -> None:
    """This shape also fires for a service-account key minted seconds
    ago (IAM propagation delay), not just a genuinely bad credential. The
    display message must say so and suggest retrying — without ever leaking
    `.detail`, which can carry a username or process id (see module docstring).
    """
    driver_text = (
        "Unable to generate access token: ('invalid_grant: Invalid grant:"
        " account not found')"
    )
    exc = classify_connection_error("bigquery", RuntimeError(driver_text))

    assert type(exc) is WarehouseAuthError
    assert "propagat" in str(exc).lower()
    assert "again" in str(exc).lower()
    assert driver_text not in str(exc)


def test_test_connection_classifies_bigquery_permission_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver_text = (
        "Access denied while running query: Access Denied: Project my-proj:"
        " User does not have bigquery.jobs.create permission"
    )
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(_bq_config("analytics"))

    assert success is False
    assert type(exc) is WarehousePermissionError
    assert "my-proj" not in str(exc)
    assert driver_text in exc.detail


def test_test_connection_classifies_bigquery_unknown_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver_text = "Not found: Dataset my-project:analytics was not found"
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(_bq_config("analytics"))

    assert success is False
    assert type(exc) is WarehouseDatabaseNotFoundError
    assert "my-project:analytics" not in str(exc)
    assert driver_text in exc.detail


def test_test_connection_classifies_snowflake_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver_text = (
        "Failed to connect to DB: xy12345.snowflakecomputing.com:443."
        " Incorrect username or password was specified."
    )
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(
        SnowflakeSourceConfig(
            type="snowflake",
            account="xy12345",
            warehouse="wh",
            database="db",
            user="u",
            password="p",
        )
    )

    assert success is False
    assert type(exc) is WarehouseAuthError
    assert "xy12345.snowflakecomputing.com" not in str(exc)
    assert driver_text in exc.detail


def test_test_connection_classifies_snowflake_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver_text = (
        "250001: Could not connect to Snowflake backend after 3 attempt(s).Aborting"
    )
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(
        SnowflakeSourceConfig(
            type="snowflake",
            account="xy12345",
            warehouse="wh",
            database="db",
            user="u",
            password="p",
        )
    )

    assert success is False
    assert type(exc) is WarehouseUnreachableError
    assert driver_text in exc.detail


def test_test_connection_names_an_unrecognized_snowflake_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fires before credentials are even checked — a dedicated authored
    sentence naming the account identifier the user themselves typed."""
    driver_text = (
        "290404 (08001): 404 Not Found: post xy12345.snowflakecomputing.com:443"
        "/session/v1/login-request"
    )
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(
        SnowflakeSourceConfig(
            type="snowflake",
            account="xy12345",
            warehouse="wh",
            database="db",
            user="u",
            password="p",
        )
    )

    assert success is False
    assert type(exc) is WarehouseProbeError
    assert "account identifier" in str(exc)
    assert driver_text in exc.detail


@pytest.mark.parametrize(
    "driver_text",
    [
        "('communication error', ConnectionRefusedError(111, 'refused'))",
        "('connection time out', TimeoutError())",
    ],
)
def test_test_connection_classifies_redshift_unreachable(
    monkeypatch: pytest.MonkeyPatch, driver_text: str
) -> None:
    """redshift_connector stringifies its wire fields as a tuple/dict repr,
    so each text must be matched inside that shape — don't collapse these to
    one representative case."""
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(
        RedshiftSourceConfig(type="redshift", host="h", dbname="d")
    )

    assert success is False
    assert type(exc) is WarehouseUnreachableError
    assert "ConnectionRefusedError" not in str(exc)
    assert "TimeoutError" not in str(exc)
    assert driver_text in exc.detail


def test_test_connection_builds_the_adapter_from_connect_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test and render hand build_adapter the same config: Redshift's socket
    timeout is raised past the query cap by its dialect's connect_credentials."""
    built: list[dict[str, Any]] = []

    def _build(creds: dict[str, Any], **_kwargs: Any) -> _RecordingAdapter:
        built.append(creds)
        return _RecordingAdapter()

    monkeypatch.setattr(
        "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter", _build
    )

    success, _ = probe_connection(
        RedshiftSourceConfig(type="redshift", host="h", dbname="d")
    )

    assert success is True
    assert (
        built[0]["connect_timeout"] > get_execution_config().max_query_duration_seconds
    )


def test_test_connection_falls_back_to_a_generic_message_when_unclassified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception no bucket's evidence supports still fails closed with
    generic, non-leaking display copy rather than the raw driver text."""
    driver_text = "some never-before-seen driver internals: host 10.9.8.7"
    _failing(monkeypatch, RuntimeError(driver_text))

    success, exc = probe_connection(
        PostgresSourceConfig(
            type="postgres", host="h", dbname="d", user="u", password="p"
        )
    )

    assert success is False
    assert type(exc) is WarehouseProbeError
    assert "10.9.8.7" not in str(exc)
    assert str(exc) != ""
    assert driver_text in exc.detail


def test_test_connection_details_a_message_less_driver_exception_by_type_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A driver exception with no message at all (str(exc) == "") must still
    leave something in .detail for logs — the exception's type name, not an
    empty string that would tell an operator nothing failed at all."""
    _failing(monkeypatch, RuntimeError())

    success, exc = probe_connection(
        PostgresSourceConfig(
            type="postgres", host="h", dbname="d", user="u", password="p"
        )
    )

    assert success is False
    assert exc.detail == "RuntimeError"


# ── bulk_tables_for_config: the tables-only listing ──────────────────────────


def test_bulk_tables_for_config_lists_table_names_by_schema(tmp_path: Path) -> None:
    tables = bulk_tables_for_config(
        _duckdb_with_two_schemas(tmp_path), ["sales", "marketing"]
    )

    assert tables["sales"] == ["orders"]
    assert tables["marketing"] == ["campaigns"]


def test_bulk_tables_for_config_propagates_not_implemented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_config = _duckdb_with_two_schemas(tmp_path)

    class _NoBulkFormDialect(DuckDBDialect):
        cursor_supports_driver_limit = False

        def bulk_tables_sql(self, scope: str, schemas: object) -> str:
            raise NotImplementedError("no bulk form")

    monkeypatch.setattr(
        "dbt_charts.core.dialects.get_dialect", lambda _type: _NoBulkFormDialect()
    )
    with pytest.raises(NotImplementedError):
        bulk_tables_for_config(source_config, ["sales", "marketing"])


def test_bulk_tables_for_config_normalizes_driver_errors_to_runtime_error(
    tmp_path: Path,
) -> None:
    source_config = DuckDBSourceConfig(
        type="duckdb", path=str(tmp_path / "missing_dir" / "wh.duckdb")
    )
    with pytest.raises(RuntimeError, match="bulk_tables_for_config query failed"):
        bulk_tables_for_config(source_config, ["sales", "marketing"])


def test_bulk_tables_for_config_raises_on_max_rows_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DCT_MAX_ROWS_CEILING", "1")

    with pytest.raises(RuntimeError, match="truncated.*table count"):
        bulk_tables_for_config(
            _duckdb_with_two_schemas(tmp_path), ["sales", "marketing"]
        )


def test_bulk_tables_for_config_reads_tables_not_columns_for_bigquery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from dbt_charts.core.compile.models.source import BigQuerySourceConfig

    captured: dict[str, str] = {}

    class _CapturingAdapter:
        connections = SimpleNamespace(
            get_thread_connection=lambda: SimpleNamespace(handle=object())
        )

        def connection_named(self, name: str):
            from contextlib import nullcontext

            return nullcontext()

        def execute(self, sql: str, **kwargs: object):
            captured["sql"] = sql
            table = SimpleNamespace(
                column_names=["table_schema", "table_name"],
                rows=[("d", "t")],
            )
            return None, table

    monkeypatch.setattr(
        "dbt_charts.core.execute.adapters.dbt_adapter_factory.build_adapter",
        lambda creds, **kwargs: _CapturingAdapter(),
    )
    tables = bulk_tables_for_config(
        BigQuerySourceConfig(type="bigquery", project="p", dataset="d", location="US"),
        ["d"],
    )

    assert tables == {"d": ["t"]}
    assert "`region-us`.INFORMATION_SCHEMA.TABLES" in captured["sql"]


def test_bulk_tables_for_config_lists_only_the_requested_schemas(
    tmp_path: Path,
) -> None:
    source_config = _duckdb_with_two_schemas(tmp_path)

    assert bulk_tables_for_config(source_config, ["SALES"]) == {"sales": ["orders"]}


def test_bulk_tables_for_config_with_no_schemas_runs_no_query(tmp_path: Path) -> None:
    missing = DuckDBSourceConfig(
        type="duckdb", path=str(tmp_path / "missing_dir" / "wh.duckdb")
    )

    assert bulk_tables_for_config(missing, []) == {}
