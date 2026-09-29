"""Regression tests for the closed database-source configuration contract."""

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.source import (
    BigQuerySourceConfig,
    CsvSourceConfig,
    DbtProfileSourceConfig,
    DuckDBSourceConfig,
    HttpSourceConfig,
    JsonSourceConfig,
    ParquetSourceConfig,
    PostgresSourceConfig,
    RedshiftSourceConfig,
    SnowflakeSourceConfig,
    TrinoSourceConfig,
)


class TestDatabaseSourceConfigContracts:
    """Database-source options are typed and unknown keys fail at parse time."""

    def test_postgres_documents_every_dbt_credential_option(self) -> None:
        """Postgres options round-trip through the typed source model."""
        config = PostgresSourceConfig(
            type="postgres",
            host="localhost",
            dbname="mydb",
            user="u",
            password="p",
            connect_timeout=10,
            role="reader",
            search_path="analytics,public",
            keepalives_idle=600,
            sslmode="verify-full",
            sslcert="cert.pem",
            sslkey="key.pem",
            sslrootcert="root.pem",
            application_name="dbt-charts",
            retries=3,
        )
        assert config.application_name == "dbt-charts"

    def test_snowflake_documents_every_dbt_credential_option(self) -> None:
        """Snowflake options round-trip through the typed source model."""
        config = SnowflakeSourceConfig(
            type="snowflake",
            account="myaccount",
            user="reader",
            database="DB",
            warehouse="WH",
            schema="S",
            authenticator="oauth",
            private_key_path="key.pem",
            private_key_passphrase="passphrase",
            token="token",
            oauth_client_id="client",
            oauth_client_secret="secret",
            query_tag="dbt-charts",
            client_session_keep_alive=True,
            host="account.snowflakecomputing.com",
            port=443,
            proxy_host="proxy.example.com",
            proxy_port=8080,
            protocol="https",
            connect_timeout=10,
            connect_retries=3,
            retry_on_database_errors=True,
            retry_all=True,
            insecure_mode=False,
            reuse_connections=True,
            s3_stage_vpce_dns_name="vpce.example.com",
            platform_detection_timeout_seconds=2.5,
        )
        assert config.authenticator == "oauth"

    def test_bigquery_documents_every_dbt_credential_option(self) -> None:
        """BigQuery options round-trip through the typed source model."""
        config = BigQuerySourceConfig(
            type="bigquery",
            project="project",
            dataset="dataset",
            execution_project="billing-project",
            quota_project="quota-project",
            api_endpoint="https://bigquery.googleapis.com",
            priority="interactive",
            maximum_bytes_billed=1_000_000,
            impersonate_service_account="reader@example.iam.gserviceaccount.com",
            job_retry_deadline_seconds=30,
            job_retries=3,
            job_creation_timeout_seconds=5,
            job_execution_timeout_seconds=60,
            token="token",
            refresh_token="refresh-token",
            client_id="client-id",
            client_secret="client-secret",
            token_uri="https://oauth.example.com/token",
            workload_pool_provider_path="projects/1/locations/global/pools/p/providers/p",
            service_account_impersonation_url="https://iamcredentials.googleapis.com",
            token_endpoint={"url": "https://oauth.example.com/token"},
            compute_region="us-central1",
            dataproc_cluster_name="cluster",
            gcs_bucket="bucket",
            submission_method="serverless",
            dataproc_batch={"name": "batch"},
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        assert config.execution_project == "billing-project"

    def test_redshift_documents_every_dbt_credential_option(self) -> None:
        """Redshift options round-trip through the typed source model."""
        config = RedshiftSourceConfig(
            type="redshift",
            host="cluster.example.com",
            dbname="analytics",
            method="iam",
            user="reader",
            password="password",
            cluster_id="cluster",
            iam_profile="arn:aws:iam::123:role/reader",
            autocreate=True,
            db_groups=["analytics"],
            ra3_node=True,
            connect_timeout=10,
            role="reader",
            sslmode="verify-ca",
            retries=3,
            retry_all=True,
            region="us-east-1",
            access_key_id="access-key",
            secret_access_key="secret-key",
            idc_region="us-east-1",
            issuer_url="https://issuer.example.com",
            idp_listen_port=7890,
            idc_client_display_name="dbt-charts",
            idp_response_timeout=30,
            token_endpoint={"url": "https://token.example.com"},
            is_serverless=True,
            serverless_work_group="workgroup",
            serverless_acct_id="123",
            tcp_keepalive=True,
            tcp_keepalive_idle=30,
            tcp_keepalive_interval=10,
            tcp_keepalive_count=3,
        )
        assert config.serverless_work_group == "workgroup"

    def test_redshift_default_sslmode_is_valid_for_dbt(self) -> None:
        """The typed default must not replace dbt's SSL enum with null."""
        config = RedshiftSourceConfig(
            type="redshift", host="cluster.example.com", dbname="analytics"
        )
        assert config.model_dump(by_alias=True)["sslmode"] == "prefer"

    def test_trino_documents_every_dbt_credential_option(self) -> None:
        """Trino options round-trip through the typed source model."""
        config = TrinoSourceConfig(
            type="trino",
            host="trino.example.com",
            catalog="hive",
            schema="analytics",
            method="kerberos",
            user="reader",
            password="password",
            impersonation_user="analyst",
            client_tags=["dbt-charts"],
            roles={"hive": "reader"},
            cert=True,
            http_scheme="https",
            http_headers={"X-Trace": "trace"},
            session_properties={"query_max_run_time": "1m"},
            prepared_statements_enabled=True,
            retries=3,
            timezone="UTC",
            suppress_cert_warning=False,
            client_certificate="cert.pem",
            client_private_key="key.pem",
            keytab="keytab",
            principal="reader@EXAMPLE.COM",
            krb5_config="krb5.conf",
            service_name="trino",
            mutual_authentication=True,
            force_preemptive=True,
            hostname_override="trino.internal",
            sanitize_mutual_error_response=True,
            delegate=True,
            jwt_token="jwt",
        )
        assert config.method == "kerberos"

    def test_duckdb_documents_every_dbt_credential_option(self) -> None:
        """DuckDB options round-trip through the typed source model."""
        config = DuckDBSourceConfig(
            type="duckdb",
            path="analytics.duckdb",
            schema="analytics",
            duckdb_config={"enable_external_access": True},
            extensions=["httpfs", {"name": "json"}],
            settings={"memory_limit": "4GB"},
            secrets=[{"type": "s3", "key_id": "key", "provider": "credential_chain"}],
            external_root="data",
            use_credential_provider="aws",
            attach=[{"path": "other.duckdb", "read_only": True}],
            filesystems=[{"fs": "memory"}],
            remote={"host": "remote.example.com", "port": 443, "user": "reader"},
            plugins=[{"module": "my_plugin", "config": {"enabled": True}}],
            disable_transactions=True,
            keep_open=False,
            module_paths=["plugins"],
            retries={"connect_attempts": 2, "query_attempts": 3},
            is_ducklake=True,
        )
        assert config.duckdb_config == {"enable_external_access": True}

    def test_duckdb_extensions_accept_old_dict_shape_with_no_repo(self) -> None:
        """extensions: [{name: json}] (no repo) must keep parsing."""
        config = DuckDBSourceConfig(type="duckdb", extensions=[{"name": "json"}])
        assert config.extensions is not None
        entry = config.extensions[0]
        assert not isinstance(entry, str)
        assert entry.name == "json"

    def test_duckdb_extension_config_rejects_unknown_key(self) -> None:
        with pytest.raises(ValidationError):
            DuckDBSourceConfig(
                type="duckdb", extensions=[{"name": "json", "respo": "typo"}]
            )

    def test_duckdb_secrets_accept_old_flat_shape(self) -> None:
        """secrets: [{type: s3, key_id: k, secret: s}] (flat, no options:
        nesting) must keep parsing, matching upstream dbt-duckdb's own
        secret shape."""
        config = DuckDBSourceConfig(
            type="duckdb",
            secrets=[{"type": "s3", "key_id": "k", "secret": "s"}],
        )
        assert config.secrets is not None
        assert config.secrets[0].type == "s3"

    def test_duckdb_secrets_accept_provider_field(self) -> None:
        config = DuckDBSourceConfig(
            type="duckdb",
            secrets=[{"type": "s3", "provider": "credential_chain"}],
        )
        assert config.secrets is not None

    def test_duckdb_secret_config_rejects_missing_type(self) -> None:
        with pytest.raises(ValidationError):
            DuckDBSourceConfig(type="duckdb", secrets=[{"name": "my_secret"}])

    def test_duckdb_secret_config_rejects_null_provider_field(self) -> None:
        """A null provider field value must be rejected, not silently
        rendered as the literal string 'None' (scalar), dropped (top-level),
        or embedded in a list/map. None is not a valid CREATE SECRET value."""
        with pytest.raises(ValidationError):
            DuckDBSourceConfig(type="duckdb", secrets=[{"type": "s3", "key_id": None}])

    def test_duckdb_secret_config_rejects_nested_provider_field(self) -> None:
        """A provider field value must be a scalar, a list of scalars, or a
        flat mapping of scalars; a nested mapping is not a shape DuckDB's
        CREATE SECRET SQL can render."""
        with pytest.raises(ValidationError):
            DuckDBSourceConfig(
                type="duckdb", secrets=[{"type": "s3", "opts": {"a": {"b": 1}}}]
            )

    def test_duckdb_rejects_database_key(self) -> None:
        """DuckDB rejects the legacy 'database' key at parse time.

        The runtime also rejects it — both layers must agree. This test ensures
        the error fires at Pydantic validation, not deep in the adapter.
        """
        with pytest.raises(ValueError, match="'path'"):
            DuckDBSourceConfig(database=":memory:")

    def test_dbt_input_aliases_normalize_to_source_fields(self) -> None:
        """Credential aliases remain accepted without reopening the model."""
        postgres = PostgresSourceConfig(
            **{
                "type": "postgres",
                "host": "localhost",
                "database": "analytics",
                "user": "reader",
                "pass": "password",
            }
        )
        redshift = RedshiftSourceConfig(
            **{
                "type": "redshift",
                "host": "cluster.example.com",
                "database": "analytics",
                "pass": "password",
            }
        )
        bigquery = BigQuerySourceConfig(
            type="bigquery",
            project="project",
            dataset="dataset",
            retries=2,
            timeout_seconds=60,
            dataproc_region="us-central1",
        )
        duckdb = DuckDBSourceConfig(
            type="duckdb", config_options={"enable_external_access": True}
        )

        assert postgres.dbname == "analytics"
        assert postgres.password == "password"
        assert redshift.dbname == "analytics"
        assert redshift.password == "password"
        assert bigquery.job_retries == 2
        assert bigquery.job_execution_timeout_seconds == 60
        assert bigquery.compute_region == "us-central1"
        assert duckdb.duckdb_config == {"enable_external_access": True}

    @pytest.mark.parametrize(
        ("config_cls", "required_kwargs"),
        [
            (
                PostgresSourceConfig,
                {
                    "type": "postgres",
                    "host": "h",
                    "dbname": "d",
                    "user": "u",
                    "password": "p",
                },
            ),
            (
                SnowflakeSourceConfig,
                {"type": "snowflake", "account": "a", "database": "d", "schema": "s"},
            ),
            (
                BigQuerySourceConfig,
                {"type": "bigquery", "project": "p", "dataset": "d"},
            ),
            (RedshiftSourceConfig, {"type": "redshift", "host": "h", "dbname": "d"}),
            (
                TrinoSourceConfig,
                {"type": "trino", "host": "h", "catalog": "c", "schema": "s"},
            ),
            (DuckDBSourceConfig, {"type": "duckdb"}),
        ],
    )
    def test_database_source_rejects_unknown_field(
        self, config_cls: type, required_kwargs: dict
    ) -> None:
        """A misspelled connector option must fail at the Pydantic boundary."""
        with pytest.raises(ValidationError, match="extra_forbidden"):
            config_cls(**required_kwargs, typo_field=1)


class TestNonDbSourceConfigForbid:
    """File/HTTP/dbt_profile configs inherit extra='forbid' from BaseSourceConfig."""

    @pytest.mark.parametrize(
        ("config_cls", "required_kwargs"),
        [
            (CsvSourceConfig, {"type": "csv", "files": {"data": "foo.csv"}}),
            (
                ParquetSourceConfig,
                {"type": "parquet", "files": {"data": "foo.parquet"}},
            ),
            (JsonSourceConfig, {"type": "json", "files": {"data": "foo.json"}}),
            (HttpSourceConfig, {"type": "http", "url": "https://example.com"}),
            (DbtProfileSourceConfig, {"type": "dbt_profile", "profile": "my_profile"}),
        ],
    )
    def test_non_db_config_rejects_unknown_field(
        self, config_cls: type, required_kwargs: dict
    ) -> None:
        """Non-DB source configs are closed contracts — unknown kwargs must raise."""
        with pytest.raises(ValidationError):
            config_cls(**required_kwargs, typo_field=1)


class TestEnvVarResolution:
    """BaseSourceConfig resolves {{ env_var('VAR') }} across all string fields."""

    def test_postgres_env_var_in_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DB_PASSWORD", "super_secret")
        config = PostgresSourceConfig(
            type="postgres",
            host="localhost",
            port=5432,
            dbname="mydb",
            user="u",
            password="{{ env_var('DB_PASSWORD') }}",
        )
        assert config.password == "super_secret"

    def test_postgres_env_var_in_documented_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env var resolution applies to every documented connector field."""
        monkeypatch.setenv("APPLICATION_NAME", "dbt-charts")
        config = PostgresSourceConfig(
            type="postgres",
            host="localhost",
            port=5432,
            dbname="mydb",
            user="u",
            password="p",
            application_name="{{ env_var('APPLICATION_NAME') }}",
        )
        assert config.application_name == "dbt-charts"

    def test_env_var_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing required env var raises ValueError at parse time (non-credential field)."""
        monkeypatch.delenv("MISSING_VAR_DEFINITELY", raising=False)
        with pytest.raises(ValueError, match="MISSING_VAR_DEFINITELY"):
            PostgresSourceConfig(
                type="postgres",
                host="{{ env_var('MISSING_VAR_DEFINITELY') }}",
                port=5432,
                dbname="mydb",
                user="u",
                password="p",
            )

    def test_missing_password_env_var_defers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dbt's SecretRenderer defers `password` rendering: a missing password
        env_var is left as a literal (it fails at connect, not parse) rather than
        raising. We match dbt — passwords legitimately contain `{{`/`%`."""
        monkeypatch.delenv("MISSING_PG_PASSWORD", raising=False)
        config = PostgresSourceConfig(
            type="postgres",
            host="localhost",
            port=5432,
            dbname="mydb",
            user="u",
            password="{{ env_var('MISSING_PG_PASSWORD') }}",
        )
        assert config.password == "{{ env_var('MISSING_PG_PASSWORD') }}"

    def test_file_source_resolves_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """M5 regression: file/HTTP sources also resolve env_var() syntax.

        Before M5, _resolve_all_env_vars lived only on _DbSourceShim (the DB
        passthrough base). BaseSourceConfig (file/HTTP) had no resolution —
        consistent behavior now requires all source configs to resolve env_var().
        """
        from dbt_charts.core.compile.models.source import HttpSourceConfig

        monkeypatch.setenv("API_TOKEN", "Bearer xyz")
        config = HttpSourceConfig(
            type="http",
            url="https://api.example.com",
            headers={"Authorization": "{{ env_var('API_TOKEN') }}"},
        )
        assert config.headers == {"Authorization": "Bearer xyz"}
