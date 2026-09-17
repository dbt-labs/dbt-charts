"""Unit tests for SQL dialect abstraction layer.

Tests cover:
- Base SQLDialect class behavior
- Each dialect implementation (PostgreSQL, DuckDB, MySQL, etc.)
- Dialect registry and get_dialect() function
- Parameter generation, temp tables, and filter macros
"""

import pytest

from dbt_charts.core.dialects import (
    DIALECTS,
    AthenaDialect,
    BigQueryDialect,
    ClickHouseDialect,
    DatabricksDialect,
    DuckDBDialect,
    MySQLDialect,
    PostgresDialect,
    RedshiftDialect,
    SnowflakeDialect,
    SparkDialect,
    SQLDialect,
    SQLiteDialect,
    SQLServerDialect,
    get_dialect,
    list_dialects,
)


class TestSQLDialectBase:
    """Tests for the base SQLDialect abstract class."""

    def test_cannot_instantiate_base_class(self) -> None:
        """Base SQLDialect cannot be instantiated directly."""
        with pytest.raises(TypeError):
            SQLDialect()  # type: ignore

    def test_base_class_has_required_abstract_method(self) -> None:
        """Base class requires param() to be implemented."""

        class IncompleteDialect(SQLDialect):
            name = "incomplete"

        with pytest.raises(TypeError):
            IncompleteDialect()  # type: ignore


class TestPostgresDialect:
    """Tests for PostgreSQL dialect."""

    @pytest.fixture
    def dialect(self) -> PostgresDialect:
        return PostgresDialect()

    def test_name(self, dialect: PostgresDialect) -> None:
        """PostgreSQL dialect has correct name."""
        assert dialect.name == "postgres"

    def test_param_single(self, dialect: PostgresDialect) -> None:
        """Single parameter uses $N format."""
        assert dialect.param(1) == "$1"
        assert dialect.param(2) == "$2"
        assert dialect.param(10) == "$10"

    def test_params_list(self, dialect: PostgresDialect) -> None:
        """Multiple parameters generated correctly."""
        assert dialect.params(3) == ["$1", "$2", "$3"]
        assert dialect.params(1) == ["$1"]
        assert dialect.params(0) == []

    def test_statement_timeout_sql(self, dialect: PostgresDialect) -> None:
        """Postgres emits a server-side SET statement_timeout."""
        assert dialect.statement_timeout_sql(30) == "SET statement_timeout = '30s'"


class TestDuckDBDialect:
    """Tests for DuckDB dialect."""

    @pytest.fixture
    def dialect(self) -> DuckDBDialect:
        return DuckDBDialect()

    def test_name(self, dialect: DuckDBDialect) -> None:
        """DuckDB dialect has correct name."""
        assert dialect.name == "duckdb"

    def test_param(self, dialect: DuckDBDialect) -> None:
        """DuckDB uses $N parameters like PostgreSQL."""
        assert dialect.param(1) == "$1"
        assert dialect.param(2) == "$2"

    def test_statement_timeout_sql_is_noop(self, dialect: DuckDBDialect) -> None:
        """DuckDB has no server to enforce a statement timeout against."""
        assert dialect.statement_timeout_sql(30) is None


class TestSQLiteDialectTimeout:
    """SQLite's statement_timeout_sql no-op (mirrors DuckDB — no server)."""

    def test_statement_timeout_sql_is_noop(self) -> None:
        assert SQLiteDialect().statement_timeout_sql(30) is None


class TestMySQLDialect:
    """Tests for MySQL dialect."""

    @pytest.fixture
    def dialect(self) -> MySQLDialect:
        return MySQLDialect()

    def test_name(self, dialect: MySQLDialect) -> None:
        """MySQL dialect has correct name."""
        assert dialect.name == "mysql"

    def test_param_is_placeholder(self, dialect: MySQLDialect) -> None:
        """MySQL uses %s for all parameters (index ignored)."""
        assert dialect.param(1) == "%s"
        assert dialect.param(2) == "%s"
        assert dialect.param(100) == "%s"


class TestSnowflakeDialect:
    """Tests for Snowflake dialect."""

    @pytest.fixture
    def dialect(self) -> SnowflakeDialect:
        return SnowflakeDialect()

    def test_name(self, dialect: SnowflakeDialect) -> None:
        """Snowflake dialect has correct name."""
        assert dialect.name == "snowflake"

    def test_param(self, dialect: SnowflakeDialect) -> None:
        """Snowflake uses ? for parameters."""
        assert dialect.param(1) == "?"
        assert dialect.param(2) == "?"

    def test_statement_timeout_sql(self, dialect: SnowflakeDialect) -> None:
        """Snowflake emits ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS."""
        assert (
            dialect.statement_timeout_sql(45)
            == "ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 45"
        )


class TestBigQueryDialect:
    """Tests for BigQuery dialect."""

    @pytest.fixture
    def dialect(self) -> BigQueryDialect:
        return BigQueryDialect()

    def test_name(self, dialect: BigQueryDialect) -> None:
        """BigQuery dialect has correct name."""
        assert dialect.name == "bigquery"

    def test_param(self, dialect: BigQueryDialect) -> None:
        """BigQuery uses @paramN format."""
        assert dialect.param(1) == "@param1"
        assert dialect.param(2) == "@param2"

    def test_statement_timeout_sql_is_noop(self, dialect: BigQueryDialect) -> None:
        """BigQuery's timeout is a job-level setting, not SQL — see sql_adapter.py."""
        assert dialect.statement_timeout_sql(60) is None


class TestRedshiftDialect:
    """Tests for Redshift dialect."""

    @pytest.fixture
    def dialect(self) -> RedshiftDialect:
        return RedshiftDialect()

    def test_name(self, dialect: RedshiftDialect) -> None:
        """Redshift dialect has correct name."""
        assert dialect.name == "redshift"

    def test_param_like_postgres(self, dialect: RedshiftDialect) -> None:
        """Redshift uses PostgreSQL-style $N parameters."""
        assert dialect.param(1) == "$1"
        assert dialect.param(2) == "$2"

    def test_statement_timeout_sql_inherited_from_postgres(
        self, dialect: RedshiftDialect
    ) -> None:
        """Redshift inherits Postgres's SET statement_timeout."""
        assert dialect.statement_timeout_sql(30) == "SET statement_timeout = '30s'"


class TestSQLServerDialect:
    """Tests for SQL Server dialect."""

    @pytest.fixture
    def dialect(self) -> SQLServerDialect:
        return SQLServerDialect()

    def test_name(self, dialect: SQLServerDialect) -> None:
        """SQL Server dialect has correct name."""
        assert dialect.name == "sqlserver"

    def test_param(self, dialect: SQLServerDialect) -> None:
        """SQL Server uses @pN format."""
        assert dialect.param(1) == "@p1"
        assert dialect.param(2) == "@p2"


class TestDatabricksDialect:
    """Tests for Databricks dialect."""

    @pytest.fixture
    def dialect(self) -> DatabricksDialect:
        return DatabricksDialect()

    def test_name(self, dialect: DatabricksDialect) -> None:
        """Databricks dialect has correct name."""
        assert dialect.name == "databricks"

    def test_param(self, dialect: DatabricksDialect) -> None:
        """Databricks uses :paramN format."""
        assert dialect.param(1) == ":param1"
        assert dialect.param(2) == ":param2"

    def test_statement_timeout_sql_is_noop(self, dialect: DatabricksDialect) -> None:
        """Databricks has no verified session-level timeout SQL across all compute types."""
        assert dialect.statement_timeout_sql(90) is None


class TestAthenaDialect:
    """Tests for Athena dialect."""

    @pytest.fixture
    def dialect(self) -> AthenaDialect:
        return AthenaDialect()

    def test_name(self, dialect: AthenaDialect) -> None:
        """Athena dialect has correct name."""
        assert dialect.name == "athena"

    def test_param(self, dialect: AthenaDialect) -> None:
        """Athena uses ? for parameters."""
        assert dialect.param(1) == "?"
        assert dialect.param(2) == "?"


class TestClickHouseDialect:
    """Tests for ClickHouse dialect."""

    @pytest.fixture
    def dialect(self) -> ClickHouseDialect:
        return ClickHouseDialect()

    def test_name(self, dialect: ClickHouseDialect) -> None:
        assert dialect.name == "clickhouse"

    def test_param(self, dialect: ClickHouseDialect) -> None:
        """ClickHouse drivers bind named %(name)s parameters."""
        assert dialect.param(1) == "%(p1)s"
        assert dialect.param(2) == "%(p2)s"
        assert dialect.uses_named_params is True

    def test_driver_limit_is_not_trusted(self, dialect: ClickHouseDialect) -> None:
        """dbt-clickhouse accepts execute(limit=) and ignores it, so the row cap
        must come from the post-fetch slice."""
        assert dialect.cursor_supports_driver_limit is False

    def test_no_timeout_sql(self, dialect: ClickHouseDialect) -> None:
        """The cap travels as a connection setting, not a SET."""
        assert dialect.statement_timeout_sql(30) is None

    def test_connect_credentials_caps_an_unset_profile(
        self, dialect: ClickHouseDialect
    ) -> None:
        creds = dialect.connect_credentials({"type": "clickhouse", "host": "h"}, 30)
        assert creds["custom_settings"] == {"max_execution_time": 30}
        assert creds["host"] == "h"

    def test_connect_credentials_writes_the_cap_it_is_given(
        self, dialect: ClickHouseDialect
    ) -> None:
        """It does not re-decide against the profile: deciding twice is how the
        enforced cap drifts from the reported one."""
        creds = dialect.connect_credentials(
            {"custom_settings": {"max_execution_time": 5}}, 30
        )
        assert creds["custom_settings"]["max_execution_time"] == 30

    def test_connect_credentials_leaves_the_source_config_alone(
        self, dialect: ClickHouseDialect
    ) -> None:
        """The pool keys its connection pool on the dict it was handed, so a
        mutation here would fork pools by cap."""
        source = {"type": "clickhouse", "custom_settings": {"max_threads": 2}}
        dialect.connect_credentials(source, 30)
        assert source["custom_settings"] == {"max_threads": 2}

    @pytest.mark.parametrize(
        ("authored", "expected"),
        [
            # Looser than the ceiling: the ceiling stands.
            (9999, 30),
            # Stricter: the profile's own cap stands, normalized to whole seconds.
            (5, 5),
            ("5", 5),
            (5.0, 5),
        ],
    )
    def test_resolve_timeout_seconds_applies_the_cap_as_a_ceiling(
        self, dialect: ClickHouseDialect, authored: object, expected: int
    ) -> None:
        resolved = dialect.resolve_timeout_seconds(
            {"custom_settings": {"max_execution_time": authored}}, 30
        )
        assert resolved == expected

    def test_the_enforced_cap_is_the_one_written_to_the_connection(
        self, dialect: ClickHouseDialect
    ) -> None:
        """What a timeout error reports is _timeout_seconds, so the resolved cap
        and the setting the warehouse enforces must be the same number."""
        source = {"custom_settings": {"max_execution_time": 5}}
        resolved = dialect.resolve_timeout_seconds(source, 120)
        creds = dialect.connect_credentials(source, resolved)
        assert resolved == 5
        assert creds["custom_settings"]["max_execution_time"] == 5

    def test_a_null_setting_is_an_unset_one(self, dialect: ClickHouseDialect) -> None:
        """YAML `max_execution_time:` with no value is absence, not a value to
        weigh against the ceiling."""
        source = {"custom_settings": {"max_execution_time": None}}
        assert dialect.resolve_timeout_seconds(source, 30) == 30

    @pytest.mark.parametrize(
        "authored",
        [
            "soon",
            "",
            [30],
            # ClickHouse types the setting UInt64 and casts with int(), so a
            # fractional cap truncates — and 0 means NO limit, so 0.5 would
            # disable the cap the feature exists to enforce.
            0.5,
            0,
            -1,
            float("nan"),
            float("inf"),
            "nan",
            # bool is an int in Python; True must not read as a 1s cap.
            True,
        ],
    )
    def test_resolve_timeout_seconds_rejects_a_cap_clickhouse_cannot_honor(
        self, dialect: ClickHouseDialect, authored: object
    ) -> None:
        """No guess is safe: keeping it may run uncapped, overwriting it may cap
        what the author did not ask to cap."""
        with pytest.raises(ValueError, match="max_execution_time"):
            dialect.resolve_timeout_seconds(
                {"custom_settings": {"max_execution_time": authored}}, 30
            )

    def test_other_dialects_carry_neither_seam(self) -> None:
        """Both base seams are no-ops: only ClickHouse's cap is credential-shaped
        and only ClickHouse's profile can spell a cap of its own."""
        source = {"type": "postgres", "host": "h"}
        postgres = get_dialect("postgres")
        assert postgres.connect_credentials(source, 30) is source
        assert postgres.resolve_timeout_seconds(source, 30) == 30

    @pytest.mark.parametrize(
        "message",
        [
            # HTTP driver: the server's symbolic error name is in the body.
            "ClickHouse exception:  HTTPDriver for http://h:8123 returned response "
            "code 500)\n Code: 159. DB::Exception: Timeout exceeded: elapsed 1000.2 "
            "ms, maximum: 1000.0 ms. (TIMEOUT_EXCEEDED) (version 24.8.1)",
            # Native driver: only the numeric code is guaranteed.
            "Code: 159.\nDB::Exception: Timeout exceeded: elapsed 2.1 seconds",
            # The symbolic arm alone, with no numeric code: it is what the
            # Code: 159 arm cannot cover, so it is pinned separately.
            "DB::Exception: Timeout exceeded (TIMEOUT_EXCEEDED)",
        ],
    )
    def test_timeout_error_is_recognized(
        self, dialect: ClickHouseDialect, message: str
    ) -> None:
        assert dialect.is_statement_timeout_error(RuntimeError(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "Code: 47. DB::Exception: Unknown expression identifier `nope` "
            "(UNKNOWN_IDENTIFIER)",
            # A code that merely contains 159 is not error 159.
            "Code: 1590. DB::Exception: something else",
            "Read timed out. (read timeout=300)",
            # ClickHouse echoes the offending query back, so a board that
            # selects the bare token is not a timeout — the symbolic name only
            # counts inside the parentheses the server prints it in.
            "Code: 47. DB::Exception: Unknown expression identifier `nope` in "
            "scope SELECT 'TIMEOUT_EXCEEDED', nope FROM t. (UNKNOWN_IDENTIFIER)",
        ],
    )
    def test_other_errors_are_not_timeouts(
        self, dialect: ClickHouseDialect, message: str
    ) -> None:
        assert dialect.is_statement_timeout_error(RuntimeError(message)) is False


class TestDialectRegistry:
    """Tests for the dialect registry and get_dialect() function."""

    def test_get_postgres_dialect(self) -> None:
        """Get PostgreSQL dialect."""
        dialect = get_dialect("postgres")
        assert isinstance(dialect, PostgresDialect)
        assert dialect.name == "postgres"

    def test_get_postgresql_alias(self) -> None:
        """postgresql is an alias for postgres."""
        dialect = get_dialect("postgresql")
        assert isinstance(dialect, PostgresDialect)

    def test_get_duckdb_dialect(self) -> None:
        """Get DuckDB dialect."""
        dialect = get_dialect("duckdb")
        assert isinstance(dialect, DuckDBDialect)

    def test_get_mysql_dialect(self) -> None:
        """Get MySQL dialect."""
        dialect = get_dialect("mysql")
        assert isinstance(dialect, MySQLDialect)

    def test_get_mariadb_alias(self) -> None:
        """mariadb is an alias for mysql."""
        dialect = get_dialect("mariadb")
        assert isinstance(dialect, MySQLDialect)

    def test_get_snowflake_dialect(self) -> None:
        """Get Snowflake dialect."""
        dialect = get_dialect("snowflake")
        assert isinstance(dialect, SnowflakeDialect)

    def test_get_bigquery_dialect(self) -> None:
        """Get BigQuery dialect."""
        dialect = get_dialect("bigquery")
        assert isinstance(dialect, BigQueryDialect)

    def test_get_redshift_dialect(self) -> None:
        """Get Redshift dialect."""
        dialect = get_dialect("redshift")
        assert isinstance(dialect, RedshiftDialect)

    def test_get_sqlserver_dialect(self) -> None:
        """Get SQL Server dialect."""
        dialect = get_dialect("sqlserver")
        assert isinstance(dialect, SQLServerDialect)

    def test_get_mssql_alias(self) -> None:
        """mssql is an alias for sqlserver."""
        dialect = get_dialect("mssql")
        assert isinstance(dialect, SQLServerDialect)

    def test_get_databricks_dialect(self) -> None:
        """Get Databricks dialect."""
        dialect = get_dialect("databricks")
        assert isinstance(dialect, DatabricksDialect)

    def test_get_spark_alias(self) -> None:
        """spark shares Databricks' SQL grammar (SparkDialect subclasses
        DatabricksDialect) but is its own dialect, not a shared instance —
        dbt-spark's cursor lacks fetchmany, unlike dbt-databricks'."""
        dialect = get_dialect("spark")
        assert isinstance(dialect, SparkDialect)
        assert isinstance(dialect, DatabricksDialect)
        assert dialect is not get_dialect("databricks")

    def test_databricks_and_spark_differ_on_driver_limit_support(self) -> None:
        """The one behavioral difference this split exists for: dbt-databricks'
        cursor implements fetchmany, dbt-spark's (Hive/ODBC wrappers) does not."""
        assert get_dialect("databricks").cursor_supports_driver_limit is True
        assert get_dialect("spark").cursor_supports_driver_limit is False

    def test_get_athena_dialect(self) -> None:
        """Get Athena dialect."""
        dialect = get_dialect("athena")
        assert isinstance(dialect, AthenaDialect)

    def test_get_presto_alias(self) -> None:
        """presto is an alias for athena."""
        dialect = get_dialect("presto")
        assert isinstance(dialect, AthenaDialect)

    def test_get_trino_alias(self) -> None:
        """trino is an alias for athena."""
        dialect = get_dialect("trino")
        assert isinstance(dialect, AthenaDialect)

    def test_get_clickhouse_dialect(self) -> None:
        """Get ClickHouse dialect."""
        dialect = get_dialect("clickhouse")
        assert isinstance(dialect, ClickHouseDialect)

    def test_unknown_dialect_returns_postgres(self) -> None:
        """Unknown dialect type falls back to PostgreSQL."""
        dialect = get_dialect("unknown_db")
        assert isinstance(dialect, PostgresDialect)

    def test_case_insensitive(self) -> None:
        """Dialect lookup is case-insensitive."""
        dialect = get_dialect("POSTGRES")
        assert isinstance(dialect, PostgresDialect)

        dialect = get_dialect("MySQL")
        assert isinstance(dialect, MySQLDialect)

    def test_singleton_instances(self) -> None:
        """Same dialect instance is returned for repeated calls."""
        dialect1 = get_dialect("postgres")
        dialect2 = get_dialect("postgres")
        assert dialect1 is dialect2

        dialect3 = get_dialect("postgresql")
        assert dialect1 is dialect3

    def test_dialects_dict_contains_all(self) -> None:
        """DIALECTS dict contains all expected entries."""
        expected_keys = [
            "postgres",
            "postgresql",
            "duckdb",
            "mysql",
            "mariadb",
            "snowflake",
            "bigquery",
            "redshift",
            "sqlserver",
            "mssql",
            "databricks",
            "spark",
            "athena",
            "presto",
            "trino",
            "clickhouse",
        ]
        for key in expected_keys:
            assert key in DIALECTS, f"Missing dialect: {key}"

    def test_list_dialects(self) -> None:
        """list_dialects() returns all supported profile types."""
        dialects = list_dialects()
        assert "postgres" in dialects
        assert "mysql" in dialects
        assert "duckdb" in dialects
        assert len(dialects) >= 15  # At least all the expected keys


class TestDialectEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_param_index_zero(self) -> None:
        """Param index 0 (unusual but valid)."""
        dialect = PostgresDialect()
        assert dialect.param(0) == "$0"

    def test_param_large_index(self) -> None:
        """Large param index."""
        dialect = PostgresDialect()
        assert dialect.param(999) == "$999"

    def test_params_generates_correct_count(self) -> None:
        """params() generates exactly the requested count."""
        dialect = BigQueryDialect()
        params = dialect.params(5)
        assert len(params) == 5
        assert params == ["@param1", "@param2", "@param3", "@param4", "@param5"]
