"""Tests for source configuration and defaults.

Tests the dbt_charts.core.compile.models.source module and source propagation
in the normalizer.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import (
    VALID_SOURCE_TYPES,
    BigQuerySourceConfig,
    CsvSourceConfig,
    DuckDBSourceConfig,
    HttpSourceConfig,
    PostgresSourceConfig,
    SnowflakeSourceConfig,
    TrinoSourceConfig,
    compile,
    is_api_source,
    is_database_source,
    is_file_source,
    parse_source_config,
)
from dbt_charts.core.compile.config import ProjectSourcesConfig, load_project_sources
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.diagnostics.codes_compile import ERR_SOURCE_CREDENTIAL_LITERAL
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import PROJECT_CONFIG_NAME


class TestSourceTypes:
    """Tests for source configuration types."""

    @pytest.mark.parametrize(
        ("data", "expected_type"),
        [
            (
                {
                    "type": "postgres",
                    "host": "localhost",
                    "dbname": "analytics",
                    "user": "user",
                    "password": "pass",
                },
                PostgresSourceConfig,
            ),
            (
                {
                    "type": "snowflake",
                    "account": "my_account",
                    "user": "user",
                    "database": "ANALYTICS",
                    "warehouse": "COMPUTE_WH",
                },
                SnowflakeSourceConfig,
            ),
            (
                {
                    "type": "trino",
                    "host": "trino.example.com",
                    "user": "analyst",
                    "database": "hive",
                    "schema": "analytics",
                },
                TrinoSourceConfig,
            ),
            (
                {"type": "duckdb", "path": "./data/analytics.duckdb"},
                DuckDBSourceConfig,
            ),
            (
                {"type": "csv", "files": {"sales": "data/sales.csv"}},
                CsvSourceConfig,
            ),
            (
                {
                    "type": "http",
                    "url": "https://api.example.com",
                    "headers": {"Authorization": "Bearer token"},
                },
                HttpSourceConfig,
            ),
        ],
    )
    def test_parse_source_config_type_dispatch(self, data, expected_type):
        """parse_source_config dispatches to the correct typed config class."""
        config = parse_source_config(data)
        assert isinstance(config, expected_type)
        assert config.type == data["type"]

    def test_trino_catalog_alias_populates_database(self):
        """`catalog` and `database` both address the Trino catalog at the model layer."""
        via_catalog = parse_source_config(
            {
                "type": "trino",
                "host": "trino.example.com",
                "user": "analyst",
                "catalog": "hive",
                "schema": "analytics",
            }
        )
        assert isinstance(via_catalog, TrinoSourceConfig)
        assert via_catalog.database == "hive"

    def test_trino_auth_fields_are_typed(self):
        """Multi-auth fields are explicit on the Trino source model."""
        config = TrinoSourceConfig(
            type="trino",
            host="trino.example.com",
            user="analyst",
            database="hive",
            schema="analytics",
            method="ldap",
            password="secret",
            http_scheme="https",
        )
        assert config.method == "ldap"
        assert config.http_scheme == "https"

    def test_snowflake_source_config_no_password(self):
        """Regression: password must be optional — OAuth users don't supply one."""
        config = SnowflakeSourceConfig(
            type="snowflake",
            account="xy12345.us-east-1",
            user="svc_user",
            database="ANALYTICS",
            warehouse="COMPUTE_WH",
        )
        assert config.password is None

    def test_snowflake_source_config_oauth_authenticator(self):
        """OAuth authenticator is an explicit Snowflake source field."""
        config = SnowflakeSourceConfig(
            type="snowflake",
            account="xy12345.us-east-1",
            user="svc_user",
            database="ANALYTICS",
            warehouse="COMPUTE_WH",
            authenticator="externalbrowser",
        )
        assert config.authenticator == "externalbrowser"

    def test_csv_source_requires_nonempty_files(self):
        """CSV source requires a non-empty files mapping."""
        with pytest.raises(ValueError, match="files must not be empty"):
            CsvSourceConfig(type="csv", files={})

    def test_lookml_no_longer_a_source_type(self):
        """'lookml' is not a valid source type — the LookML surface was removed."""
        assert "lookml" not in VALID_SOURCE_TYPES
        with pytest.raises(ValueError, match="Unknown source type"):
            parse_source_config({"type": "lookml", "path": "lkml"})

    def test_bigquery_source_config_resolves_env_vars(self, monkeypatch):
        """parse_source_config with env_var syntax yields resolved BigQuerySourceConfig."""
        monkeypatch.setenv("TEST_BQ_PROJECT", "my-gcp-project")
        monkeypatch.setenv("TEST_BQ_DATASET", "transforms_bi")

        config = parse_source_config(
            {
                "type": "bigquery",
                "project": "{{ env_var('TEST_BQ_PROJECT') }}",
                "dataset": "{{ env_var('TEST_BQ_DATASET') }}",
                "location": "US",
            }
        )

        assert isinstance(config, BigQuerySourceConfig)
        assert config.project == "my-gcp-project"
        assert config.dataset == "transforms_bi"
        assert config.location == "US"
        assert config.keyfile is None

    def test_bigquery_keyfile_json_accepted(self):
        """keyfile_json (inline dict) is a valid credential shape."""
        config = parse_source_config(
            {
                "type": "bigquery",
                "project": "my-project",
                "dataset": "ds",
                "keyfile_json": {"type": "service_account", "project_id": "my-project"},
            }
        )
        assert isinstance(config, BigQuerySourceConfig)
        assert config.keyfile_json == {
            "type": "service_account",
            "project_id": "my-project",
        }
        assert config.keyfile is None

    def test_bigquery_keyfile_path_accepted(self):
        """keyfile (path string) is a valid credential shape."""
        config = parse_source_config(
            {
                "type": "bigquery",
                "project": "my-project",
                "dataset": "ds",
                "keyfile": "/path/to/key.json",
            }
        )
        assert isinstance(config, BigQuerySourceConfig)
        assert config.keyfile == "/path/to/key.json"
        assert config.keyfile_json is None

    def test_bigquery_both_keyfile_and_keyfile_json_raises(self):
        """Providing both keyfile and keyfile_json must raise ValueError."""
        with pytest.raises(ValueError, match="keyfile"):
            parse_source_config(
                {
                    "type": "bigquery",
                    "project": "my-project",
                    "dataset": "ds",
                    "keyfile": "/path/to/key.json",
                    "keyfile_json": {"type": "service_account"},
                }
            )

    def test_bigquery_no_keyfile_infers_oauth_method(self):
        """BigQuerySourceConfig with no keyfile infers method='oauth' (ADC)."""
        config = parse_source_config(
            {"type": "bigquery", "project": "p", "dataset": "ds"}
        )
        assert isinstance(config, BigQuerySourceConfig)
        assert config.method == "oauth"

    def test_bigquery_keyfile_json_infers_service_account_json_method(self):
        """BigQuerySourceConfig with keyfile_json infers method='service-account-json'."""
        config = parse_source_config(
            {
                "type": "bigquery",
                "project": "p",
                "dataset": "ds",
                "keyfile_json": {"type": "service_account"},
            }
        )
        assert isinstance(config, BigQuerySourceConfig)
        assert config.method == "service-account-json"

    def test_bigquery_keyfile_path_infers_service_account_method(self):
        """BigQuerySourceConfig with keyfile infers method='service-account'."""
        config = parse_source_config(
            {
                "type": "bigquery",
                "project": "p",
                "dataset": "ds",
                "keyfile": "/creds/key.json",
            }
        )
        assert isinstance(config, BigQuerySourceConfig)
        assert config.method == "service-account"

    def test_bigquery_explicit_method_preserved(self):
        """Explicit method is kept as-is, not overridden by inference."""
        config = parse_source_config(
            {"type": "bigquery", "project": "p", "dataset": "ds", "method": "oauth"}
        )
        assert isinstance(config, BigQuerySourceConfig)
        assert config.method == "oauth"

    def test_bigquery_method_in_model_dump(self):
        """model_dump includes method so build_adapter receives it."""
        config = parse_source_config(
            {"type": "bigquery", "project": "p", "dataset": "ds"}
        )
        assert isinstance(config, BigQuerySourceConfig)
        d = config.model_dump(by_alias=True)
        assert d["method"] == "oauth"

    def test_parse_source_config_unknown_type(self):
        """Test parsing unknown source type fails."""
        with pytest.raises(ValueError, match="Unknown source type"):
            parse_source_config({"type": "unknown"})

    def test_parse_source_config_missing_type(self):
        """Test parsing without type fails."""
        with pytest.raises(ValueError, match="must have a 'type' field"):
            parse_source_config({"host": "localhost"})


class TestSourceTypeGuards:
    """Tests for source type guard functions."""

    def test_is_database_source(self):
        """Test database source detection."""
        postgres = PostgresSourceConfig(
            type="postgres", host="localhost", dbname="test", user="u", password="p"
        )
        duckdb = DuckDBSourceConfig(type="duckdb", path=":memory:")
        csv = CsvSourceConfig(type="csv", files={"data": "data.csv"})

        assert is_database_source(postgres) is True
        assert is_database_source(duckdb) is True
        assert is_database_source(csv) is False

    def test_is_file_source(self):
        """Test file source detection."""
        csv = CsvSourceConfig(type="csv", files={"data": "data.csv"})
        http = HttpSourceConfig(type="http", url="https://api.example.com")

        assert is_file_source(csv) is True
        assert is_file_source(http) is False

    def test_is_api_source(self):
        """Test API source detection."""
        http = HttpSourceConfig(type="http", url="https://api.example.com")
        csv = CsvSourceConfig(type="csv", files={"data": "data.csv"})

        assert is_api_source(http) is True
        assert is_api_source(csv) is False


class TestSourceShorthand:
    """Tests for AuthoredBoard.source shorthand field."""

    def test_source_shorthand_sets_get_default_source(self):
        """AuthoredBoard(source='foo').get_default_source() == 'foo'."""
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard

        board = AuthoredBoard(source="foo", rows=["x"])
        assert board.get_default_source() == "foo"

    def test_source_shorthand_in_yaml_compiles(self):
        """source: shorthand in YAML is accepted and propagates to queries."""
        from dbt_charts.core.compile import compile

        yaml_content = """
title: Test Dashboard
source: my_db

queries:
  q:
    sql: SELECT 1

charts:
  c:
    query: q
    type: kpi
    value: value

rows:
  - c
"""
        result = compile(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        assert result.board.queries["q"].source == "my_db"


class TestBoardSourceDefaults:
    """Tests for board-level source defaults."""

    def test_board_with_source_default(self):
        """Test board with default source applies to queries."""
        yaml_content = """
title: Test Dashboard
source: test_db

queries:
  sales:
    sql: SELECT * FROM sales

charts:
  chart:
    query: sales
    type: table

rows:
  - chart
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        query = result.board.queries["sales"]
        assert query.source == "test_db"

    def test_query_source_overrides_default(self):
        """Test query-level source overrides board default."""
        yaml_content = """
title: Test Dashboard
source: default_db

queries:
  sales:
    sql: SELECT * FROM sales
    source: override_db

charts:
  chart:
    query: sales
    type: table

rows:
  - chart
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        query = result.board.queries["sales"]
        # Query-level source should win
        assert query.source == "override_db"

    def test_csv_query_type_is_rejected(self):
        """type: csv in a query is no longer supported; use type: sql with a csv source."""
        yaml_content = """
title: Test Dashboard

queries:
  data:
    type: csv
    file: data.csv

charts:
  chart:
    query: data
    type: table

rows:
  - chart
"""
        result = compile(yaml_content)

        assert not result.success
        assert any("csv" in e.message.lower() for e in result.errors)

    def test_inline_query_gets_default_source(self):
        """Test inline queries in charts get default source."""
        yaml_content = """
title: Test Dashboard
source: default_profile

charts:
  chart:
    query:
      sql: SELECT 1
    type: kpi
    value: value

rows:
  - chart
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        chart = result.board.charts["chart"]
        # Inline query should have gotten the default source
        assert chart.query.source == "default_profile"

    def test_nested_board_inherits_source(self):
        """Test nested boards inherit parent source through inline charts."""
        yaml_content = """
title: Parent Board
source: parent_profile

queries:
  q1:
    sql: SELECT 1

charts:
  c1:
    query: q1
    type: kpi
    value: value

rows:
  - c1
  - title: Nested Section
    text: |
      This section has an inline chart
    cols:
      - query:
          sql: SELECT 2 AS value
        type: kpi
        value: value
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        # Parent query should have default source
        assert result.board.queries["q1"].source == "parent_profile"
        # Check the inline chart in the nested board inherits the source
        nested_board = result.board.layout.items[1].board
        # Get the inline chart from the nested board's layout
        inline_chart = nested_board.layout.items[0].chart
        assert inline_chart is not None
        # The inline query should have inherited the source
        assert inline_chart.query.source == "parent_profile"

    def test_parent_default_source_propagates_to_nested_board_queries(self):
        """Regression: default_source from parent context must reach the query
        registry of a nested board so inline queries pick it up."""
        yaml_content = """
title: Parent
source: warehouse

rows:
  - title: Nested
    cols:
      - query:
          sql: SELECT 42 AS n
        type: kpi
        value: n
"""
        result = compile(yaml_content)

        assert result.success, f"Compilation failed: {result.errors}"
        nested_board = result.board.layout.items[0].board
        inline_chart = nested_board.layout.items[0].chart
        assert inline_chart is not None
        assert inline_chart.query.source == "warehouse"


class TestProjectSources:
    """Tests for project-level sources configuration."""

    def test_load_project_sources_reads_fresh_per_call(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """Edits to dbt_charts.yml must be visible on the next call.

        load_project_sources does not cache; each call re-reads disk.
        """
        dbt_charts_yml = tmp_path / "dbt_charts.yml"
        dbt_charts_yml.write_text(
            "sources:\n  first:\n    type: duckdb\n    path: first.duckdb\n"
        )

        first = load_project_sources(local_project(tmp_path))
        assert "first" in first.sources

        # Edit the file in place — no invalidation call.
        dbt_charts_yml.write_text(
            "sources:\n  second:\n    type: duckdb\n    path: second.duckdb\n"
        )

        second = load_project_sources(local_project(tmp_path))
        assert "second" in second.sources
        assert "first" not in second.sources, (
            "load_project_sources returned stale cached data; requires fresh read"
        )

    def test_load_project_sources_requires_project(self):
        """project is required — no cwd fallback."""
        with pytest.raises(TypeError):
            load_project_sources()  # type: ignore[call-arg]

    def test_pre_rename_config_names_are_no_longer_read(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Pre-rename config spellings are not a recognized project config.

        A project carrying only a legacy config filename loads zero sources
        rather than silently reviving a filename dct no longer authors.
        """
        (tmp_path / "legacy_config.yml").write_text(
            "sources:\n  legacy:\n    type: duckdb\n    path: l.duckdb\n"
        )

        loaded = load_project_sources(local_project(tmp_path))

        assert "legacy" not in loaded.sources

    def test_project_config_name(self) -> None:
        assert PROJECT_CONFIG_NAME == "dbt_charts.yml"

    @pytest.mark.parametrize(
        ("entry", "offending"),
        [
            # An unquoted YAML key parses as a number, and would otherwise reach
            # the model as a bad keyword argument (TypeError, not ValueError).
            (
                "    type: csv\n    files:\n      rows: data/r.csv\n    2024: x\n",
                "2024",
            ),
            # A list `type:` is unhashable, so even the type lookup raises.
            ("    type: [csv]\n", "csv"),
        ],
    )
    def test_malformed_source_entry_is_stamped_not_crashed(
        self,
        tmp_path: Path,
        local_project: Callable[..., FilesystemProject],
        entry: str,
        offending: str,
    ) -> None:
        """A shape nobody validated still fails as ERR-SOURCE-CONFIG-INVALID.

        ``parse_source_config`` promises ValueError, so it rejects these itself
        rather than letting a TypeError out and making every caller catch a type
        the contract never named.
        """
        (tmp_path / PROJECT_CONFIG_NAME).write_text(f"sources:\n  marts:\n{entry}")

        with pytest.raises(CompilationError) as exc:
            load_project_sources(local_project(tmp_path))

        assert exc.value.code is not None
        assert exc.value.code.code == "ERR-SOURCE-CONFIG-INVALID"
        assert "marts" in str(exc.value)
        assert offending in str(exc.value)

    def test_load_sources_from_file(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """Test loading sources from dbt_charts.yml."""
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  analytics:\n"
            "    type: postgres\n"
            "    host: localhost\n"
            "    dbname: analytics\n"
            "    user: user\n"
            "    password: \"{{ env_var('PW') }}\"\n"
        )

        sources = load_project_sources(local_project(tmp_path))
        assert "analytics" in sources.sources

    def test_load_project_sources_is_scoped_per_project_dir(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """Sources loaded for different project roots must not bleed across calls."""
        first_project = tmp_path / "first"
        second_project = tmp_path / "second"
        first_project.mkdir()
        second_project.mkdir()

        (first_project / "dbt_charts.yml").write_text(
            "sources:\n  first_db:\n    type: duckdb\n    path: first.duckdb\n"
        )
        (second_project / "dbt_charts.yml").write_text(
            "sources:\n  second_db:\n    type: duckdb\n    path: second.duckdb\n"
        )

        first_sources = load_project_sources(local_project(first_project))
        second_sources = load_project_sources(local_project(second_project))

        assert "first_db" in first_sources.sources
        assert "second_db" in second_sources.sources
        assert "first_db" not in second_sources.sources
        assert "second_db" not in first_sources.sources

    def test_load_config_does_not_leak_sources_into_another_project(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """A served project's registry must not become another project's registry.

        ``load_config`` installs its argument's dbt_charts.yml into the
        process-global ``_config``. One Cloud worker serves many orgs and one
        pytest process runs many packages' suites, so a later project reading
        its own registry must see only its own ``sources:`` — never the last
        project ``load_config`` happened to be called with.
        """
        from dbt_charts.core.compile.config import load_config, reset_config

        served = tmp_path / "served"
        other = tmp_path / "other"
        served.mkdir()
        other.mkdir()

        (served / "dbt_charts.yml").write_text(
            "sources:\n  served_db:\n    type: duckdb\n    path: served.duckdb\n"
        )
        (other / "dbt_charts.yml").write_text(
            "sources:\n  other_db:\n    type: duckdb\n    path: other.duckdb\n"
        )

        try:
            load_config(local_project(served))
            sources = load_project_sources(local_project(other)).sources
        finally:
            reset_config()

        assert set(sources) == {"other_db"}

    def test_load_project_sources_resolves_env_vars(
        self, tmp_path, monkeypatch, local_project: Callable[..., FilesystemProject]
    ):
        """Project sources should resolve env_var() expressions before runtime use."""
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  analytics_bq:\n"
            "    type: bigquery\n"
            "    project: \"{{ env_var('TEST_BQ_PROJECT') }}\"\n"
            "    dataset: \"{{ env_var('TEST_BQ_DATASET') }}\"\n"
        )
        monkeypatch.setenv("TEST_BQ_PROJECT", "demo-project")
        monkeypatch.setenv("TEST_BQ_DATASET", "analytics")

        sources = load_project_sources(local_project(tmp_path))

        assert sources.sources["analytics_bq"]["project"] == "demo-project"
        assert sources.sources["analytics_bq"]["dataset"] == "analytics"


class TestSourcesWithCompiler:
    """Integration tests for sources with the compiler."""

    def test_sql_query_without_source_fails(self):
        """Test SQL query without source and no default fails."""
        yaml_content = """
title: Test Dashboard

queries:
  sales:
    sql: SELECT * FROM sales

charts:
  chart:
    query: sales
    type: table

rows:
  - chart
"""
        result = compile(yaml_content)

        assert not result.success
        assert any("source" in str(e).lower() for e in result.errors)

    def test_http_query_without_url_fails(self):
        """HTTP query without url raises a compile error."""
        yaml_content = """
title: Test Dashboard

queries:
  api:
    type: http
    method: GET

charts:
  chart:
    query: api
    type: table

rows:
  - chart
"""
        result = compile(yaml_content)

        assert not result.success
        assert any(
            "url" in str(e).lower() or "source" in str(e).lower() for e in result.errors
        )


class TestNamedSourceResolution:
    """Tests for named source resolution in compilation and execution.

    Connection-bearing sources live in the project registry (D-01/D-02), passed
    via ``project_sources=``, never inline in the board YAML.
    """

    def test_sources_preserved_in_compiled_board(self):
        """Test that named sources are preserved in Board."""
        yaml_content = """
title: Test Dashboard

queries:
  test_query:
    source: profiles
    sql: SELECT 1 AS x

charts:
  test_chart:
    query: test_query
    type: table

rows:
  - test_chart
"""
        project_sources = ProjectSourcesConfig(
            sources={"profiles": {"type": "duckdb", "path": ":memory:"}}
        )
        result = compile(yaml_content, project_sources=project_sources)
        assert result.success
        assert "profiles" in result.board.sources
        assert result.board.sources["profiles"]["type"] == "duckdb"

    def test_multiple_sources_preserved(self):
        """Test multiple named sources are all preserved."""
        yaml_content = """
title: Multi-Source Dashboard

queries:
  qa:
    source: source_a
    sql: SELECT 1 as x
  qb:
    source: source_b
    sql: SELECT 2 as y

charts:
  ca:
    query: qa
    type: table
  cb:
    query: qb
    type: table

rows:
  - ca
  - cb
"""
        project_sources = ProjectSourcesConfig(
            sources={
                "source_a": {"type": "duckdb", "path": ":memory:"},
                "source_b": {"type": "duckdb", "path": ":memory:"},
            }
        )
        result = compile(yaml_content, project_sources=project_sources)
        assert result.success
        assert "source_a" in result.board.sources
        assert "source_b" in result.board.sources


class TestNamedSourceExecution:
    """Tests for named source resolution during query execution."""

    def test_executor_resolves_source_reference(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test that executor resolves string source to config."""
        from dbt_charts.core.execute import Executor

        yaml_content = """
title: Test Dashboard

queries:
  get_answer:
    source: test_db
    sql: SELECT 42 AS answer

charts:
  answer_chart:
    query: get_answer
    type: kpi
    value: answer

rows:
  - answer_chart
"""
        project_sources = ProjectSourcesConfig(
            sources={"test_db": {"type": "duckdb", "path": ":memory:"}}
        )
        result = compile(yaml_content, project_sources=project_sources)
        assert result.success

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        data = executor.execute_query("get_answer")

        assert len(data) == 1
        assert data[0]["answer"] == 42

    def test_executor_with_cte_query(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test that CTE-based queries work as replacement for init_sql views."""
        from dbt_charts.core.execute import Executor

        yaml_content = """
title: Profile Dashboard

queries:
  column_stats:
    source: profiles
    sql: |
      WITH profile_columns AS (
        SELECT 'col1' as name, 10 as null_pct
        UNION ALL
        SELECT 'col2' as name, 50 as null_pct
      )
      SELECT * FROM profile_columns ORDER BY null_pct DESC

charts:
  null_rates:
    query: column_stats
    type: bar
    x: name
    y: null_pct

rows:
  - null_rates
"""
        project_sources = ProjectSourcesConfig(
            sources={"profiles": {"type": "duckdb", "path": ":memory:"}}
        )
        result = compile(yaml_content, project_sources=project_sources)
        assert result.success

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        data = executor.execute_query("column_stats")

        assert len(data) == 2
        assert data[0]["name"] == "col2"
        assert data[0]["null_pct"] == 50
        assert data[1]["name"] == "col1"
        assert data[1]["null_pct"] == 10

    def test_multiple_queries_same_source(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Test multiple queries using the same DuckDB source."""
        from dbt_charts.core.execute import Executor

        yaml_content = """
title: Multi-Query Dashboard

queries:
  users:
    source: db
    sql: SELECT 1 AS id, 'Alice' AS name
  orders:
    source: db
    sql: SELECT 1 AS user_id, 100 AS amount

charts:
  users_table:
    query: users
    type: table
  orders_table:
    query: orders
    type: table

rows:
  - users_table
  - orders_table
"""
        project_sources = ProjectSourcesConfig(
            sources={"db": {"type": "duckdb", "path": ":memory:"}}
        )
        result = compile(yaml_content, project_sources=project_sources)
        assert result.success

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        users_data = executor.execute_query("users")
        assert len(users_data) == 1
        assert users_data[0]["name"] == "Alice"

        orders_data = executor.execute_query("orders")
        assert len(orders_data) == 1
        assert orders_data[0]["amount"] == 100

    def test_source_not_in_registry_raises_at_compile(self):
        """A source name absent from the loaded registry is now caught at
        compile time (see TestSourceNameValidatedAtCompile), not deferred to
        execute-time resolution.
        """
        yaml_content = """
title: Test Dashboard

queries:
  test_query:
    source: unknown_source
    sql: SELECT 1 as value

charts:
  test_chart:
    query: test_query
    type: kpi
    value: value

rows:
  - test_chart
"""
        project_sources = ProjectSourcesConfig(
            sources={"known_source": {"type": "duckdb", "path": ":memory:"}}
        )
        result = compile(yaml_content, project_sources=project_sources)
        assert not result.success
        codes = [e.code for e in result.errors if hasattr(e, "code")]
        assert "ERR-SOURCE-NOT-FOUND" in codes, result.errors

    def test_executor_resolves_source_reference_from_board_sources(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """Source reference should use compiled board sources when available."""
        import duckdb

        from dbt_charts.core.execute import Executor

        db_path = tmp_path / "warehouse.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE metrics AS SELECT 7 AS value")
        conn.close()

        yaml_content = """
title: Source Reference Dashboard

queries:
  metric:
    source: warehouse
    sql: SELECT value FROM metrics

charts:
  metric_chart:
    query: metric
    type: kpi
    value: value

rows:
  - metric_chart
"""
        project_sources = ProjectSourcesConfig(
            sources={"warehouse": {"type": "duckdb", "path": str(db_path)}}
        )
        result = compile(yaml_content, project_sources=project_sources)
        assert result.success

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        assert executor.execute_query("metric") == [{"value": 7}]


class TestSourcesDefaultRemoved:
    """TDD tests for the removal of sources.default from project-level config.

    Steps 1-3 of the worksheet: written first, must fail until implemented.
    """

    def test_sources_default_in_dbt_charts_yml_raises_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Step 1: sources.default in dbt_charts.yml raises a clear error, not silently honored."""
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  default: analytics\n"
            "  analytics:\n"
            "    type: duckdb\n"
            "    path: analytics.duckdb\n"
        )
        with pytest.raises(TypeError, match="sources.default is no longer supported"):
            load_project_sources(local_project(tmp_path))

    def test_board_source_still_injected_without_project_default(self) -> None:
        """Step 2: board-level `source: warehouse` still injects into queries as default."""
        from dbt_charts.core.compile import compile

        yaml_content = """
title: Test
source: warehouse

queries:
  q:
    sql: SELECT 1

charts:
  c:
    query: q
    type: kpi
    value: value

rows:
  - c
"""
        result = compile(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        assert result.board.queries["q"].source == "warehouse"

    def test_sql_without_source_raises_df_compile_source_required(self) -> None:
        """Step 2: plain SQL query with no source and no board default still raises ERR-SOURCE-REQUIRED."""
        from dbt_charts.core.compile import compile

        yaml_content = """
title: Test

queries:
  q:
    sql: SELECT 1

charts:
  c:
    query: q
    type: kpi
    value: value

rows:
  - c
"""
        result = compile(yaml_content)
        assert not result.success
        codes = [e.code for e in result.errors if hasattr(e, "code")]
        assert "ERR-SOURCE-REQUIRED" in codes, (
            f"Expected ERR-SOURCE-REQUIRED in errors, got: {result.errors}"
        )

    def test_host_default_source_satisfies_sourceless_sql(self) -> None:
        """A host (e.g. the playground scratch endpoint) compiling composed content
        with no charts/meta.yml can supply host_default_source as the fallback
        default, so a sourceless SQL query no longer raises ERR-SOURCE-REQUIRED.
        """
        from dbt_charts.core.compile import compile

        yaml_content = """
title: Test

queries:
  q:
    sql: SELECT 1

charts:
  c:
    query: q
    type: kpi
    value: value

rows:
  - c
"""
        result = compile(yaml_content, host_default_source="examples_db")
        codes = [e.code for e in result.errors if hasattr(e, "code")]
        assert "ERR-SOURCE-REQUIRED" not in codes, (
            f"host_default_source should satisfy the sourceless query, got: {result.errors}"
        )

    def test_values_query_without_source_compiles(self) -> None:
        """Step 2: values query without source still compiles fine (excluded from source injection)."""
        from dbt_charts.core.compile import compile

        yaml_content = """
title: Test

queries:
  inline:
    type: values
    rows:
      - {x: 1, y: 2}

charts:
  c:
    query: inline
    type: table

rows:
  - c
"""
        result = compile(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"

    def test_default_source_injection_skips_http_and_schema_queries(self) -> None:
        """A board-level default source only injects into SQL-shaped queries
        (sql) — http and schema queries have no connection
        source to inject into and must be unaffected.
        """
        yaml_content = """
title: Test
source: warehouse

queries:
  api:
    type: http
    url: https://example.com/data
  probe:
    type: schema

charts:
  c:
    query: api
    type: table

rows:
  - c
"""
        result = compile(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        assert result.board.queries["probe"].source is None

    def test_sources_only_in_dbt_charts_yml_loads(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Step 3: sources authored in dbt_charts.yml load correctly with no _sources.yaml."""
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  analytics:\n    type: duckdb\n    path: analytics.duckdb\n"
        )
        sources = load_project_sources(local_project(tmp_path))
        assert "analytics" in sources.sources

    def test_sources_yaml_file_is_ignored(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Step 3: _sources.yaml is no longer loaded; only dbt_charts.yml sources are used."""
        (tmp_path / "_sources.yaml").write_text(
            "sources:\n  yaml_only_source:\n    type: duckdb\n    path: yaml.duckdb\n"
        )
        sources = load_project_sources(local_project(tmp_path))
        assert "yaml_only_source" not in sources.sources, (
            "_sources.yaml must not be loaded after removal of _sources.yaml support"
        )

    def test_compile_file_loads_sources_from_dbt_charts_yml(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Step 3: compile_file merges project sources from dbt_charts.yml, not _sources.yaml."""
        import duckdb

        from dbt_charts.core.compile import compile_file
        from dbt_charts.core.execute import Executor

        project_dir = tmp_path / "project"
        boards_dir = project_dir / "charts"
        boards_dir.mkdir(parents=True)
        db_path = project_dir / "analytics.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute("CREATE TABLE metrics AS SELECT 42 AS answer")
        conn.close()

        (project_dir / "dbt_charts.yml").write_text(
            f"sources:\n  analytics:\n    type: duckdb\n    path: {db_path.name}\n"
        )
        board_path = boards_dir / "dashboard.yml"
        board_path.write_text(
            "title: Project Source Dashboard\n"
            "source: analytics\n"
            "\n"
            "queries:\n"
            "  get_answer:\n"
            "    sql: SELECT answer FROM metrics\n"
            "\n"
            "charts:\n"
            "  answer_chart:\n"
            "    query: get_answer\n"
            "    type: kpi\n"
            "    value: answer\n"
            "\n"
            "rows:\n"
            "  - answer_chart\n"
        )

        project = local_project(project_dir)
        result = compile_file(project.path("charts/dashboard.yml").read_board())
        assert result.success
        assert "analytics" in result.board.sources

        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        data = executor.execute_query("get_answer")
        assert data == [{"answer": 42}]


class TestSourceNameValidatedAtCompile:
    """A query's `source:` name is validated against the loaded registry at
    compile time, moving today's execute-time ERR-SOURCE-NOT-FOUND
    earlier — but only when a registry was actually supplied. Compiling
    in-memory YAML with no project registry defers to the execute-time
    backstop, since there is nothing to validate against.
    """

    def test_unknown_source_name_raises_at_compile_with_registry(self) -> None:
        yaml_content = """
title: Test

queries:
  q:
    source: nope
    sql: SELECT 1

charts:
  c:
    query: q
    type: kpi
    value: value

rows:
  - c
"""
        project_sources = ProjectSourcesConfig(
            sources={"analytics": {"type": "duckdb", "path": ":memory:"}}
        )
        result = compile(yaml_content, project_sources=project_sources)
        assert not result.success
        codes = [e.code for e in result.errors if hasattr(e, "code")]
        assert "ERR-SOURCE-NOT-FOUND" in codes, result.errors
        message = result.errors[0].message
        assert "nope" in message
        assert "analytics" in message

    def test_known_source_name_compiles_with_registry(self) -> None:
        yaml_content = """
title: Test

queries:
  q:
    source: analytics
    sql: SELECT 1

charts:
  c:
    query: q
    type: kpi
    value: value

rows:
  - c
"""
        project_sources = ProjectSourcesConfig(
            sources={"analytics": {"type": "duckdb", "path": ":memory:"}}
        )
        result = compile(yaml_content, project_sources=project_sources)
        assert result.success, f"Compilation failed: {result.errors}"

    def test_unknown_source_name_defers_to_execute_when_no_registry_loaded(
        self,
    ) -> None:
        """In-memory `compile(yaml_content)` with no project registry has no
        registry to validate the name against — the execute-time resolver
        remains the backstop for an unknown name in this context.
        """
        yaml_content = """
title: Test

queries:
  q:
    source: nope
    sql: SELECT 1

charts:
  c:
    query: q
    type: kpi
    value: value

rows:
  - c
"""
        result = compile(yaml_content)
        assert result.success, f"Compilation failed: {result.errors}"
        assert result.board.queries["q"].source == "nope"

    def test_source_null_dict_raises_required_error(self) -> None:
        """Programmatic `source=None` in a raw query dict produces ERR-SOURCE-REQUIRED,
        not a raw Pydantic ValidationError with 'string_type' in the message.
        """
        from dbt_charts.core.compile.errors import CompilationError
        from dbt_charts.core.compile.normalize.queries import normalize_query

        with pytest.raises(CompilationError) as exc_info:
            normalize_query(
                "q",
                {"sql": "SELECT 1", "source": None},
                default_source=None,
                sources={"analytics": {"type": "duckdb", "path": ":memory:"}},
            )
        assert exc_info.value.code.code == "ERR-SOURCE-REQUIRED"


class TestInlineFileSourcePaths:
    """D-12: a `source:` string that looks like a file path is a file reference,

    not a registry name — allowed inline (unlike connection-bearing dicts,
    which D-01/D-02/D-09 still reject at compile).
    """

    def test_query_source_csv_path_resolves_to_csv_source_config(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = local_project(tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "sales.csv").write_text("total\n1\n")
        yaml_content = """
title: Test

queries:
  sales:
    source: ./data/sales.csv
    sql: SELECT * FROM sales

charts:
  c:
    query: sales
    type: kpi
    value: total

rows:
  - c
"""
        result = compile(yaml_content, base_dir=project.directory())
        assert result.success, f"Compilation failed: {result.errors}"
        assert result.board.queries["sales"].source == "./data/sales.csv"
        source_cfg = result.board.sources["./data/sales.csv"]
        assert source_cfg["type"] == "csv"
        assert source_cfg["files"] == {"sales": "data/sales.csv"}

    def test_query_source_json_path_infers_json_type(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = local_project(tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "events.json").write_text("[]")
        yaml_content = """
title: Test

queries:
  events:
    source: ./data/events.json
    sql: SELECT * FROM events

charts:
  c:
    query: events
    type: kpi
    value: total

rows:
  - c
"""
        result = compile(yaml_content, base_dir=project.directory())
        assert result.success, f"Compilation failed: {result.errors}"
        assert result.board.sources["./data/events.json"]["type"] == "json"
        assert result.board.sources["./data/events.json"]["files"] == {
            "events": "data/events.json"
        }

    def test_board_level_source_path_inherits_to_query(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A board/meta `source:` file path also works as a query default (D-04's
        source-inheritance cascade applies the same way to a file ref)."""
        project = local_project(tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "sales.csv").write_text("total\n1\n")
        yaml_content = """
title: Test
source: ./data/sales.csv

queries:
  sales:
    sql: SELECT * FROM sales

charts:
  c:
    query: sales
    type: kpi
    value: total

rows:
  - c
"""
        result = compile(yaml_content, base_dir=project.directory())
        assert result.success, f"Compilation failed: {result.errors}"
        assert result.board.queries["sales"].source == "./data/sales.csv"
        assert "./data/sales.csv" in result.board.sources

    def test_bare_registry_name_is_not_treated_as_file_ref(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """A plain name with no '/' or data extension stays a registry-name lookup."""
        project = local_project(tmp_path)
        yaml_content = """
title: Test

queries:
  q:
    source: warehouse
    sql: SELECT 1

charts:
  c:
    query: q
    type: kpi
    value: total

rows:
  - c
"""
        result = compile(yaml_content, base_dir=project.directory())
        assert result.success, f"Compilation failed: {result.errors}"
        assert result.board.queries["q"].source == "warehouse"
        assert "warehouse" not in result.board.sources

    def test_unknown_file_extension_is_a_compile_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = local_project(tmp_path)
        yaml_content = """
title: Test

queries:
  q:
    source: ./data/sales.xlsx
    sql: SELECT * FROM sales

charts:
  c:
    query: q
    type: kpi
    value: total

rows:
  - c
"""
        result = compile(yaml_content, base_dir=project.directory())
        assert not result.success
        assert any("extension" in e.message for e in result.errors)

    def test_non_identifier_stem_is_a_compile_error(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """No silent sanitization: a stem like 'my-sales' (hyphen) is rejected,
        not auto-renamed to a valid identifier."""
        project = local_project(tmp_path)
        yaml_content = """
title: Test

queries:
  q:
    source: ./data/my-sales.csv
    sql: SELECT * FROM my_sales

charts:
  c:
    query: q
    type: kpi
    value: total

rows:
  - c
"""
        result = compile(yaml_content, base_dir=project.directory())
        assert not result.success
        assert any("identifier" in e.message for e in result.errors)

    def test_path_escaping_project_root_is_rejected(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """Reuses ProjectDirectory.path()'s existing escape guard — no new
        path-safety code needed."""
        project = local_project(tmp_path)
        yaml_content = """
title: Test

queries:
  q:
    source: ../../etc/sales.csv
    sql: SELECT * FROM sales

charts:
  c:
    query: q
    type: kpi
    value: total

rows:
  - c
"""
        result = compile(yaml_content, base_dir=project.directory())
        assert not result.success
        assert any("escape" in e.message for e in result.errors)

    def test_absolute_path_source_is_rejected(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """An absolute path is refused — same ProjectDirectory.path() guard as
        the relative-escape case, exercised on the other invalid-path shape."""
        project = local_project(tmp_path)
        yaml_content = """
title: Test

queries:
  q:
    source: /etc/sales.csv
    sql: SELECT * FROM sales

charts:
  c:
    query: q
    type: kpi
    value: total

rows:
  - c
"""
        result = compile(yaml_content, base_dir=project.directory())
        assert not result.success
        assert any("must be relative" in e.message for e in result.errors)

    def test_inline_dict_source_still_forbidden_alongside_file_paths(self) -> None:
        """D-09/D-12: file paths are allowed inline; connection-bearing dicts are not."""
        yaml_content = """
title: Test

queries:
  q:
    source:
      type: postgres
      host: db.example.com
    sql: SELECT 1

charts:
  c:
    query: q
    type: kpi
    value: total

rows:
  - c
"""
        result = compile(yaml_content)
        assert not result.success
        codes = [e.code for e in result.errors if hasattr(e, "code")]
        assert "ERR-SOURCE-INLINE-FORBIDDEN" in codes


_TWO_ANCHOR_BOARD_YAML = """
title: Test

queries:
  sales:
    source: {ref}
    sql: SELECT * FROM orders

charts:
  c:
    query: sales
    type: kpi
    value: total

rows:
  - c
"""


class TestInlineFileSourceTwoAnchors:
    """An inline file source ref resolves against the board directory or the
    project root. Exactly one existing candidate wins; both existing is a
    compile error naming both paths; neither existing keeps the board-directory
    candidate so the missing file is a per-chart execution error."""

    def test_root_relative_ref_resolves_from_nested_board_dir(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = local_project(tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "orders.parquet").write_text("dummy")
        result = compile(
            _TWO_ANCHOR_BOARD_YAML.format(ref="data/orders.parquet"),
            base_dir=project.directory("charts/sales"),
        )
        assert result.success, f"Compilation failed: {result.errors}"
        source_cfg = result.board.sources["data/orders.parquet"]
        assert source_cfg["files"] == {"orders": "data/orders.parquet"}

    def test_board_relative_ref_still_works_from_nested_board_dir(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = local_project(tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "orders.parquet").write_text("dummy")
        result = compile(
            _TWO_ANCHOR_BOARD_YAML.format(ref="../../data/orders.parquet"),
            base_dir=project.directory("charts/sales"),
        )
        assert result.success, f"Compilation failed: {result.errors}"
        source_cfg = result.board.sources["../../data/orders.parquet"]
        assert source_cfg["files"] == {"orders": "data/orders.parquet"}

    def test_ambiguous_when_both_anchors_have_the_file(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = local_project(tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "orders.parquet").write_text("root copy")
        (tmp_path / "charts" / "sales" / "data").mkdir(parents=True)
        (tmp_path / "charts" / "sales" / "data" / "orders.parquet").write_text(
            "board copy"
        )
        result = compile(
            _TWO_ANCHOR_BOARD_YAML.format(ref="data/orders.parquet"),
            base_dir=project.directory("charts/sales"),
        )
        assert not result.success
        assert result.errors[0].code == "ERR-FILE-SOURCE-AMBIGUOUS"
        message = result.errors[0].message
        assert "'charts/sales/data/orders.parquet'" in message
        assert "'data/orders.parquet'" in message

    def test_missing_from_both_anchors_keeps_the_board_anchor(
        self, tmp_path: Path, local_project: Callable[..., FilesystemProject]
    ) -> None:
        project = local_project(tmp_path)
        result = compile(
            _TWO_ANCHOR_BOARD_YAML.format(ref="data/orders.parquet"),
            base_dir=project.directory("charts/sales"),
        )
        assert result.success, f"Compilation failed: {result.errors}"
        source_cfg = result.board.sources["data/orders.parquet"]
        assert source_cfg["files"] == {"orders": "charts/sales/data/orders.parquet"}


class TestRegistryCredentialLiterals:
    """The root registry rejects raw secret literals; env_var references pass.

    dbt_charts.yml is committed to git, so a literal secret leaks the moment it is
    pushed. The registry accepts a secret only as an env_var()/Jinja reference or
    via a `type: dbt_profile` source that delegates to an out-of-repo profiles.yml.
    """

    def test_literal_password_rejected(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  wh:\n"
            "    type: postgres\n"
            "    host: localhost\n"
            "    dbname: analytics\n"
            "    user: user\n"
            "    password: hunter2\n"
        )
        with pytest.raises(CompilationError) as exc:
            load_project_sources(local_project(tmp_path))
        assert exc.value.code is ERR_SOURCE_CREDENTIAL_LITERAL
        assert "password" in str(exc.value)
        assert "wh" in str(exc.value)

    @pytest.mark.parametrize(
        "field", ["password", "private_key", "keyfile_json", "token", "secret"]
    )
    def test_every_known_secret_field_rejected_as_literal(
        self, tmp_path, field, local_project: Callable[..., FilesystemProject]
    ):
        """Every field in the known-secret set is rejected when authored literally.

        Uses Snowflake because its typed credential fields include several
        secret-bearing authentication options. The raw literal is caught before
        Pydantic parsing regardless.
        """
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  wh:\n"
            "    type: snowflake\n"
            "    account: acct\n"
            "    user: user\n"
            "    database: DB\n"
            "    warehouse: WH\n"
            f"    {field}: literal-secret-value\n"
        )
        with pytest.raises(CompilationError) as exc:
            load_project_sources(local_project(tmp_path))
        assert exc.value.code is ERR_SOURCE_CREDENTIAL_LITERAL
        assert field in str(exc.value)

    def test_inlined_jinja_constant_rejected(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """A Jinja expression that inlines the secret (no env_var) is still a leak."""
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  wh:\n"
            "    type: postgres\n"
            "    host: localhost\n"
            "    dbname: analytics\n"
            "    user: user\n"
            "    password: \"{{ 'hunter2' }}\"\n"
        )
        with pytest.raises(CompilationError) as exc:
            load_project_sources(local_project(tmp_path))
        assert exc.value.code is ERR_SOURCE_CREDENTIAL_LITERAL

    def test_null_optional_secret_is_not_a_literal(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """An explicit-null optional secret (Snowflake OAuth) is 'not provided'."""
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  wh:\n"
            "    type: snowflake\n"
            "    account: acct\n"
            "    user: user\n"
            "    database: DB\n"
            "    warehouse: WH\n"
            "    password: null\n"
        )
        sources = load_project_sources(local_project(tmp_path))
        assert "wh" in sources.sources

    def test_env_var_password_reference_allowed(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  wh:\n"
            "    type: postgres\n"
            "    host: localhost\n"
            "    dbname: analytics\n"
            "    user: user\n"
            "    password: \"{{ env_var('WH_PW') }}\"\n"
        )
        sources = load_project_sources(local_project(tmp_path))
        assert "wh" in sources.sources

    def test_dbt_profile_source_has_no_inline_secret(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  wh:\n    type: dbt_profile\n    profile: analytics\n"
        )
        sources = load_project_sources(local_project(tmp_path))
        assert "wh" in sources.sources

    def test_literal_secret_on_declared_field_rejected(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """Declared DB credential fields cannot carry raw secret literals."""
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  wh:\n"
            "    type: snowflake\n"
            "    account: acct\n"
            "    user: user\n"
            "    database: DB\n"
            "    warehouse: WH\n"
            "    token: sk-abc123\n"
        )
        with pytest.raises(CompilationError) as exc:
            load_project_sources(local_project(tmp_path))
        assert exc.value.code is ERR_SOURCE_CREDENTIAL_LITERAL
        assert "token" in str(exc.value)

    def test_inline_keyfile_json_dict_rejected(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  bq:\n"
            "    type: bigquery\n"
            "    project: demo\n"
            "    dataset: analytics\n"
            "    keyfile_json:\n"
            "      private_key: abc\n"
            "      client_email: sa@demo.iam.gserviceaccount.com\n"
        )
        with pytest.raises(CompilationError) as exc:
            load_project_sources(local_project(tmp_path))
        assert exc.value.code is ERR_SOURCE_CREDENTIAL_LITERAL
        assert "keyfile_json" in str(exc.value)

    def test_keyfile_path_is_not_a_secret(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """A path that POINTS at a secret is not itself secret material."""
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  bq:\n"
            "    type: bigquery\n"
            "    project: demo\n"
            "    dataset: analytics\n"
            "    keyfile: secrets/key.json\n"
        )
        sources = load_project_sources(local_project(tmp_path))
        assert "bq" in sources.sources


class TestRegistryTypedAtLoad:
    """Registry entries are parsed through SourceConfig at load — not lazily."""

    def test_unknown_source_type_errors_at_load(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  wh:\n    type: not_a_real_type\n"
        )
        with pytest.raises(CompilationError) as exc:
            load_project_sources(local_project(tmp_path))
        assert "wh" in str(exc.value)

    def test_missing_required_field_errors_at_load(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """A postgres entry missing dbname fails at load, not lazily at resolve."""
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n"
            "  wh:\n"
            "    type: postgres\n"
            "    host: localhost\n"
            "    user: user\n"
            "    password: \"{{ env_var('PW') }}\"\n"
        )
        with pytest.raises(CompilationError) as exc:
            load_project_sources(local_project(tmp_path))
        assert "wh" in str(exc.value)


class TestExecuteTimeSourceCodePropagation:
    """The resolver's diagnostic code must survive the adapter registry's
    QueryResult flattening — a zero-source deployment's per-chart failure is
    ERR-SOURCE-NOT-FOUND-EMPTY, not the ERR-INTERNAL fallback (which downstream
    surfaces, e.g. Cloud's Data Sources mapping, can do nothing with).
    """

    def test_unknown_source_query_error_carries_resolver_code(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        from dbt_charts.core.diagnostics.execution import QueryError
        from dbt_charts.core.execute import Executor

        yaml_content = """
title: Test

queries:
  q:
    source: warehouse_prod
    sql: SELECT 1 AS value

charts:
  c:
    query: q
    type: kpi
    value: value

rows:
  - c
"""
        # Empty registry: compile skips the source-name check, so the failure
        # happens at execute time via the resolver.
        result = compile(yaml_content, project_sources=ProjectSourcesConfig(sources={}))
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )

        with pytest.raises(QueryError) as exc:
            executor.execute_query("q")
        assert exc.value.code is not None
        assert exc.value.code.code == "ERR-SOURCE-NOT-FOUND-EMPTY"
        assert "warehouse_prod" in str(exc.value)

    def test_allowlisted_not_found_diagnostic_keeps_code_and_hints(
        self, local_project: Callable[..., FilesystemProject]
    ) -> None:
        """The Cloud-shaped path: AllowlistedSourceResolver raises
        ERR-SOURCE-NOT-FOUND with structured fields; handle_adapter_error must
        carry both onto the classified QueryError so its Diagnostic keeps the
        code AND its hint_generator gets the `source` field it requires.
        Forwarding the code without the fields made the hint call raise
        ``TypeError: suggest_close_source() missing 1 required positional
        argument: 'source'`` — extra fields are absorbed by its ``**_kwargs``,
        missing ones are not — failing the whole board render.
        """
        from dbt_charts.core.compile.models.query.normalized import SqlQuery
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.execute.source_resolver import (
            AllowlistedSourceResolver,
        )

        registry = build_adapter_registry(
            local_project(Path.cwd()), resolver=AllowlistedSourceResolver()
        )
        result = registry.execute(
            SqlQuery(sql="SELECT 1", source="warehouse_prod", limit=None),
            query_name="q",
        )
        assert not result.is_success
        assert result.error is not None
        assert result.error.code is not None
        assert result.error.code.code == "ERR-SOURCE-NOT-FOUND"
        assert result.error.fields["source"] == "warehouse_prod"

        # Mirrors Executor's own raise sites: stamp query_name onto the
        # already-built error, then build the Diagnostic from it.
        result.error.fields.setdefault("query_name", "q")
        diagnostic = result.error.to_diagnostic()
        assert diagnostic.code == "ERR-SOURCE-NOT-FOUND"
        assert "warehouse_prod" in diagnostic.message
        # Query attribution rides structurally, not as a message suffix:
        # from_code skips ExecutionError's " (query: q)" decoration, and
        # display_message() re-appends it from this field for CLI/SVG output.
        assert diagnostic.query == "q"


class TestParseSourceConfigEnv:
    """``env`` is the one render the parser performs: a sealed caller (Cloud,
    reading a tenant's committed YAML) passes ``{}`` and the process
    environment is unreachable; the default is the live environment."""

    def test_sealed_parse_cannot_read_the_process_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.compile.models.source import parse_source_config

        monkeypatch.setenv("SEALED_SECRET", "data/from_env.csv")
        data = {"type": "csv", "files": {"t": "{{ env_var('SEALED_SECRET') }}"}}

        default = parse_source_config(data)
        assert default.model_dump(by_alias=True)["files"]["t"] == "data/from_env.csv"
        with pytest.raises(ValueError, match="SEALED_SECRET"):
            parse_source_config(data, env={})
        supplied = parse_source_config(data, env={"SEALED_SECRET": "data/given.csv"})
        assert supplied.model_dump(by_alias=True)["files"]["t"] == "data/given.csv"
